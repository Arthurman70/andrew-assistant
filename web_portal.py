"""Small authenticated web gateway. AI sessions and speech engines stay on the host PC.

Public mode binds a private reverse-proxy interface; bridge mode binds PC loopback.
Neither mode accepts arbitrary upstream addresses or shell commands from requests.
"""
import argparse
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent
SESSION_SECONDS=90*24*3600
SESSION_RENEW_SECONDS=24*3600
GET_PATHS={'/api/status','/api/providers','/api/providers?refresh=1','/api/devices','/api/models',
           '/api/browser-notifications','/api/photo','/api/camera-frame'}
POST_PATHS={'/api/command','/api/browser-voice','/api/provider','/api/settings','/api/pi-update',
            '/api/pc-task-stop','/api/improvements','/api/improvement-install','/api/improvement-rollback',
            '/api/bambu','/api/followup/close','/api/camera','/api/test-provider','/api/connect-google-voice','/api/connect-claude','/api/connect-grok','/api/memory-delete','/api/test-speaker'}

def allowed(path,method):
    if method=='POST':return path in POST_PATHS
    clean=path.split('?')[0]
    if path in GET_PATHS or clean=='/api/camera-frame':return True
    import re
    return bool(re.fullmatch(r'/api/camera-media/[a-f0-9]{32}\.(jpg|mp4)',clean) or
                re.fullmatch(r'/api/improvement-review/[\w-]{1,100}',clean))

def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(24)
    return {'salt':salt,'hash':hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()}

def check_password(password,record):
    return hmac.compare_digest(password_hash(password,record['salt'])['hash'],record['hash'])

class Portal(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,config,mode='public',root=ROOT):
        self.config=config;self.mode=mode;self.root=Path(root)
        self.sessions={};self.preflight={};self.failures={};self.lock=threading.RLock()
        self.session_db=None
        if mode=='public':
            path=Path(config.get('config_path',self.root/'private/config.json')).resolve().parent/'sessions.sqlite3'
            path.parent.mkdir(parents=True,exist_ok=True)
            self.session_db=sqlite3.connect(path,check_same_thread=False)
            if os.name!='nt':path.chmod(0o600)
            self.session_db.execute('CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, csrf TEXT NOT NULL, until REAL NOT NULL, renew_at REAL NOT NULL, auth_tag TEXT NOT NULL)')
            self.session_db.execute('DELETE FROM sessions WHERE until<=? OR auth_tag!=?',(time.time(),self.auth_tag()))
            self.session_db.commit()
            self.sessions={row[0]:dict(zip(('csrf','until','renew_at','auth_tag'),row[1:])) for row in self.session_db.execute('SELECT id,csrf,until,renew_at,auth_tag FROM sessions')}
        super().__init__(address,Handler)
    def auth_tag(self):
        value={k:self.config.get(k) for k in ('email','password','origin')}
        return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    def save_session(self,id,session):
        if self.session_db:
            with self.session_db:self.session_db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?)',(id,session['csrf'],session['until'],session['renew_at'],session['auth_tag']))
    def delete_session(self,id):
        self.sessions.pop(id,None)
        if self.session_db:
            with self.session_db:self.session_db.execute('DELETE FROM sessions WHERE id=?',(id,))
    def server_close(self):
        super().server_close()
        with self.lock:
            if self.session_db:self.session_db.close();self.session_db=None

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def reply(self,code,value,kind='application/json',cookie=None):
        content=value if isinstance(value,bytes) else json.dumps(value).encode()
        self.send_response(code);self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(content)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin')
        self.send_header('Permissions-Policy','microphone=(self), camera=(self), geolocation=()')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
        if not cookie and getattr(self,'session_refresh',None):cookie=self.session_refresh
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(content)

    def cookie(self,name):
        from http.cookies import SimpleCookie
        try:return SimpleCookie(self.headers.get('Cookie','')).get(name).value
        except Exception:return ''

    def session(self):
        token=self.cookie('andrew_session')
        if not re.fullmatch(r'[A-Za-z0-9_-]{64}',token):return None
        id=hashlib.sha256(token.encode()).hexdigest();now=time.time()
        with self.server.lock:
            session=self.server.sessions.get(id)
            if session and session['until']>now and hmac.compare_digest(session['auth_tag'],self.server.auth_tag()):
                if now>=session['renew_at']:
                    session.update(until=now+SESSION_SECONDS,renew_at=now+SESSION_RENEW_SECONDS)
                    self.server.save_session(id,session)
                    self.session_refresh=self.session_cookie(token)
                return session
            if session:self.server.delete_session(id)
        return None
    def session_cookie(self,token):
        return 'andrew_session='+token+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age='+str(SESSION_SECONDS)

    def body(self,limit=1500000):
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=limit:raise ValueError('Request is too large or empty.')
        value=json.loads(self.rfile.read(size))
        if not isinstance(value,dict):raise ValueError('Use a JSON object.')
        return value

    def csrf(self,session):
        return (self.headers.get('Origin')==self.server.config['origin'] and
                hmac.compare_digest(self.headers.get('X-Andrew-CSRF',''),session['csrf']))

    def proxy(self,payload=None):
        path=self.path
        if not allowed(path,self.command):return self.reply(404,{'error':'Not found.'})
        if self.server.mode=='bridge' and path=='/api/command':path='/api/browser-command'
        headers={'Authorization':'Bearer '+self.server.config['bridge_token'],'Content-Type':'application/json'}
        if self.server.mode=='bridge':headers={'X-Andrew-Local':'1','Content-Type':'application/json'}
        req=urllib.request.Request(self.server.config['upstream']+path,None if payload is None else json.dumps(payload).encode(),headers)
        try:
            with urllib.request.urlopen(req,timeout=900 if path in ('/api/browser-voice','/api/browser-command','/api/browser-notifications') else 185) as response:
                self.reply(response.status,response.read(),response.headers.get('Content-Type','application/json'))
        except urllib.error.HTTPError as exc:self.reply(exc.code,exc.read(),exc.headers.get('Content-Type','application/json'))
        except Exception:self.reply(503,{'error':'Your host PC is offline. Open Andrew on the PC and try again.'})

    def do_GET(self):
        if self.server.mode=='bridge':
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.config['bridge_token']):
                return self.reply(401,{'error':'Authentication required.'})
            return self.proxy()
        if self.headers.get('Host')!=urlsplit(self.server.config['origin']).netloc:
            return self.reply(400,{'error':'Use the configured Andrew address.'})
        if self.path=='/health':return self.reply(200,{'ready':True})
        if self.path=='/widget.js':return self.reply(200,(self.server.root/'web/widget.js').read_bytes(),'text/javascript')
        if self.path in ('/manifest.webmanifest','/sw.js','/assets/icon-192.png','/assets/icon-512.png'):
            path=self.server.root/('web'+self.path if self.path in ('/manifest.webmanifest','/sw.js') else self.path.lstrip('/'))
            return self.reply(200,path.read_bytes(),'application/manifest+json' if self.path.endswith('webmanifest') else ('text/javascript' if self.path.endswith('.js') else 'image/png'))
        session=self.session()
        if not session:
            if self.path!='/':return self.reply(401,{'error':'Please sign in.'})
            token=secrets.token_urlsafe(32)
            with self.server.lock:
                self.server.preflight={k:v for k,v in self.server.preflight.items() if v>time.time()}
                if len(self.server.preflight)>500:self.server.preflight.clear()
                self.server.preflight[token]=time.time()+600
            page=(self.server.root/'web/login.html').read_text(encoding='utf-8').replace('{{csrf}}',token)
            return self.reply(200,page.encode(),'text/html; charset=utf-8','andrew_login='+token+'; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=600')
        if self.path=='/':
            page='web/password.html' if self.server.config.get('temporary') else 'app.html'
            html=(self.server.root/page).read_text(encoding='utf-8')
            html=html.replace('</head>','<meta name="andrew-csrf" content="'+session['csrf']+'"><link rel="manifest" href="/manifest.webmanifest"><meta name="theme-color" content="#14232a"><script src="/assets/browser.js"></script></head>')
            return self.reply(200,html.encode(),'text/html; charset=utf-8')
        if self.server.config.get('temporary'):return self.reply(403,{'error':'Change your temporary password first.'})
        if self.path in ('/assets/andrew.png','/assets/upgrade.js','/assets/upgrade.css','/assets/browser.js'):
            kind='image/png' if self.path.endswith('.png') else ('text/css' if self.path.endswith('.css') else 'text/javascript')
            return self.reply(200,(self.server.root/self.path.lstrip('/')).read_bytes(),kind)
        return self.proxy()

    def do_POST(self):
        try:
            if self.server.mode=='bridge':
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.config['bridge_token']):
                    return self.reply(401,{'error':'Authentication required.'})
                return self.proxy(self.body())
            if self.headers.get('Host')!=urlsplit(self.server.config['origin']).netloc:return self.reply(400,{'error':'Invalid address.'})
            if self.path=='/auth/login':return self.login()
            session=self.session()
            if not session:return self.reply(401,{'error':'Please sign in.'})
            if not self.csrf(session):return self.reply(403,{'error':'Reload Andrew and try again.'})
            data=self.body()
            if self.path=='/auth/logout':
                with self.server.lock:self.server.delete_session(hashlib.sha256(self.cookie('andrew_session').encode()).hexdigest())
                return self.reply(200,{'ok':True},cookie='andrew_session=; Secure; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if self.path=='/auth/password':
                password=data.get('password','')
                if not isinstance(password,str) or not 12<=len(password)<=200:raise ValueError('Choose a password of 12 to 200 characters.')
                if not check_password(data.get('current',''),self.server.config['password']):return self.reply(403,{'error':'Current password is incorrect.'})
                if check_password(password,self.server.config['password']):raise ValueError('Choose a different password.')
                with self.server.lock:
                    self.server.config.update(password=password_hash(password),temporary=False)
                    path=self.server.config['config_path'];Path(path).write_text(json.dumps(self.server.config),encoding='utf-8')
                    id=hashlib.sha256(self.cookie('andrew_session').encode()).hexdigest()
                    session['auth_tag']=self.server.auth_tag()
                    self.server.sessions={id:session}
                    with self.server.session_db:self.server.session_db.execute('DELETE FROM sessions')
                    self.server.save_session(id,session)
                return self.reply(200,{'ok':True})
            if self.server.config.get('temporary'):return self.reply(403,{'error':'Change your temporary password first.'})
            return self.proxy(data)
        except (ValueError,TypeError,KeyError):self.reply(400,{'error':'Invalid request. Check the fields and try again.'})

    def login(self):
        token=self.cookie('andrew_login')
        with self.server.lock:valid=self.server.preflight.get(token,0)>time.time()
        if not valid or not self.csrf({'csrf':token}):return self.reply(403,{'error':'Reload the sign-in page.'})
        data=self.body(16000)
        ip=self.headers.get('X-Forwarded-For',self.client_address[0]).split(',')[-1].strip()
        with self.server.lock:
            failures=[t for t in self.server.failures.get(ip,[]) if t>time.time()-900]
            if len(failures)>=8:return self.reply(429,{'error':'Too many attempts. Try again in 15 minutes.'})
            self.server.failures[ip]=failures+[time.time()]
            if len(self.server.failures)>2000:self.server.failures={ip:self.server.failures[ip]}
        email=str(data.get('email','')).strip().lower();password=data.get('password','')
        if not isinstance(password,str) or len(password)>200:raise ValueError()
        # Always hash even for an unknown email to avoid account enumeration timing.
        matches=check_password(password,self.server.config['password'])
        if not matches or not hmac.compare_digest(email,self.server.config['email']):return self.reply(401,{'error':'Email or password is incorrect.'})
        key=secrets.token_urlsafe(48)
        with self.server.lock:
            self.server.preflight.pop(token,None);self.server.failures.pop(ip,None)
            for id,session in list(self.server.sessions.items()):
                if session['until']<=time.time():self.server.delete_session(id)
            id=hashlib.sha256(key.encode()).hexdigest()
            session={'csrf':secrets.token_urlsafe(32),'until':time.time()+SESSION_SECONDS,'renew_at':time.time()+SESSION_RENEW_SECONDS,'auth_tag':self.server.auth_tag()}
            self.server.sessions[id]=session;self.server.save_session(id,session)
        self.reply(200,{'ok':True},cookie=self.session_cookie(key))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True)
    parser.add_argument('--mode',choices=['public','bridge'],default='public');args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'));config['config_path']=str(Path(args.config).resolve())
    Portal((config.get('bind','127.0.0.1'),config.get('port',18770)),config,args.mode).serve_forever()

if __name__=='__main__':main()
