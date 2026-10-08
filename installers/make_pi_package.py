"""Generate a PRIVATE host-paired Pi package from a local host installation."""
import argparse
from pathlib import Path
import subprocess
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--pc-ip',required=True);args=parser.parse_args()
    subprocess.run([sys.executable,str(ROOT/'prepare.py'),'--pc-ip',args.pc_ip],check=True)
    names=['pi/agent.py','pi/camera_capture.py','pi/display.py','pi/screen.py','pi/screen.html','pi/install.sh',
           'pi/install-screen.sh','pi/andrew-relay.service','pi/andrew-screen.service','wake_detector.py','wake_capture.py',
           'background_guard.py','voice_tuning.py','wake_tuning.json','audio_utils.py','interruption.py','pi/relay-config.json','data/server.crt',
           'assets/andrew.png','assets/upgrade.js','assets/upgrade.css','assets/weather.js','assets/tasks.js','assets/voice_offline.wav']
    target=ROOT/'data/Andrew-Pi-private-pairing.zip'
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name in names:
            content=(ROOT/name).read_bytes()
            if Path(name).suffix in ('.sh','.service'):content=content.replace(b'\r\n',b'\n')
            bundle.writestr('andrew/'+('pc.crt' if name=='data/server.crt' else Path(name).name),content)
        bundle.write(ROOT/'data/pi_ed25519.pub','andrew/paired-host.pub')
        bundle.writestr('install-pi.sh',(ROOT/'installers/install-pi.sh').read_bytes().replace(b'\r\n',b'\n'))
    print('Private Pi pairing package created in data. Copy it to your Pi; do not upload it publicly.')
if __name__=='__main__':main()
