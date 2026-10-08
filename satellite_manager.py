"""Restore the paired satellite from the PC, including after a read-only Pi boot."""
import hashlib
import io
import ipaddress
import json
import os
import shutil
from pathlib import Path
import subprocess
import tarfile
import time
import threading

ROOT = Path(__file__).resolve().parent
SSH = Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe' if os.name=='nt' else Path(shutil.which('ssh') or '/usr/bin/ssh')
SOURCES=[('pi/agent.py','agent.py'),('pi/activate-agent.sh','activate-agent.sh'),
         ('pi/camera_capture.py','camera_capture.py'),
         ('pi/prepare-voice-ram.sh','prepare-voice-ram.sh'),('pi/prepare-display-ram.sh','prepare-display-ram.sh'),
         ('pi/activate-display.sh','activate-display.sh'),('pi/display.py','display.py'),
         ('pi/screen.py','screen.py'),('pi/screen.html','screen.html'),
         ('wake_detector.py','wake_detector.py'),('wake_capture.py','wake_capture.py'),
         ('background_guard.py','background_guard.py'),('audio_utils.py','audio_utils.py'),
         ('voice_tuning.py','voice_tuning.py'),('wake_tuning.json','wake_tuning.json'),
         ('interruption.py','interruption.py'),
         ('assets/upgrade.js','assets/upgrade.js'),('assets/upgrade.css','assets/upgrade.css'),
         ('assets/weather.js','assets/weather.js'),
         ('assets/tasks.js','assets/tasks.js'),
         ('assets/voice_offline.wav','voice_offline.wav')]

def deployment_version():
    digest=hashlib.sha256()
    for source,_ in SOURCES: digest.update((ROOT/source).read_bytes().replace(b'\r\n',b'\n'))
    return digest.hexdigest()[:12]


class SatelliteManager:
    def __init__(self, health):
        self.health = health
        self.status = {'state': 'waiting', 'error': ''}
        self.update_requested=threading.Event()
        if (ROOT/'data/pi-update-queued.json').exists():
            self.status.update(update_queued=True,target_version=deployment_version())

    def queue_update(self):
        version=deployment_version()
        self.status.update(update_queued=True,target_version=version)
        self.update_requested.set()
        (ROOT/'data/pi-update-queued.json').write_text(json.dumps({'version':version,'at':time.time()}),encoding='utf-8')
        return 'Pi update queued. I will send the current version when the paired Pi reconnects.'

    def pairing(self):
        try:return json.loads((ROOT/'data/satellite-pairing.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}

    def ssh(self, address, command, payload=None, timeout=15):
        pairing=self.pairing()
        alias=pairing.get('host_alias')
        if not alias:
            rows=(ROOT/'data/pi_known_hosts').read_text(encoding='utf-8').splitlines()
            alias=next((row.split()[0] for row in rows if row and not row.startswith('#')),address)
        return subprocess.run([str(SSH), '-F', 'NUL', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=5', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlias='+alias, '-o', 'UserKnownHostsFile='+str(ROOT/'data/pi_known_hosts'),
            '-i', str(ROOT/'data/pi_ed25519'), pairing.get('user','andrew')+'@'+address, command],
            input=payload, capture_output=True, check=True, timeout=timeout,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def restore(self, address):
        if self.pairing().get('mode')=='writable':return self.update_writable(address)
        self.ssh(address, 'test "$(hostname)" = andrew-pi')
        self.status.update(state='restoring', error='')
        self.ssh(address, 'sudo install -d -o andrew -g andrew -m 755 /run/andrew')
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode='w') as archive:
            for src, dest in SOURCES:
                content = (ROOT/src).read_bytes().replace(b'\r\n', b'\n')
                entry = tarfile.TarInfo(dest); entry.size = len(content); entry.mode = 0o644
                archive.addfile(entry, io.BytesIO(content))
            content=deployment_version().encode()
            entry=tarfile.TarInfo('deployment-version.txt');entry.size=len(content);entry.mode=0o644
            archive.addfile(entry,io.BytesIO(content))
            archive.add(ROOT/'runtime/pi-python.tar.gz',arcname='python-bundle.tar.gz')
            archive.add(ROOT/'runtime/wake',arcname='wake')
            archive.add(ROOT/'assets/andrew.png',arcname='andrew.png')
        self.ssh(address, 'tar -xf - -C /run/andrew', bundle.getvalue(), timeout=60)
        self.ssh(address, 'sudo systemctl stop andrew-relay; sudo bash /run/andrew/prepare-voice-ram.sh && tar -xzf /run/andrew/python-bundle.tar.gz -C /run/andrew/python',timeout=60)
        self.ssh(address, 'sudo bash /run/andrew/activate-agent.sh', timeout=25)
        (ROOT/'data/satellite-address.json').write_text(json.dumps({'address':address}), encoding='utf-8')
        self.status.update(state='ready', error='')
        self.status.update(update_queued=False,installed_version=deployment_version())
        (ROOT/'data/pi-update-queued.json').unlink(missing_ok=True)

    def update_writable(self,address):
        self.status.update(state='updating',error='')
        bundle=io.BytesIO()
        with tarfile.open(fileobj=bundle,mode='w') as archive:
            for source,destination in SOURCES:
                content=(ROOT/source).read_bytes().replace(b'\r\n',b'\n')
                item=tarfile.TarInfo(destination);item.size=len(content);item.mode=0o644;archive.addfile(item,io.BytesIO(content))
            content=deployment_version().encode();item=tarfile.TarInfo('deployment-version.txt');item.size=len(content);archive.addfile(item,io.BytesIO(content))
        self.ssh(address,'mkdir -p /opt/andrew/assets && tar -xf - -C /opt/andrew',bundle.getvalue(),timeout=30)
        self.ssh(address,'sudo systemctl restart andrew-relay andrew-screen andrew-display',timeout=30)
        self.status.update(state='ready',error='',update_queued=False,installed_version=deployment_version())
        (ROOT/'data/pi-update-queued.json').unlink(missing_ok=True)

    def restore_display(self,address):
        if self.pairing().get('mode')=='writable':return
        cache=ROOT/'runtime/pi-display.tar.gz'
        if not cache.is_file(): return
        try:
            self.ssh(address,'test -x /run/andrew/display/root/usr/bin/chromium')
        except subprocess.CalledProcessError:
            self.status.update(display='restoring')
            self.ssh(address,'sudo bash /run/andrew/prepare-display-ram.sh --restore',cache.read_bytes(),timeout=240)
        self.ssh(address,'sudo bash /run/andrew/activate-display.sh',timeout=30)
        self.status.update(display='ready')

    def run(self):
        time.sleep(8)
        while True:
            version = deployment_version()
            if self.health.get('version') == version and time.time()-self.health.get('seen',0)<25:
                self.status.update(state='ready',error='',update_queued=False,installed_version=version)
                (ROOT/'data/pi-update-queued.json').unlink(missing_ok=True)
                if self.health.get('display',{}).get('running'):
                    self.status.update(display='ready')
                else:
                    try: self.restore_display(self.health['ip'])
                    except (KeyError,OSError,subprocess.SubprocessError): self.status.update(display='waiting')
            else:
                candidates = [self.pairing().get('address','andrew-pi.local')]
                address = self.health.get('ip')
                try:
                    if not address:
                        address = json.loads((ROOT/'data/satellite-address.json').read_text())['address']
                    if ipaddress.ip_address(address).is_private: candidates.insert(0,address)
                except (OSError,ValueError,KeyError): pass
                for address in dict.fromkeys(candidates):
                    try:
                        self.restore(address)
                        try: self.restore_display(address)
                        except (OSError,subprocess.SubprocessError): self.status.update(display='waiting')
                        break
                    except (OSError,subprocess.SubprocessError):
                        self.status.update(state='waiting',error='Waiting for the paired Pi to reconnect.')
            self.update_requested.wait(30);self.update_requested.clear()
