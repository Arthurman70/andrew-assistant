"""Device-bound repeating alarm clips. Private library files never enter exports."""
import hashlib
import io
import secrets
import threading
import time
import wave
from pathlib import Path
import numpy as np


def wav_bytes(samples,rate=24000):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate)
        wav.writeframes((np.clip(samples,-1,1)*32767).astype('<i2').tobytes())
    return out.getvalue()

def sound_label(path):return path.parent.name.split(' - ')[0]+' · '+path.stem.split('-',1)[-1].split('_B00M')[0].replace('_',' ')


def chime():
    rate=24000;clip=np.zeros(rate*3,dtype=np.float32)
    for start,note in ((0,659.25),(.55,880),(1.1,1174.66)):
        t=np.arange(int(rate*1.4))/rate
        envelope=np.minimum(1,t/.015)*np.exp(-t*3.5)
        samples=.24*envelope*(np.sin(2*np.pi*note*t)+.2*np.sin(2*np.pi*note*2*t))
        offset=int(start*rate);clip[offset:offset+len(samples)]+=samples
    return wav_bytes(clip)


def private_clip(path):
    """Read at most twelve seconds of PCM; play a normalized four-second clip."""
    if path.stat().st_size>64_000_000:raise ValueError('Choose a WAV file smaller than 64 MB.')
    with wave.open(str(path),'rb') as wav:
        width,channels,rate=wav.getsampwidth(),wav.getnchannels(),wav.getframerate()
        if channels>8 or not 8000<=rate<=192000:raise ValueError('Choose a normal PCM WAV sound.')
        raw=wav.readframes(min(wav.getnframes(),rate*12))
    if width==1:values=(np.frombuffer(raw,dtype=np.uint8).astype(np.float32)-128)/128
    elif width==2:values=np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768
    elif width==3:
        b=np.frombuffer(raw,dtype=np.uint8).reshape(-1,3).astype(np.int32)
        values=b[:,0]|(b[:,1]<<8)|(b[:,2]<<16);values=((values^0x800000)-0x800000).astype(np.float32)/8388608
    elif width==4:values=np.frombuffer(raw,dtype='<i4').astype(np.float32)/2147483648
    else:raise ValueError('Choose an 8, 16, 24 or 32-bit PCM WAV sound.')
    values=values.reshape(-1,channels).mean(axis=1)
    if not len(values) or np.max(np.abs(values))<.00001:raise ValueError('That sound is silent. Choose another clip.')
    # Find audible content instead of playing a long silent cinematic lead-in.
    block=max(1,rate//4);peaks=[np.max(np.abs(values[i:i+block])) for i in range(0,len(values),block)]
    first=next(i for i,v in enumerate(peaks) if v>=max(peaks)*.2)
    offset=max(0,first*block-int(rate*.1));values=values[offset:offset+rate*4]
    if rate>24000:
        n=np.arange(-31,32);cutoff=.45*24000/rate
        kernel=2*cutoff*np.sinc(2*cutoff*n)*np.hamming(len(n));kernel/=kernel.sum()
        values=np.convolve(values,kernel,mode='same')
    samples=np.interp(np.arange(int(len(values)*24000/rate))*rate/24000,np.arange(len(values)),values)
    samples*=.42/max(.00001,np.max(np.abs(samples)))
    fade=min(480,len(samples)//2);samples[:fade]*=np.linspace(0,1,fade);samples[-fade:]*=np.linspace(1,0,fade)
    return wav_bytes(samples)


class AlarmAudio:
    def __init__(self,app,downloads=None,clock=time.monotonic):
        self.app=app;self.downloads=Path(downloads) if downloads else Path.home()/'Downloads';self.clock=clock
        self.lock=threading.RLock();self.episodes={};self.frames={};self.cache=None;self.dispatch=None
        self.library={};self.scanned=0;self.default=chime();self.label='Gentle chime';self.error=''
        # Old one-shot alarms left ringing by the former announcement-only
        # implementation must not suddenly replay days later on upgrade.
        with app.lock:
            old=app.db.execute("SELECT id FROM timers WHERE kind='alarm' AND status='ringing' AND due<? AND COALESCE(repeat_days,'[]')='[]'",(time.time()-86400,)).fetchall()
        for row in old:self.episodes[row['id']]={'token':secrets.token_hex(8),'muted':True}

    def catalog(self,refresh=False):
        if refresh or not self.scanned or self.clock()-self.scanned>180:
            files={}
            if self.downloads.exists():
                for folder in sorted(self.downloads.glob('Cinematic*')):
                    if not folder.is_dir() or folder.is_symlink():continue
                    for path in sorted(folder.rglob('*.wav')):
                        if len(files)>=500:break
                        if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):continue
                        key=hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:24]
                        files[key]=path
            self.library=files;self.scanned=self.clock() or .001
        return [{'id':'builtin','label':'Gentle chime · built in'}]+[
            {'id':key,'label':sound_label(path)[:120]} for key,path in self.library.items()]

    def sound(self):
        selected=self.app.get('alarm_sound') or 'builtin'
        with self.lock:
            path=None
            if selected!='builtin':self.catalog();path=self.library.get(selected)
            stamp=(selected,path.stat().st_mtime_ns if path and path.exists() else None)
            if self.cache and self.cache[0]==stamp:return self.cache[1]
            self.error='';self.label='Gentle chime'
            try:
                if selected!='builtin' and not path:raise ValueError('Selected sound is unavailable; using the built-in chime.')
                content=private_clip(path) if path else self.default
                if path:self.label=sound_label(path)[:120]
            except (OSError,ValueError,wave.Error):
                content=self.default;self.error='Selected sound is unavailable; using the built-in chime.'
            revision=hashlib.sha256(content).hexdigest()[:16];self.cache=(stamp,content,revision)
            return content

    def choose(self,key):
        if key not in {v['id'] for v in self.catalog(True)}:raise ValueError('Choose a sound from Andrew’s alarm sound list.')
        if key!='builtin':private_clip(self.library[key])  # Reject unusable files before changing the setting.
        self.app.set('alarm_sound',key);self.cache=None;self.sound();self.sync()
        return 'Alarm sound changed to '+self.label+'.'

    def begin(self,row):
        with self.lock:self.episodes[row['id']]={'token':secrets.token_hex(8),'muted':False}
        self.sync()

    def sync(self):
        with self.app.lock:rows=[dict(r) for r in self.app.db.execute("SELECT * FROM timers WHERE kind='alarm' AND status='ringing'")]
        self.sound()
        with self.lock:
            ids={r['id'] for r in rows};self.episodes={k:v for k,v in self.episodes.items() if k in ids}
            for row in rows:self.episodes.setdefault(row['id'],{'token':secrets.token_hex(8),'muted':False})
            for source in ('pc','pi','browser'):
                active=[{'id':r['id'],'token':self.episodes[r['id']]['token'],'device':r.get('browser_device')} for r in rows if (r['source'] or 'pc')==source and not self.episodes[r['id']]['muted']]
                key=':'.join(sorted(a['token'] for a in active))+':'+self.cache[2] if active else ''
                frame=self.frames.get(source)
                if not frame or frame['key']!=key:
                    if frame:frame['cancel'].set()
                    self.frames[source]={'key':key,'alarms':active,'cancel':threading.Event(),'sent':-1000}

    def silence(self,source):
        self.sync()
        with self.lock:
            for row in self.frames[source]['alarms']:self.episodes[row['id']]['muted']=True
        self.sync()

    def resume(self,source):
        with self.app.lock:ids=[r['id'] for r in self.app.db.execute("SELECT id FROM timers WHERE kind='alarm' AND status='ringing' AND source=?",(source,))]
        with self.lock:
            for identity in ids:
                if identity in self.episodes:self.episodes[identity]['muted']=False
        self.sync()

    def status(self):
        self.sync()
        with self.lock:return {'selected':self.app.get('alarm_sound') or 'builtin','label':self.label,'error':self.error,'revision':self.cache[2],
            'devices':{s:{'key':f['key'],'alarms':list(f['alarms'])} for s,f in self.frames.items()}}

    def tick(self):
        self.sync()
        with self.lock:
            frame=self.frames['pc'];now=self.clock()
            if not frame['key'] or now-frame['sent']<8 or not self.dispatch:return
            packet=dict(frame)
        if self.dispatch(self.sound(),packet['cancel']):
            with self.lock:
                if self.frames['pc']['key']==packet['key']:self.frames['pc']['sent']=now

    def run(self):
        while True:
            try:self.tick()
            except Exception:pass  # Keep retrying after a disconnected output.
            time.sleep(.25)
