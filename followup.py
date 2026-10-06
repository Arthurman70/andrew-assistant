"""A bounded, source-specific conversation window following an addressed reply."""
import secrets
import threading
import time

class FollowupSessions:
    def __init__(self,app,clock=time.monotonic):
        self.app=app;self.clock=clock;self.lock=threading.RLock();self.sessions={}
    def enabled(self,source):
        return source in ('pc','pi') and bool(self.app.get('followup_enabled')) and bool(self.app.get(source+'_listening')) and not self.app.snooze_remaining(source)
    def prepare(self,source,person=None):
        if not self.enabled(source):self.close(source);return None
        seconds=max(3,min(20,int(self.app.get('followup_seconds') or 12)))
        grant=secrets.token_urlsafe(24)
        with self.lock:self.sessions[source]={'grant':grant,'person':person,'seconds':seconds,'armed':False,'prepared':self.clock(),'until':0,'ready_at':0}
        return grant
    def arm(self,source,grant):
        if not self.enabled(source):self.close(source);return False
        with self.lock:
            entry=self.sessions.get(source)
            if not entry or entry['grant']!=grant or entry['armed']:return False
            entry.update(armed=True,ready_at=self.clock()+.35,until=self.clock()+.35+entry['seconds'])
            return True
    def view(self,source):
        if not self.enabled(source):self.close(source);return {'active':False,'remaining':0}
        with self.lock:
            entry=self.sessions.get(source)
            if not entry or not entry['armed']:return {'active':False,'remaining':0}
            remaining=max(0,entry['until']-self.clock())
            return {'active':entry['ready_at']<=self.clock()<entry['until'],'remaining':remaining,'grant':entry['grant'] if remaining else None}
    def claim(self,source,grant):
        if not self.enabled(source):self.close(source);return None
        with self.lock:
            entry=self.sessions.get(source)
            # The capture must start in the displayed window. An authenticated
            # satellite may finish its bounded utterance just after it expires.
            if not entry or not entry['armed'] or entry['grant']!=grant or not entry['ready_at']<=self.clock()<=entry['until']+20:return None
            self.sessions.pop(source,None)
            return dict(entry)
    def close(self,source):
        with self.lock:self.sessions.pop(source,None)
    def status(self):
        return {source:{k:v for k,v in self.view(source).items() if k!='grant'} for source in ('pc','pi')}
