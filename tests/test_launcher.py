import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import launcher

READY = {'name': 'Tom', 'pc_voice': {'ready': True}}


class LauncherTests(unittest.TestCase):
    def setUp(self):
        (launcher.ROOT / 'data/tests').mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=launcher.ROOT / 'data/tests')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'data').mkdir()

    def test_healthy_service_not_restarted_even_in_repair_mode(self):
        with patch.object(launcher, 'health', return_value=READY), patch.object(launcher, 'spawn') as spawn:
            self.assertEqual(launcher.ensure_server(self.root, True), (READY, False))
        spawn.assert_not_called()

    def test_start_waits_until_healthy(self):
        child = Mock()
        with patch.object(launcher, 'health', return_value=None), patch.object(launcher, 'owned_processes', return_value=[]), patch.object(launcher, 'spawn', return_value=child) as spawn, patch.object(launcher, 'wait_ready', return_value=READY) as wait:
            self.assertEqual(launcher.ensure_server(self.root), (READY, True))
        spawn.assert_called_once_with(self.root, 'server.py', 'server')
        wait.assert_called_once_with(60, child)

    def test_slow_start_does_not_duplicate_service(self):
        with patch.object(launcher, 'health', return_value=None), patch.object(launcher, 'owned_processes', return_value=[Mock()]), patch.object(launcher, 'wait_ready', return_value=READY), patch.object(launcher, 'spawn') as spawn:
            self.assertEqual(launcher.ensure_server(self.root), (READY, False))
        spawn.assert_not_called()

    def test_normal_start_does_not_kill_hung_service(self):
        with patch.object(launcher, 'health', return_value=None), patch.object(launcher, 'owned_processes', return_value=[Mock()]), patch.object(launcher, 'wait_ready', return_value=None), patch.object(launcher, 'stop_stuck') as stop:
            with self.assertRaisesRegex(launcher.LaunchError, 'Repair Andrew'):
                launcher.ensure_server(self.root)
        stop.assert_not_called()

    def test_repair_restarts_only_unresponsive_service(self):
        calls = []
        with patch.object(launcher, 'health', return_value=None), patch.object(launcher, 'owned_processes', return_value=[Mock()]), patch.object(launcher, 'wait_ready', side_effect=[None, READY]), patch.object(launcher, 'stop_stuck', side_effect=lambda root: calls.append('stop')), patch.object(launcher, 'spawn', side_effect=lambda *args: calls.append('start')):
            self.assertEqual(launcher.ensure_server(self.root, True), (READY, True))
        self.assertEqual(calls, ['stop', 'start'])

    def test_failed_startup_not_reported_as_ready(self):
        with patch.object(launcher, 'health', return_value=None):
            with self.assertRaisesRegex(launcher.LaunchError, 'could not start'):
                launcher.wait_ready(60, SimpleNamespace(poll=lambda: 1))

    def test_only_exact_python_entrypoint_is_owned(self):
        def process(name, *args):
            return SimpleNamespace(info={'name': name, 'cmdline': list(args)})
        target = str((self.root / 'server.py').resolve())
        mine = process('pythonw.exe', 'pythonw.exe', target)
        others = [process('pythonw.exe', 'pythonw.exe', target + '.backup'),
                  process('pythonw.exe', 'pythonw.exe', str(self.root.parent / 'server.py')),
                  process('pythonw.exe', 'pythonw.exe', 'companion_native.py', target),
                  process('chrome.exe', 'chrome.exe', target)]
        with patch.object(launcher.psutil, 'process_iter', return_value=[mine, *others]):
            self.assertEqual(launcher.owned_processes(self.root, 'server.py'), [mine])

    def test_bridge_does_not_trust_stale_pid_file(self):
        (self.root / 'data/web-bridge.json').write_text(json.dumps({'bridge_token': 'test'}))
        (self.root / 'data/web-bridge.pid').write_text('1234')
        response = Mock(); response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(); response.read.return_value = json.dumps(READY).encode()
        with patch.object(launcher, 'owned_processes', return_value=[]), patch.object(launcher, 'spawn') as spawn, patch.object(launcher.urllib.request, 'urlopen', return_value=response):
            self.assertIn('bridge is running', launcher.ensure_bridge(self.root))
        spawn.assert_called_once_with(self.root, 'bridge_runner.py', 'web-bridge')

    def test_existing_crash_logs_preserved(self):
        python = self.root / '.venv/Scripts/pythonw.exe'
        python.parent.mkdir(parents=True); python.touch()
        error = self.root / 'data/server-error.log'; error.write_text('previous crash\n')
        with patch.object(launcher.subprocess, 'Popen') as popen:
            launcher.spawn(self.root, 'server.py', 'server')
        self.assertEqual(error.read_text(), 'previous crash\n')
        self.assertEqual(popen.call_args.args[0], [str(python), str(self.root / 'server.py')])

    def test_missing_runtime_explains_repair(self):
        with self.assertRaisesRegex(launcher.LaunchError, 'installer'):
            launcher.spawn(self.root, 'server.py')

    def test_another_http_service_is_not_andrew(self):
        response = Mock(); response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(); response.read.return_value = b'{"ready":true}'
        with patch.object(launcher.urllib.request, 'urlopen', return_value=response):
            self.assertIsNone(launcher.health())


if __name__ == '__main__':
    unittest.main()
