"""Local Parakeet recognition and neural speech. PCM stays in memory."""
import io
from pathlib import Path
import threading
import time
import wave
import re
from collections import OrderedDict
import numpy as np
from reading import MAX_TEXT,speech_chunks,combine_wavs

ROOT = Path(__file__).resolve().parent


class SpeechEngine:
    def __init__(self,voice_choice=None,recognition_choice=None,speed_choice=None):
        self.asr = None
        self.voice = None
        self.kokoro = None
        self.nano=None;self.asr_kind=None
        self.voice_choice=voice_choice or (lambda:'bm_george')
        self.recognition_choice=recognition_choice or (lambda:'parakeet')
        self.speed_choice=speed_choice or (lambda:1.0)
        self.asr_lock = threading.Lock()
        self.tts_lock = threading.Lock()
        self.cache=OrderedDict();self.cache_bytes=0
        self.status = {'recognition': 'loading', 'voice': 'loading', 'error': ''}

    def warmup(self):
        try:
            if self.voice_choice()=='nano':
                from nano_voice import NanoVoice
                self.nano=NanoVoice();self.nano.start()
            self.transcribe(bytes(16000*2), 'Andrew')
            self.synthesize('Ready.')
            for text in ('I missed the request. Please say my name and try again.',
                         'One moment.', 'I can hear you. What would you like me to do?'):
                self.synthesize(text)
        except Exception as exc:
            self.status['error'] = str(exc)[:200]

    def transcribe(self, pcm, name='Andrew'):
        if len(pcm) % 2 or not 640 <= len(pcm) <= 16000*2*22:
            raise ValueError('Expected at most 22 seconds of mono 16 kHz PCM audio.')
        with self.asr_lock:
            choice=self.recognition_choice()
            model=ROOT/'runtime/speech/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8'
            if choice=='parakeet' and (model/'encoder.int8.onnx').exists():
                if self.asr_kind!='parakeet':
                    import sherpa_onnx
                    self.asr=sherpa_onnx.OfflineRecognizer.from_transducer(
                        encoder=str(model/'encoder.int8.onnx'),decoder=str(model/'decoder.int8.onnx'),
                        joiner=str(model/'joiner.int8.onnx'),tokens=str(model/'tokens.txt'),num_threads=6,
                        model_type='nemo_transducer',provider='cpu')
                    self.asr_kind='parakeet'
                self.status['recognition']='NVIDIA Parakeet TDT v3 · local'
            elif self.asr_kind!='whisper':
                from faster_whisper import WhisperModel
                self.asr = WhisperModel(str(ROOT / 'runtime/speech/whisper-small.en'),
                    device='cpu', compute_type='int8', cpu_threads=6, local_files_only=True)
                self.status['recognition'] = 'Whisper small.en · local'
                self.asr_kind='whisper'
            audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
            peak = float(np.max(np.abs(audio)))
            if peak < 0.001:
                return ''
            # Gentle gain for quiet microphones, capped to avoid amplifying noise excessively.
            audio *= min(4.0, 0.8 / max(peak, 0.01))
            started=time.monotonic()
            if self.asr_kind=='parakeet':
                stream=self.asr.create_stream();stream.accept_waveform(16000,audio);self.asr.decode_stream(stream)
                self.status['last_recognition_seconds']=round(time.monotonic()-started,2)
                return stream.result.text.strip()
            segments, _ = self.asr.transcribe(audio, language='en', beam_size=3,
                condition_on_previous_text=False, vad_filter=True,
                vad_parameters={'min_silence_duration_ms': 350, 'speech_pad_ms': 200},
                initial_prompt=f'The voice assistant is named {name}.',
                no_speech_threshold=0.6)
            return ' '.join(s.text.strip() for s in segments).strip()

    def synthesize(self, text):
        with self.tts_lock:
            text=spoken_text(str(text))
            if len(text)>MAX_TEXT:raise ValueError('Split readings longer than 32000 characters into parts.')
            selected=self.voice_choice()
            key=(selected,float(self.speed_choice()),text)
            started=time.monotonic()
            if key in self.cache:
                self.cache.move_to_end(key)
                self.status.update(last_synthesis_seconds=0,cache_hit=True)
                return self.cache[key]
            last_error=None
            for choice in dict.fromkeys((selected,'af_heart','piper')):
                try:
                    chunks=list(speech_chunks(text))
                    wav=self._synthesize(chunks[0],choice) if len(chunks)==1 else combine_wavs(self._synthesize(chunk,choice) for chunk in chunks)
                    with wave.open(io.BytesIO(wav),'rb') as audio:
                        if audio.getsampwidth()!=2 or audio.getnframes()<audio.getframerate()*.05:
                            raise RuntimeError('Voice returned no playable speech.')
                        samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').astype(np.int32)
                        if not len(samples) or np.max(np.abs(samples))<4:
                            raise RuntimeError('Voice returned silent audio.')
                    self.status.update(last_synthesis_seconds=round(time.monotonic()-started,3),cache_hit=False,
                        error='' if choice==selected else 'Selected voice unavailable; using the backup local voice.')
                    # Don't permanently cache a temporary fallback under another voice.
                    if choice==selected and len(wav)<=4_000_000:
                        self.cache[key]=wav;self.cache_bytes+=len(wav)
                        while len(self.cache)>64 or self.cache_bytes>16_000_000:
                            _,old=self.cache.popitem(last=False);self.cache_bytes-=len(old)
                    return wav
                except Exception as exc:last_error=exc
            self.status['error']='Local voices could not generate speech.'
            raise RuntimeError(self.status['error']) from last_error

    def clear_cache(self):
        with self.tts_lock:self.cache.clear();self.cache_bytes=0

    def _synthesize(self, text, selected):
            if selected=='nano':
                if self.nano is None:
                    from nano_voice import NanoVoice
                    self.nano=NanoVoice()
                wav=self.nano.synthesize(text)
                self.status['voice']='Chatterbox Nano · local'
                return wav
            if selected!='piper':
                if self.kokoro is None:
                    from kokoro_onnx import Kokoro
                    import onnxruntime as ort
                    options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
                    session=ort.InferenceSession(str(ROOT/'runtime/speech/kokoro-v1.0.onnx'),options,
                                                providers=['CPUExecutionProvider'])
                    self.kokoro=Kokoro.from_session(session,str(ROOT/'runtime/speech/voices-v1.0.bin'))
                samples,rate=self.kokoro.create(str(text),voice=selected,speed=float(self.speed_choice()),
                    lang='en-gb' if selected.startswith('b') else 'en-us',sentence_pause=.22,clause_pause=.12)
                output=io.BytesIO()
                with wave.open(output,'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(rate)
                    wav.writeframes((samples*32767).clip(-32768,32767).astype('<i2').tobytes())
                self.status['voice']='Kokoro '+selected+' · local'
                return output.getvalue()
            if self.voice is None:
                from piper import PiperVoice
                self.voice = PiperVoice.load(str(ROOT / 'runtime/speech/en_US-ryan-high.onnx'))
                self.status['voice'] = 'Piper Ryan · local'
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                self.voice.synthesize_wav(str(text), wav)
            return output.getvalue()

    @staticmethod
    def cue():
        rate = 22050
        t = np.arange(int(rate*0.14)) / rate
        samples = (np.sin(2*np.pi*740*t) * np.sin(np.pi*np.arange(len(t))/len(t)) * 2600).astype('<i2')
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate)
            wav.writeframes(samples.tobytes())
        return output.getvalue()


def spoken_text(text):
    text=re.sub(r'```[\s\S]*?```','The code is on screen.',text)
    text=re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)',r'\1',text)
    text=re.sub(r'https?://\S+','the linked page',text)
    text=re.sub(r'(?m)^\s*(?:#{1,6}|[-*])\s+','',text)
    text=text.replace('**','').replace('`','')
    return re.sub(r'\s+',' ',text).strip() or 'Done.'


class VoiceSession:
    def __init__(self):
        self.lock = threading.RLock()
        self.armed = {}
        self.state = {'pc': {}, 'pi': {}}

    def update(self, source, **values):
        with self.lock:
            self.state.setdefault(source, {}).update(values, updated=time.time())

    def arm(self, source, seconds=12):
        with self.lock:
            self.armed[source] = time.monotonic()+seconds
            self.update(source, phase='listening', error='')

    def addressed(self, text, name, source):
        import re
        # Punctuation and short introductory words must not defeat the wake name.
        # Preserve times such as 7:30 and model identifiers in the actual request.
        # A little earlier background speech must not hide the addressed request.
        pattern = r'\b' + re.escape(name) + r'\b[\s,.!?:;-]*(.*)$'
        match = re.search(pattern, text.strip(), re.I)
        with self.lock:
            armed = self.armed.get(source, 0) > time.monotonic()
            if match:
                command = match[1].strip()
            elif armed:
                command = text.strip()
            else:
                return None
            if not command:
                self.arm(source)
                return ''
            self.armed[source] = 0
            return command

    def snapshot(self):
        with self.lock:
            return {source: dict(values) for source, values in self.state.items()}
