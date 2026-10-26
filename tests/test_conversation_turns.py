import io
import queue
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
from background_guard import BackgroundGuard
from conversation_turns import empty_utterance, followup_allowed, MAX_CAPTURE_SECONDS
from core import Andrew, ROOT
from followup import FollowupSessions
from speech_engine import SpeechEngine, VoiceSession
from voice_routing import PlaybackGate, VoiceGate
from wake_capture import WakeCapture
from tests.test_voice_reliability import wav_bytes


class TurnCaptureTests(unittest.TestCase):
    def capture(self, active=False):
        with patch('wake_capture.WakeDetector') as detector:
            detector.return_value.name='Andrew';detector.return_value.accept.return_value=None
            capture=WakeCapture(ROOT,'Andrew')
        capture.active=active;capture.threshold=25
        capture.command_vad=Mock();capture.command_vad.is_speech.return_value=True
        return capture

    def test_pause_between_thought_and_correction_preserves_both(self):
        capture=self.capture(True)
        first=np.full(320,300,dtype='<i2').tobytes(); correction=np.full(320,600,dtype='<i2').tobytes()
        for _ in range(60):self.assertIsNone(capture.feed(first,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(70):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        capture.command_vad.is_speech.return_value=True
        for _ in range(30):self.assertIsNone(capture.feed(correction,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(89):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        result=capture.feed(bytes(640),'Andrew')
        self.assertIn(first*60,result);self.assertIn(correction*30,result)

    def test_long_thought_survives_two_second_pause_and_is_bounded(self):
        capture=self.capture(True);frame=np.full(320,400,dtype='<i2').tobytes()
        for _ in range(300):self.assertIsNone(capture.feed(frame,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(100):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        capture.command_vad.is_speech.return_value=True
        for _ in range(2599):self.assertIsNone(capture.feed(frame,'Andrew'))
        result=capture.feed(frame,'Andrew')
        self.assertEqual(len(result),MAX_CAPTURE_SECONDS*32000);self.assertFalse(capture.active)

    def test_single_sound_does_not_start_followup(self):
        capture=self.capture();capture.offer_followup('grant',12)
        frame=np.full(320,300,dtype='<i2').tobytes()
        capture.feed(frame,'Andrew');capture.command_vad.is_speech.return_value=False
        for _ in range(15):capture.feed(bytes(640),'Andrew')
        self.assertFalse(capture.active);self.assertEqual(capture.request,b'')

    def test_short_yes_or_no_answer_is_not_discarded(self):
        capture=self.capture();capture.offer_followup('grant',12)
        frame=np.full(320,300,dtype='<i2').tobytes()
        for _ in range(8):self.assertIsNone(capture.feed(frame,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(89):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        self.assertTrue(capture.feed(bytes(640),'Andrew'))

    def test_used_followup_cannot_reopen_after_an_empty_turn(self):
        capture=self.capture();capture.offer_followup('grant',12)
        frame=np.full(320,300,dtype='<i2').tobytes()
        for _ in range(5):capture.feed(frame,'Andrew')
        self.assertTrue(capture.active)
        capture.command_vad.is_speech.return_value=False
        for _ in range(250):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        self.assertFalse(capture.active)
        capture.offer_followup('grant',12)
        self.assertIsNone(capture.followup_grant)

    def test_media_start_cancels_unaddressed_followup_capture(self):
        capture=self.capture();capture.offer_followup('grant',12)
        frame=np.full(320,300,dtype='<i2').tobytes()
        for _ in range(5):capture.feed(frame,'Andrew')
        self.assertTrue(capture.active)
        self.assertIsNone(capture.feed(frame,'Andrew',media=True))
        self.assertFalse(capture.active);self.assertEqual(capture.request,b'')

    def test_chatter_requires_a_distinct_foreground_voice(self):
        guard=BackgroundGuard()
        for _ in range(300):guard.observe(400,True)
        self.assertFalse(guard.accepts_followup(450))
        self.assertTrue(guard.accepts_followup(1200))


class ConversationDeliveryTests(unittest.TestCase):
    def setUp(self):
        import server
        self.server=server
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.temp.name)
        self.now=[0];self.follow=FollowupSessions(self.app,lambda:self.now[0])
        self.patches=[patch.object(server,'APP',self.app),patch.object(server,'FOLLOWUP',self.follow),
            patch.object(server,'SESSIONS',VoiceSession()),patch.object(server,'VOICE_GATE',VoiceGate()),
            patch.object(server,'PLAYBACK',PlaybackGate()),patch.object(server,'SPEECH',queue.Queue()),
            patch.object(server.ENGINE,'synthesize',return_value=wav_bytes()),
            patch.object(self.app.memory,'current',return_value='Sam')]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.app.db.close();self.temp.cleanup()

    def test_noise_filler_and_empty_transcripts_do_not_call_ai_or_speak(self):
        with patch.object(self.app,'command',side_effect=AssertionError('No empty commands')):
            for text in ('','  ','[noise]','um uh hmm','Andrew'):
                with patch.object(self.server.ENGINE,'transcribe',return_value=text):
                    result=self.server.handle_audio(bytes(32000),'pc',wake_detected=True)
                self.assertTrue(result['silent']);self.assertEqual(result['answer'],'')
        self.assertTrue(self.server.SPEECH.empty())

    def test_pending_task_says_one_moment_and_does_not_open_listening(self):
        def task(*args):
            self.app.request.pending_work=True
            return 'Let me complete that for you. What should I call you?'
        with patch.object(self.server.ENGINE,'transcribe',return_value='open my spreadsheet'),patch.object(self.app,'command',side_effect=task) as command:
            result=self.server.handle_audio(bytes(32000),'pc',wake_detected=True)
        command.assert_called_once();self.assertEqual(result['answer'],'One moment.')
        self.assertTrue(result['pending']);self.assertIsNone(self.server.SPEECH.get_nowait()[3])

    def test_task_results_do_not_listen_but_a_necessary_question_does(self):
        with patch.object(self.server.INTERRUPT,'active',return_value=False):
            self.server.pc_task_finished('The spreadsheet is ready.','pc')
            self.assertIsNone(self.server.SPEECH.get_nowait()[3])
            self.server.pc_task_finished('Which spreadsheet should I open?','pc')
            self.assertTrue(self.server.SPEECH.get_nowait()[3])

    def test_long_followup_can_finish_after_original_window(self):
        grant=self.follow.prepare('pc','Sam');self.follow.arm('pc',grant)
        self.now[0]=60
        self.assertEqual(self.follow.claim('pc',grant)['person'],'Sam')
        self.assertIsNone(self.follow.claim('pc',grant))
        grant=self.follow.prepare('pc');self.follow.arm('pc',grant);self.now[0]=150
        self.assertIsNone(self.follow.claim('pc',grant))

    def test_recognition_accepts_full_thought_but_rejects_oversize_audio(self):
        engine=SpeechEngine(recognition_choice=lambda:'whisper')
        model=Mock();model.transcribe.return_value=([SimpleNamespace(text='The whole thought.')],None)
        with patch.dict('sys.modules',{'faster_whisper':SimpleNamespace(WhisperModel=Mock(return_value=model))}):
            self.assertEqual(engine.transcribe(np.full(16000*45,1000,dtype='<i2').tobytes()),'The whole thought.')
        with self.assertRaises(ValueError):engine.transcribe(bytes(32000*66))


class TurnPolicyTests(unittest.TestCase):
    def test_farewells_and_device_confirmations_end_the_window(self):
        for text in ('thank you','goodbye','that is all','set a timer for tea','open Chrome'):
            self.assertFalse(followup_allowed(text,'Done.'))
        self.assertTrue(followup_allowed('Why does it rain?','Moist air cools and condenses.'))
        self.assertTrue(followup_allowed('set a timer','How long?'))
        self.assertFalse(followup_allowed('open Chrome','Which tab?',pending=True))

    def test_video_hallucination_is_silent_in_followup(self):
        self.assertTrue(empty_utterance('Thank you for watching!',True))
        self.assertFalse(empty_utterance('Read the words thank you for watching',True))


if __name__=='__main__':unittest.main()
