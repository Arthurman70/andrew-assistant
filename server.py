"""Loopback control panel and TLS-only authenticated LAN relay."""
import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import queue
import re
import secrets
import subprocess
import ssl
import threading
import time
from urllib.parse import urlparse
from core import Andrew, ROOT, request_json
from pc_audio import PCVoice
from voice_routing import VoiceGate, PlaybackGate
from speech_engine import SpeechEngine, VoiceSession
import providers
import improvements
from camera import Camera
from satellite_manager import SatelliteManager
from pc_agent import PCAgent
from speaker_player import SpeakerPlayer
from interruption import SpeechInterruption
from followup import FollowupSessions
from browser_companion import BrowserCompanion

APP = Andrew()
FOLLOWUP=FollowupSessions(APP);APP.followup=FOLLOWUP
COMPANION=BrowserCompanion(APP,ROOT);APP.companion=COMPANION
TOKEN_PATH = ROOT / 'data' / 'relay-token.txt'
if not TOKEN_PATH.exists():
    TOKEN_PATH.write_text(secrets.token_urlsafe(48), encoding='utf-8')
TOKEN = TOKEN_PATH.read_text().strip()
SPEECH = queue.Queue(maxsize=16)
SPEAKING = threading.Event()
SPEECH_READY = threading.Event()
VOICE_GATE = VoiceGate()
PLAYBACK = PlaybackGate()
ENGINE = SpeechEngine(lambda:APP.get('voice'),lambda:APP.get('recognition'),lambda:APP.get('voice_speed'))
SESSIONS = VoiceSession()
PI_AUDIO = queue.Queue(maxsize=8)
BROWSER_AUDIO = queue.Queue(maxsize=32)
PI_HEALTH = {}
SATELLITE = SatelliteManager(PI_HEALTH)
PLAYER=SpeakerPlayer()
RETRY_TEXT='I missed the request. Please say my name and try again.'


def queue_speech(message, cancel=None, ticket=None, followup=None):
    # Backpressure rather than silently throwing away an answer on a full queue.
    SPEECH.put((message,cancel,ticket or INTERRUPT.ticket('pc'),followup),timeout=10)


def voice_wav(text):
    try:return ENGINE.synthesize(text)
    except Exception:
        fallback=ROOT/'assets/voice_retry.wav'
        return fallback.read_bytes() if fallback.is_file() else ENGINE.cue()


def speak(text, followup=None):
    if APP.get('pc_speech'):
        queue_speech(str(text),followup=followup)


def speech_worker():
    try:PLAYER.start()
    except Exception:pass  # A later playback reconnects automatically.
    SPEECH_READY.set()
    while True:
        message = SPEECH.get()
        try:
            if isinstance(message,tuple):
                message,cancel,*rest=message
                ticket=rest[0] if rest else INTERRUPT.ticket('pc')
                followup=rest[1] if len(rest)>1 else None
            else:message,cancel,ticket,followup=message,None,INTERRUPT.ticket('pc'),None
            if ticket.is_set():continue
            if cancel and cancel.is_set():continue
            wav = message if isinstance(message, bytes) else voice_wav(message)
            if ticket.is_set() or (cancel and cancel.is_set()):continue
            def started(**values):
                SPEAKING.set();PLAYBACK.record(wav,'pc')
                SESSIONS.update('pc',playback_started_at=time.time(),playback_state='playing',error='',
                    output=values['device'],output_fallback=values['fallback'])
            PLAYER.play(wav,APP.get('pc_output'),APP.get('pc_volume'),started)
            if followup and not ticket.is_set() and not INTERRUPT.active('pc'):FOLLOWUP.arm('pc',followup)
            SESSIONS.update('pc',playback_finished_at=time.time(),playback_state='paused' if INTERRUPT.active('pc') else 'finished')
        except Exception as exc:
            SESSIONS.update('pc',playback_state='failed',error='Speaker playback failed. Choose a connected speaker in Audio settings.')
            APP.event('PC speaker needs attention; the answer remains visible.')
        finally:
            SPEAKING.clear();SPEECH.task_done()


class ResponseProgress:
    """A cancellable, short acknowledgement for requests that need more time."""
    def __init__(self,source):
        self.source=source;self.done=threading.Event();self.ticket=INTERRUPT.ticket(source)
        self.timer=threading.Timer(1.5,self.announce);self.timer.daemon=True
    def announce(self):
        try:
            if self.done.is_set() or self.ticket.is_set():return
            wav=voice_wav('One moment.')
            if self.done.is_set() or self.ticket.is_set():return
            if self.source=='pc' and APP.get('pc_speech'):queue_speech(wav,self.done,self.ticket)
            elif self.source=='pi':PI_AUDIO.put((time.time()+8,base64.b64encode(wav).decode(),self.done),timeout=2)
        except Exception:pass  # Final answer delivery is independent of this optional cue.
    def __enter__(self):self.timer.start();return self
    def __exit__(self,*args):self.done.set();self.timer.cancel()


def voice_command(text, source, pc_speak=False):
    if APP.snooze_remaining(source) or not APP.get(source+'_listening'):
        return {'accepted':False,'answer':'','sleeping':True}
    if not VOICE_GATE.claim(text, source,now=getattr(APP.request,'captured_at',None)):
        SESSIONS.update(source,phase='answered on other device')
        return {'accepted': False, 'answer': '', 'duplicate':True}
    try:
        with ResponseProgress(source):answer = APP.command(text, source)
        if not isinstance(answer,str) or not answer.strip():
            answer='I could not finish that request. Please try again.'
        if (not getattr(APP.request,'followup_context',False) and getattr(APP.request,'voice_context',False) and not APP.memory.current(source)
                and time.time()>APP.memory.prompted.get(source,0)
                and not re.search(r'call|text|message|stop|cancel|snooze|guest',text,re.I)):
            APP.memory.prompted[source]=time.time()+600
            answer+=' What should I call you?'
    except ValueError as exc:
        answer = str(exc)
    except Exception:
        answer = 'That connection is not responding. Please try again. Your local commands still work.'
    silent=bool(getattr(APP.request,'speech_silent',False))
    if pc_speak and not silent:speak(answer)
    return {'accepted': True, 'answer': answer,'silent':silent}


def audio_response(text, source, ticket=None, followup=None):
    if ticket and ticket.is_set():return {}
    wav = voice_wav(text)
    if ticket and ticket.is_set():return {}
    if source == 'pc':
        if APP.get('pc_speech'):queue_speech(wav,ticket=ticket,followup=followup)
        elif followup:FOLLOWUP.arm('pc',followup)
        return {}
    PLAYBACK.record(wav,'pi')
    return {'audio':base64.b64encode(wav).decode(),**({'followup_grant':followup} if followup else {})}


def pc_task_finished(answer, source):
    SESSIONS.update(source,last_answer=answer,answered_at=time.time())
    if source=='browser':
        try:BROWSER_AUDIO.put_nowait({'answer':answer,'at':time.time()})
        except queue.Full:pass
        return
    if INTERRUPT.active(source):return
    state=APP.pc_agent.status() if APP.pc_agent else {}
    person=state.get('person') if state.get('source')==source and time.time()-state.get('finished_at',0)<5 else APP.memory.current(source)
    grant=FOLLOWUP.prepare(source,person)
    if source=='pi':
        if time.time()-APP.relay_seen<20:
            encoded=base64.b64encode(voice_wav(answer)).decode()
            PI_AUDIO.put((time.time()+60,encoded,None,grant),timeout=10)
        else:speak(answer,followup=FOLLOWUP.prepare('pc',person))
    else:
        if APP.get('pc_speech'):speak(answer,followup=grant)
        elif grant:FOLLOWUP.arm('pc',grant)


APP.pc_agent=PCAgent(APP,notify=pc_task_finished)
IMPROVEMENTS=improvements.manager(APP)
IMPROVEMENTS.notify=pc_task_finished
APP.camera=Camera(APP,notify=pc_task_finished)
APP.notify=pc_task_finished

def resumed_audio(wav,source):
    if source=='pc':queue_speech(wav)

INTERRUPT=SpeechInterruption(PLAYER,resumed_audio)

def speech_control(source,action):
    FOLLOWUP.close(source)
    result=INTERRUPT.control(source,action)
    if action in ('pause','stop'):
        PLAYBACK.stop(source)
        if source=='pc':SPEAKING.clear()
        else:
            while not PI_AUDIO.empty():
                try:PI_AUDIO.get_nowait()
                except queue.Empty:break
    SESSIONS.update(source,playback_state='paused' if action=='pause' else action,phase='waiting for name')
    return result

APP.speech_control=speech_control
APP.speech_interruption=INTERRUPT


def handle_audio(pcm, source, manual=False, captured_at=None,wake_detected=False,followup=None):
    if APP.snooze_remaining(source) or not APP.get(source+'_listening'):
        return {'accepted':False,'transcript':'','answer':'','sleeping':True}
    continuation=FOLLOWUP.claim(source,followup) if followup else None
    if not wake_detected and not continuation:
        return {'accepted':False,'transcript':'','answer':'','wake_required':True}
    end = captured_at or time.time()
    if PLAYBACK.overlaps(end-len(pcm)/32000,end):
        return {'accepted':False,'transcript':'','answer':'','echo_suppressed':True}
    ticket=INTERRUPT.ticket(source)
    started=time.monotonic()
    SESSIONS.update(source, phase='recognizing', error='')
    def retry(reason):
        if APP.snooze_remaining(source) or not APP.get(source+'_listening'):
            return {'accepted':False,'transcript':'','answer':'','sleeping':True}
        SESSIONS.update(source,phase='listening',last_heard='',last_request='',last_answer=RETRY_TEXT,
            answered_at=time.time(),recognition_result=reason,response_seconds=round(time.monotonic()-started,3))
        return {'accepted':False,'transcript':'','answer':RETRY_TEXT,'retry':True,**audio_response(RETRY_TEXT,source)}
    if not pcm:return retry('no speech after wake')
    try:text = ENGINE.transcribe(pcm, APP.get('name'))
    except Exception:return retry('recognition failed')
    recognized=time.monotonic()
    if APP.snooze_remaining(source) or not APP.get(source+'_listening'):
        return {'accepted':False,'transcript':'','answer':'','sleeping':True}
    if PLAYBACK.overlaps(end-len(pcm)/32000,end):
        SESSIONS.update(source,phase='listening')
        return {'accepted':False,'transcript':'','answer':'','echo_suppressed':True}
    SESSIONS.update(source, last_heard=text[:240], phase='listening')
    if not text:
        return retry('speech not understood')
    # Capture already established the wake boundary. Keep names mentioned inside a request.
    command=re.sub(r'^(?:hey\s+)?'+re.escape(APP.get('name'))+r'\b[\s,.!?:;-]*','',text,flags=re.I).strip()
    if not command:
        return retry('wake name without request')
    SESSIONS.update(source,phase='thinking',last_heard=command,last_request=command)
    APP.request.captured_at=end
    APP.memory.begin_voice(pcm,source,command)
    identified=time.monotonic()
    if continuation and continuation.get('person'):
        matched=APP.memory.current(source)
        if matched and matched!=continuation['person']:
            APP.memory.end_voice();return {'accepted':False,'transcript':'','answer':'','wake_required':True}
        if matched is None and getattr(APP.request,'embedding',None) is None:APP.request.person=continuation['person']
    if command.lower().strip().rstrip('.!?') not in ('shut up','stop talking','pause speaking','be quiet','continue','keep going','resume speaking'):
        ticket=INTERRUPT.new_request(source)
    person=APP.memory.current(source);APP.request.followup_context=bool(continuation)
    try:result = voice_command(command,source,False)
    finally:
        APP.memory.end_voice();APP.request.captured_at=None;APP.request.followup_context=False
    answered=time.monotonic()
    if not result['accepted']: return result | {'transcript':text}
    SESSIONS.update(source,phase='speaking',last_answer=result['answer'],answered_at=time.time())
    if not result.get('silent'):
        grant=FOLLOWUP.prepare(source,person)
        result.update(audio_response(result['answer'],source,ticket,grant))
    SESSIONS.update(source,phase='listening',recognition_result='understood',
        response_seconds=round(time.monotonic()-started,3),timing={
            'recognition':round(recognized-started,3),'speaker_match':round(identified-recognized,3),
            'answer':round(answered-identified,3),'voice':round(time.monotonic()-answered,3)})
    return result | {'transcript':text}


class CapturePlayback:
    def is_set(self):
        return SPEAKING.is_set() or PLAYBACK.is_set()


PC_VOICE = PCVoice(APP, handle_audio, CapturePlayback(),speech_control)


def scheduler():
    while True:
        IMPROVEMENTS.notify_completed()
        for message in APP.due():
            if message['source'] == 'pi' and time.time()-APP.relay_seen < 20:
                try:
                    PI_AUDIO.put((time.time()+30,base64.b64encode(voice_wav(message['text'])).decode()),timeout=10)
                except queue.Full: speak(message['text'])
            else: speak(message['text'])
        time.sleep(0.5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Never log voice transcripts, account tokens, or photo bodies.

    def send(self, status, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def trusted_local(self):
        return (self.server.server_port == 8765 and self.client_address[0] == '127.0.0.1'
                and self.headers.get('Host') in ('127.0.0.1:8765', 'localhost:8765'))

    def authorized(self):
        if self.path.startswith('/api/companion/'):
            return self.trusted_local() and not self.headers.get('Origin') and self.headers.get('X-Andrew-Companion')=='1' and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+COMPANION.token)
        if self.trusted_local():
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://127.0.0.1:8765', 'http://localhost:8765'):
                return False
            if self.command == 'POST' and self.headers.get('X-Andrew-Local') != '1':
                return False
            return True
        return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN)

    def do_GET(self):
        if self.path == '/' and self.trusted_local():
            return self.send(200, (ROOT / 'app.html').read_bytes(), 'text/html; charset=utf-8')
        if not self.authorized():
            return self.send(401, {'error': 'Authentication required.'})
        if self.path.startswith('/api/companion/poll?'):
            from urllib.parse import parse_qs
            session=parse_qs(urlparse(self.path).query).get('session',[''])[0]
            try:return self.send(200,COMPANION.poll(session))
            except ValueError as exc:return self.send(400,{'error':str(exc)})
        if self.path=='/assets/andrew.png':
            return self.send(200,(ROOT/'assets/andrew.png').read_bytes(),'image/png')
        if self.path in ('/assets/upgrade.js','/assets/upgrade.css'):
            path=ROOT/'assets'/self.path.rsplit('/',1)[1]
            return self.send(200,path.read_bytes(),'text/javascript; charset=utf-8' if path.suffix=='.js' else 'text/css; charset=utf-8')
        if self.path=='/api/browser-notifications' and self.trusted_local():
            try:
                item=BROWSER_AUDIO.get_nowait()
                if time.time()-item['at']<120:item['audio']=base64.b64encode(voice_wav(item['answer'])).decode()
                return self.send(200,item)
            except queue.Empty:return self.send(200,{})
        if self.path == '/api/status':
            return self.send(200, APP.status() | {'pc_listening': APP.get('pc_listening'),
                'pc_voice': PC_VOICE.status(), 'voice_sessions':SESSIONS.snapshot(),'followup':FOLLOWUP.status(),'followup_enabled':APP.get('followup_enabled'),
                'speech_engine':dict(ENGINE.status)|{'expressive_ready':bool(ENGINE.nano and ENGINE.nano.ready.is_set())},'pi_voice':dict(PI_HEALTH),
                'satellite':dict(SATELLITE.status),
                'pc_task':APP.pc_agent.status(),'browser_companion':COMPANION.status(),
                'improvements':IMPROVEMENTS.items(),
                'camera':APP.camera.status(),
                'listening':{s:{'snooze_until':APP.get(s+'_snooze_until'),
                    'remaining':APP.snooze_remaining(s),'wake_mode':APP.get(s+'_wake_mode')} for s in ('pc','pi')},
                'playback_active':PLAYBACK.is_set(),'speech_control':INTERRUPT.status(),
                'pc_input':APP.get('pc_input'),'pc_output':APP.get('pc_output'),'pi_output':APP.get('pi_output'),
                'pi_input':APP.get('pi_input'),'pi_listening':APP.get('pi_listening'),
                'pc_volume':APP.get('pc_volume'),'pi_volume':APP.get('pi_volume'),'voice':APP.get('voice'),
                'recognition':APP.get('recognition'),'voice_speed':APP.get('voice_speed'),
                'games':{source:APP.games.snapshot(source) for source in ('pc','pi','browser')},
                'selected_model':APP.get(APP.get('provider')+'_model') or 'Account default'})
        if self.path == '/api/health' and self.trusted_local():
            import os
            return self.send(200,{'ready':True,'pid':os.getpid()})
        if self.path.startswith('/api/improvement-review/') and self.trusted_local():
            try:
                path=IMPROVEMENTS.get(self.path.rsplit('/',1)[-1])
                patch=(path/'changes.patch').read_text(encoding='utf-8') if (path/'changes.patch').exists() else ''
                report=(path/'tests.log').read_text(encoding='utf-8',errors='replace')[-30000:] if (path/'tests.log').exists() else ''
                installer=(path/'installer.log').read_text(encoding='utf-8',errors='replace')[-12000:] if (path/'installer.log').exists() else ''
                return self.send(200,{'review':improvements.read_json(path/'review.json'),'patch':patch,'tests':report,'installer':installer})
            except ValueError as exc: return self.send(400,{'error':str(exc)})
        if self.path == '/api/relay':
            result = APP.job()
            result['camera_control']=APP.camera.control()
            try:
                while True:
                    item = PI_AUDIO.get_nowait()
                    expiry,audio=item[:2]
                    if expiry>time.time() and (len(item)<3 or not item[2] or not item[2].is_set()):
                        result['audio']=audio
                        if len(item)>3 and item[3]:result['followup_grant']=item[3]
                        PLAYBACK.record(base64.b64decode(audio),'pi')
                        break
            except queue.Empty: pass
            result['followup']=FOLLOWUP.view('pi')
            result['speech_control']=INTERRUPT.status('pi')
            result['protocol'] = 2
            result['pause_capture'] = SPEAKING.is_set() or PLAYBACK.is_set()
            result['audio_settings'] = {'speaker':APP.get('pi_output') or 'auto',
                'microphone':APP.get('pi_input') or 'auto','listening':APP.get('pi_listening'),'volume':APP.get('pi_volume'),
                'snooze_remaining':APP.snooze_remaining('pi'),'wake_mode':APP.get('pi_wake_mode')}
            return self.send(200, result)
        if self.path == '/api/devices' and self.trusted_local():
            return self.send(200, PC_VOICE.devices())
        if self.path in ('/api/providers','/api/providers?refresh=1') and self.trusted_local():
            return self.send(200, {'providers':providers.catalog(force='refresh=1' in self.path)})
        if self.path == '/api/models':
            try:
                return self.send(200, request_json('http://127.0.0.1:11434/api/tags', timeout=3))
            except Exception:
                return self.send(503, {'error': 'Local AI is not running yet.'})
        if self.path == '/api/photo' and self.trusted_local():
            captures=[m for m in APP.camera.status()['captures'] if m['kind']=='photo']
            if captures: return self.send(200,(APP.camera.media/(captures[0]['id']+'.jpg')).read_bytes(),'image/jpeg')
            files = sorted((ROOT / 'data/photos').glob('*.jpg'), key=lambda p: p.stat().st_mtime)
            return self.send(200, files[-1].read_bytes(), 'image/jpeg') if files else self.send(404, {'error': 'No requested photo yet.'})
        if self.path.split('?')[0]=='/api/camera-frame':
            with APP.camera.lock:
                if APP.camera.preview_until>time.time() and APP.camera.frame and time.time()-APP.camera.frame_at<5:
                    return self.send(200,APP.camera.frame,'image/jpeg')
            return self.send(404,{'error':'No active camera preview.'})
        if self.path.startswith('/api/camera-media/'):
            filename=self.path.rsplit('/',1)[-1]
            if not re.fullmatch(r'[a-f0-9]{32}\.(jpg|mp4)',filename): return self.send(400,{'error':'Invalid capture.'})
            path=APP.camera.media/filename
            if not path.is_file(): return self.send(404,{'error':'Capture not found.'})
            return self.send(200,path.read_bytes(),'image/jpeg' if filename.endswith('.jpg') else 'video/mp4')
        return self.send(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.authorized():
            return self.send(401, {'error': 'Authentication required.'})
        try:
            size = int(self.headers.get('Content-Length', 0))
            if size < 1 or size > (30_000_000 if self.path=='/api/camera-capture' else 5_500_000):
                raise ValueError('Invalid request size.')
            self.connection.settimeout(60 if self.path=='/api/camera-capture' else 15)
            raw = self.rfile.read(size)
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object.')
            if self.path=='/api/companion/hello':return self.send(200,COMPANION.hello(data.get('version'),data.get('browser')))
            if self.path=='/api/companion/result':return self.send(200,{'ok':COMPANION.result(data.get('session'),data.get('message',{}))})
            if self.path=='/api/companion/ask':
                if data.get('session') not in COMPANION.clients:raise ValueError('Reconnect the companion first.')
                return self.send(200,{'answer':APP.command(data.get('request',''),'pc')})
            if self.path in ('/api/browser-command','/api/browser-voice') and self.trusted_local():
                text=data.get('text','')
                if self.path=='/api/browser-voice':
                    if data.get('activated') is not True:raise ValueError('Tap Talk before using the microphone.')
                    pcm=base64.b64decode(data.get('pcm',''),validate=True)
                    if not 3200<=len(pcm)<=480000:raise ValueError('Record a request lasting under fifteen seconds.')
                    text=ENGINE.transcribe(pcm)
                phrase=text.lower().strip().rstrip('.!?')
                if phrase in ('shut up','stop talking','pause speaking','be quiet','continue','keep going','resume speaking'):
                    return self.send(200,{'transcript':text,'answer':'Speech paused.' if phrase not in ('continue','keep going','resume speaking') else 'Continuing.','speech_action':'resume' if phrase in ('continue','keep going','resume speaking') else 'pause'})
                if type(data.get('volume')) in (int,float) and 0<=data['volume']<=100:APP.set('browser_volume',data['volume'])
                APP.request.voice_context=False
                answer=APP.command(text,'browser')
                SESSIONS.update('browser',last_request=text,last_answer=answer,answered_at=time.time())
                result={'transcript':text,'answer':answer,'volume':APP.get('browser_volume')}
                if data.get('speak') is True:result['audio']=base64.b64encode(voice_wav(answer)).decode()
                return self.send(200,result)
            if self.path=='/api/followup/close' and self.trusted_local():
                for source in ('pc','pi'):FOLLOWUP.close(source)
                return self.send(200,{'answer':'Follow-up listening closed.'})
            if self.path == '/api/speech-control':
                source=data.get('source','pc') if self.trusted_local() else 'pi'
                return self.send(200,{'answer':speech_control(source,data.get('action')),'silent':True})
            if self.path == '/api/pi-update' and self.trusted_local():
                return self.send(200,{'answer':SATELLITE.queue_update(),'satellite':dict(SATELLITE.status)})
            if self.path == '/api/voice':
                pcm = base64.b64decode(data.get('pcm',''),validate=True)
                return self.send(200,handle_audio(pcm,'pc' if self.trusted_local() else 'pi',
                    False,time.time()-min(30,max(0,float(data.get('age',0)))),data.get('wake_detected') is True,data.get('followup')))
            if self.path=='/api/followup/arm':
                source='pc' if self.trusted_local() else 'pi'
                return self.send(200,{'armed':FOLLOWUP.arm(source,data.get('grant')), 'followup':FOLLOWUP.view(source)})
            if self.path == '/api/relay-health' and not self.trusted_local():
                PI_HEALTH.update({k:data[k] for k in ('device','speaker','peak','phase','error','protocol','version','outputs','inputs','wakes','display','camera','wake_phrase','background_guard','empty_wakes','rejected_wakes','responses','playback_started_at','playback_finished_at','playback_state') if k in data},
                                 seen=time.time(),ip=self.client_address[0])
                APP.relay_seen = time.time()
                return self.send(200,{'ok':True})
            if self.path == '/api/listen' and self.trusted_local():
                source = data.get('source','pc')
                if source not in ('pc','pi'): raise ValueError('Choose PC or Pi.')
                if APP.snooze_remaining(source): raise ValueError('That microphone is snoozed. Use Wake now to resume early.')
                if source == 'pi' and time.time()-APP.relay_seen >= 20:
                    raise ValueError('The Pi is reconnecting. Try again when its microphone shows online.')
                if source == 'pc' and not PC_VOICE.status()['ready']:
                    raise ValueError('Connect or select a PC microphone first.')
                # This control does not bypass the wake-name privacy boundary.
                cue = ENGINE.cue()
                if source == 'pc':queue_speech(cue)
                else: PI_AUDIO.put((time.time()+20,base64.b64encode(cue).decode()),timeout=10)
                return self.send(200,{'ok':True,'message':'Say '+APP.get('name')+' to begin. No speech is transcribed before the name.'})
            if self.path == '/api/test-speaker' and self.trusted_local():
                source = data.get('source','pc')
                if source == 'pi' and time.time()-APP.relay_seen >= 20:
                    raise ValueError('The Pi is reconnecting. Try again when it shows online.')
                text = f'Hello. I am {APP.get("name")}. This is the {"Raspberry Pi" if source=="pi" else "PC"} speaker.'
                if source == 'pc':queue_speech(text)
                elif source == 'pi':
                    PI_AUDIO.put((time.time()+30,base64.b64encode(voice_wav(text)).decode()),timeout=10)
                else: raise ValueError('Choose PC or Pi.')
                return self.send(200,{'ok':True})
            if self.path == '/api/provider' and self.trusted_local():
                answer = providers.choose(APP,data.get('provider',''),data.get('model'))
                return self.send(200,{'answer':answer})
            if self.path == '/api/connect-google-voice' and self.trusted_local():
                return self.send(200,{'answer':APP.communications.connect()})
            if self.path == '/api/memory-delete' and self.trusted_local():
                with APP.lock,APP.db:APP.db.execute('DELETE FROM memories WHERE id=?',(str(data.get('id','')),))
                APP.conversations.clear()
                return self.send(200,{'ok':True})
            if self.path == '/api/connect-claude' and self.trusted_local():
                from claude_provider import connect
                message=connect()
                providers.CACHE.clear()
                return self.send(200,{'ok':True,'message':message})
            if self.path == '/api/test-provider' and self.trusted_local():
                provider,model,entry=providers.resolve(APP,data.get('provider'),data.get('model'))
                answer=APP.ai('Say exactly: Andrew connection works.',provider,model)
                return self.send(200,{'answer':answer,'provider':provider,'model':model})
            if self.path == '/api/improvements' and self.trusted_local():
                mode=data.get('auto_install',True)
                if not isinstance(mode,bool):raise ValueError('Choose automatic install or preview only.')
                return self.send(200,{'answer':IMPROVEMENTS.request(data.get('request',''),'browser' if data.get('source')=='browser' else 'pc',data.get('provider'),data.get('model'),auto_install=mode)})
            if self.path in ('/api/improvement-install','/api/improvement-rollback') and self.trusted_local():
                return self.send(200,{'answer':IMPROVEMENTS.launch('install' if self.path.endswith('install') else 'rollback',data.get('id'))})
            if self.path == '/api/connect-grok' and self.trusted_local():
                import os
                os.startfile(str(ROOT/'Connect Grok.cmd'))
                return self.send(200,{'ok':True})
            if self.path == '/api/command':
                if data.get('voice') is True:
                    return self.send(200, voice_command(data.get('text', ''),
                        'pc' if self.trusted_local() else 'pi', data.get('speak') is True))
                source = 'pc' if self.trusted_local() else 'pi'
                ticket=INTERRUPT.new_request(source) if data.get('text','').lower().strip() not in ('shut up','stop talking','pause speaking','be quiet','continue','keep going','resume speaking') else INTERRUPT.ticket(source)
                answer = APP.command(data.get('text', ''), source)
                if data.get('speak') is True and not getattr(APP.request,'speech_silent',False) and not ticket.is_set():
                    if source=='pc':
                        grant=FOLLOWUP.prepare('pc',APP.memory.current('pc'))
                        if APP.get('pc_speech'):queue_speech(answer,followup=grant)
                        elif grant:FOLLOWUP.arm('pc',grant)
                    else: PI_AUDIO.put((time.time()+60,base64.b64encode(voice_wav(answer)).decode()),timeout=10)
                return self.send(200, {'answer': answer})
            if self.path == '/api/photo':
                APP.accept_photo(data['job_id'], base64.b64decode(data['jpeg'], validate=True))
                return self.send(200, {'ok': True})
            if self.path=='/api/camera-frame' and not self.trusted_local():
                APP.camera.accept_frame(data.get('session'),base64.b64decode(data.get('jpeg',''),validate=True))
                return self.send(200,{'ok':True})
            if self.path=='/api/camera-capture' and not self.trusted_local():
                APP.camera.accept(data.get('job_id'),data.get('kind'),base64.b64decode(data.get('content',''),validate=True))
                return self.send(200,{'ok':True})
            if self.path=='/api/camera-error' and not self.trusted_local():
                APP.camera.failed(data.get('job_id'),data.get('message','Camera capture failed.'))
                return self.send(200,{'ok':True})
            if self.path=='/api/camera' and self.trusted_local():
                action=data.get('action')
                if action=='preview':answer=APP.camera.preview()
                elif action=='stop':answer=APP.camera.stop()
                elif action in ('photo','video'):answer=APP.camera.request(action,data.get('seconds',10),'pc')
                elif action=='analyze':answer=APP.camera.analyze(data.get('id') or APP.camera.latest(),data.get('question') or 'Describe what you can see.',data.get('provider'),data.get('model'),'pc')
                else:raise ValueError('Choose preview, stop, photo, video, or analyze.')
                return self.send(200,{'answer':answer})
            if self.path == '/api/job-result' and not self.trusted_local():
                APP.complete_job(data['job_id'],data.get('error',''))
                return self.send(200,{'ok':True})
            if self.path == '/api/relay-error':
                APP.event('Pi reported: ' + str(data.get('message', 'Unknown error'))[:200])
                return self.send(200, {'ok': True})
            if self.path == '/api/settings' and self.trusted_local():
                for key in data:
                    if key not in ('ha_url', 'ha_token', 'pc_speech', 'pc_listening', 'pc_input', 'pc_output', 'pi_output', 'pi_input', 'pi_listening', 'local_model', 'openai_model', 'claude_model','grok_model','pc_volume','pi_volume','voice','pc_wake_mode','pi_wake_mode','memory_auto','recognition','voice_speed','followup_enabled','followup_seconds'):
                        raise ValueError('Unsupported setting.')
                if 'ha_url' in data and data['ha_url']:
                    parsed = urlparse(data['ha_url'])
                    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                        raise ValueError('Use a Home Assistant HTTP(S) base URL.')
                    host = parsed.hostname
                    try:
                        local = ipaddress.ip_address(host).is_private
                    except ValueError:
                        local = host.endswith('.local') or host == 'localhost'
                    if not local:
                        raise ValueError('Home Assistant must be on your local network.')
                for key, value in data.items():
                    if key in ('pc_volume','pi_volume'):
                        if type(value) not in (int,float) or not 0<=value<=100: raise ValueError('Choose a volume from 0 to 100.')
                    elif key=='voice':
                        if value not in ('nano','bm_george','bm_fable','am_michael','am_puck','af_heart','af_bella','piper'): raise ValueError('Choose a listed voice.')
                    elif key=='recognition':
                        if value not in ('parakeet','whisper'):raise ValueError('Choose Parakeet or Whisper.')
                    elif key=='voice_speed':
                        if type(value) not in (int,float) or not .8<=value<=1.3:raise ValueError('Voice speed must be between 0.8 and 1.3.')
                    elif key=='followup_seconds':
                        if type(value)!=int or not 3<=value<=20:raise ValueError('Choose a follow-up window from 3 to 20 seconds.')
                    elif key=='followup_enabled':
                        if type(value)!=bool:raise ValueError('Enable or disable follow-up listening.')
                    elif key in ('pc_wake_mode','pi_wake_mode'):
                        if value not in ('adaptive','strict'): raise ValueError('Choose adaptive or strict wake detection.')
                    elif key in ('pc_speech', 'pc_listening','pi_listening','memory_auto'):
                        if not isinstance(value, bool):
                            raise ValueError('Speech setting must be true or false.')
                    elif not isinstance(value, str) or len(value) > 2000:
                        raise ValueError('Invalid setting value.')
                    if key.endswith('_model') and value and not re.fullmatch(r'[\w.:-]{1,100}', value):
                        raise ValueError('Use a model identifier, not command arguments.')
                for key, value in data.items():
                    APP.set(key, value)
                return self.send(200, {'ok': True})
            return self.send(404, {'error': 'Not found.'})
        except ValueError as exc:
            return self.send(400, {'error': str(exc)})
        except Exception:
            return self.send(503, {'error': 'That service is unavailable or not connected. No paid fallback was used. Check the setup guide.'})


def main():
    IMPROVEMENTS.recover_interrupted()
    threading.Thread(target=ENGINE.warmup, daemon=True).start()
    threading.Thread(target=speech_worker, daemon=True).start()
    threading.Thread(target=PC_VOICE.run, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=SATELLITE.run, daemon=True).start()
    local = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    if (ROOT / 'data/server.crt').exists():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(ROOT / 'data/server.crt', ROOT / 'data/server.key')
        lan = ThreadingHTTPServer(('0.0.0.0', 8766), Handler)
        lan.socket = context.wrap_socket(lan.socket, server_side=True)
        threading.Thread(target=lan.serve_forever, daemon=True).start()
    print('Andrew control panel: http://127.0.0.1:8765', flush=True)
    local.serve_forever()


if __name__ == '__main__':
    main()
