import io
import json
import os
import queue
import struct
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
import numpy as np
from core import Andrew,ROOT
from browser_companion import BrowserCompanion,extension_id
from companion_native import read_message,write_message,origin
from followup import FollowupSessions
from wake_capture import WakeCapture
from pc_agent import PCAgent,decision
from improvements import check_source

class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.tmp.name);self.broker=BrowserCompanion(self.app,ROOT)
    def tearDown(self):self.app.db.close();self.tmp.cleanup()
    def test_authenticated_connection_queues_command_and_waits_for_matching_observation(self):
        session=self.broker.hello('0.1','Chrome')['session'];observed=[]
        def browser():
            job=self.broker.poll(session);observed.append(job)
            self.assertFalse(self.broker.result('wrong',{'id':job['id'],'result':{}}))
            self.broker.result(session,{'id':job['id'],'result':{'searched':True,'tab':12}})
        worker=threading.Thread(target=browser);worker.start();result=self.broker.perform({'action':'search','query':'raspberry pi'},'search for raspberry pi');worker.join(2)
        self.assertTrue(result['searched']);self.assertEqual(observed[0]['args']['query'],'raspberry pi');self.assertTrue(self.broker.status()['connected'])
    def test_local_and_execution_urls_and_unrequested_tab_close_are_denied(self):
        for value in ['javascript:alert(1)','http://127.0.0.1:8765','http://192.168.1.2','file:///secret']:
            with self.assertRaises(ValueError):self.broker.perform({'action':'navigate','url':value})
        with self.assertRaises(ValueError):self.broker.perform({'action':'close'},'read the page')
    def test_native_framing_preserves_utf8_and_rejects_incomplete_or_oversized_frames(self):
        value={'text':'Hello · 你好'};output=io.BytesIO();write_message(output,value);output.seek(0);self.assertEqual(read_message(output),value)
        for content in [b'abc',struct.pack('=I',1048577),struct.pack('=I',3)+b'{']:
            with self.assertRaises(ValueError):read_message(io.BytesIO(content))
    def test_native_origin_matches_fixed_extension_identity(self):
        self.assertEqual(origin(ROOT),'chrome-extension://'+extension_id(ROOT)+'/');self.assertEqual(len(extension_id(ROOT)),32)
    @unittest.skipUnless(os.name=='nt','Windows native launcher')
    def test_windows_launcher_flushes_replies_before_input_pipe_closes(self):
        compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.exists():self.skipTest('Windows C# compiler unavailable')
        folder=Path(self.tmp.name);exe=folder/'host.exe';script=folder/'echo.py';expected='chrome-extension://test/'
        script.write_text("import sys,struct\nwhile True:\n header=sys.stdin.buffer.read(4)\n if not header:break\n body=sys.stdin.buffer.read(struct.unpack('=I',header)[0])\n sys.stdout.buffer.write(header+body);sys.stdout.buffer.flush()\n",encoding='utf-8')
        (folder/'launcher.ini').write_text(str(Path(sys.executable).resolve())+'\n'+str(script)+'\n'+expected+'\n',encoding='utf-8')
        compiled=subprocess.run([str(compiler),'/nologo','/target:exe','/out:'+str(exe),str(ROOT/'companion/NativeLauncher.cs')],capture_output=True,text=True,timeout=30)
        self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
        process=subprocess.Popen([str(exe),expected],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE);replies=queue.Queue()
        def receive():
            try:
                for _ in range(2):replies.put(read_message(process.stdout))
            except Exception as exc:replies.put(exc)
        thread=threading.Thread(target=receive,daemon=True);thread.start()
        try:
            for value in [{'text':'Hello 你好'},{'text':'second live request'}]:
                write_message(process.stdin,value);self.assertEqual(replies.get(timeout=8),value)
        finally:
            process.stdin.close()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            thread.join(2);process.stdout.close();process.stderr.close()
    def test_manifest_has_only_named_automatic_sites_and_optional_site_grants(self):
        manifest=json.loads((ROOT/'companion/manifest.json').read_text());self.assertNotIn('https://*/*',manifest['host_permissions']);self.assertIn('activeTab',manifest['permissions'])
        self.assertNotIn('cookies',manifest['permissions']);self.assertNotIn('history',manifest['permissions']);check_source('companion/manifest.json',json.dumps(manifest))
        manifest['host_permissions'].append('https://*/*')
        with self.assertRaises(ValueError):check_source('companion/manifest.json',json.dumps(manifest))
    def test_browser_search_task_uses_companion_without_instantiating_native_ui(self):
        self.app.companion=Mock();self.app.companion.perform.return_value={'searched':True,'tab':12}
        plan=iter([{'action':'browser','args':{'action':'search','query':'raspberry pi'}},{'action':'finish','status':'complete','answer':'Search opened.'}])
        agent=PCAgent(self.app,planner=lambda *a:json.dumps(next(plan)),controller_factory=lambda:(_ for _ in ()).throw(AssertionError('Not native UI')))
        agent.run('Search for raspberry pi','pc');self.assertEqual(agent.status()['state'],'complete');self.app.companion.perform.assert_called_once()
    def test_simple_search_prefers_connected_extension(self):
        self.app.companion=Mock();self.app.companion.status.return_value={'connected':True};self.app.companion.perform.return_value={'searched':True}
        with patch('webbrowser.open',side_effect=AssertionError('Not fallback browser')):self.assertIn('connected browser',self.app.command('search the web for raspberry pi'))
        self.assertEqual(self.app.companion.perform.call_args.args[0],{'action':'search','query':'raspberry pi'})
    def test_browser_javascript_behavior_in_mocked_chrome(self):
        node=shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
        result=subprocess.run([node,str(ROOT/'tests/companion-browser.cjs')],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.tmp.name);self.time=[0];self.follow=FollowupSessions(self.app,lambda:self.time[0])
    def tearDown(self):self.app.db.close();self.tmp.cleanup()
    def test_window_starts_after_playback_not_when_reply_is_prepared(self):
        grant=self.follow.prepare('pc');self.assertFalse(self.follow.view('pc')['active']);self.follow.arm('pc',grant);self.assertFalse(self.follow.view('pc')['active']);self.time[0]=1;self.assertTrue(self.follow.view('pc')['active']);self.time[0]=13;self.assertFalse(self.follow.view('pc')['active'])
    def test_grant_is_single_use_and_source_scoped(self):
        grant=self.follow.prepare('pc','Sam');self.follow.arm('pc',grant);self.time[0]=1;self.assertIsNone(self.follow.claim('pi',grant));self.assertEqual(self.follow.claim('pc',grant)['person'],'Sam');self.assertIsNone(self.follow.claim('pc',grant))
    def test_snooze_and_disable_close_conversation(self):
        grant=self.follow.prepare('pc');self.follow.arm('pc',grant);self.time[0]=1;self.app.set('pc_snooze_until',9999999999);self.assertFalse(self.follow.view('pc')['active']);self.assertIsNone(self.follow.claim('pc',grant))
        self.app.set('pc_snooze_until',0);self.app.set('followup_enabled',False);self.assertIsNone(self.follow.prepare('pc'))
    def capture(self):
        with patch('wake_capture.WakeDetector') as detector:
            detector.return_value.name='Andrew';detector.return_value.accept.return_value=None;capture=WakeCapture(ROOT,'Andrew')
        capture.command_vad=Mock();capture.command_vad.is_speech.return_value=True;return capture
    def test_foreground_followup_is_captured_without_keyword_and_expiry_is_not_extended(self):
        capture=self.capture();frame=np.full(320,300,dtype='<i2').tobytes()
        with patch('wake_capture.time.monotonic',return_value=100):capture.offer_followup('grant',12)
        with patch('wake_capture.time.monotonic',return_value=105):
            capture.offer_followup('grant',12);self.assertEqual(capture.followup_until,112);capture.feed(frame,'Andrew');self.assertTrue(capture.active);self.assertEqual(capture.last_grant,'grant')
        for _ in range(6):capture.feed(frame,'Andrew')
        capture.command_vad.is_speech.return_value=False
        for _ in range(35):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        self.assertIsNotNone(capture.feed(bytes(640),'Andrew'))
    def test_background_media_does_not_start_followup_capture(self):
        capture=self.capture();capture.offer_followup('grant',12);capture.feed(np.full(320,300,dtype='<i2').tobytes(),'Andrew',media=True);self.assertFalse(capture.active)
    def test_server_rejects_fake_followup_without_transcription(self):
        import server
        with patch.object(server,'APP',self.app),patch.object(server,'FOLLOWUP',self.follow),patch.object(server.ENGINE,'transcribe',side_effect=AssertionError('No background ASR')):
            self.assertTrue(server.handle_audio(bytes(32000),'pc',followup='fake')['wake_required'])
    def test_valid_followup_transcribes_and_answers_without_new_wake(self):
        import server
        from tests.test_voice_reliability import wav_bytes
        grant=self.follow.prepare('pc');self.follow.arm('pc',grant);self.time[0]=1
        with patch.object(server,'APP',self.app),patch.object(server,'FOLLOWUP',self.follow),patch.object(server.PLAYBACK,'overlaps',return_value=False),patch.object(server.ENGINE,'transcribe',return_value='What about tomorrow?'),patch.object(self.app,'command',return_value='Tomorrow works.'),patch.object(server.ENGINE,'synthesize',return_value=wav_bytes()),patch.object(server,'SPEECH',queue.Queue()):
            result=server.handle_audio(bytes(32000),'pc',followup=grant);self.assertTrue(result['accepted']);self.assertEqual(result['answer'],'Tomorrow works.');self.assertFalse(self.follow.view('pc')['active']);queued=server.SPEECH.get_nowait();self.assertTrue(queued[3])

    def test_async_task_question_also_opens_followup_after_its_speech(self):
        import server
        self.app.pc_agent=Mock();self.app.pc_agent.status.return_value={'source':'pc','finished_at':0,'person':None}
        with patch.object(server,'APP',self.app),patch.object(server,'FOLLOWUP',self.follow),patch.object(server,'SPEECH',queue.Queue()),patch.object(server.INTERRUPT,'active',return_value=False):
            server.pc_task_finished('Which browser tab should I use?','pc')
            queued=server.SPEECH.get_nowait();self.assertEqual(queued[0],'Which browser tab should I use?');self.assertTrue(queued[3]);self.assertFalse(self.follow.view('pc')['active'])
            self.follow.arm('pc',queued[3]);self.time[0]=1;self.assertTrue(self.follow.view('pc')['active'])
