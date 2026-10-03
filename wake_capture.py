"""Capture one request only after keyword detection. No background transcription."""
from collections import deque
import numpy as np
import webrtcvad
from wake_detector import WakeDetector
from background_guard import BackgroundGuard


class WakeCapture:
    def __init__(self,directory,name):
        self.detector=WakeDetector(directory,name)
        self.vad=webrtcvad.Vad(3)
        self.command_vad=webrtcvad.Vad(1)
        self.context=deque(maxlen=150)  # Volatile keyword context, never a recording.
        self.background=BackgroundGuard()
        self.cooldown=0
        self.cautious=True
        self.rejected_wakes=0
        self.empty_wakes=0
        self.request=bytearray()
        self.phase='waiting for name'
        self.active=False
        self.silence=self.voiced=self.frames=0
        self.threshold=30

    def reset(self):
        self.detector.reset();self.context.clear();self.request.clear()
        self.active=False;self.phase='waiting for name'
        self.silence=self.voiced=self.frames=0

    def feed(self,frame,name,mode='adaptive',media=False):
        if name!=self.detector.name:
            self.detector.configure(name);self.reset()
        values=np.frombuffer(frame,dtype='<i2').astype(np.float32)
        rms=float(np.sqrt(np.mean(values*values)))
        if not self.active:
            speech=self.vad.is_speech(frame,16000) and rms>=30
            self.background.observe(rms,speech)
            self.cautious=self.background.cautious(media,mode)
            if self.cooldown:
                self.cooldown-=1
                return None
            self.context.append(frame)
            boundary=self.detector.accept(frame)
            if boundary is None: return None
            if not self.background.accepts_wake(media,mode):
                self.rejected_wakes+=1
                self.reset();self.cooldown=75
                return None
            # Retain only the verified wake address and subsequent request.
            context=b''.join(self.context)
            retained=max(0,min(len(context),(self.detector.samples-boundary)*2))
            self.request.extend(context[-retained:] if retained else b'')
            self.context.clear();self.active=True;self.phase='listening'
            # The strict foreground gate has already accepted the wake name.
            # Quiet/short command syllables need a more tolerant speech detector.
            self.command_vad=webrtcvad.Vad(1)
            self.threshold=self.background.command_floor()
            # Keyword decoding lags the sound slightly. Count the request
            # syllables already retained after the wake boundary too.
            command_start=getattr(self.detector,'command_start',boundary)
            if not isinstance(command_start,int):command_start=boundary
            post_wake=max(0,min(len(self.request),(self.detector.samples-command_start)*2))
            command_audio=bytes(self.request[-post_wake:]) if post_wake else b''
            for at in range(0,len(command_audio)-639,640):
                kept=command_audio[at:at+640]
                level=np.frombuffer(kept,dtype='<i2').astype(np.float32)
                if self.command_vad.is_speech(kept,16000) and float(np.sqrt(np.mean(level*level)))>=self.threshold:
                    self.voiced+=1
            return None
        self.request.extend(frame);self.frames+=1
        speech=self.command_vad.is_speech(frame,16000) and rms>=self.threshold
        self.voiced+=int(speech);self.silence=0 if speech else self.silence+1
        # Allow a natural pause between a verb and its object, or two commands.
        if self.voiced>=6 and (self.silence>=36 or self.frames>=900):
            result=bytes(self.request)
            self.reset()
            return result
        if self.frames>=250 and self.voiced<6:
            self.background.missed_request();self.empty_wakes+=1
            self.reset();self.cooldown=25
            # No request followed the wake. Return quietly to idle instead of
            # speaking to the room (especially at night). Actual speech that
            # fails recognition still gets the server's audible retry.
            return None
        return None
