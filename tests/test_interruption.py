import io
import threading
import unittest
import wave
from unittest.mock import Mock
from interruption import remaining_wav, SpeechInterruption

def wav():
    content=io.BytesIO()
    with wave.open(content,'wb') as audio:
        audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
        audio.writeframes(b'\x01\x00'*32000)
    return content.getvalue()

class InterruptionTests(unittest.TestCase):
    def test_resume_retains_only_unspoken_audio(self):
        with wave.open(io.BytesIO(remaining_wav(wav(),12000)),'rb') as audio:
            self.assertEqual(audio.getnframes(),20000);self.assertEqual(audio.getframerate(),16000)
            self.assertEqual(audio.readframes(1),b'\x01\x00')
    def test_pause_cancels_queued_generation_and_resume_never_reexecutes_actions(self):
        player=Mock();player.resume.return_value=wav();sender=Mock()
        control=SpeechInterruption(player,sender);previous=control.ticket('pc')
        control.control('pc','pause');self.assertTrue(previous.is_set());self.assertTrue(control.active('pc'))
        self.assertFalse(control.ticket('pc').is_set());control.control('pc','resume')
        sender.assert_called_once_with(player.resume.return_value,'pc');player.pause.assert_called_once()
        self.assertFalse(control.active('pc'))
    def test_new_request_discards_old_paused_reply(self):
        player=Mock();control=SpeechInterruption(player,Mock());control.control('pc','pause')
        control.new_request('pc');player.stop.assert_called_once();self.assertFalse(control.active('pc'))
    def test_controls_cannot_target_arbitrary_devices(self):
        control=SpeechInterruption(Mock(),Mock())
        with self.assertRaises(ValueError):control.control('remote','pause')
        with self.assertRaises(ValueError):control.control('pc','execute')
