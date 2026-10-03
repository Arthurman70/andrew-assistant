"""Bounded speech worker, separate dependencies and no listening socket."""
import base64
import json
from pathlib import Path
import queue
import subprocess
import threading
import atexit

ROOT=Path(__file__).resolve().parent
class NanoVoice:
    def __init__(self):self.process=None;self.responses=queue.Queue();self.ready=threading.Event();atexit.register(self.close)
    def close(self):
        if self.process and self.process.poll() is None:self.process.kill()
        self.process=None
        self.ready.clear()
    def start(self):
        if self.process and self.process.poll() is None:return
        self.responses=queue.Queue()
        self.ready.clear()
        self.process=subprocess.Popen([str(ROOT/'runtime/chatterbox-env/Scripts/python.exe'),str(ROOT/'nano_voice_worker.py')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process=self.process;responses=self.responses
        def read():
            for line in process.stdout:
                try:value=json.loads(line)
                except ValueError:continue
                if value.get('ready'):self.ready.set()
                else:responses.put(value)
            responses.put(None)
        threading.Thread(target=read,daemon=True).start()
    def synthesize(self,text):
        self.start()
        if not self.ready.is_set():raise RuntimeError('Nano voice is warming up.')
        try:
            self.process.stdin.write(json.dumps({'text':text})+'\n');self.process.stdin.flush()
            value=self.responses.get(timeout=15)
            if value is None:raise RuntimeError('Nano voice stopped.')
            if 'audio' not in value:raise RuntimeError(value.get('error','No voice response.'))
            return base64.b64decode(value['audio'],validate=True)
        except Exception:
            self.close();raise
