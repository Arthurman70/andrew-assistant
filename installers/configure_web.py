"""Create private, portable web/PC configuration; outputs are never release assets."""
import argparse
import io
import json
from pathlib import Path
import re
import secrets
import sys
import tarfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from web_portal import password_hash
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--domain',required=True);parser.add_argument('--email',required=True)
    parser.add_argument('--ssh-target',required=True);parser.add_argument('--server-bind',default='127.0.0.1')
    parser.add_argument('--output',default=str(ROOT/'data/web-setup'));args=parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9.-]+',args.domain) or '.' not in args.domain:raise ValueError('Use a domain name, without scheme or path.')
    if not re.fullmatch(r'[\w.-]+@[\w.-]+',args.ssh_target):raise ValueError('Use a restricted SSH user@host.')
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    if (out/'web-bridge.json').exists():raise ValueError('Setup already exists. Preserve its keys; use a new output directory for a new pairing.')
    token=secrets.token_urlsafe(48);password=secrets.token_urlsafe(20);key=ed25519.Ed25519PrivateKey.generate()
    (out/'web_tunnel_ed25519').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption()))
    (out/'web_tunnel_ed25519.pub').write_bytes(key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH))
    public={'bind':args.server_bind,'port':18770,'origin':'https://'+args.domain,'email':args.email.lower(),'password':password_hash(password),'temporary':True,'bridge_token':token,'upstream':'http://127.0.0.1:18765'}
    (out/'web-bridge.json').write_text(json.dumps({'port':8780,'upstream':'http://127.0.0.1:8765','bridge_token':token,'ssh_target':args.ssh_target}),encoding='utf-8')
    (out/'website-login.txt').write_text('URL: '+public['origin']+'\nEmail: '+args.email+'\nTemporary password: '+password+'\nKeep this file private.\n',encoding='utf-8')
    with tarfile.open(out/'server-private.tar.gz','w:gz') as tar:
        for name in ['web_portal.py','app.html','assets/andrew.png','assets/upgrade.js','assets/upgrade.css','assets/browser.js','assets/icon-192.png','assets/icon-512.png']:
            tar.add(ROOT/name,arcname=name)
        for file in (ROOT/'web').glob('*'):tar.add(file,arcname='web/'+file.name)
        raw=json.dumps(public).encode();entry=tarfile.TarInfo('private/config.json');entry.size=len(raw);entry.mode=0o600;tar.addfile(entry,io.BytesIO(raw))
    print('Private web setup created. Follow DEPLOYMENT.md. Do not publish its output.')
if __name__=='__main__':main()
