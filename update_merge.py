"""Preserve local/self-improved code across official package updates."""
import argparse
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import time

PRIVATE={'.git','.venv','data','runtime','downloads','dist','__pycache__','node_modules'}

def merge(base,local,incoming):
    if local==incoming or incoming==base:return local
    if local==base:return incoming
    try:
        b=base.decode('utf-8').replace('\r\n','\n').splitlines(keepends=True);l=local.decode('utf-8').replace('\r\n','\n').splitlines(keepends=True);u=incoming.decode('utf-8').replace('\r\n','\n').splitlines(keepends=True)
        if l==b:return incoming
        if u==b:return local
    except UnicodeError:raise ValueError('Both copies of a binary file changed')
    def edits(target):return [(i,j,target[x:y]) for tag,i,j,x,y in difflib.SequenceMatcher(None,b,target,autojunk=False).get_opcodes() if tag!='equal']
    left,right=edits(l),edits(u)
    combined=list(left)
    for edit in right:
        if edit in combined:continue
        i,j,_=edit
        for a,z,_ in left:
            if max(i,a)<min(j,z) or (i==j and a<=i<=z) or (a==z and i<=a<=j):
                raise ValueError('Local and upstream edits overlap')
        combined.append(edit)
    result=list(b)
    for i,j,replacement in sorted(combined,key=lambda e:(e[0],e[1]),reverse=True):result[i:j]=replacement
    return ''.join(result).encode('utf-8')

def safe(root,name):
    parts=PurePosixPath(name).parts
    if not parts or PurePosixPath(name).is_absolute() or any(p in PRIVATE or p in ('.','..') for p in name.split('/')) or ':' in name or '\\' in name:raise ValueError('Unsafe package path')
    path=root/name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('Package path escapes installation')
    return path

def baseline(destination,name):
    saved=destination/'data/update-baseline'/name
    if saved.is_file():return saved.read_bytes()
    if (destination/'.git').exists():
        result=subprocess.run(['git','show','HEAD:'+name],cwd=destination,capture_output=True)
        if result.returncode==0:return result.stdout
    for job in sorted((destination/'data/improvements').glob('*/review.json')):
        try:review=json.loads(job.read_text())
        except (ValueError,OSError):continue
        original=job.parent/'base'/name
        if review.get('status')=='installed' and name in review.get('candidate_hashes',{}) and original.is_file():return original.read_bytes()
    return None

def install(source,destination):
    source,destination=Path(source).resolve(),Path(destination).resolve()
    if destination==source:return {'status':'unchanged','preserved':[],'conflicts':[]}
    registry=source/'package_manifest.json'
    if registry.exists():files=json.loads(registry.read_text())['files']
    else:files={p.relative_to(source).as_posix():None for p in source.rglob('*') if p.is_file() and not any(n in PRIVATE for n in p.relative_to(source).parts)}
    files=dict(files)
    if registry.exists():files['package_manifest.json']=None
    incoming={};planned={};conflicts=[];preserved=[]
    for name,expected in files.items():
        original=safe(source,name);target=safe(destination,name);data=original.read_bytes()
        checksums={hashlib.sha256(data).hexdigest()}
        try:
            data.decode('utf-8')
            canonical=data.replace(b'\r\n',b'\n')
            checksums.update((hashlib.sha256(canonical).hexdigest(),hashlib.sha256(canonical.replace(b'\n',b'\r\n')).hexdigest()))
        except UnicodeError:pass
        if expected and expected not in checksums:raise ValueError('Package checksum failed: '+name)
        incoming[name]=data
        if not target.exists():planned[name]=data;continue
        local=target.read_bytes()
        if local==data:planned[name]=data;continue
        base=baseline(destination,name)
        if base is None:
            conflicts.append(name);continue
        try:planned[name]=merge(base,local,data)
        except ValueError:conflicts.append(name);continue
        if name.endswith('.py'):
            try:ast.parse(planned[name].decode('utf-8-sig'),filename=name)
            except (SyntaxError,UnicodeError):conflicts.append(name);continue
        if planned[name]!=data:preserved.append(name)
    data_root=destination/'data';data_root.mkdir(parents=True,exist_ok=True)
    if conflicts:
        pending=data_root/'pending-updates'/time.strftime('%Y%m%d-%H%M%S');pending.mkdir(parents=True)
        for name,data in incoming.items():
            target=pending/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        report={'status':'needs_merge','conflicts':conflicts,'preserved':preserved,'candidate':str(pending),'message':'Local improvements are safe. No live code was overwritten; the incoming update is saved for merging.'}
        (data_root/'update-status.json').write_text(json.dumps(report,indent=2));return report
    backup=data_root/'update-backups'/time.strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True)
    existed={}
    for name in planned:
        target=safe(destination,name);existed[name]=target.is_file()
        if existed[name]:
            saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,saved)
    touched=[]
    try:
        for name,data in planned.items():
            target=safe(destination,name);target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(target.name+'.updating');temporary.write_bytes(data);os.replace(temporary,target);touched.append(name)
    except Exception:
        for name in reversed(touched):
            target=safe(destination,name)
            if existed[name]:shutil.copy2(backup/name,target)
            else:target.unlink(missing_ok=True)
        raise ValueError('Update could not be applied. Previous code was restored from the backup.')
    for name,data in incoming.items():
        saved=data_root/'update-baseline'/name;saved.parent.mkdir(parents=True,exist_ok=True);saved.write_bytes(data)
    report={'status':'installed','preserved':preserved,'conflicts':[],'backup':str(backup)}
    (data_root/'update-status.json').write_text(json.dumps(report,indent=2));return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source');parser.add_argument('destination');args=parser.parse_args()
    result=install(args.source,args.destination);print(json.dumps(result));raise SystemExit(2 if result['conflicts'] else 0)
