"""Local keyword-only spotting; it cannot output a background transcript."""
from pathlib import Path
import hashlib
import numpy as np
import sentencepiece as spm
import sherpa_onnx
import webrtcvad


def wake_phrase(name):
    return 'Hey '+name.strip()


class WakeDetector:
    def __init__(self,directory,name='Andrew'):
        self.directory=Path(directory);self.name=None
        self.activity=webrtcvad.Vad(2)
        self.configure(name)

    def configure(self,name):
        if self.name==name: return
        processor=spm.SentencePieceProcessor(model_file=str(self.directory/'bpe.model'))
        # Spot the entire address, not a name mentioned in another conversation.
        tokens=' '.join(processor.encode(wake_phrase(name).upper(),out_type=str))
        key=hashlib.sha256(('hey-address-v2:'+name).encode()).hexdigest()[:10]
        keywords=self.directory/('keyword-'+key+'.txt')
        keywords.write_text(tokens+' :2.0 #0.25 @WAKE\n',encoding='utf-8')
        self.spotter=sherpa_onnx.KeywordSpotter(tokens=str(self.directory/'tokens.txt'),
            encoder=str(next(self.directory.glob('encoder*.int8.onnx'))),
            decoder=str(next(self.directory.glob('decoder*.int8.onnx'))),
            joiner=str(next(self.directory.glob('joiner*.int8.onnx'))),
            keywords_file=str(keywords),num_threads=1,num_trailing_blanks=1)
        self.name=name;self.reset()

    def reset(self):
        self.stream=self.spotter.create_stream();self.samples=0
        self.quiet_frames=0
        self.command_start=0

    def accept(self,pcm):
        # KWS resets its internal token timestamps after long blank runs. Reset
        # our own stream during idle silence first, so sample and token clocks
        # remain aligned when the next person speaks. No speech-to-text here.
        self.quiet_frames=0 if self.activity.is_speech(pcm,16000) else self.quiet_frames+1
        if self.quiet_frames>=25:self.reset()
        samples=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768
        # Foreground validation uses ORIGINAL levels before this bounded gain.
        samples*=min(4.0,.95/max(.01,float(np.max(np.abs(samples)))))
        self.stream.accept_waveform(16000,samples);self.samples+=len(pcm)//2
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            result=self.spotter.keyword_spotter.get_result(self.stream)
            if result.keyword and result.timestamps:
                command_start=int((result.timestamps[-1]+.12)*16000)
                # Keep the verified address for ASR context: cutting at an
                # estimated word end was chopping "use" off model commands.
                # Nothing before the detected "Hey <name>" is retained.
                boundary=int(result.timestamps[0]*16000)
                # If the native decoder reset inside continuous background
                # speech, its old relative timestamp is unsafe for lookback.
                # Discard earlier audio rather than submitting it to ASR.
                if self.samples-command_start>int(.7*16000):boundary=command_start=self.samples
                self.command_start=max(0,min(command_start,self.samples))
                return max(0,min(boundary,self.samples))
        return None
