"""Checked updater, frozen per operation so it can also improve/restore itself.

Runs for an authorized improvement request or explicit install. The test copy
keeps private runtime files out; it is not an OS security sandbox.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import psutil
from improvements import ROOT, ID_PATTERN, check_source, digest, editable, read_json, safe_path, update
import re

HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def verify_base(root, review):
    for name, expected in review['snapshot_hashes'].items():
        if digest(safe_path(root,name)) != expected:
            raise ValueError('Andrew changed since this proposal was drafted. Request a fresh improvement before installing.')
    for name, expected in review['base_hashes'].items():
        if not editable(name) or digest(safe_path(root,name)) != expected:
            raise ValueError('A proposed file changed since the draft. Nothing was installed.')


def verify_candidate(path, review):
    if not review.get('candidate_hashes') or set(review['candidate_hashes']) != set(review['base_hashes']):
        raise ValueError('The proposal manifest is incomplete.')
    for name, expected in review['candidate_hashes'].items():
        if not editable(name): raise ValueError('The proposal includes a protected file.')
        candidate = safe_path(path/'candidate',name)
        if digest(candidate) != expected: raise ValueError('Candidate code changed after review. Request a fresh proposal.')
        check_source(name,candidate.read_text(encoding='utf-8'))


def run_tests(root, path, review):
    work = path / 'test-work'
    if work.exists(): raise ValueError('This proposal was already tested. Request a new draft to retry.')
    work.mkdir()
    for name, expected in review['snapshot_hashes'].items():
        source = safe_path(path/'base',name)
        if digest(source) != expected: raise ValueError('The source snapshot changed after drafting.')
        target = safe_path(work,name)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
    for name in review['candidate_hashes']:
        if name.startswith('tests/') and name in review['snapshot_hashes']:continue
        target = safe_path(work,name)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(safe_path(path/'candidate',name),target)
    (work/'data/tests').mkdir(parents=True,exist_ok=True)
    env = os.environ.copy()
    for key in list(env):
        if key.endswith(('_API_KEY','_AUTH_TOKEN','_OAUTH_TOKEN')): env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # First run the original regression bodies against the candidate app. New
    # tests participate too. Changed existing tests cannot erase a failing check.
    command=[str(root/'.venv/Scripts/python.exe'),'-B','-m','unittest','discover','-s','tests','-v']
    with (path/'tests.log').open('w',encoding='utf-8') as log:
        proc=subprocess.run(command,cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240,creationflags=HIDDEN)
        changed_tests=[n for n in review['candidate_hashes'] if n.startswith('tests/') and n in review['snapshot_hashes']]
        if not proc.returncode and changed_tests:
            for name in changed_tests:shutil.copyfile(safe_path(path/'candidate',name),safe_path(work,name))
            log.write('\nChecking the updated test suite as well.\n');log.flush()
            proc=subprocess.run(command,cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240,creationflags=HIDDEN)
    if proc.returncode: raise ValueError('Regression tests failed. No live files changed. Review the test report before requesting a new draft.')
    return 'Regression tests passed in a separate source copy.'


def prepare_backup(root,path,review):
    for name, expected in review['base_hashes'].items():
        if expected is not None:
            dest = safe_path(path/'backup',name)
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(safe_path(root,name),dest)
            if digest(dest) != expected: raise ValueError('Backup verification failed. No update was installed.')


def replace_bytes(target, data):
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary = target.with_name(target.name+'.andrew-update')
    temporary.write_bytes(data)
    os.replace(temporary,target)


def install_files(root,path,review):
    for name in review['candidate_hashes']:
        replace_bytes(safe_path(root,name),safe_path(path/'candidate',name).read_bytes())


def restore_files(root,path,review):
    # Validate every backup before beginning any restoration.
    for name, expected in review['base_hashes'].items():
        if not editable(name): raise ValueError('Invalid rollback path.')
        if expected is not None and digest(safe_path(path/'backup',name)) != expected:
            raise ValueError('Backup verification failed; manual recovery is needed.')
    for name, expected in review['base_hashes'].items():
        target = safe_path(root,name)
        if expected is None:
            target.unlink(missing_ok=True)
        else:
            replace_bytes(target,safe_path(path/'backup',name).read_bytes())


def verify_installed(root,review):
    for name, expected in review['candidate_hashes'].items():
        if digest(safe_path(root,name)) != expected:
            raise ValueError('Code has changed since this update. Undo newer changes first; this rollback would overwrite them.')


def stop_server(root,pid):
    process = psutil.Process(pid)
    expected = (root/'server.py').resolve()
    if not any(Path(arg).resolve()==expected for arg in process.cmdline()[1:] if arg.endswith('server.py')):
        raise ValueError('The server process changed. No unrelated application was stopped.')
    process.terminate()
    process.wait(timeout=15)


def start_server(root):
    with (root/'data/server.log').open('a',encoding='utf-8') as out, (root/'data/server-error.log').open('a',encoding='utf-8') as err:
        return subprocess.Popen([str(root/'.venv/Scripts/pythonw.exe'),str(root/'server.py')],cwd=root,
            stdout=out,stderr=err,stdin=subprocess.DEVNULL,creationflags=HIDDEN)


def healthy(process):
    for _ in range(60):
        if process.poll() is not None: return False
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=1) as response:
                result = json.load(response)
            # Windows venv pythonw is a launcher; the serving interpreter is its child.
            if result.get('ready'):
                actual=psutil.Process(int(result['pid']))
                if actual.pid==process.pid or process.pid in [p.pid for p in actual.parents()]:
                    return True
        except Exception: pass
        time.sleep(1)
    return False


def stop_started(process):
    try:
        parent=psutil.Process(process.pid)
        children=parent.children(recursive=True)
        for child in reversed(children):
            try:child.terminate()
            except psutil.NoSuchProcess:pass
        psutil.wait_procs(children,timeout=5)
        if parent.is_running():parent.terminate()
        process.wait(timeout=10)
    except psutil.NoSuchProcess:pass


def execute(action,path,pid,root=ROOT):
    review = read_json(path/'review.json')
    update(path,installer_pid=os.getpid())
    stopped = False
    changed = False
    process = None
    try:
        if action=='install':
            verify_base(root,review)
            verify_candidate(path,review)
            validation = run_tests(root,path,review)
            verify_base(root,review)
            verify_candidate(path,review)
            prepare_backup(root,path,review)
            update(path,status='installing',validation=validation,message='Tests passed. Installing the small change.')
        elif action=='rollback':
            verify_installed(root,review)
        else:
            raise ValueError('Unknown update action.')
        time.sleep(2)  # Let the command acknowledgement reach its speaker/browser.
        stop_server(root,pid)
        stopped = True
        if action=='install':
            changed = True
            install_files(root,path,review)
        else:
            restore_files(root,path,review)
            changed = True
        update(path,status='restarting',live_files_changed=action=='install',message='Reconnecting and checking startup.')
        process = start_server(root)
        if not healthy(process): raise ValueError('Andrew did not start successfully with the changed code.')
        update(path,message='Host startup checked. Finishing device and website updates.')
        if action=='install':
            try:
                request=urllib.request.Request('http://127.0.0.1:8765/api/pi-update',data=b'{}',
                    headers={'Content-Type':'application/json','X-Andrew-Local':'1','Origin':'http://127.0.0.1:8765'},method='POST')
                with urllib.request.urlopen(request,timeout=3) as response:json.load(response)
            except Exception:pass
        try:
            from web_sync import sync
            deployment=sync(root,list(review['candidate_hashes']))
        except Exception as exc:
            deployment='Host update completed. Website deployment needs attention: '+str(exc)[:600]
        update(path,status='installed' if action=='install' else 'rolled_back',deployment_message=deployment,
            live_files_changed=action=='install',message='Installed and startup checked. Undo is available.' if action=='install' else 'Previous code restored and startup checked.')
    except Exception as exc:
        detail = str(exc)[:1200] if isinstance(exc,ValueError) else 'The update process could not complete.'
        if stopped:
            try:
                if process and process.poll() is None:
                    stop_started(process)
                if changed and action=='install': restore_files(root,path,review)
                restored = start_server(root)
                if not healthy(restored): raise ValueError('The previous version also needs startup attention.')
                detail += ' The previous code is running.'
            except Exception:
                update(path,status='recovery_needed',message=detail+' Automatic recovery needs attention.',live_files_changed=changed)
                return
        update(path,status='rolled_back' if stopped else ('installed' if action=='rollback' else 'failed'),
            live_files_changed=action=='rollback' and not stopped,message=detail)


if __name__ == '__main__':
    action, reference, pid = sys.argv[1:]
    if action not in ('install','rollback') or not re.fullmatch(ID_PATTERN,reference):
        raise SystemExit('Invalid update command')
    execute(action,safe_path(ROOT/'data/improvements',reference),int(pid))
