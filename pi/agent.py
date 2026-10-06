"""Lightweight audio satellite. Recognition/voice generation run locally on the PC."""
from array import array
import base64
from collections import deque
import json
import hashlib
import math
import os
from pathlib import Path
import queue
import re
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request
from wake_capture import WakeCapture
from wake_detector import wake_phrase
from audio_utils import scale_wav,playback_timeout
from camera_capture import CameraCapture
from interruption import InterruptDetector, remaining_wav
import io
import wave

BASE = Path(os.environ.get('ANDREW_BASE_DIR','/opt/andrew'))
CONFIG = json.loads((BASE/'relay-config.json').read_text())
TLS = ssl.create_default_context(cafile=str(BASE/'pc.crt'))
VERSION_FILE=Path(__file__).parent/'deployment-version.txt'
STATE = {'protocol':2,'version':VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12],
         'phase':'starting','device':'','speaker':'','peak':0,'error':'','wakes':0}
BUSY = threading.Event()
REQUEST_BUSY = threading.Event()
PAUSED = threading.Event()
PLAY_LOCK = threading.Lock()
REQUESTS = queue.Queue(maxsize=1)
ANNOUNCEMENTS=queue.Queue(maxsize=4)
DEVICE_CACHE={}
SPEECH_STATE={'process':None,'wav':None,'start':0,'paused':None,'until':0,'revision':0,'cancel_until':0}
SPEECH_LOCK=threading.RLock()

def speech_control(action, notify=True):
    with SPEECH_LOCK:
        process=SPEECH_STATE['process']
        if action in ('pause','stop'):
            if process and process.poll() is None:
                with wave.open(io.BytesIO(SPEECH_STATE['wav']),'rb') as audio:
                    frames=int(max(0,time.monotonic()-SPEECH_STATE['start'])*audio.getframerate())
                SPEECH_STATE['paused']=remaining_wav(SPEECH_STATE['wav'],frames) if action=='pause' else None
                SPEECH_STATE['until']=time.monotonic()+120
                process.terminate()
            SPEECH_STATE['cancel_until']=time.monotonic()+1
            if action=='stop':SPEECH_STATE['paused']=None
            while not ANNOUNCEMENTS.empty():
                try:ANNOUNCEMENTS.get_nowait()
                except queue.Empty:break
        elif action=='resume':
            content=SPEECH_STATE['paused'] if SPEECH_STATE['until']>time.monotonic() else None
            SPEECH_STATE['paused']=None;SPEECH_STATE['cancel_until']=0
            if content:ANNOUNCEMENTS.put_nowait(base64.b64encode(content).decode())
    if notify:
        def update():
            try:api('/api/speech-control',{'action':action})
            except Exception:STATE['error']='Speech stopped locally; PC connection needs attention.'
        threading.Thread(target=update,daemon=True).start()


def snoozed():
    return time.monotonic()<CONFIG.get('snooze_deadline',0)

def api(path, data=None):
    req = urllib.request.Request(CONFIG['pc_url']+path,
        None if data is None else json.dumps(data).encode(),
        {'Authorization':'Bearer '+CONFIG['token'],'Content-Type':'application/json'})
    with urllib.request.urlopen(req,context=TLS,timeout=900 if path=='/api/voice' else (60 if path=='/api/camera-capture' else 8)) as reply:
        return json.load(reply)

def devices(command):
    cached=DEVICE_CACHE.get(command)
    if cached and time.monotonic()-cached[0]<15:return cached[1]
    result = subprocess.run([command,'-l'],capture_output=True,text=True,check=True,timeout=6)
    choices=re.findall(r'card (\d+): (\w+) \[(.+?)\], device (\d+):',result.stdout)
    DEVICE_CACHE[command]=(time.monotonic(),choices)
    return choices

def microphone():
    choices = devices('arecord')
    configured = CONFIG.get('microphone_device','auto')
    selected = next((c for c in choices if any(w in c[2].lower() for w in ('usb','webcam','camera'))),
                    next(iter(choices),None))
    if not selected: raise RuntimeError('No microphone is connected. Retrying automatically.')
    if configured != 'auto':
        selected = next((c for c in choices if f'plughw:{c[1]},{c[3]}'==configured),None)
        if not selected: raise RuntimeError('Selected microphone is disconnected. Reconnect it or choose Automatic.')
    subprocess.run(['amixer','-c',selected[1],'sset','Mic','cap'],capture_output=True,timeout=5)
    STATE['device'] = selected[2]
    return CONFIG.get('microphone_device') if CONFIG.get('microphone_device') not in (None,'auto') else f'plughw:{selected[1]},{selected[3]}'

def speaker():
    choices = devices('aplay')
    configured = os.environ.get('ANDREW_SPEAKER_DEVICE',CONFIG.get('speaker_device','auto'))
    if configured != 'auto':
        if any(f'plughw:{c[1]},{c[3]}'==configured for c in choices):
            STATE['speaker'] = configured
            return configured
    selected = next((c for c in choices if 'usb' in c[2].lower()),None)
    selected = selected or next((c for c in choices if c[1]=='Headphones'),next(iter(choices),None))
    if not selected: raise RuntimeError('No speaker output is available.')
    STATE['speaker'] = selected[2]
    return f'plughw:{selected[1]},{selected[3]}'

def play(encoded):
    with PLAY_LOCK:
        BUSY.set(); STATE['phase']='speaking'
        try:
            STATE.update(playback_started_at=time.time(),playback_state='playing')
            content=base64.b64decode(encoded,validate=True)
            with SPEECH_LOCK:
                if time.monotonic()<SPEECH_STATE['cancel_until']:return
                process=subprocess.Popen(['aplay','-q','-D',speaker()],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                SPEECH_STATE.update(process=process,wav=content,start=time.monotonic())
            process.communicate(scale_wav(content,CONFIG.get('volume',80)),timeout=playback_timeout(content))
            if process.returncode and process.returncode>=0:raise RuntimeError('Speaker playback failed.')
            STATE.update(playback_finished_at=time.time(),playback_state='finished')
        except Exception:
            DEVICE_CACHE.clear();STATE['playback_state']='failed';raise
        finally:
            with SPEECH_LOCK:SPEECH_STATE['process']=None
            BUSY.clear(); STATE['phase']='listening'

def photo(job):
    if job.get('kind')!='photo' or job.get('expires',0)<=time.time(): return
    with tempfile.TemporaryDirectory() as folder:
        file=Path(folder)/'requested.jpg'
        subprocess.run(['fswebcam','--device',CONFIG.get('camera_device','/dev/video0'),
            '--no-banner','--resolution','640x480','--skip','5',str(file)],
            check=True,timeout=12,capture_output=True)
        api('/api/photo',{'job_id':job['id'],'jpeg':base64.b64encode(file.read_bytes()).decode()})

def poll():
    camera=CameraCapture(api,CONFIG,STATE)
    camera.thread.start()
    while True:
        try:
            result=api('/api/relay')
            if STATE.get('error','').startswith(('PC connection or playback:','Lost the PC connection.')):
                STATE['error']=''
            camera.configure(result.get('camera_control',{}))
            CONFIG['name']=result.get('name','Andrew')
            control=result.get('speech_control',{})
            if control.get('revision',0)>SPEECH_STATE['revision']:
                SPEECH_STATE['revision']=control['revision']
                speech_control(control.get('action'),notify=False)
            if result.get('pause_capture'): PAUSED.set()
            else: PAUSED.clear()
            if result.get('audio_settings'):
                CONFIG['speaker_device']=result['audio_settings'].get('speaker','auto')
                CONFIG['microphone_device']=result['audio_settings'].get('microphone','auto')
                CONFIG['listening']=result['audio_settings'].get('listening',True)
                CONFIG['volume']=result['audio_settings'].get('volume',80)
                CONFIG['snooze_deadline']=time.monotonic()+max(0,float(result['audio_settings'].get('snooze_remaining',0)))
                CONFIG['wake_mode']=result['audio_settings'].get('wake_mode','adaptive')
            STATE['outputs']=[{'id':f'plughw:{c[1]},{c[3]}','name':c[2]} for c in devices('aplay')]
            STATE['inputs']=[{'id':f'plughw:{c[1]},{c[3]}','name':c[2]} for c in devices('arecord')]
            if result.get('audio'):
                ANNOUNCEMENTS.put(result['audio'],timeout=10)
            if result.get('job'):
                job=result['job']
                if job.get('kind') in ('photo','video'):
                    try:camera.jobs.put_nowait(job)
                    except queue.Full:api('/api/camera-error',{'job_id':job['id'],'message':'Camera queue is busy. Try again.'})
                elif job.get('kind')=='display':
                    error=''
                    try:
                        if job.get('expires',0)<=time.time(): raise ValueError('Display request expired.')
                        request=urllib.request.Request('http://127.0.0.1:8771/',job['payload'].encode(),
                            {'Content-Type':'application/json','X-Andrew-Display':'1'})
                        with urllib.request.urlopen(request,timeout=15) as reply: json.load(reply)
                    except Exception as exc: error='Display unavailable: '+str(exc)[:130]
                    api('/api/job-result',{'job_id':job['id'],'error':error})
            try:
                with urllib.request.urlopen('http://127.0.0.1:8771/',timeout=2) as display:
                    STATE['display']=json.load(display)
            except Exception: STATE['display']={'running':False}
            api('/api/relay-health',dict(STATE))
        except Exception:
            STATE['error']='Lost the PC connection. Reconnecting automatically.'
        time.sleep(.5)

def worker():
    while True:
        pcm,captured_at=REQUESTS.get()
        if snoozed() or not CONFIG.get('listening',True):
            REQUEST_BUSY.clear();REQUESTS.task_done();continue
        REQUEST_BUSY.set(); BUSY.set(); STATE['phase']='recognizing'
        try:
            result=api('/api/voice',{'pcm':base64.b64encode(pcm).decode(),'age':time.monotonic()-captured_at,'wake_detected':True})
            STATE['error']=''
            if result.get('audio'):
                play(result['audio']);STATE['responses']=STATE.get('responses',0)+1
            if result.get('accepted') and result.get('answer'):
                print('Voice request recognized and answered through this satellite.',flush=True)
        except Exception as exc:
            STATE['error']='Voice request: '+str(exc)[:120]
            try:play(base64.b64encode((Path(__file__).parent/'voice_offline.wav').read_bytes()).decode())
            except Exception:pass  # Preserve the original visible connection/output error.
        finally:
            REQUEST_BUSY.clear(); BUSY.clear();REQUESTS.task_done()
            STATE['phase']='snoozed' if snoozed() else 'waiting for name'

def capture():
    device=microphone()
    selected = CONFIG.get('microphone_device','auto')
    capture=WakeCapture(Path(__file__).parent/'wake',CONFIG.get('name','Andrew'))
    interrupt=InterruptDetector(Path(__file__).parent/'wake',CONFIG.get('name','Andrew'))
    process=subprocess.Popen(['arecord','-q','-D',device,'-f','S16_LE','-r','16000','-c','1','-t','raw'],stdout=subprocess.PIPE)
    try:
        STATE.update(phase='waiting for name',error='')
        print('Keyword-only microphone ready. Background transcription disabled. Camera closed.',flush=True)
        while CONFIG.get('listening',True) and not snoozed() and selected == CONFIG.get('microphone_device','auto'):
            frame=process.stdout.read(640)
            if len(frame)!=640: raise RuntimeError('Microphone disconnected; reconnecting.')
            occupied=PLAY_LOCK.locked() or REQUEST_BUSY.is_set()
            paused=bool(SPEECH_STATE['paused'] and SPEECH_STATE['until']>time.monotonic())
            action=interrupt.feed(frame,CONFIG.get('name','Andrew'),occupied or paused,paused)
            if action:speech_control(action);capture.reset()
            if occupied or PAUSED.is_set():
                capture.reset();continue
            samples=array('h',frame)
            STATE['peak']=max(abs(v) for v in samples)
            was_active=capture.active
            media=STATE.get('display',{}).get('mode') in ('browser','video')
            request=capture.feed(frame,CONFIG.get('name','Andrew'),CONFIG.get('wake_mode','adaptive'),media)
            STATE.update(wake_phrase=wake_phrase(CONFIG.get('name','Andrew')),background_guard=capture.cautious,
                         empty_wakes=capture.empty_wakes,rejected_wakes=capture.rejected_wakes)
            if not was_active and capture.active: STATE['wakes']+=1
            if not BUSY.is_set(): STATE['phase']=capture.phase
            if request is not None:
                REQUEST_BUSY.set();STATE['phase']='recognizing'
                REQUESTS.put((request,time.monotonic()))
    finally:
        process.terminate()
        try: process.wait(timeout=2)
        except subprocess.TimeoutExpired: process.kill();process.wait(timeout=2)
        process.stdout.close()

def main():
    def announce():
        while True:
            try: play(ANNOUNCEMENTS.get())
            except Exception as exc: STATE['error']='Speaker playback: '+str(exc)[:120]
    threading.Thread(target=announce,daemon=True).start()
    threading.Thread(target=poll,daemon=True).start()
    threading.Thread(target=worker,daemon=True).start()
    while True:
        if snoozed():
            STATE.update(phase='snoozed',peak=0,error='');time.sleep(.2);continue
        if not CONFIG.get('listening',True):
            STATE.update(phase='paused',peak=0); time.sleep(.3); continue
        try: capture()
        except Exception as exc:
            STATE.update(phase='reconnecting',error=str(exc)[:180],peak=0)
            print(STATE['error'],flush=True);time.sleep(2)

if __name__=='__main__': main()
