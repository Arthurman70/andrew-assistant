"""Host-side authenticated gateway and reconnecting outbound SSH tunnel."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from web_portal import Portal
ROOT=Path(__file__).resolve().parent
def main():
    config=json.loads((ROOT/'data/web-bridge.json').read_text(encoding='utf-8'))
    server=Portal(('127.0.0.1',config.get('port',8780)),config,mode='bridge')
    threading.Thread(target=server.serve_forever,daemon=True).start()
    while True:
        command=[str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe'),'-N','-F','NUL',
                 '-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=10',
                 '-o','ServerAliveInterval=20','-o','ServerAliveCountMax=3','-o','StrictHostKeyChecking=yes',
                 '-o','UserKnownHostsFile='+str(ROOT/'data/vps_known_hosts'),'-i',str(ROOT/'data/web_tunnel_ed25519'),
                 '-R','127.0.0.1:18765:127.0.0.1:'+str(config.get('port',8780)),config['ssh_target']]
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:process.wait()
        finally:
            if process.poll() is None:process.terminate()
        time.sleep(10)
if __name__=='__main__':main()
