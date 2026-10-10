import ast
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pi_screensaver',ROOT/'pi/screensaver.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class Clock:
    now=0
    def __call__(self):return self.now


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock();self.timer=module.IdleTimer(self.clock)
        self.clock.now=1;self.timer.desktop(True)

    def test_fifteen_minutes_without_activity(self):
        self.clock.now=899;self.assertFalse(self.timer.due())
        self.clock.now=900;self.assertTrue(self.timer.due())

    def test_normal_status_polling_does_not_reset_idle(self):
        for now in range(1,901):
            self.clock.now=now;self.timer.voice(False)
        self.assertTrue(self.timer.due())

    def test_keyboard_or_pointer_activity_resets_the_timer(self):
        self.clock.now=899;self.timer.desktop(False)
        self.clock.now=1700;self.assertFalse(self.timer.due())
        self.timer.desktop(True)
        self.clock.now=2598;self.assertFalse(self.timer.due())
        self.clock.now=2599;self.assertTrue(self.timer.due())

    def test_a_conversation_keeps_the_screen_awake(self):
        self.clock.now=899;self.timer.voice(True)
        self.clock.now=900;self.assertFalse(self.timer.due())
        self.clock.now=1799;self.assertTrue(self.timer.due())

    def test_busy_lease_expires_if_the_relay_stops(self):
        self.timer.seconds=1;self.clock.now=10;self.timer.voice(True)
        self.clock.now=12;self.assertFalse(self.timer.due())
        self.clock.now=13;self.assertTrue(self.timer.due())


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.saver=module.ScreenSaver(self.folder.name,True);self.saver.enabled=True
        self.clock=Clock();self.saver.timer=module.IdleTimer(self.clock)
        self.clock.now=1;self.saver.desktop(True);self.clock.now=900
        self.process=Mock();self.process.poll.return_value=None

    def test_animation_uses_a_separate_local_profile_and_window(self):
        with patch.object(module.subprocess,'Popen',return_value=self.process) as spawn:
            self.saver.tick();self.saver.tick()
        self.assertEqual(spawn.call_count,1)
        args=spawn.call_args.args[0]
        self.assertIn('--kiosk',args)
        self.assertEqual(args[-1],'http://127.0.0.1:8770/screensaver')
        self.assertIn('--user-data-dir='+str(Path(self.folder.name)/'screensaver-profile'),args)

    def test_active_video_prevents_the_animation(self):
        with patch.object(module.subprocess,'Popen') as spawn:
            self.saver.tick(playing_video=True)
            spawn.assert_not_called()
        self.assertFalse(self.saver.timer.due())

    def test_wake_updates_the_name_and_closes_only_the_saver(self):
        with patch.object(self.saver,'_hide') as hide:
            self.saver.activity('Hey Charlie')
        hide.assert_called_once();self.assertEqual(self.saver.wake_phrase,'Hey Charlie')
        self.assertFalse(self.saver.timer.due())

    def test_voice_wake_has_no_dependency_on_the_pc_or_transcription(self):
        source=ast.parse((ROOT/'pi/agent.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='display_wake')
        targets=[]
        import urllib.request
        urlopen=Mock(return_value=Mock())
        env={'CONFIG':{'name':'Charlie'},'wake_phrase':lambda n:'Hey '+n,'json':json,
             'urllib':types.SimpleNamespace(request=types.SimpleNamespace(Request=urllib.request.Request,urlopen=urlopen)),
             'threading':types.SimpleNamespace(Thread=lambda target,daemon:types.SimpleNamespace(start=lambda:targets.append(target)))}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'wake-test','exec'),env)
        env['display_wake']();self.assertEqual(len(targets),1);targets[0]()
        request=urlopen.call_args.args[0]
        self.assertEqual(request.full_url,'http://127.0.0.1:8771/')
        self.assertEqual(json.loads(request.data),{'action':'wake','wake_phrase':'Hey Charlie'})
        self.assertNotIn('pcm',json.loads(request.data))

if __name__=='__main__':unittest.main()
