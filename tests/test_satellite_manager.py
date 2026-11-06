import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from satellite_manager import SatelliteManager


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.manager=SatelliteManager({})
        self.manager.pairing=Mock(return_value={'mode':'writable'})
        self.manager.restore=Mock()
        self.manager.restore_display=Mock()

    def probe(self,version='current',**services):
        state={'relay':'active','screen':'active','display':'active'}|services
        self.manager.ssh=Mock(return_value=subprocess.CompletedProcess([],0,
            json.dumps({'version':version,'services':state}).encode(),b''))

    def test_pc_outage_does_not_reinstall_or_restart_a_running_pi(self):
        self.probe()
        for _ in range(12):self.manager.reconcile('192.168.1.40','current')
        self.manager.restore.assert_not_called()
        self.manager.restore_display.assert_not_called()
        self.assertTrue(all('sudo' not in call.args[1] for call in self.manager.ssh.call_args_list))
        self.assertEqual(self.manager.status['installed_version'],'current')
        self.assertEqual(self.manager.status['state'],'waiting')

    def test_actual_update_is_installed_once_then_left_running(self):
        self.probe('old')
        self.manager.reconcile('pi','current')
        self.manager.restore.assert_called_once_with('pi')
        self.probe()
        self.manager.reconcile('pi','current')
        self.manager.restore.assert_called_once()

    def test_only_stopped_services_are_started(self):
        self.probe(screen='failed',display='inactive')
        self.manager.reconcile('pi','current')
        self.manager.ssh.assert_called_with('pi',
            'sudo systemctl start andrew-screen andrew-display',timeout=30)
        self.manager.restore.assert_not_called()

    def test_services_starting_are_not_interrupted(self):
        self.probe(relay='activating',display='deactivating')
        self.manager.reconcile('pi','current')
        self.assertEqual(self.manager.ssh.call_count,1)

    def test_unreachable_pi_does_not_trigger_blind_deployment(self):
        self.probe()
        self.manager.ssh.side_effect=subprocess.TimeoutExpired('ssh',20)
        with self.assertRaises(subprocess.TimeoutExpired):self.manager.reconcile('pi','current')
        self.manager.restore.assert_not_called()

    def test_read_only_installation_checks_volatile_source(self):
        self.probe()
        self.manager.pairing.return_value={'mode':'readonly'}
        self.manager.reconcile('pi','current')
        self.assertIn('/run/andrew',self.manager.ssh.call_args.args[1])

    def test_remembers_wifi_address_without_rewriting_unchanged_cache(self):
        with tempfile.TemporaryDirectory() as folder,patch('satellite_manager.ROOT',Path(folder)):
            (Path(folder)/'data').mkdir()
            self.manager.remember_address('192.168.1.40')
            cache=Path(folder)/'data/satellite-address.json'
            self.assertEqual(json.loads(cache.read_text()),{'address':'192.168.1.40'})
            with patch.object(Path,'write_text') as write:
                self.manager.remember_address('192.168.1.40')
                self.manager.remember_address('8.8.8.8')
                self.manager.remember_address('invalid')
                write.assert_not_called()

    def test_ready_keyword_runtime_is_not_reinstalled(self):
        self.manager.ssh=Mock()
        with patch('keyword_grammar.model_path',return_value=Path('/model')):
            self.manager.prepare_keywords('pi')
        self.assertEqual(self.manager.ssh.call_count,1)
        self.assertNotIn('pip install',self.manager.ssh.call_args.args[1])

    def test_missing_host_model_keeps_existing_small_detector(self):
        self.manager.ssh=Mock()
        with patch('keyword_grammar.model_path',return_value=None):
            self.manager.prepare_keywords('pi')
        self.manager.ssh.assert_not_called()


if __name__=='__main__':unittest.main()
