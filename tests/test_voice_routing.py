import unittest
from voice_routing import VoiceGate, PlaybackGate
import io
import wave


class RoutingTests(unittest.TestCase):
    def test_reply_audio_cannot_trigger_another_microphone(self):
        wav = io.BytesIO()
        with wave.open(wav,'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(bytes(32000*2))
        gate = PlaybackGate()
        gate.record(wav.getvalue(),'pc',now=10)
        self.assertFalse(gate.overlaps(7,9.9))
        self.assertTrue(gate.overlaps(9,11))
        self.assertTrue(gate.overlaps(11,13))
        self.assertFalse(gate.overlaps(13,15))
    def test_two_microphones_execute_one_request(self):
        gate = VoiceGate()
        self.assertTrue(gate.claim('Set a five minute timer.', 'pi', 10))
        self.assertFalse(gate.claim('set a five minute timer', 'pc', 11))

    def test_repeated_request_and_later_request_are_allowed(self):
        gate = VoiceGate()
        self.assertTrue(gate.claim('what time is it', 'pc', 10))
        self.assertTrue(gate.claim('what time is it', 'pc', 11))
        self.assertTrue(gate.claim('what time is it', 'pi', 20))

    def test_distinct_commands_from_different_rooms(self):
        gate = VoiceGate()
        self.assertTrue(gate.claim('what time is it', 'pc', 10))
        self.assertTrue(gate.claim('cancel tea timer', 'pi', 11))
