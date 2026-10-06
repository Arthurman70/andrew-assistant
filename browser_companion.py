"""Authenticated, task-scoped RPC broker for the Andrew browser companion."""
import hashlib
import base64
import json
import queue
import re
import secrets
import threading
import time
from pathlib import Path
from local_commands import web_url
from urllib.parse import urlsplit
import ipaddress

HOST_NAME='org.andrew.companion'
ACTIONS={'tabs','search','navigate','inspect','read','click','type','scroll','close','back','forward','reload'}

def extension_id(root):
    manifest=json.loads((Path(root)/'companion/manifest.json').read_text(encoding='utf-8'))
    digest=hashlib.sha256(base64.b64decode(manifest['key'])).digest()[:16]
    return ''.join(chr(97+(n>>4))+chr(97+(n&15)) for n in digest)

def browser_url(value):
    value=web_url(value);host=urlsplit(value).hostname.lower()
    if host in ('localhost','::1') or host.endswith('.localhost'):raise ValueError('Local control pages use the PC interface.')
    try:
        if ipaddress.ip_address(host).is_private:raise ValueError('Local control pages use the PC interface.')
    except ValueError as exc:
        if 'Local control' in str(exc):raise
    return value

class BrowserCompanion:
    def __init__(self,app,root,clock=time.monotonic):
        self.app=app;self.root=Path(root);self.clock=clock;self.lock=threading.RLock();self.clients={};self.pending={}
        config=app.directory/'browser-companion.json'
        if not config.exists():config.write_text(json.dumps({'token':secrets.token_urlsafe(36)}),encoding='utf-8')
        self.token=json.loads(config.read_text(encoding='utf-8'))['token']
    def status(self):
        with self.lock:
            live=[c for c in self.clients.values() if self.clock()-c['seen']<20]
        return {'connected':bool(live),'browsers':sorted({c['browser'] for c in live}),'extension_id':extension_id(self.root),'preferred':True}
    def hello(self,version,browser):
        session=secrets.token_urlsafe(24)
        with self.lock:self.clients[session]={'seen':self.clock(),'browser':browser if browser in ('Chrome','Edge') else 'Browser','version':str(version)[:30],'queue':queue.Queue(maxsize=32)}
        return {'session':session,'connected':True}
    def poll(self,session):
        with self.lock:
            client=self.clients.get(session)
            if not client:raise ValueError('Browser connection expired. Reconnect the companion.')
            client['seen']=self.clock()
        try:return client['queue'].get(timeout=8)
        except queue.Empty:return {'type':'idle'}
    def result(self,session,message):
        with self.lock:
            client=self.clients.get(session)
            if client:client['seen']=self.clock()
            pending=self.pending.get(message.get('id'))
            if not pending or pending['session']!=session:return False
            if pending['queue'].empty():pending['queue'].put(message)
            return True
    def perform(self,args,request='',timeout=25):
        if not isinstance(args,dict) or args.get('action') not in ACTIONS:raise ValueError('Unsupported companion action.')
        action=args['action'];args=dict(args)
        if action=='navigate':args['url']=browser_url(args.get('url',''))
        if action=='search':
            if not isinstance(args.get('query'),str) or not 1<=len(args['query'])<=1000:raise ValueError('Use a search query under 1000 characters.')
        if action=='type' and (not isinstance(args.get('text'),str) or len(args['text'])>32000):raise ValueError('Use editor text under 32000 characters.')
        if action=='close' and not re.search(r'\b(close|stop|exit)\b',request,re.I):raise ValueError('Closing a browser tab must be requested.')
        if action=='read' and (type(args.get('offset',0))!=int or not 0<=args.get('offset',0)<=1000000):raise ValueError('Use a bounded document offset.')
        for key in ('tab','window'):
            if key in args and (type(args[key])!=int or args[key]<0):raise ValueError('Use a returned browser tab ID.')
        with self.lock:
            live=[(id,c) for id,c in self.clients.items() if self.clock()-c['seen']<20]
            if not live:raise ValueError('The companion is not connected. Use the PC browser controls as a fallback.')
            session,client=max(live,key=lambda row:row[1]['seen']);id=secrets.token_hex(16);reply=queue.Queue(maxsize=1)
            self.pending[id]={'session':session,'queue':reply}
        try:
            client['queue'].put({'type':'command','id':id,'args':args,'request':request[:32000]},timeout=2)
            message=reply.get(timeout=timeout)
            if message.get('error'):raise ValueError(str(message['error'])[:500])
            result=message.get('result')
            if not isinstance(result,dict):raise ValueError('The browser did not return an observed result.')
            return dict(result,adapter='Andrew browser companion')
        except queue.Empty:raise ValueError('The browser took too long. Inspect the current page before retrying.')
        finally:
            with self.lock:self.pending.pop(id,None)
