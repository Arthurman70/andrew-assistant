"""Desktop idle timer and an isolated animation window; never owns normal apps."""
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time


class IdleTimer:
    def __init__(self, clock=time.monotonic, seconds=900):
        self.clock, self.seconds = clock, seconds
        self.last = clock()
        self.desktop_idle = False
        self.busy_until = 0

    def activity(self):
        self.last = self.clock()

    def desktop(self, idle):
        self.desktop_idle = idle
        if idle:
            # swayidle reports after one second without physical input.
            self.last = max(self.last, self.clock() - 1)
        else:
            self.activity()

    def voice(self, busy):
        if busy:
            self.activity()
            self.busy_until = self.clock() + 3

    def due(self):
        now = self.clock()
        return self.desktop_idle and now >= self.busy_until and now - self.last >= self.seconds


class ScreenSaver:
    def __init__(self, runtime, desktop=False):
        self.runtime = Path(runtime)
        self.enabled = desktop and bool(shutil.which('swayidle'))
        try:
            seconds = max(5, min(86400, int(os.environ.get('ANDREW_SCREENSAVER_SECONDS', '900'))))
        except ValueError:
            seconds = 900
        self.timer = IdleTimer(seconds=seconds)
        self.lock = threading.RLock()
        self.process = None
        self.monitor = None
        self.wake_phrase = 'Hey Andrew'
        self.error = ''

    def active(self):
        return bool(self.process and self.process.poll() is None)

    def status(self):
        with self.lock:
            return {'enabled': self.enabled, 'active': self.active(), 'timeout_seconds': self.timer.seconds,
                    'wake_phrase': self.wake_phrase, 'error': self.error}

    def start_monitor(self, display_script):
        if not self.enabled:
            return
        idle = shlex.join([sys.executable, str(display_script), '--desktop-idle'])
        resume = shlex.join([sys.executable, str(display_script), '--desktop-active'])
        with (self.runtime / 'idle.log').open('ab', buffering=0) as log:
            self.monitor = subprocess.Popen(['swayidle', '-C', '/dev/null', '-w', 'timeout', '1', idle,
                                             'resume', resume], stdout=log, stderr=log, start_new_session=True)

    def _hide(self):
        if self.active():
            # Chromium may move a child into another process group. Match only
            # this screensaver's separate profile, never a user's browser.
            marker = ('--user-data-dir=' + str(self.runtime / 'screensaver-profile')).encode()
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    if marker in (entry / 'cmdline').read_bytes().split(b'\0'):
                        os.kill(int(entry.name), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
            self.process.terminate()
            try:
                self.process.wait(2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(2)
        self.process = None

    def activity(self, wake_phrase=None):
        with self.lock:
            if wake_phrase:
                self.wake_phrase = str(wake_phrase)[:100]
            self.timer.activity()
            self._hide()

    def desktop(self, idle):
        with self.lock:
            self.timer.desktop(idle)
            if not idle:
                self._hide()

    def voice(self, busy, wake_phrase=None):
        with self.lock:
            if wake_phrase:
                self.wake_phrase = str(wake_phrase)[:100]
            self.timer.voice(busy)
            if busy:
                self._hide()

    def tick(self, playing_video=False):
        with self.lock:
            if playing_video:
                self.timer.activity()
                self._hide()
            if self.process and self.process.poll() is not None:
                self.process = None
                self.timer.activity()
            if not self.enabled or self.active() or not self.timer.due():
                return
            profile = self.runtime / 'screensaver-profile'
            profile.mkdir(exist_ok=True, mode=0o700)
            try:
                with (self.runtime / 'screensaver.log').open('ab', buffering=0) as log:
                    self.process = subprocess.Popen(['chromium', '--ozone-platform=wayland', '--kiosk',
                        '--no-first-run', '--disable-session-crashed-bubble', '--disable-extensions',
                        '--disable-background-networking', '--password-store=basic', '--disk-cache-size=1048576',
                        '--user-data-dir=' + str(profile), 'http://127.0.0.1:8770/screensaver'],
                        stdout=log, stderr=log, start_new_session=True)
                self.error = ''
            except OSError:
                self.error = 'The screensaver window could not start.'
                self.timer.activity()

    def close(self):
        with self.lock:
            self._hide()
            if self.monitor and self.monitor.poll() is None:
                os.killpg(self.monitor.pid, signal.SIGTERM)
                try:
                    self.monitor.wait(2)
                except subprocess.TimeoutExpired:
                    self.monitor.kill()
                    self.monitor.wait(2)
