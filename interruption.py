"""Local speech interruption; no background transcription or saved microphone audio."""
from collections import deque
import hashlib
import io
import threading
import time
import wave


def remaining_wav(content, frames):
    with wave.open(io.BytesIO(content),'rb') as src:
        frames=max(0,min(int(frames),src.getnframes()))
        src.setpos(frames)
        out=io.BytesIO()
        with wave.open(out,'wb') as dest:
            dest.setparams(src.getparams());dest.writeframes(src.readframes(src.getnframes()-frames))
    return out.getvalue()


class SpeechInterruption:
    def __init__(self, player, resume_sender):
        self.player=player;self.resume_sender=resume_sender
        self.lock=threading.RLock()
        self.events={s:threading.Event() for s in ('pc','pi')}
        self.states={s:{'paused':False,'revision':0,'action':'','at':0} for s in ('pc','pi')}

    def ticket(self, source):
        with self.lock:return self.events.setdefault(source,threading.Event())

    def status(self, source=None):
        with self.lock:
            return dict(self.states.get(source,{})) if source else {s:dict(v) for s,v in self.states.items()}

    def active(self, source):
        state=self.status(source)
        return bool(state.get('paused') and time.time()-state['at']<120)

    def control(self, source, action):
        if source not in ('pc','pi'):raise ValueError('Choose the PC or Pi speaker.')
        if action not in ('pause','resume','stop'):raise ValueError('Choose pause, resume, or stop.')
        with self.lock:
            state=self.states[source]
            self.events[source].set();self.events[source]=threading.Event()
            state.update(paused=action=='pause',revision=state['revision']+1,action=action,at=time.time())
        if source=='pc':
            if action=='pause':self.player.pause()
            elif action=='stop':self.player.stop()
            else:
                content=self.player.resume()
                if content:self.resume_sender(content,source)
                else:return 'There is no paused reply to continue.'
        return 'Speech paused. Say continue to resume.' if action=='pause' else ('Continuing the paused reply.' if action=='resume' else 'Speech stopped.')

    def new_request(self, source):
        # A new request replaces a paused answer. Completed device actions are
        # not repeated when an answer is resumed or discarded.
        if self.active(source):self.control(source,'stop')
        return self.ticket(source)


class InterruptDetector:
    """Keyword spotting only. Bare control words are enabled within an active reply."""
    def __init__(self, directory, name):
        self.directory=directory;self.name=None
        self.levels=deque(maxlen=300);self.cooldown=0;self.enabled=False
        self.configure(name)

    def configure(self, name):
        from pathlib import Path
        import sentencepiece as spm
        import sherpa_onnx
        folder=Path(self.directory)
        tokens=spm.SentencePieceProcessor(model_file=str(folder/'bpe.model'))
        rows=[]
        for action,phrases in {'pause':['shut up','stop talking','pause speaking','be quiet'],
                               'resume':['continue','contin you','con tin you','keep going','resume speaking']}.items():
            for phrase in phrases:
                for prefix in ('','Hey '+name+' '):
                    text=prefix+phrase
                    rows.append(' '.join(tokens.encode(text.upper(),out_type=str))+
                                (' :3.0 #0.10' if action=='resume' else (' :2.0 #0.35' if prefix else ' :2.0 #0.30'))+' @'+action.upper())
        file=folder/('interrupt-'+hashlib.sha256(name.encode()).hexdigest()[:10]+'.txt')
        file.write_text('\n'.join(rows)+'\n',encoding='utf-8')
        self.spotter=sherpa_onnx.KeywordSpotter(tokens=str(folder/'tokens.txt'),
            encoder=str(next(folder.glob('encoder*.int8.onnx'))),decoder=str(next(folder.glob('decoder*.int8.onnx'))),
            joiner=str(next(folder.glob('joiner*.int8.onnx'))),keywords_file=str(file),num_threads=1,num_trailing_blanks=1)
        self.name=name;self.stream=self.spotter.create_stream()

    def feed(self, frame, name, enabled, paused=False):
        import numpy as np
        if name!=self.name:self.configure(name)
        samples=np.frombuffer(frame,dtype='<i2').astype(np.float32)
        level=float(np.sqrt(np.mean(samples*samples)))
        self.levels.append(level)
        if not enabled:
            if self.enabled:self.stream=self.spotter.create_stream()
            self.enabled=False;return None
        self.enabled=True
        if self.cooldown:self.cooldown-=1;return None
        samples=samples/32768
        samples*=min(4.0,.95/max(.01,float(np.max(np.abs(samples)))))
        self.stream.accept_waveform(16000,samples)
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            result=self.spotter.keyword_spotter.get_result(self.stream)
            if result.keyword:
                # Require a foreground interruption above ongoing speaker echo.
                values=list(self.levels);ambient=sorted(values[:-60] or [25.])
                floor=ambient[int((len(ambient)-1)*.65)]
                foreground=sorted(values[-50:])
                voice=foreground[int((len(foreground)-1)*(.95 if paused else .8))]
                self.last_hit={'keyword':result.keyword,'voice':voice,'floor':floor}
                self.stream=self.spotter.create_stream();self.cooldown=50
                if voice<max(60,floor*(1.3 if paused else 2.2)):return None
                action=result.keyword.lower()
                if action=='resume' and not paused:return None
                return action
        return None
