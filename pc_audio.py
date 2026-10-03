"""Hotplug-aware microphone capture with shared offline recognition."""
from collections import deque
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import numpy as np
from wake_capture import WakeCapture
from wake_detector import wake_phrase
from interruption import InterruptDetector
from media_activity import MediaActivity

ROOT = Path(__file__).resolve().parent

class PCVoice:
    def __init__(self, app, audio_handler, speaking, interrupt_handler=None):
        self.app, self.audio_handler, self.speaking = app, audio_handler, speaking
        self.interrupt_handler=interrupt_handler
        self.state = {'ready': False, 'device': '', 'error': '', 'peak': 0, 'commands': 0,
                      'phase': 'starting', 'last_heard': '', 'last_answer': '', 'wakes':0}
        self.inventory = {'inputs': [], 'outputs': [], 'default_input': '', 'default_output': ''}
        self.lock = threading.RLock()
        self.desired = None
        self.busy = threading.Event()
        self.requests = queue.Queue(maxsize=1)
        self.media=MediaActivity()

    def update(self, **values):
        with self.lock: self.state.update(values)

    def status(self):
        with self.lock: return dict(self.state)

    def devices(self):
        with self.lock: return dict(self.inventory)

    def monitor(self):
        while True:
            try:
                result = subprocess.run([str(ROOT / '.venv/Scripts/python.exe'), str(ROOT / 'audio_devices.py')],
                    capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
                inventory = json.loads(result.stdout)
                chosen = self.app.get('pc_input') or 'auto'
                available = [d['name'] for d in inventory['inputs']]
                if chosen == 'auto': chosen = inventory['default_input'] or next(iter(available), None)
                if chosen not in available: chosen = None
                with self.lock: self.inventory, self.desired = inventory, chosen
            except Exception as exc:
                self.update(error='Audio device discovery: ' + str(exc)[:120])
            time.sleep(3)

    def worker(self):
        while True:
            pcm, captured_at = self.requests.get()
            if self.app.snooze_remaining('pc') or not self.app.get('pc_listening'):
                self.busy.clear();self.requests.task_done();continue
            self.busy.set(); self.update(phase='recognizing')
            try:
                result = self.audio_handler(pcm, 'pc', captured_at=captured_at,wake_detected=True)
                values = {'last_heard': result.get('transcript', ''), 'error': '', 'phase': 'listening'}
                if result.get('answer'): values['last_answer'] = result['answer']
                self.update(**values)
                if result.get('accepted') and result.get('answer'):
                    with self.lock: self.state['commands'] += 1
            except Exception as exc:
                self.update(error=str(exc)[:180], phase='listening')
            finally:
                self.busy.clear();self.requests.task_done()

    def run(self):
        threading.Thread(target=self.monitor, daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self.media.run, daemon=True).start()
        while True:
            if self.app.snooze_remaining('pc'):
                self.update(ready=False,phase='snoozed',peak=0,error='');time.sleep(.2);continue
            if not self.app.get('pc_listening'):
                self.update(ready=False, phase='paused', peak=0); time.sleep(0.3); continue
            if not self.desired:
                self.update(ready=False, phase='disconnected', device='', peak=0,
                            error='Choose a connected microphone, or reconnect it. Andrew will retry automatically.')
                time.sleep(1); continue
            try: self.listen(self.desired)
            except Exception as exc:
                self.update(ready=False, phase='reconnecting', error=str(exc)[:160], peak=0); time.sleep(2)

    def listen(self, chosen):
        import sounddevice as sd
        import webrtcvad
        # This process captures only. Playback is isolated in its own process.
        sd._terminate(); sd._initialize()
        devices = sd.query_devices()
        device_id = next(i for i,d in enumerate(devices) if d['name'] == chosen and d['max_input_channels'])
        frames = queue.Queue(maxsize=100)
        def callback(data, count, timing, status):
            if self.app.snooze_remaining('pc'): return
            try: frames.put_nowait(bytes(data))
            except queue.Full: pass
        capture=WakeCapture(ROOT/'runtime/wake',self.app.get('name'))
        interrupt=InterruptDetector(ROOT/'runtime/wake',self.app.get('name'))
        with sd.RawInputStream(device=device_id, samplerate=16000, blocksize=320,
                               dtype='int16', channels=1, callback=callback) as stream:
            self.update(ready=True, device=chosen, error='', phase='listening')
            while self.app.get('pc_listening') and not self.app.snooze_remaining('pc') and self.desired == chosen and stream.active:
                try: frame = frames.get(timeout=.5)
                except queue.Empty:
                    if self.speaking.is_set() or self.busy.is_set() or self.app.snooze_remaining('pc'):
                        capture.reset();continue
                    if not stream.active: raise RuntimeError('Microphone stopped delivering audio; reconnecting.')
                    continue
                samples = np.frombuffer(frame, dtype='<i2').astype(np.float32)
                self.update(peak=int(np.max(np.abs(samples))))
                paused=bool(getattr(self.app,'speech_interruption',None) and self.app.speech_interruption.active('pc'))
                occupied=self.speaking.is_set() or self.busy.is_set()
                action=interrupt.feed(frame,self.app.get('name'),occupied or paused,paused)
                if action and self.interrupt_handler:
                    self.interrupt_handler('pc',action);capture.reset()
                    self.update(phase='speech paused' if action=='pause' else 'continuing',error='')
                if occupied:
                    capture.reset();continue
                was_active=capture.active
                request=capture.feed(frame,self.app.get('name'),self.app.get('pc_wake_mode'),self.media.active())
                self.update(wake_phrase=wake_phrase(self.app.get('name')),background_guard=capture.cautious,
                            media_detected=self.media.active(),media_guard_ready=self.media.ready,
                            empty_wakes=capture.empty_wakes,rejected_wakes=capture.rejected_wakes)
                if not was_active and capture.active:
                    with self.lock: self.state['wakes']+=1
                if not self.busy.is_set(): self.update(phase=capture.phase)
                if request is not None:
                    self.busy.set()
                    self.update(phase='recognizing')
                    # Capture pauses until this bounded request is handled.
                    # There is only one producer, so no detected request is dropped.
                    self.requests.put((request,time.time()))
