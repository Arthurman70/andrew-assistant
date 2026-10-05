import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import Mock,patch
from web_sync import sync

class WebsiteUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'data').mkdir();(self.root/'app.html').write_text('<html>updated</html>')
    def tearDown(self):self.tmp.cleanup()
    def pair(self):
        (self.root/'data/web-deploy.json').write_text(json.dumps({'ssh_target':'owner@host.example'}))
    def test_unpaired_website_is_queued_without_an_ssh_attempt(self):
        with patch('web_sync.subprocess.run') as run:
            self.assertIn('queued',sync(self.root,['app.html']));run.assert_not_called()
        self.assertEqual(json.loads((self.root/'data/web-update-queued.json').read_text())['files'],['app.html'])
    def test_deployment_transmits_only_changed_public_source_and_preserves_private_config(self):
        self.pair()
        with patch('web_sync.subprocess.run',return_value=Mock()) as run:
            self.assertIn('preserved',sync(self.root,['app.html','data/web-bridge.json','core.py']))
        self.assertEqual(run.call_count,2)
        payload=run.call_args.kwargs['input']
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:gz') as tar:
            self.assertEqual(tar.getnames(),['app.html'])
            self.assertEqual(tar.extractfile('app.html').read(),b'<html>updated</html>')
        self.assertIn('StrictHostKeyChecking=yes',run.call_args.args[0])
        self.assertFalse((self.root/'data/web-update-queued.json').exists())
    def test_failed_gateway_health_restores_website_backup_and_keeps_queue(self):
        self.pair()
        with patch('web_sync.subprocess.run',side_effect=[Mock(),subprocess.CalledProcessError(1,'ssh'),Mock()]) as run:
            with self.assertRaisesRegex(ValueError,'restored'):sync(self.root,['app.html'])
        self.assertEqual(run.call_count,3)
        self.assertIn('source.tar.gz',run.call_args.args[0][-1]);self.assertTrue((self.root/'data/web-update-queued.json').exists())
    def test_new_web_assets_are_deployable_but_private_paths_are_not(self):
        self.pair();(self.root/'assets').mkdir();(self.root/'assets/new.js').write_text('const example=1;')
        with patch('web_sync.subprocess.run',return_value=Mock()) as run:
            sync(self.root,['assets/new.js','assets/private/secret.py'])
        with tarfile.open(fileobj=io.BytesIO(run.call_args.kwargs['input']),mode='r:gz') as tar:
            self.assertEqual(tar.getnames(),['assets/new.js'])

    def test_failed_website_restore_reports_attention_instead_of_claiming_success(self):
        self.pair()
        failure=subprocess.CalledProcessError(1,'ssh')
        with patch('web_sync.subprocess.run',side_effect=[Mock(),failure,failure]):
            with self.assertRaisesRegex(ValueError,'recovery need attention'):sync(self.root,['app.html'])
        self.assertTrue((self.root/'data/web-update-queued.json').exists())
