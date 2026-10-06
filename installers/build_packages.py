"""Build public packages exclusively from staged/committed, non-private source files."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile
ROOT=Path(__file__).resolve().parents[1]
def main():
    files=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().strip('\0').split('\0')
    for file in files:
        if any(part in ('data','runtime','.venv','downloads','.git') for part in Path(file).parts):raise ValueError('Private file in export: '+file)
    def exported(name):
        if name=='package_manifest.json':return (ROOT/name).read_bytes()
        data=subprocess.check_output(['git','show',':'+name],cwd=ROOT)
        if Path(name).suffix in ('.ps1','.cmd'):return data.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
        return data.replace(b'\r\n',b'\n') if Path(name).suffix in ('.sh','.service') else data
    registry={name:hashlib.sha256(exported(name)).hexdigest() for name in files if name!='package_manifest.json'}
    (ROOT/'package_manifest.json').write_text(json.dumps({'files':registry},indent=2)+'\n',encoding='utf-8')
    if 'package_manifest.json' not in files:files.append('package_manifest.json')
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    def add(archive,file):
        content=exported(file)
        archive.writestr('Andrew/'+file,content)
    with zipfile.ZipFile(dist/'Andrew-Windows.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for file in files:add(archive,file)
    with zipfile.ZipFile(dist/'Andrew-Pi.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            if file.startswith(('pi/','installers/','assets/','web/')) or file in ('README.md','LICENSE','COPYING','THIRD_PARTY.md','prepare.py','wake_detector.py','wake_capture.py','background_guard.py','voice_tuning.py','wake_tuning.json','audio_utils.py','interruption.py'):
                add(archive,file)
    with zipfile.ZipFile(dist/'Andrew-Web.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            if file.startswith(('web/','assets/','installers/')) or file in ('web_portal.py','app.html','bridge_runner.py','README.md','DEPLOYMENT.md','LICENSE','COPYING','THIRD_PARTY.md'):
                add(archive,file)
    if shutil.which('iexpress'):
        stage=dist/'setup';stage.mkdir(exist_ok=True)
        shutil.copy2(dist/'Andrew-Windows.zip',stage/'Andrew-Windows.zip');shutil.copy2(ROOT/'installers/setup.ps1',stage/'setup.ps1')
        (stage/'install.cmd').write_text('@echo off\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"\n',encoding='ascii')
        sed='''[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=Andrew setup finished. Open the desktop shortcut.
TargetName={target}
FriendlyName=Andrew Assistant Setup
AppLaunched=cmd.exe /c install.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[Strings]
FILE0="Andrew-Windows.zip"
FILE1="setup.ps1"
FILE2="install.cmd"
[SourceFiles]
SourceFiles0={source}
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
'''.format(target=str(dist/'Andrew-Setup.exe'),source=str(stage)+'\\')
        (stage/'Andrew.sed').write_text(sed,encoding='ascii')
        subprocess.run(['iexpress','/N','/Q',str(stage/'Andrew.sed')],check=True,timeout=90)
        if not (dist/'Andrew-Setup.exe').is_file():raise RuntimeError('Installer executable was not produced.')
    names=['Andrew-Windows.zip','Andrew-Pi.zip','Andrew-Web.zip','Andrew-Setup.exe','Andrew-Android.apk']
    lines=[hashlib.sha256((dist/name).read_bytes()).hexdigest()+'  '+name for name in names if (dist/name).is_file()]
    (dist/'SHA256SUMS.txt').write_text('\n'.join(lines)+'\n',encoding='ascii')
    print('Public packages built:',', '.join(name for name in names if (dist/name).is_file()))
if __name__=='__main__':main()
