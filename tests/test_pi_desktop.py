import importlib.util
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

class DesktopDisplayTests(unittest.TestCase):
    def setUp(self):
        path_patch=patch.object(sys,'path',[str(Path(__file__).resolve().parents[1]/'pi')]+sys.path)
        path_patch.start();self.addCleanup(path_patch.stop)
        spec=importlib.util.spec_from_file_location('pi_desktop_test',Path(__file__).resolve().parents[1]/'pi/display.py')
        self.display=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.display)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.display.RUNTIME=Path(self.temp.name)
        self.fake=Mock();self.fake.poll.return_value=None

    def open(self,url,desktop):
        self.display.DESKTOP=desktop
        with patch.object(self.display,'stop_display'),patch.object(self.display.time,'sleep'),patch.object(self.display.urllib.request,'urlopen',side_effect=OSError),patch.object(self.display.subprocess,'Popen',return_value=self.fake) as spawn:
            self.display.navigate(url)
            return spawn.call_args.args[0]

    def test_assistant_is_fullscreen_without_taking_over_the_desktop_compositor(self):
        args=self.open(self.display.HOME_URL,True)
        self.assertEqual(args[0],'chromium')
        self.assertIn('--kiosk',args);self.assertNotIn('cage',args)
        self.assertIn('--user-data-dir=/run/andrew/browser-profile',args)

    def test_desktop_button_closes_only_the_assistant_and_sets_desktop_mode(self):
        self.display.DESKTOP=True;self.display.process=self.fake
        with patch.object(self.display,'stop_display') as stop:
            self.display.show_desktop()
        stop.assert_called_once();self.assertEqual(self.display.current,'desktop')
        self.assertIsNone(self.display.process);self.assertTrue(self.display.status()['desktop_available'])

    def test_desktop_web_navigation_keeps_normal_browser_controls(self):
        args=self.open('https://example.org/',True)
        self.assertIn('--new-window',args)
        self.assertNotIn('--kiosk',args)
        self.assertFalse(any(arg.startswith('--app=') for arg in args))

    def test_existing_kiosk_installations_still_use_their_compositor(self):
        args=self.open(self.display.HOME_URL,False)
        self.assertEqual(args[:3],['cage','-d','--'])
        self.assertIn('--kiosk',args)

    def test_invalid_urls_cannot_launch_programs(self):
        for url in ('file:///etc/passwd','javascript:alert(1)','https://x.example/\ncommand','https://user:pass@example.org/'):
            with self.subTest(url=url),patch.object(self.display.subprocess,'Popen') as spawn:
                with self.assertRaises(ValueError):self.display.navigate(url)
                spawn.assert_not_called()

if __name__=='__main__':unittest.main()
