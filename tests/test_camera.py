import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from core import Andrew,ROOT
from camera import Camera


class CameraTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data/tests').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data/tests')
        self.app=Andrew(self.temp.name)
        self.camera=Camera(self.app)
        self.app.camera=self.camera
        self.app.relay_seen=time.time()

    def tearDown(self):
        self.app.db.close();self.temp.cleanup()

    def test_turn_on_routes_to_timed_preview_and_stop_revokes_frame(self):
        with patch.object(self.app,'home',side_effect=AssertionError('Not a home automation command')):
            answer=self.app.command('turn on the camera')
        self.assertIn('two minutes',answer)
        token=self.camera.preview_id
        self.camera.accept_frame(token,b'\xff\xd8test')
        self.app.command('turn off the camera')
        self.assertIsNone(self.camera.frame)
        with self.assertRaises(ValueError):self.camera.accept_frame(token,b'\xff\xd8test')

    def test_preview_expiry_drops_memory_frame(self):
        self.camera.preview();self.camera.frame=b'\xff\xd8test'
        self.camera.preview_until=time.time()-1
        self.assertEqual(self.camera.status()['preview_remaining'],0)
        self.assertIsNone(self.camera.frame)

    def test_photo_local_until_explicit_ai_request_and_upload_single_use(self):
        self.app.command('snap a photo',source='pi')
        job=self.app.job()['job']
        with patch.object(self.camera,'analyze',side_effect=AssertionError('No implicit upload')):
            self.camera.accept(job['id'],'photo',b'\xff\xd8test')
        item=self.camera.status()['captures'][0]
        self.assertEqual(item['sent_to'],[])
        with self.assertRaises(ValueError):self.camera.accept(job['id'],'photo',b'\xff\xd8test')

    def test_cancel_revokes_video_upload_and_relay_lease(self):
        self.app.command('record a video for ten seconds')
        job=self.app.job()['job']
        self.assertEqual(job['kind'],'video')
        self.assertEqual(json.loads(job['payload'])['seconds'],10)
        self.assertIn(job['id'],self.camera.control()['allowed_jobs'])
        self.camera.stop()
        self.assertNotIn(job['id'],self.camera.control()['allowed_jobs'])
        with self.assertRaises(ValueError):self.camera.accept(job['id'],'video',b'0000ftyp00000000')

    def test_offline_camera_and_overlong_video_rejected(self):
        with self.assertRaises(ValueError):self.app.command('record a video for two minutes')
        self.app.relay_seen=0
        with self.assertRaises(ValueError):self.app.command('turn on camera')

    def test_send_photo_selects_requested_account(self):
        with patch.object(self.camera,'latest',return_value='a'*32),patch.object(self.camera,'analyze',return_value='sent') as send:
            self.assertEqual(self.app.command('send the latest photo to Claude and ask what is this','pi'),'sent')
            send.assert_called_once_with('a'*32,'what is this','claude',source='pi')

    def test_show_photo_alone_does_not_upload_it(self):
        with patch.object(self.camera,'analyze',side_effect=AssertionError('No permission to upload')):
            self.assertIn('not been sent',self.app.command('show me the latest photo'))

    def test_video_commands_accept_both_common_forms(self):
        for phrase in ('record a ten second video','record a 10 second video','take a video for 10 seconds'):
            with patch.object(self.camera,'request',return_value='ok') as capture:
                self.assertEqual(self.camera.route(phrase,'pi'),'ok')
                capture.assert_called_once_with('video',10,'pi')
