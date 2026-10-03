"""On-request webcam worker. Preview is RAM-only; clips contain no microphone audio."""
import base64
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time


class CameraCapture:
    def __init__(self,api,config,state):
        self.api,self.config,self.state=api,config,state
        self.state['camera']={'phase':'off','error':''}
        self.jobs=queue.Queue(maxsize=2)
        self.allowed=set()
        self.preview_deadline=0
        self.preview_id=''
        self.thread=threading.Thread(target=self.run,daemon=True)

    def configure(self,control):
        self.allowed=set(control.get('allowed_jobs',[]))
        self.preview_deadline=time.monotonic()+min(300,max(0,float(control.get('preview_remaining',0))))
        self.preview_id=control.get('preview_id','')

    def device(self):
        configured=self.config.get('camera_device','auto')
        if configured!='auto' and re.fullmatch(r'/dev/video\d+',configured) and Path(configured).exists():
            return configured
        for link in sorted(Path('/dev/v4l/by-id').glob('*video-index0')):
            return str(link.resolve())
        for path in sorted(Path('/sys/class/video4linux').glob('video*')):
            if (path/'index').read_text().strip()=='0' and 'usb' in str((path/'device').resolve()).lower():
                return '/dev/'+path.name
        raise ValueError('No USB webcam video device is connected.')

    def still(self,path):
        subprocess.run(['fswebcam','--device',self.device(),'--no-banner','--resolution','640x480',
                        '--skip','5','--jpeg','85',str(path)],capture_output=True,check=True,timeout=10)

    def video(self,path,seconds,reference):
        ffmpeg=shutil.which('ffmpeg')
        env=os.environ.copy()
        if not ffmpeg:
            ffmpeg='/run/andrew/display/root/usr/bin/ffmpeg'
            libraries='/run/andrew/display/root/usr/lib/aarch64-linux-gnu'
            env['LD_LIBRARY_PATH']=':'.join([libraries]+[libraries+'/'+name for name in ('blas','lapack','pulseaudio')])
        if not Path(ffmpeg).is_file(): raise ValueError('The Pi video capture tool is still being prepared.')
        args=[ffmpeg,'-loglevel','error','-y','-f','v4l2','-framerate','15','-video_size','640x480',
              '-i',self.device(),'-t',str(seconds),'-an','-c:v','libx264','-threads','1','-preset','ultrafast',
              '-crf','28','-pix_fmt','yuv420p','-movflags','+faststart','-fs','20000000',str(path)]
        proc=subprocess.Popen(args,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+seconds+15
        try:
            while proc.poll() is None:
                if reference not in self.allowed: raise ValueError('Recording cancelled. Camera closed.')
                if time.monotonic()>deadline: raise ValueError('Video capture timed out. Camera closed.')
                time.sleep(.15)
            if proc.returncode: raise ValueError('Video capture failed. Check the webcam connection.')
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=3)

    def capture(self,job):
        reference=job['id']
        if reference not in self.allowed: return
        kind=job['kind']
        self.state['camera']={'phase':'capturing','kind':kind,'error':''}
        try:
            with tempfile.TemporaryDirectory(prefix='camera-',dir='/run/andrew') as folder:
                if kind=='photo':
                    path=Path(folder)/'photo.jpg';self.still(path)
                else:
                    seconds=int(json.loads(job.get('payload') or '{}').get('seconds',10))
                    if not 1<=seconds<=30: raise ValueError('Video duration must be 1 to 30 seconds.')
                    path=Path(folder)/'video.mp4';self.video(path,seconds,reference)
                if reference not in self.allowed: return
                self.api('/api/camera-capture',{'job_id':reference,'kind':kind,
                    'content':base64.b64encode(path.read_bytes()).decode()})
            self.state['camera']={'phase':'off','error':''}
        except Exception as exc:
            if reference not in self.allowed:
                self.state['camera']={'phase':'off','error':''}
                return
            message=str(exc)[:160] if isinstance(exc,ValueError) else 'Webcam capture failed. Reconnect the USB webcam and try again.'
            self.state['camera']={'phase':'error','error':message}
            try:self.api('/api/camera-error',{'job_id':reference,'message':message})
            except Exception:pass

    def run(self):
        while True:
            try:
                job=self.jobs.get(timeout=.2)
                self.capture(job)
                continue
            except queue.Empty:pass
            if self.preview_id and time.monotonic()<self.preview_deadline:
                session=self.preview_id
                try:
                    with tempfile.TemporaryDirectory(prefix='preview-',dir='/run/andrew') as folder:
                        path=Path(folder)/'preview.jpg';self.still(path)
                        if session==self.preview_id and time.monotonic()<self.preview_deadline:
                            self.api('/api/camera-frame',{'session':session,'jpeg':base64.b64encode(path.read_bytes()).decode()})
                    self.state['camera']={'phase':'preview','error':''}
                except Exception:
                    self.state['camera']={'phase':'error','error':'Camera preview unavailable. Check the webcam.'}
                time.sleep(.7)
            elif self.state.get('camera',{}).get('phase')=='preview':
                self.state['camera']={'phase':'off','error':''}
