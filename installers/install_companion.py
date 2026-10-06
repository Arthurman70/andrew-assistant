"""Register the native host; keep Python runtime files outside Chrome's load folder."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from browser_companion import extension_id,HOST_NAME

def clean_extension(root):
    root=Path(root).resolve();folder=root/'companion'
    if folder.is_symlink() or folder.resolve()!=folder:raise ValueError('Use the ordinary companion folder inside your Andrew installation.')
    legacy=folder/'install.py'
    if legacy.exists():
        if legacy.is_symlink():raise ValueError('The legacy companion installer needs attention.')
        backup=root/'data/companion-setup-backups'/str(time.time_ns());backup.mkdir(parents=True,exist_ok=True)
        shutil.move(str(legacy),str(backup/'install.py'))
    cache=folder/'__pycache__'
    if cache.exists():
        if cache.resolve()!=cache or cache.is_symlink():raise ValueError('The companion cache path needs attention.')
        shutil.rmtree(cache)
    if any(p.name.startswith('_') and p.name not in ('_locales','_metadata') for p in folder.iterdir()):
        raise ValueError('Chrome cannot load a reserved filename in the companion folder. Keep runtime files outside it.')

def prepare(root=ROOT):
    root=Path(root).resolve();clean_extension(root);target=root/'runtime/browser-companion';target.mkdir(parents=True,exist_ok=True)
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.exists():raise ValueError('Windows .NET C# compiler is unavailable. Native host preparation needs attention.')
    exe=target/'andrew-companion.exe'
    subprocess.run([str(compiler),'/nologo','/target:exe','/out:'+str(exe),str(root/'companion/NativeLauncher.cs')],check=True,capture_output=True)
    origin='chrome-extension://'+extension_id(root)+'/'
    (target/'launcher.ini').write_text(str(root/'.venv/Scripts/python.exe')+'\n'+str(root/'companion_native.py')+'\n'+origin+'\n',encoding='utf-8')
    manifest=root/'data/browser-native-host.json'
    manifest.write_text(json.dumps({'name':HOST_NAME,'description':'Andrew companion, paired to this PC','path':str(exe),'type':'stdio','allowed_origins':[origin]},indent=2),encoding='utf-8')
    return manifest

def register(manifest,browser='chrome'):
    import winreg
    vendor='Google/Chrome' if browser=='chrome' else 'Microsoft/Edge'
    key='Software/'+vendor+'/NativeMessagingHosts/'+HOST_NAME
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,key.replace('/','\\')) as handle:
        winreg.SetValueEx(handle,'',0,winreg.REG_SZ,str(Path(manifest).resolve()))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--register',action='store_true');parser.add_argument('--browser',choices=['chrome','edge'],default='chrome');args=parser.parse_args()
    manifest=prepare()
    if args.register:register(manifest,args.browser)
    print('Native host prepared'+(' and registered' if args.register else '')+'. Load the companion folder in your browser to connect.')
