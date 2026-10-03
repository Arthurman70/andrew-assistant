import unittest
from speech_engine import VoiceSession


class SessionTests(unittest.TestCase):
    def test_wake_name_preserves_times_and_model_ids(self):
        session=VoiceSession()
        self.assertEqual(session.addressed('Hey Andrew, set an alarm for 7:30 am.','Andrew','pi'),
                         'set an alarm for 7:30 am.')
        self.assertEqual(session.addressed('Andrew, use local model qwen3:0.6b.','Andrew','pc'),
                         'use local model qwen3:0.6b.')

    def test_wake_after_background_and_name_change(self):
        session=VoiceSession()
        self.assertEqual(session.addressed('Some earlier speech. Alex, what time is it?','Alex','pc'),
                         'what time is it?')
        self.assertIsNone(session.addressed('Andrew, what time is it?','Alex','pc'))

    def test_talk_and_name_only_arm_only_one_device(self):
        session=VoiceSession()
        self.assertEqual(session.addressed('Andrew!','Andrew','pi'),'')
        self.assertIsNone(session.addressed('what time is it','Andrew','pc'))
        self.assertEqual(session.addressed('what time is it','Andrew','pi'),'what time is it')
        self.assertIsNone(session.addressed('background speech','Andrew','pi'))
