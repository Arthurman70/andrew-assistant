import base64
import io
import queue
import tempfile
import time
import unittest
import wave
from unittest.mock import Mock,patch
import numpy as np
from core import Andrew,ROOT
from speech_engine import SpeechEngine,VoiceSession
from voice_routing import VoiceGate,PlaybackGate
from wake_capture import WakeCapture
from wake_detector import WakeDetector


def wav_bytes():
    out=io.BytesIO()
    with wave.open(out,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
        audio.writeframes(np.full(4000,1000,dtype='<i2').tobytes())
    return out.getvalue()


class CaptureTests(unittest.TestCase):
    def detector(self):
        detector=WakeDetector.__new__(WakeDetector)
        detector.activity=Mock();detector.activity.is_speech.return_value=True
        detector.spotter=Mock();detector.spotter.is_ready.return_value=False
        detector.reset()
        return detector

    def test_idle_detector_resets_its_sample_clock_before_native_reset(self):
        detector=self.detector();detector.activity.is_speech.return_value=False
        original=detector.spotter.create_stream.call_count
        for _ in range(25):detector.accept(bytes(640))
        self.assertEqual(detector.spotter.create_stream.call_count,original+1)
        self.assertEqual(detector.samples,320)

    def test_stale_keyword_timestamp_cannot_include_earlier_speech(self):
        detector=self.detector();detector.samples=160000
        detector.spotter.is_ready.return_value=True
        detector.spotter.keyword_spotter.get_result.return_value=Mock(keyword='WAKE',timestamps=[.8])
        self.assertEqual(detector.accept(bytes(640)),160320)

    def make_capture(self):
        with patch('wake_capture.WakeDetector') as detector:
            detector.return_value.name='Andrew'
            capture=WakeCapture(ROOT,'Andrew')
        capture.active=True;capture.threshold=25
        capture.command_vad=Mock()
        return capture

    def test_short_command_finishes_after_720ms(self):
        capture=self.make_capture()
        frame=np.full(320,300,dtype='<i2').tobytes()
        capture.command_vad.is_speech.return_value=True
        for _ in range(6):self.assertIsNone(capture.feed(frame,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(35):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        result=capture.feed(bytes(640),'Andrew')
        self.assertTrue(result);self.assertFalse(capture.active)

    def test_empty_wake_returns_quietly_to_idle(self):
        capture=self.make_capture();capture.command_vad.is_speech.return_value=False
        for _ in range(249):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        self.assertFalse(capture.active);self.assertEqual(capture.phase,'waiting for name')
        self.assertEqual(capture.empty_wakes,1)

    def test_natural_pause_does_not_cut_off_the_second_command(self):
        capture=self.make_capture()
        first=np.full(320,300,dtype='<i2').tobytes()
        second=np.full(320,600,dtype='<i2').tobytes()
        capture.command_vad.is_speech.return_value=True
        for _ in range(10):self.assertIsNone(capture.feed(first,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(28):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        capture.command_vad.is_speech.return_value=True
        for _ in range(12):self.assertIsNone(capture.feed(second,'Andrew'))
        capture.command_vad.is_speech.return_value=False
        for _ in range(35):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        result=capture.feed(bytes(640),'Andrew')
        self.assertIn(first*10,result);self.assertIn(second*12,result)

    def test_no_keyword_never_captures_or_sends_background(self):
        capture=self.make_capture();capture.active=False;capture.detector.accept.return_value=None
        for _ in range(500):self.assertIsNone(capture.feed(bytes(640),'Andrew'))
        self.assertEqual(capture.request,b'');self.assertFalse(capture.active)

    def test_pre_wake_audio_is_excluded(self):
        capture=self.make_capture();capture.active=False
        capture.detector.accept.return_value=1000;capture.detector.samples=1000
        capture.background.accepts_wake=Mock(return_value=True)
        capture.context.append(b'private earlier audio')
        capture.feed(bytes(640),'Andrew')
        self.assertTrue(capture.active);self.assertEqual(capture.request,b'')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        import server
        self.server=server
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests');self.app=Andrew(self.temp.name)
        self.wav=wav_bytes()
        self.patches=[patch.object(server,'APP',self.app),patch.object(server,'SESSIONS',VoiceSession()),
            patch.object(server,'VOICE_GATE',VoiceGate()),patch.object(server,'PLAYBACK',PlaybackGate()),
            patch.object(server,'SPEECH',queue.Queue(maxsize=16)),patch.object(server.ENGINE,'synthesize',return_value=self.wav)]
        for p in self.patches:p.start()
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.app.db.close();self.temp.cleanup()
    def request(self,source='pc',pcm=bytes(32000)):
        return self.server.handle_audio(pcm,source,wake_detected=True)
    def assert_spoken(self,result,source):
        self.assertTrue(result['answer'])
        if source=='pi':self.assertEqual(base64.b64decode(result['audio']),self.wav)
        else:self.assertEqual(self.server.SPEECH.get_nowait()[0],self.wav)

    def test_empty_capture_responds_on_both_devices_without_asr(self):
        with patch.object(self.server.ENGINE,'transcribe',side_effect=AssertionError('No silence transcription')):
            for source in ('pc','pi'):self.assert_spoken(self.request(source,b''),source)

    def test_empty_transcript_and_recognition_error_are_audible(self):
        for failure in ('',RuntimeError('ASR down')):
            for source in ('pc','pi'):
                with patch.object(self.server.ENGINE,'transcribe',**({'side_effect':failure} if isinstance(failure,Exception) else {'return_value':failure})):
                    result=self.request(source);self.assertTrue(result['retry']);self.assert_spoken(result,source)

    def test_name_only_has_spoken_retry_not_dead_listening_animation(self):
        with patch.object(self.server.ENGINE,'transcribe',return_value='Andrew'):
            result=self.request();self.assertTrue(result['retry']);self.assert_spoken(result,'pc')

    def test_provider_exception_has_audible_response(self):
        with patch.object(self.server.ENGINE,'transcribe',return_value='Explain rain'),patch.object(self.app,'command',side_effect=RuntimeError('offline')):
            result=self.request();self.assertIn('not responding',result['answer']);self.assert_spoken(result,'pc')

    def test_snooze_during_failed_recognition_stays_silent(self):
        def fail(*args):
            self.app.command('sleep for ten minutes');raise RuntimeError('ASR failed')
        with patch.object(self.server.ENGINE,'transcribe',side_effect=fail):
            self.assertTrue(self.request()['sleeping'])
        self.assertTrue(self.server.SPEECH.empty())

    def test_tts_failure_never_reexecutes_action(self):
        with patch.object(self.server.ENGINE,'transcribe',return_value='set a tea timer for five minutes'), \
             patch.object(self.server.ENGINE,'synthesize',side_effect=RuntimeError('voice down')):
            result=self.request()
        self.assertTrue(result['accepted']);self.assertEqual(len(self.app.status()['timers']),1)
        self.assertGreater(len(self.server.SPEECH.get_nowait()[0]),44)

    def test_unknown_speaker_skips_unnecessary_matching(self):
        self.app.command('my name is Sam')
        with patch.object(self.app.memory,'embedding',side_effect=AssertionError('No profiles to match')):
            self.app.memory.begin_voice(bytes(64000),'pc','what time is it')
            self.assertIsNone(self.app.memory.current('pc'))
        self.app.memory.end_voice()

    def test_introduction_still_computes_voice_profile(self):
        with patch.object(self.app.memory,'embedding',return_value=np.array([1.,0.])) as embedding:
            self.app.memory.begin_voice(bytes(64000),'pc','my name is Sam');embedding.assert_called_once()
        self.app.memory.end_voice()

    def test_common_connectivity_command_never_waits_for_cloud(self):
        with patch.object(self.app,'ai',side_effect=AssertionError('No cloud')):
            for text in ('Can you hear me?','Are you there?','Hello','Thank you',"What's the time?"):
                self.assertTrue(self.app.command(text))

    def test_canceled_progress_is_not_delivered_late(self):
        progress=self.server.ResponseProgress('pc');progress.done.set();progress.announce()
        self.assertTrue(self.server.SPEECH.empty())

    def test_busy_queue_uses_backpressure_instead_of_silent_drop(self):
        target=Mock()
        with patch.object(self.server,'SPEECH',target):self.server.speak('Timer ready.')
        target.put.assert_called_once();target.put_nowait.assert_not_called()


class FastVoiceTests(unittest.TestCase):
    def test_cache_respects_voice_and_speed(self):
        voice=['af_heart'];speed=[1.0]
        engine=SpeechEngine(lambda:voice[0],speed_choice=lambda:speed[0])
        with patch.object(engine,'_synthesize',return_value=wav_bytes()) as synth:
            engine.synthesize('Ready.');engine.synthesize('Ready.');self.assertEqual(synth.call_count,1)
            speed[0]=1.2;engine.synthesize('Ready.');self.assertEqual(synth.call_count,2)
            voice[0]='am_michael';engine.synthesize('Ready.');self.assertEqual(synth.call_count,3)

    def test_primary_failure_and_empty_audio_use_local_backup(self):
        for bad in (RuntimeError('voice failed'),b''):
            engine=SpeechEngine(lambda:'nano')
            with patch.object(engine,'_synthesize',side_effect=[bad,wav_bytes()]) as synth:
                self.assertEqual(engine.synthesize('Ready.'),wav_bytes())
                self.assertEqual(synth.call_args.args[1],'af_heart')
            self.assertFalse(engine.cache)

    def test_cache_is_bounded(self):
        engine=SpeechEngine()
        with patch.object(engine,'_synthesize',return_value=wav_bytes()):
            for i in range(90):engine.synthesize('Response '+str(i))
        self.assertEqual(len(engine.cache),64)

    def test_silent_wav_is_replaced_by_audible_backup(self):
        silent=bytearray(wav_bytes());silent[44:]=bytes(len(silent)-44)
        engine=SpeechEngine(lambda:'nano')
        with patch.object(engine,'_synthesize',side_effect=[bytes(silent),wav_bytes()]):
            self.assertEqual(engine.synthesize('Ready.'),wav_bytes())

    def test_same_command_from_other_device_two_seconds_later_is_new(self):
        gate=VoiceGate()
        self.assertTrue(gate.claim('what time is it','pc',10))
        self.assertFalse(gate.claim('what time is it','pi',10.7))
        self.assertTrue(gate.claim('what time is it','pi',12.5))

    def test_disconnected_output_falls_back_to_connected_pc_speaker(self):
        import audio_player
        devices=[{'name':'Connected speaker','max_output_channels':2}]
        with patch.object(audio_player.sd,'_terminate'),patch.object(audio_player.sd,'_initialize'), \
             patch.object(audio_player.sd,'query_devices',return_value=devices), \
             patch.object(audio_player.sd,'default',Mock(device=(-1,0))), \
             patch.object(audio_player.sd,'RawOutputStream') as output:
            started=Mock();audio_player.play(wav_bytes(),'Disconnected USB',5,started)
            self.assertEqual(output.call_args.kwargs['device'],0)
            self.assertTrue(started.call_args.kwargs['fallback'])
            chunks=output.return_value.__enter__.return_value.write.call_args_list
            self.assertEqual(sum(len(c.args[0]) for c in chunks),8000)
            self.assertGreater(len(chunks),1)


if __name__=='__main__':unittest.main()
