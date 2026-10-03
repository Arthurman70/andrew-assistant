"""Adapt to ambient speech using numerical levels only; no text or saved audio."""
from collections import deque
from voice_tuning import load as load_tuning


class BackgroundGuard:
    def __init__(self):
        self.tuning=load_tuning()
        self.recent=deque(maxlen=300)  # Six seconds of levels / VAD flags, no samples.
        self.frames=0
        self.cautious_until=0
        self.failures=deque(maxlen=6)

    def observe(self, rms, speech):
        self.frames+=1
        self.recent.append((float(rms),bool(speech)))
        if len(self.recent)>=100 and sum(s for _,s in self.recent)/len(self.recent)>.45:
            self.cautious_until=self.frames+200

    def cautious(self, media=False, mode='adaptive'):
        return bool(mode=='strict' or media or self.frames<100 or self.frames<self.cautious_until)

    def accepts_wake(self, media=False, mode='adaptive'):
        rows=list(self.recent)
        if len(rows)<100: return False
        ambient=sorted(r for r,_ in (rows[:-75] or rows[:max(1,len(rows)//3)]))
        foreground=sorted(r for r,s in rows[-60:] if s)
        if not foreground: return False
        floor=ambient[int((len(ambient)-1)*.65)]
        voice=foreground[int((len(foreground)-1)*.75)]
        cautious=self.cautious(media,mode)
        ratio=2.2 if mode=='strict' else (1.7 if cautious else self.tuning['quiet_foreground_ratio'])
        minimum=60 if mode=='strict' else (40 if cautious else self.tuning['quiet_min_rms'])
        return voice>=max(minimum,floor*ratio)

    def command_floor(self):
        if not self.recent: return 35
        # Use quiet portions for the command VAD floor. The separate foreground
        # gate rejects a wake that does not stand out from ongoing speech.
        rows=list(self.recent)
        levels=sorted(r for r,_ in (rows[:-75] or rows))
        return max(25,min(300,levels[int((len(levels)-1)*.2)]*1.3))

    def missed_request(self):
        self.failures.append(self.frames)
        if sum(self.frames-at<3000 for at in self.failures)>=3:
            self.cautious_until=max(self.cautious_until,self.frames+4500)
