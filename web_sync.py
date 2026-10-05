"""Deploy code-only website updates using an explicitly paired admin SSH target."""
import io
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess
import tarfile
import time
from code_map import source_name

WEB_FILES={'web_portal.py','app.html','assets/upgrade.js','assets/upgrade.css','assets/browser.js',
           'web/login.html','web/password.html','web/sw.js','web/widget.js','web/manifest.webmanifest'}


def sync(root,changed):
    root=Path(root);files=sorted({name for name in changed if name in WEB_FILES or
        (source_name(name) and name.startswith(('assets/','web/')) and Path(name).suffix in ('.js','.css','.html'))})
    if not files:return 'No website source changes.'
    config=root/'data/web-deploy.json';queue=root/'data/web-update-queued.json'
    queue.write_text(json.dumps({'files':files,'at':time.time()}),encoding='utf-8')
    if not config.exists():return 'Website files updated on the host. Website deployment is queued until admin SSH pairing is configured.'
    target=json.loads(config.read_text(encoding='utf-8')).get('ssh_target','')
    if not re.fullmatch(r'[\w.-]+@[\w.-]+',target):raise ValueError('Invalid website SSH pairing.')
    ssh=str(Path(os.environ.get('WINDIR','C:/Windows'))/'System32/OpenSSH/ssh.exe') if os.name=='nt' else shutil.which('ssh')
    archive=io.BytesIO()
    removed=[]
    with tarfile.open(fileobj=archive,mode='w:gz') as tar:
        for name in files:
            if not (root/name).exists():removed.append(name);continue
            data=(root/name).read_bytes();entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o644
            tar.addfile(entry,io.BytesIO(data))
    # All paths and commands below are fixed application paths; private/config
    # is never uploaded or replaced. Keep a code backup for gateway recovery.
    stamp=time.strftime('%Y%m%d-%H%M%S')
    backup='/opt/andrew-web-backups/'+stamp
    prepare_script="from pathlib import Path;import tarfile,json;root=Path('/opt/andrew-web');dest=Path("+repr(backup)+");dest.mkdir(parents=True,exist_ok=True,mode=0o700);files="+repr(files)+";missing=[n for n in files if not (root/n).exists()];(dest/'missing.json').write_text(json.dumps(missing));tar=tarfile.open(dest/'source.tar.gz','w:gz');[(tar.add(root/n,arcname=n)) for n in files if (root/n).exists()];tar.close()"
    prepare='python3 -c '+shlex.quote(prepare_script)
    base=[ssh,'-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',target]
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    subprocess.run(base+[prepare],capture_output=True,check=True,timeout=20,creationflags=flags)
    deletion=' && '.join('rm -f '+shlex.quote('/opt/andrew-web/'+name) for name in removed)
    health_script="import json,time,urllib.request;from pathlib import Path;from urllib.parse import urlsplit;c=json.loads(Path('/opt/andrew-web/private/config.json').read_text());url='http://'+c.get('bind','127.0.0.1')+':'+str(c.get('port',18770))+'/health';time.sleep(1);request=urllib.request.Request(url,headers={'Host':urlsplit(c['origin']).netloc});assert json.load(urllib.request.urlopen(request,timeout=5))['ready']"
    command='tar -xzf - -C /opt/andrew-web'+(' && '+deletion if deletion else '')+' && chown -R root:andrew-web /opt/andrew-web/assets /opt/andrew-web/web && python3 -m py_compile /opt/andrew-web/web_portal.py && systemctl restart andrew-web && python3 -c '+shlex.quote(health_script)
    try:
        subprocess.run(base+[command],input=archive.getvalue(),capture_output=True,check=True,timeout=25,creationflags=flags)
    except Exception:
        cleanup="import json;from pathlib import Path;root=Path('/opt/andrew-web');[(root/n).unlink(missing_ok=True) for n in json.loads(Path("+repr(backup+'/missing.json')+").read_text())]"
        try:
            subprocess.run(base+['tar -xzf '+backup+'/source.tar.gz -C /opt/andrew-web && python3 -c '+shlex.quote(cleanup)+' && systemctl restart andrew-web'],
                           capture_output=True,check=True,timeout=25,creationflags=flags)
        except Exception:
            raise ValueError('Website deployment and recovery need attention. Host code remains installed; deployment is queued.')
        raise ValueError('Website code deployment failed; previous website code was restored. Host code remains installed; deployment is queued.')
    queue.unlink(missing_ok=True)
    return 'Website code updated. Login credentials and private configuration were preserved.'
