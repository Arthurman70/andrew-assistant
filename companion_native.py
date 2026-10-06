"""Chrome native-messaging transport. Only paired loopback RPC, no shell tools."""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parent

def read_message(stream):
    header=stream.read(4)
    if not header:return None
    if len(header)!=4:raise ValueError('Incomplete message header')
    length=struct.unpack('=I',header)[0]
    if not 0<length<=1048576:raise ValueError('Message too large')
    data=bytearray()
    while len(data)<length:
        part=stream.read(length-len(data))
        if not part:raise ValueError('Incomplete message')
        data.extend(part)
    value=json.loads(data.decode('utf-8'))
    if not isinstance(value,dict):raise ValueError('Message must be an object')
    return value

def write_message(stream,value):
    data=json.dumps(value,ensure_ascii=False).encode('utf-8')
    if len(data)>1048576:raise ValueError('Native reply too large')
    stream.write(struct.pack('=I',len(data))+data);stream.flush()

def origin(root):
    key=json.loads((Path(root)/'companion/manifest.json').read_text(encoding='utf-8'))['key']
    digest=hashlib.sha256(base64.b64decode(key)).digest()[:16]
    id=''.join(chr(97+(v>>4))+chr(97+(v&15)) for v in digest)
    return 'chrome-extension://'+id+'/'

def main():
    if len(sys.argv)<2 or sys.argv[1]!=origin(ROOT):return 1
    if os.name=='nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(),os.O_BINARY);msvcrt.setmode(sys.stdout.fileno(),os.O_BINARY)
    config=json.loads((ROOT/'data/browser-companion.json').read_text(encoding='utf-8'))
    stopped=threading.Event();session={};hello={};lock=threading.Lock();workers=ThreadPoolExecutor(max_workers=4)
    def send(message):
        with lock:write_message(sys.stdout.buffer,message)
    def rpc(path,data=None,timeout=12):
        headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/json','X-Andrew-Companion':'1'}
        request=urllib.request.Request('http://127.0.0.1:8765/api/companion/'+path,None if data is None else json.dumps(data).encode(),headers)
        with urllib.request.urlopen(request,timeout=timeout) as reply:return json.load(reply)
    def dispatch(message):
        try:
            if message.get('type')=='hello':
                hello.update(message)
                value=rpc('hello',{'version':message.get('version',''),'browser':message.get('browser','Chrome')});session.update(value);send({'type':'connected'})
            elif message.get('type')=='result' and session.get('session'):
                rpc('result',{'session':session['session'],'message':message})
            elif message.get('type')=='ask' and session.get('session'):
                value=rpc('ask',{'session':session['session'],'request':message.get('request','')},timeout=185)
                send({'type':'ask_result','request_id':message.get('request_id'),**value})
        except Exception:send({'type':'error','request_id':message.get('request_id'),'error':'Andrew is unavailable. Open Andrew and reconnect the companion.'})
    def reader():
        try:
            while not stopped.is_set():
                message=read_message(sys.stdin.buffer)
                if message is None:break
                workers.submit(dispatch,message)
        except Exception:pass
        finally:stopped.set()
    threading.Thread(target=reader,daemon=True).start()
    while not stopped.is_set():
        if not session.get('session'):
            if hello:
                try:dispatch(dict(hello))
                except Exception:pass
                stopped.wait(1)
            else:stopped.wait(.2)
            continue
        try:
            command=rpc('poll?session='+session['session'])
            if command.get('type')!='idle':send(command)
        except Exception:session.clear();stopped.wait(1)
    workers.shutdown(wait=False,cancel_futures=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
