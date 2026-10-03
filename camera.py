"""Requested Pi webcam preview/capture and explicit visual questions."""
import base64
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time


class Camera:
    def __init__(self, app, notify=None):
        self.app, self.notify = app, notify
        self.lock = threading.RLock()
        self.preview_until = 0
        self.preview_id = ''
        self.frame = None
        self.frame_at = 0
        self.state = {'phase':'off','message':'Camera is off.','answer':''}
        self.analysis_lock = threading.Lock()
        self.media = app.directory/'camera'
        self.media.mkdir(parents=True,exist_ok=True)

    def status(self):
        if self.preview_until and time.time()>=self.preview_until:
            with self.lock:
                self.preview_until=0;self.preview_id='';self.frame=None
                if self.state['phase']=='preview':self.state.update(phase='off',message='Preview ended. Camera is off.')
        with self.app.lock:
            jobs=[dict(r) for r in self.app.db.execute("SELECT id,kind,status,expires FROM jobs WHERE kind IN ('photo','video') ORDER BY expires DESC LIMIT 8")]
        captures=[]
        for path in self.media.glob('*.json'):
            try: captures.append(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError,OSError): pass
        captures.sort(key=lambda m:m['at'],reverse=True)
        return dict(self.state,preview_remaining=max(0,self.preview_until-time.time()),jobs=jobs,captures=captures[:8])

    def control(self):
        with self.app.lock:
            allowed=[r[0] for r in self.app.db.execute("SELECT id FROM jobs WHERE kind IN ('photo','video') AND status='sent' AND expires>?",(time.time(),))]
        return {'preview_remaining':max(0,self.preview_until-time.time()),'preview_id':self.preview_id,'allowed_jobs':allowed}

    def online(self):
        if time.time()-self.app.relay_seen >= 20:
            raise ValueError('The Pi webcam is offline. Reconnect the Pi before opening its camera.')

    def preview(self, seconds=120):
        self.online()
        with self.lock:
            self.preview_id=secrets.token_hex(12)
            self.preview_until=time.time()+min(300,max(1,seconds))
            self.frame=None
            self.state.update(phase='preview',message='Pi webcam preview is on. It closes automatically after two minutes.')
        return 'The Pi webcam preview is on for two minutes. Ask for a photo or a short video. Say turn off the camera to close it sooner.'

    def stop(self):
        with self.lock,self.app.lock,self.app.db:
            self.preview_until=0;self.preview_id='';self.frame=None
            self.app.db.execute("UPDATE jobs SET status='cancelled' WHERE kind IN ('photo','video') AND status IN ('pending','sent')")
            self.state.update(phase='off',message='Camera off. Pending captures cancelled.')
        return 'Camera off. Pending photos and video recordings are cancelled.'

    def accept_frame(self, session, content):
        with self.lock:
            if not session or session!=self.preview_id or time.time()>=self.preview_until:
                raise ValueError('Camera preview has ended.')
            if not content.startswith(b'\xff\xd8') or len(content)>2_000_000:
                raise ValueError('Invalid camera preview frame.')
            self.frame=content;self.frame_at=time.time()

    def request(self, kind='photo',seconds=10,source='pc',question=None,provider=None,model=None):
        self.online()
        if kind not in ('photo','video'): raise ValueError('Choose a photo or video.')
        seconds=int(seconds)
        if kind=='video' and not 1<=seconds<=30: raise ValueError('Choose a video length from 1 to 30 seconds.')
        if question:
            from providers import resolve
            provider,model,_=resolve(self.app,provider,model)
        with self.app.lock,self.app.db:
            if self.app.db.execute("SELECT 1 FROM jobs WHERE kind IN ('photo','video') AND status IN ('pending','sent') AND expires>?",(time.time(),)).fetchone():
                raise ValueError('A camera capture is already in progress. Say stop camera to cancel it.')
            reference=secrets.token_hex(16)
            self.app.db.execute('INSERT INTO jobs(id,kind,expires,status,payload) VALUES (?,?,?,?,?)',
                (reference,kind,time.time()+90,'pending',json.dumps({'seconds':seconds,'source':source,
                    'question':question,'provider':provider,'model':model})))
        self.state.update(phase='capturing',message=f'Requested {kind} from the Pi webcam.',answer='')
        return ('Taking one photo.' if kind=='photo' else f'Recording a {seconds}-second video without microphone audio.') + (
            f' Then I will send it to {provider} for your question.' if question else ' It will stay on this PC until you ask to send it to an AI.')

    def accept(self, reference, kind, content):
        if kind=='photo':
            if not content.startswith(b'\xff\xd8') or len(content)>4_000_000: raise ValueError('Invalid JPEG capture.')
        elif kind=='video':
            if len(content)>22_000_000 or len(content)<16 or content[4:8]!=b'ftyp': raise ValueError('Invalid MP4 capture.')
        else: raise ValueError('Invalid capture type.')
        with self.lock,self.app.lock,self.app.db:
            row=self.app.db.execute("SELECT * FROM jobs WHERE id=? AND kind=? AND status='sent' AND expires>?",
                (reference,kind,time.time())).fetchone()
            if not row: raise ValueError('Camera request expired, was cancelled, or was already used.')
            payload=json.loads(row['payload'] or '{}')
            extension='jpg' if kind=='photo' else 'mp4'
            (self.media/(reference+'.'+extension)).write_bytes(content)
            metadata={'id':reference,'kind':kind,'at':time.time(),'seconds':payload.get('seconds') if kind=='video' else None,
                      'url':'/api/camera-media/'+reference+'.'+extension,'sent_to':[]}
            (self.media/(reference+'.json')).write_text(json.dumps(metadata),encoding='utf-8')
            self.app.db.execute("UPDATE jobs SET status='complete' WHERE id=?",(reference,))
            self.state.update(phase='ready',message=f'{kind.capitalize()} saved locally. Camera capture finished.')
        if payload.get('question'):
            self.analyze(reference,payload['question'],payload.get('provider'),payload.get('model'),payload.get('source','pc'))
        else:
            self.announce(self.state['message'],payload.get('source','pc'))

    def failed(self, reference, message):
        with self.app.lock,self.app.db:
            row=self.app.db.execute("SELECT payload FROM jobs WHERE id=? AND kind IN ('photo','video') AND status='sent'",(reference,)).fetchone()
            if not row: return
            self.app.db.execute("UPDATE jobs SET status='failed' WHERE id=?",(reference,))
        self.state.update(phase='error',message='Camera capture failed: '+str(message)[:300])
        self.announce(self.state['message'],json.loads(row['payload'] or '{}').get('source','pc'))

    def announce(self,message,source):
        self.app.event(message)
        if self.notify:
            try:self.notify(message,source)
            except Exception:pass

    def latest(self,kind=None):
        items=[m for m in self.status()['captures'] if not kind or m['kind']==kind]
        if not items: raise ValueError('Take a '+(kind or 'photo or video')+' first.')
        return items[0]['id']

    def analyze(self,reference,question='Describe what you can see.',provider=None,model=None,source='pc'):
        from providers import resolve
        provider,model,_=resolve(self.app,provider,model)
        if not re.fullmatch(r'[a-f0-9]{32}',reference): raise ValueError('Invalid capture reference.')
        metadata=json.loads((self.media/(reference+'.json')).read_text(encoding='utf-8'))
        if not isinstance(question,str) or not 1<=len(question)<=4000: raise ValueError('Ask a question about the capture.')
        if not self.analysis_lock.acquire(blocking=False): raise ValueError('I am still answering the previous camera question.')
        self.state.update(phase='analyzing',message=f'Sending this requested capture to {provider}.',answer='')
        def work():
            try:
                with tempfile.TemporaryDirectory(prefix='camera-frames-',dir=self.app.directory) as folder:
                    if metadata['kind']=='photo':
                        images=[self.media/(reference+'.jpg')]
                        prompt=question
                    else:
                        ffmpeg=shutil.which('ffmpeg')
                        if not ffmpeg: raise ValueError('Video analysis needs FFmpeg on this PC.')
                        duration=float(metadata.get('seconds') or 10)
                        subprocess.run([ffmpeg,'-loglevel','error','-i',str(self.media/(reference+'.mp4')),
                            '-vf',f'fps=4/{duration},scale=640:-2','-frames:v','4',str(Path(folder)/'frame-%02d.jpg')],
                            capture_output=True,timeout=30,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        images=sorted(Path(folder).glob('frame-*.jpg'))
                        if not images: raise ValueError('No frames could be read from that video.')
                        prompt=f'These are {len(images)} evenly sampled frames, in order, from a {duration:g}-second silent video. Explain that your answer covers sampled frames, not every moment or audio. '+question
                    self.app.request.source=source
                    answer=self.app.ai(prompt,provider,model,images=images)
                metadata['sent_to'].append({'provider':provider,'model':model,'at':time.time()})
                (self.media/(reference+'.json')).write_text(json.dumps(metadata),encoding='utf-8')
                self.state.update(phase='ready',answer=answer,message=f'{provider} replied about this capture.')
                self.announce(answer,source)
            except Exception as exc:
                message=str(exc)[:500] if isinstance(exc,ValueError) else 'The visual request could not finish. The capture remains saved locally.'
                self.state.update(phase='error',message=message)
                self.announce(message,source)
            finally:self.analysis_lock.release()
        threading.Thread(target=work,daemon=True).start()
        return f'I am sending that {metadata["kind"]} to {provider}, {model or "the account default"}, for your question.'

    def route(self,text,source='pc'):
        low=text.lower().strip()
        if low in ('turn on camera','turn on the camera','turn the camera on','turn camera on','turn on webcam','turn on the webcam','turn webcam on','turn the webcam on',
                   'open camera','open the camera','show camera','show the camera','show me the camera'):
            return self.preview()
        if low in ('turn off camera','turn off the camera','turn the camera off','close camera','close the camera',
                   'turn off webcam','turn off the webcam','turn webcam off','turn the webcam off',
                   'stop camera','stop recording','stop video recording','disable camera'):
            return self.stop()
        # Capture-and-ask is explicit permission to send that capture to the selected account.
        match=re.fullmatch(r'(?:take|snap|capture) (?:a |one )?(?:photo|picture)(?: and (?:send (?:it )?to|ask) (grok|claude|openai|chatgpt)(?: (?:to )?(.*))?)?',text,re.I)
        if match:
            return self.request('photo',source=source,provider=match[1].lower() if match[1] else None,
                                question=(match[2] or 'Describe this photo.') if match[1] else None)
        match=re.fullmatch(r'(?:record|take|capture) (?:a )?(?:(.+?)[ -])?(?:second[ -])?(?:video|clip)(?: for (.+))?',low)
        if match:
            duration=match[2] or match[1] or '10 seconds'
            if not re.search(r'second|minute',duration): duration+=' seconds'
            return self.request('video',self.app.seconds(duration),source)
        if re.fullmatch(r'show (?:me )?(?:the |my )?(?:latest |last )?(photo|picture|video|clip)',low):
            return 'Your saved captures are in the webcam panel on the PC. They have not been sent to an AI. Say send the latest photo or video to choose AI analysis.'
        match=re.fullmatch(r'(?:send|show) (?:the |my )?(?:latest |last )?(photo|picture|video|clip)(?: to (grok|claude|openai|open ai|chatgpt|local))?(?: (?:and ask|and tell me|to ask) (.+))?',text,re.I)
        if match:
            kind='photo' if match[1].lower() in ('photo','picture') else 'video'
            return self.analyze(self.latest(kind),match[3] or 'Describe what you can see.',match[2].lower() if match[2] else None,source=source)
        if low in ('what do you see','what can you see','look through the camera','look at this','describe what you see'):
            return self.request('photo',source=source,question='Describe what you can see in this requested photo.')
        if low in ('camera status','webcam status'):
            return self.state['message']
        return None
