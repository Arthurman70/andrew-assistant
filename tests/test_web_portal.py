import http.client
import hashlib
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from web_portal import Portal,password_hash,check_password,allowed,SESSION_SECONDS,SESSION_RENEW_SECONDS

class PortalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.password=password_hash('Long test password 42!')
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config={'origin':'https://andrew.example','email':'owner@example.com','password':dict(self.password),
                     'temporary':False,'bridge_token':'private-test-token','upstream':'http://127.0.0.1:9',
                     'config_path':str(Path(self.temp.name)/'config.json')}
        self.server=Portal(('127.0.0.1',0),self.config)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self,path,method='GET',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        connection.request(method,path,None if body is None else json.dumps(body),{'Host':'andrew.example',**(headers or {})})
        response=connection.getresponse();result=(response.status,dict(response.getheaders()),response.read());connection.close();return result
    def login(self):
        status,headers,page=self.request('/')
        token=re.search(rb'name="andrew-csrf" content="([^"]+)"',page).group(1).decode()
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('Secure',headers['Set-Cookie'])
        status,headers,body=self.request('/auth/login','POST',{'email':'owner@example.com','password':'Long test password 42!'},
                {'Origin':self.config['origin'],'X-Andrew-CSRF':token,'Cookie':'andrew_login='+token})
        self.assertEqual(status,200)
        cookie=headers['Set-Cookie'].split(';')[0];key=cookie.split('=',1)[1]
        self.assertIn('Max-Age='+str(SESSION_SECONDS),headers['Set-Cookie'])
        return {'Cookie':cookie,'Origin':self.config['origin'],'X-Andrew-CSRF':self.server.sessions[hashlib.sha256(key.encode()).hexdigest()]['csrf']}
    def restart(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.server=Portal(('127.0.0.1',0),self.config)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def test_session_survives_restart_without_storing_raw_cookie(self):
        headers=self.login();raw=headers['Cookie'].split('=',1)[1]
        rows=self.server.session_db.execute('SELECT id FROM sessions').fetchall()
        self.assertEqual(rows,[(hashlib.sha256(raw.encode()).hexdigest(),)])
        self.restart();status,_,page=self.request('/',headers=headers)
        self.assertEqual(status,200);self.assertIn(headers['X-Andrew-CSRF'].encode(),page)
    def test_authenticated_use_renews_cookie_and_persistent_expiry_daily(self):
        headers=self.login();id=hashlib.sha256(headers['Cookie'].split('=',1)[1].encode()).hexdigest()
        original=self.server.sessions[id]['until']
        with patch('web_portal.time.time',return_value=original-SESSION_SECONDS+SESSION_RENEW_SECONDS+1):
            status,cookies,_=self.request('/',headers=headers)
        self.assertEqual(status,200);self.assertIn('Max-Age='+str(SESSION_SECONDS),cookies['Set-Cookie'])
        self.assertGreater(self.server.sessions[id]['until'],original)
        self.restart();self.assertEqual(self.request('/',headers=headers)[0],200)
    def test_expired_session_is_denied_after_restart(self):
        headers=self.login();id=hashlib.sha256(headers['Cookie'].split('=',1)[1].encode()).hexdigest()
        self.server.sessions[id]['until']=0;self.server.save_session(id,self.server.sessions[id])
        self.restart();self.assertEqual(self.request('/api/status',headers=headers)[0],401)
    def test_logout_remains_revoked_after_restart(self):
        headers=self.login();self.request('/auth/logout','POST',{},headers)
        self.restart();self.assertEqual(self.request('/api/status',headers=headers)[0],401)
    def test_password_change_keeps_current_browser_and_revokes_other_device_durably(self):
        first=self.login();second=self.login()
        self.assertEqual(self.request('/auth/password','POST',{'current':'Long test password 42!','password':'New owner password 88!'},first)[0],200)
        self.restart();self.assertEqual(self.request('/',headers=first)[0],200)
        self.assertEqual(self.request('/api/status',headers=second)[0],401)
    def test_external_password_reset_invalidates_saved_session(self):
        headers=self.login();self.config['password']=password_hash('Reset owner password 77!')
        self.restart();self.assertEqual(self.request('/api/status',headers=headers)[0],401)
    def test_anonymous_api_is_denied_before_proxy(self):
        with patch('web_portal.urllib.request.urlopen') as upstream:
            self.assertEqual(self.request('/api/status')[0],401)
            self.assertEqual(self.request('/api/command','POST',{'text':'open browser'})[0],401)
            upstream.assert_not_called()
    def test_csrf_and_unknown_routes_never_reach_pc(self):
        headers=self.login()
        with patch('web_portal.urllib.request.urlopen') as upstream:
            bad={**headers,'Origin':'https://evil.example'}
            self.assertEqual(self.request('/api/command','POST',{'text':'hello'},bad)[0],403)
            self.assertEqual(self.request('/api/command','POST',{'text':'hello'},{'Cookie':headers['Cookie']})[0],403)
            self.assertEqual(self.request('/api/relay',headers=headers)[0],404)
            self.assertEqual(self.request('/data/web-bridge.json',headers=headers)[0],404)
            upstream.assert_not_called()
    def test_temporary_password_must_be_changed(self):
        self.config['temporary']=True;headers=self.login()
        self.assertEqual(self.request('/api/status',headers=headers)[0],403)
        self.assertEqual(self.request('/auth/password','POST',{'current':'Long test password 42!','password':'Replacement password 99!'},headers)[0],200)
        self.assertFalse(self.config['temporary']);self.assertTrue(check_password('Replacement password 99!',self.config['password']))
        self.assertNotIn('Replacement password',Path(self.config['config_path']).read_text())
    def test_logout_revokes_session(self):
        headers=self.login();self.assertEqual(self.request('/auth/logout','POST',{},headers)[0],200)
        self.assertEqual(self.request('/api/status',headers=headers)[0],401)
    def test_login_rate_limit(self):
        self.server.failures['127.0.0.1']=[__import__('time').time()]*8
        status,headers,page=self.request('/');token=re.search(rb'content="([A-Za-z0-9_-]{30,})"',page).group(1).decode()
        self.assertEqual(self.request('/auth/login','POST',{'email':'owner@example.com','password':'wrong'},
            {'Origin':self.config['origin'],'X-Andrew-CSRF':token,'Cookie':'andrew_login='+token})[0],429)
    def test_bridge_rejects_requests_without_shared_secret(self):
        self.server.mode='bridge'
        with patch('web_portal.urllib.request.urlopen') as upstream:
            self.assertEqual(self.request('/api/status')[0],401)
            self.assertEqual(self.request('/api/command','POST',{'text':'hello'})[0],401);upstream.assert_not_called()
    def test_route_allowlist_rejects_traversal_and_credentials(self):
        for path in ['/data/server.key','/api/relay','/api/camera-media/../../data/server.key','https://evil.example/api/status']:
            self.assertFalse(allowed(path,'GET'))
        self.assertTrue(allowed('/api/command','POST'));self.assertFalse(allowed('/api/settings','GET'))

if __name__=='__main__':unittest.main()
