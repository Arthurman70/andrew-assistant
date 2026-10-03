"""Own only Andrew's Pi display processes. No shell or arbitrary program execution."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlsplit

HOME_URL='http://127.0.0.1:8770/'
RUNTIME=Path('/run/andrew')
process=None
current='home'
lock=threading.RLock()
last_error=''


def stop_display():
    # PAM/logind may move Chromium into a session scope. Match only our dedicated profile.
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            args=(entry/'cmdline').read_bytes().split(b'\0')
            if b'--user-data-dir=/run/andrew/browser-profile' in args:
                os.kill(int(entry.name),signal.SIGTERM)
        except (OSError,ProcessLookupError): pass
    if process and process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try: process.wait(6)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.wait(3)


def navigate(url=HOME_URL):
    global process,current,last_error
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or any(ord(c)<33 for c in url):
        raise ValueError('A normal HTTP or HTTPS website address is required.')
    with lock:
        stop_display()
        time.sleep(.6)
        media=parsed.path.lower().endswith(('.mp4','.webm','.mkv','.mov','.m3u8'))
        # Configure only this display process's ALSA home, never the system mixer.
        try:
            import re
            with urllib.request.urlopen('http://127.0.0.1:8770/api/status',timeout=3) as reply:
                output=json.load(reply).get('pi_output','auto')
            if re.fullmatch(r'plughw:[A-Za-z0-9_]+,[0-9]+',output):
                Path(os.environ['HOME'],'.asoundrc').write_text('pcm.!default { type plug slave.pcm "'+output+'" }\n')
        except Exception: pass
        if media: (RUNTIME/'mpv.sock').unlink(missing_ok=True)
        player=(['mpv','--fs','--no-terminal','--no-config','--log-file=/run/andrew/mpv.log',
                 '--input-ipc-server=/run/andrew/mpv.sock',url] if media else
            ['chromium','--ozone-platform=wayland','--kiosk','--no-first-run','--disable-session-crashed-bubble',
             '--autoplay-policy=no-user-gesture-required','--password-store=basic',
             '--disk-cache-size=20971520','--user-data-dir=/run/andrew/browser-profile',url])
        with open(RUNTIME/'display.log','ab',buffering=0) as output:
            process=subprocess.Popen(['cage','-d','--']+player,stdout=output,stderr=output,start_new_session=True)
        current='video' if media else ('home' if url==HOME_URL else 'browser')
        if media:
            ready=False
            for _ in range(30):
                if process.poll() is not None: break
                try:
                    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                        client.settimeout(.3);client.connect('/run/andrew/mpv.sock')
                        client.sendall(b'{"command":["get_property","time-pos"],"request_id":123}\n')
                        data=client.recv(8192)
                        if b'"success"' in data and b'"data"' in data:
                            ready=True;break
                except OSError: pass
                time.sleep(.2)
        else: time.sleep(1.5)
        if process.poll() is not None or (media and not ready):
            last_error='The display could not start. Check the Pi screen connection.'
            if media: last_error='This video could not start. Check that the address links to a playable video.'
            stop_display()
            raise RuntimeError(last_error)
        last_error=''


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_): pass
    def reply(self,status,value):
        payload=json.dumps(value).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)))
        self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        self.reply(200,{'mode':current,'running':bool(process and process.poll() is None),'error':last_error})
    def do_POST(self):
        if self.headers.get('X-Andrew-Display')!='1' or self.headers.get('Origin'):
            return self.reply(403,{'error':'Local agent only.'})
        try:
            length=int(self.headers.get('Content-Length',0))
            if not 0<length<4096: raise ValueError('Invalid request.')
            data=json.loads(self.rfile.read(length));action=data.get('action')
            if action=='home': navigate()
            elif action=='open': navigate(data['url'])
            elif action in ('pause','resume'):
                if current!='video': raise ValueError('Pause and resume apply to direct video files. Use the website controls for web videos.')
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
                    client.settimeout(2);client.connect('/run/andrew/mpv.sock')
                    client.sendall((json.dumps({'command':['set_property','pause',action=='pause']})+'\n').encode())
            else: raise ValueError('Unknown display command.')
            self.reply(200,{'ok':True,'mode':current})
        except Exception as exc: self.reply(503,{'error':str(exc)[:180]})


def main():
    def shutdown(*_):
        stop_display()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,shutdown)
    for folder in ('browser-home','browser-profile','xdg'):
        (RUNTIME/folder).mkdir(exist_ok=True,mode=0o700)
    try: navigate()
    except Exception: pass
    def watch():
        while True:
            time.sleep(2)
            with lock:
                if current!='home' and process and process.poll() is not None:
                    try: navigate()
                    except Exception: pass
    threading.Thread(target=watch,daemon=True).start()
    ThreadingHTTPServer(('127.0.0.1',8771),Handler).serve_forever()

if __name__=='__main__': main()
