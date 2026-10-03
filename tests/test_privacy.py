import io
import unittest
from unittest.mock import patch
import wave
import numpy as np
from audio_utils import scale_wav
from local_commands import web_url


class PrivacyTests(unittest.TestCase):
    def test_server_never_transcribes_audio_without_wake_gate(self):
        import server
        with patch.object(server.ENGINE,'transcribe',side_effect=AssertionError('Background transcription')):
            for source in ('pc','pi'):
                result=server.handle_audio(bytes(32000),source)
                self.assertFalse(result['accepted'])
                self.assertTrue(result['wake_required'])

    def test_volume_only_scales_own_samples(self):
        buf=io.BytesIO()
        with wave.open(buf,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000)
            w.writeframes(np.array([10000,-10000,0],dtype='<i2').tobytes())
        with wave.open(io.BytesIO(scale_wav(buf.getvalue(),30)),'rb') as w:
            self.assertEqual(w.getframerate(),24000)
            self.assertEqual(np.frombuffer(w.readframes(3),dtype='<i2').tolist(),[3000,-3000,0])
        with wave.open(io.BytesIO(scale_wav(buf.getvalue(),0)),'rb') as w:
            self.assertEqual(w.readframes(3),bytes(6))

    def test_name_inside_awakened_request_is_preserved(self):
        import server
        with patch.object(server.ENGINE,'transcribe',return_value='What does the name Andrew mean?'), \
             patch.object(server.PLAYBACK,'overlaps',return_value=False), \
             patch('server.voice_command',return_value={'accepted':False,'answer':''}) as command:
            server.handle_audio(bytes(32000),'pc',wake_detected=True)
            command.assert_called_once_with('What does the name Andrew mean?','pc',False)

    def test_browser_url_rejects_execution_and_credentials(self):
        for value in ('file:///etc/passwd','javascript:alert(1)','https://user:password@example.com','https://example.com/\n--flag'):
            with self.assertRaises(ValueError): web_url(value)
        self.assertEqual(web_url('example.com'),'https://example.com')
