"""Capture one request only after keyword detection. No background transcription."""
from collections import deque
import numpy as np
import time
import webrtcvad
from wake_detector import WakeDetector
from background_guard import BackgroundGuard
from conversation_turns import MAX_CAPTURE_SECONDS, MIN_COMMAND_FRAMES, end_silence_frames


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
        self.followup_until=0;self.followup_grant=None;self.last_grant=None;self.activation='wake'
        self.sleep_only=False
        self.followup_start=deque(maxlen=10)
        self.followup_voiced=0
        self.followup_floor=35
        self.used_grants=deque(maxlen=8)

    def offer_followup(self,grant,seconds):
        if not grant or grant in self.used_grants:return
        if grant!=self.followup_grant:
            self.followup_grant=grant;self.followup_until=time.monotonic()+max(0,seconds)
            self.followup_floor=self.background.command_floor()

    def close_followup(self):
        self.followup_grant=None;self.followup_until=0
        self.followup_start.clear();self.followup_voiced=0

    def reset(self):
        self.detector.reset();self.context.clear();self.request.clear()
        self.active=False;self.phase='waiting for name'
        self.silence=self.voiced=self.frames=0
        self.followup_start.clear();self.followup_voiced=0

    def set_sleeping(self, sleeping, name):
        if sleeping != self.sleep_only or name != self.detector.name:
            self.detector.configure(name,sleep_only=sleeping)
            self.sleep_only=sleeping
            self.reset();self.close_followup();self.last_grant=None
            self.cooldown=0

    def feed_sleeping(self, frame, name, mode='adaptive', media=False):
        # Only the complete wake-up phrase is decoded while snoozed. No command
        # buffer, follow-up grant, transcript or speech model is used here.
        self.set_sleeping(True,name)
        values=np.frombuffer(frame,dtype='<i2').astype(np.float32)
        rms=float(np.sqrt(np.mean(values*values)))
        self.background.observe(rms,self.vad.is_speech(frame,16000) and rms>=30)
        self.phase='sleeping · say '+name+', wake up'
        if self.cooldown:
            self.cooldown-=1
            return False
        if self.detector.accept(frame) is None:
            return False
        accepted=self.background.accepts_wake(media,mode)
        self.reset();self.cooldown=75
        if not accepted:self.rejected_wakes+=1
        return accepted

    def feed(self,frame,name,mode='adaptive',media=False):
        self.set_sleeping(False,name)
        values=np.frombuffer(frame,dtype='<i2').astype(np.float32)
        rms=float(np.sqrt(np.mean(values*values)))
        if not self.active:
            speech=self.vad.is_speech(frame,16000) and rms>=30
            self.background.observe(rms,speech)
            self.cautious=self.background.cautious(media,mode)
            if self.cooldown:
                self.cooldown-=1
                return None
            if self.followup_grant and self.followup_until>time.monotonic() and not media:
                floor=self.followup_floor
                foreground=self.command_vad.is_speech(frame,16000) and rms>=max(40,floor*(2.2 if mode=='strict' else 1.7))
                self.followup_start.append(frame)
                self.followup_voiced=self.followup_voiced+1 if foreground else 0
                if self.followup_voiced>=5 and self.background.accepts_followup(rms,mode):
                    self.request.clear();self.request.extend(b''.join(self.followup_start))
                    self.active=True;self.activation='followup';self.last_grant=self.followup_grant
                    self.used_grants.append(self.followup_grant)
                    self.phase='follow-up listening';self.threshold=floor;self.frames=len(self.followup_start);self.voiced=5;self.silence=0
                    self.close_followup();self.context.clear();return None
                self.phase='follow-up listening'
            else:
                self.close_followup();self.phase='waiting for name'
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
            self.context.clear();self.active=True;self.phase='listening';self.activation='wake';self.last_grant=None;self.close_followup()
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
        if self.activation=='followup' and media:
            self.reset();self.close_followup();return None
        if self.activation=='followup' and self.detector.accept(frame) is not None:
            self.activation='wake';self.last_grant=None
        self.request.extend(frame);self.frames+=1
        speech=self.command_vad.is_speech(frame,16000) and rms>=self.threshold
        self.voiced+=int(speech);self.silence=0 if speech else self.silence+1
        # Allow a natural pause between a verb and its object, or two commands.
        if self.voiced>=MIN_COMMAND_FRAMES and (self.silence>=end_silence_frames(self.voiced) or len(self.request)>=MAX_CAPTURE_SECONDS*32000):
            result=bytes(self.request)
            self.reset()
            return result
        if self.frames>=250 and self.voiced<MIN_COMMAND_FRAMES:
            self.background.missed_request();self.empty_wakes+=1
            self.reset();self.cooldown=25
            # No request followed the wake. Return quietly to idle instead of
            # speaking to the room (especially at night). Actual speech that
            # fails recognition still gets the server's audible retry.
            return None
        return None
