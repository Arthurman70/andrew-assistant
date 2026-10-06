"""Keep PortAudio playback outside the microphone process without per-reply imports."""
import atexit
import base64
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from audio_utils import playback_timeout

ROOT=Path(__file__).resolve().parent


class SpeakerPlayer:
    def __init__(self):
        self.process=None;self.responses=queue.Queue();self.lock=threading.Lock()
        self.state_lock=threading.RLock();self.current=None;self.paused_audio=None;self.pause_until=0
        atexit.register(self.close)

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.kill();self.process.wait(timeout=5)
        self.process=None

    def start(self):
        if self.process and self.process.poll() is None:return
        self.process=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'audio_player.py'),'--worker'],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.responses=queue.Queue()
        process,responses=self.process,self.responses
        def read():
            for line in process.stdout:
                try:responses.put(json.loads(line))
                except ValueError:continue
            responses.put({'error':'Speaker process disconnected.'})
        threading.Thread(target=read,daemon=True).start()
        if not responses.get(timeout=10).get('ready'):
            raise RuntimeError('Speaker process could not start.')

    def play(self,wav,device,volume,on_start=lambda **values:None):
        with self.lock:
            item={'wav':wav,'frames':0,'interrupted':False}
            with self.state_lock:self.current=item
            try:
                self.start()
                self.process.stdin.write(json.dumps({'audio':base64.b64encode(wav).decode(),
                    'device':device or 'auto','volume':volume})+'\n');self.process.stdin.flush()
                deadline=time.monotonic()+playback_timeout(wav)
                while True:
                    message=self.responses.get(timeout=max(.01,deadline-time.monotonic()))
                    if message.get('error'):raise RuntimeError(message['error'])
                    if 'progress' in message:item['frames']=message['progress']
                    if message.get('started'):on_start(device=message['device'],fallback=message['fallback'])
                    if message.get('finished'):return
            except Exception:
                if item['interrupted']:return
                # Never replay a possibly partially spoken response or rerun its action.
                self.close();raise
            finally:
                with self.state_lock:
                    if self.current is item:self.current=None

    def pause(self):
        from interruption import remaining_wav
        with self.state_lock:
            item=self.current
            if item:
                item['interrupted']=True
                self.paused_audio=remaining_wav(item['wav'],item['frames'])
                self.pause_until=time.monotonic()+120
                self.close()

    def stop(self):
        self.pause()
        with self.state_lock:self.paused_audio=None;self.pause_until=0

    def resume(self):
        with self.state_lock:
            content=self.paused_audio if self.pause_until>time.monotonic() else None
            self.paused_audio=None;self.pause_until=0
            return content
