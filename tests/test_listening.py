import tempfile
import time
import unittest
from unittest.mock import patch
from core import Andrew, ROOT
from background_guard import BackgroundGuard


class ListeningTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests')
        self.app=Andrew(self.temp.name)
    def tearDown(self):
        self.app.db.close();self.temp.cleanup()
    def test_sleep_persists_expires_and_preserves_manual_pause(self):
        self.app.set('pi_listening',False)
        with patch('listening.time.time',return_value=1000):
            self.assertIn('Both microphones',self.app.command('Hey Andrew, go to sleep for thirty minutes'))
        other=Andrew(self.temp.name)
        try:
            self.assertEqual(other.snooze_remaining('pc',1001),1799)
            self.assertEqual(other.snooze_remaining('pc',2800),0)
            self.assertFalse(other.get('pi_listening'))
        finally: other.db.close()
    def test_scoped_sleep_and_resume_do_not_change_other_microphone(self):
        self.app.command('snooze for ten minutes on the Pi')
        self.assertGreater(self.app.snooze_remaining('pi'),590)
        self.assertEqual(self.app.snooze_remaining('pc'),0)
        self.app.command('cancel snooze on the Pi')
        self.assertEqual(self.app.snooze_remaining('pi'),0)
    def test_sleep_rejects_invalid_duration_and_keeps_timers(self):
        self.app.command('set a tea timer for five minutes')
        with self.assertRaises(ValueError): self.app.command('sleep for negative five minutes')
        self.app.command('sleep for thirty minutes')
        self.assertEqual(len(self.app.status()['timers']),1)
    def test_spoken_durations(self):
        self.assertEqual(self.app.seconds('twenty five minutes'),1500)
        self.assertEqual(self.app.seconds('half an hour'),1800)
        self.assertEqual(self.app.seconds('forty-five minutes'),2700)
    def test_explicit_background_feedback_is_remembered_per_device(self):
        self.app.command("that wasn't for you",'pi')
        self.assertEqual(self.app.get('pi_wake_mode'),'strict')
        self.assertEqual(self.app.get('pc_wake_mode'),'adaptive')
        self.app.command('reset wake sensitivity','pi')
        self.assertEqual(self.app.get('pi_wake_mode'),'adaptive')
    def test_sleep_blocks_queued_audio_before_transcription(self):
        import server
        self.app.command('sleep for ten minutes')
        with patch.object(server,'APP',self.app),patch.object(server.ENGINE,'transcribe',side_effect=AssertionError('No ASR while sleeping')):
            for source in ('pc','pi'):
                result=server.handle_audio(bytes(32000),source,wake_detected=True)
                self.assertTrue(result['sleeping']);self.assertFalse(result['accepted'])
    def test_sleep_during_recognition_discards_result_before_action(self):
        import server
        def transcribe(*args):
            self.app.command('sleep for ten minutes')
            return 'open browser'
        with patch.object(server,'APP',self.app),patch.object(server.ENGINE,'transcribe',side_effect=transcribe),patch('server.voice_command',side_effect=AssertionError('No action after snooze')):
            self.assertTrue(server.handle_audio(bytes(32000),'pc',wake_detected=True)['sleeping'])


class BackgroundTests(unittest.TestCase):
    def test_quieter_address_is_accepted_only_in_quiet_adaptive_mode(self):
        guard=BackgroundGuard()
        for _ in range(300):guard.observe(30,False)
        for _ in range(30):guard.observe(36,True)
        self.assertTrue(guard.accepts_wake())
        self.assertFalse(guard.accepts_wake(media=True))
        self.assertFalse(guard.accepts_wake(mode='strict'))

    def test_equal_level_background_rejected_but_close_voice_passes(self):
        guard=BackgroundGuard()
        for _ in range(300): guard.observe(1000,True)
        self.assertFalse(guard.accepts_wake(media=True))
        for _ in range(50): guard.observe(3500,True)
        self.assertTrue(guard.accepts_wake(media=True))
    def test_background_chatter_and_media_raise_caution(self):
        guard=BackgroundGuard()
        for _ in range(300): guard.observe(30,False)
        self.assertFalse(guard.cautious())
        self.assertTrue(guard.cautious(media=True))
        self.assertTrue(guard.cautious(mode='strict'))
        for _ in range(250): guard.observe(400,True)
        self.assertTrue(guard.cautious())
        for _ in range(600): guard.observe(30,False)
        self.assertFalse(guard.cautious())
    def test_repeated_empty_wakes_temporarily_raise_caution(self):
        guard=BackgroundGuard()
        for _ in range(300): guard.observe(30,False)
        for _ in range(3): guard.missed_request()
        self.assertTrue(guard.cautious())


if __name__=='__main__': unittest.main()
