"""Google Voice through the user's normal Chrome session, with no extra login."""
import queue
import threading
import time

class GoogleVoice:
    def __init__(self,browser_factory=None):
        self.jobs=queue.Queue();self.thread=None;self.browser_factory=browser_factory
        self.state={'state':'disconnected','message':'Use Google Voice in your Chrome session'};self.lock=threading.Lock()
    def status(self):
        with self.lock:return dict(self.state)
    def update(self,state,message):
        with self.lock:self.state={'state':state,'message':message}
    def connect(self):
        if self.thread and self.thread.is_alive():
            self.jobs.put(('open','','',None,time.monotonic()+30));return
        self.update('connecting','Opening Google Voice in your normal Chrome browser')
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def perform(self,kind,number,message):
        if kind not in ('text','call','hangup'):return 'That Google Voice action is not supported.'
        if not self.thread or not self.thread.is_alive():return 'Google Voice is disconnected. Connect it first.'
        done=queue.Queue(maxsize=1);self.jobs.put((kind,number,message,done,time.monotonic()+40))
        try:return done.get(timeout=45)
        except queue.Empty:return 'Google Voice did not confirm the result. Check its tab before trying again; I will not retry automatically.'
    def run(self):
        try:
            if self.browser_factory:browser=self.browser_factory()
            else:
                from google_voice_desktop import ChromeVoice
                browser=ChromeVoice()
            browser.open()
            while True:
                try:kind,number,message,done,deadline=self.jobs.get(timeout=3)
                except queue.Empty:
                    problem='Keep Google Voice selected in your regular Chrome window.'
                    try:ready=browser.ready()
                    except ValueError as exc:ready=False;problem=str(exc)
                    except Exception:ready=False;problem='Chrome desktop controls are unavailable. Keep Windows unlocked.'
                    self.update('ready' if ready else 'attention',
                        'Connected through your Chrome session' if ready else
                        problem)
                    continue
                if time.monotonic()>deadline:
                    if done:done.put('The request expired before it could run. Nothing was sent or called.')
                    continue
                if kind=='open':browser.open();continue
                try:result=browser.action(kind,number,message,deadline)
                except Exception:
                    result='Google Voice could not verify completion. Check your Chrome tab; I will not automatically repeat a call or text.'
                done.put(result)
        except Exception:
            self.update('unavailable','Google Voice needs your normal Chrome window open and Windows unlocked. Try Connect again.')
