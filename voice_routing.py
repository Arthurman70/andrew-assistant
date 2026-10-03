"""Prevent one utterance heard by two rooms from executing twice."""
import re
import threading
import time
import io
import wave


class VoiceGate:
    def __init__(self):
        self.lock = threading.Lock()
        self.recent = {}

    def claim(self, text, source, now=None):
        now = time.time() if now is None else now
        key = re.sub(r'\W+', ' ', text.lower()).strip()
        with self.lock:
            self.recent = {k: v for k, v in self.recent.items() if abs(now-v[0]) < 1.5}
            previous = self.recent.get(key)
            if previous and previous[1] != source:
                return False
            self.recent[key] = (now, source)
            return True


class PlaybackGate:
    """Discard captured clips that contain Andrew's own loudspeaker replies."""
    def __init__(self):
        self.lock = threading.Lock()
        self.windows = []

    def record(self, wav, source, now=None):
        with wave.open(io.BytesIO(wav),'rb') as audio:
            duration = audio.getnframes()/audio.getframerate()
        if duration < .3: return  # A listening cue must not swallow the next request.
        now = time.time() if now is None else now
        with self.lock:
            self.windows = [w for w in self.windows if w[1] > now-60]
            self.windows.append((now,now+duration+.3,source))

    def overlaps(self, start, end):
        with self.lock:
            return any(min(end,b)-max(start,a) > .2 for a,b,_ in self.windows)

    def stop(self, source):
        now=time.time()
        with self.lock:self.windows=[(a,min(b,now),s) if s==source else (a,b,s) for a,b,s in self.windows]

    def is_set(self):
        now = time.time()
        with self.lock: return any(a <= now <= b for a,b,_ in self.windows)
