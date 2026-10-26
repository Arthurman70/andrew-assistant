import io
import queue
import tempfile
import threading
import time
import unittest
import wave
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from core import Andrew, ROOT
from pc_audio import PCVoice
from speech_engine import SpeechEngine
from wake_capture import WakeCapture
from wake_detector import sleep_wake_phrases


def speech_wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes(np.full(4000, 1000, dtype='<i2').tobytes())
    return output.getvalue()


class SpeechMemoryTests(unittest.TestCase):
    def test_startup_status_cue_and_silence_leave_models_unloaded(self):
        engine = SpeechEngine()
        modules = {name: Mock() for name in ('sherpa_onnx', 'faster_whisper', 'kokoro_onnx', 'piper')}
        with patch.dict('sys.modules', modules):
            self.assertEqual(engine.transcribe(bytes(32000)), '')
            self.assertTrue(engine.cue())
            self.assertFalse(engine.snapshot()['recognition_loaded'])
            self.assertFalse(engine.snapshot()['voice_loaded'])
        for module in modules.values():
            self.assertEqual(module.mock_calls, [])

    def test_recognition_loads_once_then_reloads_after_idle_release(self):
        engine = SpeechEngine(recognition_choice=lambda: 'whisper')
        model = Mock()
        model.transcribe.return_value = ([SimpleNamespace(text='hello')], None)
        factory = Mock(return_value=model)
        pcm = np.full(320, 1000, dtype='<i2').tobytes()
        with patch.dict('sys.modules', {'faster_whisper': SimpleNamespace(WhisperModel=factory)}):
            self.assertEqual(engine.transcribe(pcm), 'hello')
            self.assertEqual(engine.transcribe(pcm), 'hello')
            self.assertEqual(factory.call_count, 1)
            self.assertEqual(engine.unload_idle(), [])
            engine.last_used['recognition'] -= 121
            self.assertEqual(engine.unload_idle(), ['recognition'])
            self.assertIsNone(engine.asr)
            self.assertIsNone(engine.asr_kind)
            self.assertEqual(engine.transcribe(pcm), 'hello')
            self.assertEqual(factory.call_count, 2)

    def test_idle_release_cannot_free_inflight_lazy_recognition(self):
        engine = SpeechEngine(recognition_choice=lambda: 'whisper')
        entered, finish = threading.Event(), threading.Event()
        def segments():
            entered.set()
            if not finish.wait(5):
                raise RuntimeError('Test timed out')
            yield SimpleNamespace(text='safe')
        model = Mock()
        model.transcribe.return_value = (segments(), None)
        engine.asr = model; engine.asr_kind = 'whisper'
        engine.last_used['recognition'] -= 200
        answers = []
        thread = threading.Thread(target=lambda: answers.append(engine.transcribe(np.full(320,1000,dtype='<i2').tobytes())))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(engine.unload_idle(), [])
            self.assertIs(engine.asr, model)
        finally:
            finish.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(answers, ['safe'])
        self.assertLess(time.monotonic()-engine.last_used['recognition'], 2)

    def test_idle_release_cannot_free_inflight_synthesis(self):
        engine = SpeechEngine()
        engine.kokoro = Mock()
        engine.last_used['voice'] -= 200
        entered, finish = threading.Event(), threading.Event()
        def synth(*args):
            entered.set()
            if not finish.wait(5):
                raise RuntimeError('Test timed out')
            return speech_wav()
        with patch.object(engine, '_synthesize', side_effect=synth):
            thread = threading.Thread(target=engine.synthesize, args=('Hello.',))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(engine.unload_idle(), [])
                self.assertIsNotNone(engine.kokoro)
            finally:
                finish.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertGreater(engine.cache_bytes, 0)
        self.assertEqual(engine.unload_idle(), [])
        engine.last_used['voice'] -= 121
        self.assertEqual(engine.unload_idle(), ['voice'])
        self.assertIsNone(engine.kokoro)
        self.assertEqual(engine.cache_bytes, 0)
        self.assertEqual(len(engine.cache), 0)

    def test_releases_idle_recognizer_even_while_voice_is_busy(self):
        engine = SpeechEngine()
        engine.asr = Mock(); engine.nano = Mock()
        nano = engine.nano
        engine.last_used = {'voice': 0, 'recognition': 0}
        with engine.tts_lock:
            self.assertEqual(engine.unload_idle(), ['recognition'])
            nano.close.assert_not_called()
        self.assertEqual(engine.unload_idle(), ['voice'])
        nano.close.assert_called_once()

    def test_cache_hits_extend_voice_idle_deadline(self):
        engine = SpeechEngine()
        with patch.object(engine, '_synthesize', return_value=speech_wav()) as synth:
            engine.synthesize('Hello.')
            engine.last_used['voice'] -= 200
            engine.synthesize('Hello.')
            self.assertEqual(synth.call_count, 1)
            self.assertEqual(engine.unload_idle(), [])


class SleepingWakeTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'data/tests')
        self.app = Andrew(self.temp.name)
    def tearDown(self):
        self.app.db.close(); self.temp.cleanup()

    def capture(self):
        with patch('wake_capture.WakeDetector') as detector:
            detector.return_value.name = 'Andrew'
            detector.return_value.accept.return_value = None
            capture = WakeCapture(ROOT, 'Andrew')
        return capture

    def test_sleep_uses_full_wake_phrase_with_current_name(self):
        self.assertEqual(sleep_wake_phrases('Alex'), ('Alex wake up', 'Hey Alex wake up'))

    def test_sleep_discards_followups_and_never_collects_request_audio(self):
        capture = self.capture()
        capture.request.extend(b'old command'); capture.context.append(b'earlier audio')
        capture.offer_followup('grant', 10)
        capture.active = True
        for _ in range(500):
            self.assertFalse(capture.feed_sleeping(bytes(640), 'Andrew'))
        capture.detector.configure.assert_called_once_with('Andrew', sleep_only=True)
        self.assertEqual(capture.request, b'')
        self.assertEqual(len(capture.context), 0)
        self.assertIsNone(capture.followup_grant)
        self.assertFalse(capture.active)

    def test_sleep_keyword_requires_foreground_gate_and_restores_normal_wake(self):
        capture = self.capture()
        capture.detector.accept.return_value = 0
        capture.background.accepts_wake = Mock(return_value=False)
        self.assertFalse(capture.feed_sleeping(bytes(640), 'Andrew'))
        self.assertEqual(capture.rejected_wakes, 1)
        capture.cooldown = 0
        capture.background.accepts_wake.return_value = True
        self.assertTrue(capture.feed_sleeping(bytes(640), 'Andrew'))
        capture.detector.accept.return_value = None
        self.assertIsNone(capture.feed(bytes(640), 'Andrew'))
        capture.detector.configure.assert_called_with('Andrew', sleep_only=False)
        self.assertEqual(capture.request, b'')

    def test_keyword_wakes_pc_only_and_does_not_transcribe(self):
        self.app.command('sleep for ten minutes')
        handler, cue = Mock(), Mock()
        pc = PCVoice(self.app, handler, threading.Event(), wake_handler=cue)
        self.assertTrue(pc.wake_from_sleep())
        self.assertEqual(self.app.snooze_remaining('pc'), 0)
        self.assertGreater(self.app.snooze_remaining('pi'), 0)
        handler.assert_not_called(); cue.assert_called_once()

    def test_disabled_microphone_cannot_be_woken_by_keyword_or_typed_wake(self):
        self.app.command('sleep for ten minutes')
        self.app.set('pc_listening', False)
        pc = PCVoice(self.app, Mock(), threading.Event())
        self.assertFalse(pc.wake_from_sleep())
        self.assertGreater(self.app.snooze_remaining('pc'), 0)
        self.app.command('Andrew, wake up')
        self.assertFalse(self.app.get('pc_listening'))

    def test_wake_notice_uses_only_a_cue(self):
        import server
        speech = queue.Queue()
        with patch.object(server, 'APP', self.app), patch.object(server, 'SPEECH', speech), \
             patch.object(server.ENGINE, 'synthesize', side_effect=AssertionError('Wake cue must not load TTS')):
            server.sleep_wake_notice()
            self.assertIsInstance(speech.get_nowait()[0], bytes)


if __name__ == '__main__':
    unittest.main()
