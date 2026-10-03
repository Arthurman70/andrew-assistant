import base64
import http.client
from http.server import ThreadingHTTPServer
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from core import Andrew

class BrowserVoiceTests(unittest.TestCase):
    def setUp(self):
        import server
        self.module=server;self.temp=tempfile.TemporaryDirectory();self.app=Andrew(self.temp.name)
        self.patches=[patch.object(server,'APP',self.app),patch.object(server.Handler,'trusted_local',return_value=True),
                      patch.object(server.ENGINE,'synthesize',return_value=b'fake-wav')]
        for p in self.patches:p.start()
        self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.http.shutdown();self.http.server_close();self.thread.join()
        for p in reversed(self.patches):p.stop()
        self.app.db.close();self.temp.cleanup()
    def post(self,path,data):
        c=http.client.HTTPConnection('127.0.0.1',self.http.server_port)
        c.request('POST',path,json.dumps(data),{'Content-Type':'application/json','X-Andrew-Local':'1'})
        r=c.getresponse();result=(r.status,json.loads(r.read()));c.close();return result
    def test_browser_talk_requires_explicit_activation_before_recognition(self):
        with patch.object(self.module.ENGINE,'transcribe',side_effect=AssertionError('No background transcription')):
            self.assertEqual(self.post('/api/browser-voice',{'pcm':base64.b64encode(bytes(3200)).decode()})[0],400)
    def test_browser_reply_returns_voice_instead_of_playing_pc(self):
        with patch.object(self.module,'queue_speech',side_effect=AssertionError('Wrong device')):
            code,result=self.post('/api/browser-command',{'text':'what time is it','speak':True})
        self.assertEqual(code,200);self.assertIn('It is',result['answer']);self.assertEqual(result['audio'],base64.b64encode(b'fake-wav').decode())
    def test_spoken_browser_interrupt_is_local_control_not_pc_action(self):
        with patch.object(self.module.ENGINE,'transcribe',return_value='shut up'),patch.object(self.app,'command',side_effect=AssertionError('No PC action')):
            code,result=self.post('/api/browser-voice',{'activated':True,'pcm':base64.b64encode(bytes(3200)).decode()})
        self.assertEqual(code,200);self.assertEqual(result['speech_action'],'pause')
    def test_browser_navigation_and_game_are_separate_from_pc(self):
        self.post('/api/browser-command',{'text':'open settings'});self.assertEqual(self.app.controls.snapshot()['browser']['page'],'settings')
        self.post('/api/browser-command',{'text':'play chess'});self.assertEqual(self.app.games.snapshot('browser')['kind'],'chess')
        self.assertFalse(self.app.games.snapshot('pc'))
