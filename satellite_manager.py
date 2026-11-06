"""Restore the paired satellite from the PC, including after a read-only Pi boot."""
import hashlib
import io
import ipaddress
import json
import os
import shutil
import shlex
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
         ('pi/screensaver.py','screensaver.py'),('pi/screensaver.html','screensaver.html'),
         ('wake_detector.py','wake_detector.py'),('wake_capture.py','wake_capture.py'),
         ('keyword_grammar.py','keyword_grammar.py'),('download_keyword_model.py','download_keyword_model.py'),
         ('conversation_turns.py','conversation_turns.py'),
         ('background_guard.py','background_guard.py'),('audio_utils.py','audio_utils.py'),
         ('voice_tuning.py','voice_tuning.py'),('wake_tuning.json','wake_tuning.json'),
         ('interruption.py','interruption.py'),
         ('assets/upgrade.js','assets/upgrade.js'),('assets/upgrade.css','assets/upgrade.css'),
         ('assets/weather.js','assets/weather.js'),
         ('assets/tasks.js','assets/tasks.js'),
         ('assets/alarms.js','assets/alarms.js'),
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

    def remember_address(self,address):
        try:
            if not ipaddress.ip_address(address).is_private:return
        except ValueError:return
        path=ROOT/'data/satellite-address.json'
        try:
            if json.loads(path.read_text()).get('address')==address:return
        except (OSError,ValueError):pass
        path.write_text(json.dumps({'address':address}),encoding='utf-8')

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
        self.remember_address(address)
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
        self.prepare_keywords(address)
        self.ssh(address,'sudo systemctl restart andrew-relay andrew-screen andrew-display',timeout=30)
        self.remember_address(address)
        self.status.update(state='ready',error='',update_queued=False,installed_version=deployment_version())
        (ROOT/'data/pi-update-queued.json').unlink(missing_ok=True)

    def prepare_keywords(self,address):
        """Prepare the optional local detector before restarting a writable Pi."""
        from keyword_grammar import model_path
        model=model_path(ROOT/'runtime/wake')
        if model is None:return
        probe="PYTHONPATH=/opt/andrew/python python3 -c \"import vosk; vosk.SetLogLevel(-1); vosk.Model('/opt/andrew/wake/vosk')\""
        try:self.ssh(address,probe,timeout=15);return
        except subprocess.CalledProcessError:pass
        self.status.update(state='updating',error='',detail='Preparing local wake detection.')
        self.ssh(address,'python3 -m pip install --disable-pip-version-check --no-warn-script-location --target /opt/andrew/python vosk==0.3.45',timeout=240)
        bundle=io.BytesIO()
        with tarfile.open(fileobj=bundle,mode='w:gz') as archive:
            archive.add(model,arcname='wake/vosk')
        self.ssh(address,'tar -xzf - -C /opt/andrew',bundle.getvalue(),timeout=120)
        self.ssh(address,probe,timeout=15)

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

    def reconcile(self,address,version):
        """Missing heartbeats do not mean running Pi software needs reinstalling."""
        base='/opt/andrew' if self.pairing().get('mode')=='writable' else '/run/andrew'
        probe="""import json,pathlib,subprocess
p=pathlib.Path(BASE)/'deployment-version.txt'
services={name:subprocess.run(['systemctl','is-active','andrew-'+name],capture_output=True,text=True,timeout=5).stdout.strip() for name in ('relay','screen','display')}
print(json.dumps(dict(version=p.read_text().strip() if p.exists() else '',services=services)))
""".replace('BASE',repr(base))
        reply=self.ssh(address,'python3 -c '+shlex.quote(probe),timeout=20)
        installed=json.loads(reply.stdout)
        if installed.get('version')!=version:
            self.restore(address)
            self.restore_display(address)
            return
        services=installed.get('services',{})
        stopped=[name for name in ('relay','screen','display') if services.get(name) in ('inactive','failed')]
        if stopped:
            # start is idempotent; an active microphone/display is left running.
            self.ssh(address,'sudo systemctl start '+' '.join('andrew-'+name for name in stopped),timeout=30)
        self.status.update(state='waiting',installed_version=version,
                           error='Pi services are running; waiting for the PC connection.')
        if services.get('display')=='active':self.status.update(display='ready')

    def run(self):
        time.sleep(8)
        while True:
            version = deployment_version()
            if self.health.get('version') == version and time.time()-self.health.get('seen',0)<25:
                self.status.update(state='ready',error='',update_queued=False,installed_version=version)
                self.remember_address(self.health.get('ip',''))
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
                        self.reconcile(address,version)
                        break
                    except (OSError,ValueError,subprocess.SubprocessError):
                        self.status.update(state='waiting',error='Waiting for the paired Pi to reconnect.')
            self.update_requested.wait(30);self.update_requested.clear()
