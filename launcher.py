"""Manual Windows startup/recovery, independent of Andrew's web server."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser
import psutil

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8765'


class LaunchError(RuntimeError):
    pass


def health():
    try:
        with urllib.request.urlopen(URL + '/api/status', timeout=2) as response:
            value = json.load(response)
        return value if isinstance(value, dict) and 'pc_voice' in value and 'name' in value else None
    except (OSError, ValueError):
        return None


def owned_processes(root, script):
    """Never trust a saved PID: match the actual script and interpreter instead."""
    target = os.path.normcase(str((root / script).resolve()))
    found = []
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            if process.info['name'].lower() not in ('python.exe', 'pythonw.exe'):
                continue
            args = process.info['cmdline'] or []
            if len(args) > 1 and os.path.normcase(args[1].strip('"')) == target:
                found.append(process)
        except (psutil.Error, AttributeError):
            continue
    return found


def spawn(root, script, log_name=None, extra=(), env=None):
    python = root / '.venv/Scripts/pythonw.exe'
    if not python.is_file():
        raise LaunchError('Andrew\'s Python runtime is missing. Run the Andrew installer to repair it.')
    stem = log_name or Path(script).stem
    if env is None:
        env = dict(os.environ, OLLAMA_MODELS=str(root / 'runtime/models'),
                   OLLAMA_HOST='127.0.0.1:11434', OLLAMA_NO_CLOUD='1')
    # Preserve the error that caused the previous outage.
    with (root / 'data' / (stem + '.log')).open('ab') as out, \
            (root / 'data' / (stem + '-error.log')).open('ab') as err:
        return subprocess.Popen([str(python), str(root / script), *extra], cwd=root,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, env=env,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def wait_ready(seconds, child=None):
    end = time.monotonic() + seconds
    while True:
        state = health()
        if state is not None:
            return state
        if child is not None and child.poll() is not None:
            raise LaunchError('Andrew could not start. Details are in data/server-error.log. '
                              'Your settings and memories have been kept.')
        if time.monotonic() >= end:
            return None
        time.sleep(.5)


def stop_stuck(root):
    # No browser, companion, AI client or unrelated Python process is stopped.
    processes = owned_processes(root, 'server.py')
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes, timeout=4)
    if alive:
        raise LaunchError('Andrew is still closing. Wait a few seconds and run Repair Andrew again.')


def ensure_server(root, repair=False):
    state = health()
    if state is not None:
        return state, False
    if owned_processes(root, 'server.py'):
        print('Waiting for the existing Andrew service...', flush=True)
        state = wait_ready(10)
        if state is not None:
            return state, False
        if not repair:
            raise LaunchError('Andrew is not responding. Open Repair Andrew to restart the stuck service.')
        print('Restarting the unresponsive Andrew service...', flush=True)
        stop_stuck(root)
    print('Starting Andrew. Speech models may take a moment to load...', flush=True)
    child = spawn(root, 'server.py', 'server')
    state = wait_ready(60, child)
    if state is None:
        raise LaunchError('Andrew did not become ready within a minute. Open Repair Andrew to retry. '
                          'Details are in data/server-error.log.')
    return state, True


def ensure_bridge(root):
    config_path = root / 'data/web-bridge.json'
    if not config_path.is_file():
        return 'Website connection is not configured.'
    processes = owned_processes(root, 'bridge_runner.py')
    child = None if processes else spawn(root, 'bridge_runner.py', 'web-bridge')
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        port = int(config.get('port', 8780))
        request = urllib.request.Request('http://127.0.0.1:' + str(port) + '/api/status',
            headers={'Authorization': 'Bearer ' + config['bridge_token']})
        end = time.monotonic() + 8
        while time.monotonic() < end:
            try:
                with urllib.request.urlopen(request, timeout=2) as response:
                    result = json.load(response)
                if isinstance(result, dict) and 'pc_voice' in result:
                    return 'Website bridge is running; its secure tunnel reconnects automatically.'
            except (OSError, ValueError):
                pass
            if child is not None and child.poll() is not None:
                break
            time.sleep(.5)
    except (OSError, ValueError, KeyError):
        pass
    return 'Andrew works locally. Website connection needs attention; see data/web-bridge-error.log.'


def ensure_ollama(root):
    executable = root / 'runtime/ollama/ollama.exe'
    if not executable.is_file():
        return
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=1):
            return
    except OSError:
        pass
    env = dict(os.environ, OLLAMA_MODELS=str(root / 'runtime/models'),
               OLLAMA_HOST='127.0.0.1:11434', OLLAMA_NO_CLOUD='1')
    with (root / 'data/ollama.log').open('ab') as out, (root / 'data/ollama-error.log').open('ab') as err:
        subprocess.Popen([str(executable), 'serve'], cwd=root, env=env,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


@contextmanager
def launch_lock(root):
    # The OS releases this lock even if the launcher crashes.
    import msvcrt
    with (root / 'data/launcher.lock').open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        end = time.monotonic() + 85
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise LaunchError('Another Andrew startup is still running. Please wait and try again.')
                time.sleep(.5)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def open_app():
    edge = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe'
    if edge.is_file():
        subprocess.Popen([str(edge), '--app=' + URL + '/'],
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        webbrowser.open(URL + '/')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repair', action='store_true')
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--bridge-only', action='store_true')
    args = parser.parse_args()
    (ROOT / 'data').mkdir(exist_ok=True)
    try:
        with launch_lock(ROOT):
            if args.bridge_only:
                print(ensure_bridge(ROOT), flush=True)
                return 0
            state, started = ensure_server(ROOT, args.repair)
            try:
                ensure_ollama(ROOT)
            except OSError:
                print('Optional local AI engine needs attention. Basic commands still work.', flush=True)
            try:
                print(ensure_bridge(ROOT), flush=True)
            except OSError:
                print('Website bridge could not start. Andrew is available locally.', flush=True)
        name = state.get('name') or 'Andrew'
        print(name + (' is now on.' if started else ' is already on.'), flush=True)
        state = health() or state
        if state.get('pc_voice', {}).get('ready'):
            print('PC microphone is listening. Say "Hey ' + name + '".', flush=True)
        else:
            print('If voice is unavailable, check Settings > Microphone. You can type requests now.', flush=True)
        if not state.get('relay_online'):
            print('Pi is not connected yet; it reconnects automatically when available.', flush=True)
        (ROOT / 'data/launcher-status.txt').write_text(
            time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + name + ' is on.\n', encoding='utf-8')
        if not args.background:
            open_app()
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, LaunchError) else 'Startup failed. Check the saved launcher error log.'
        (ROOT / 'data/launcher-status.txt').write_text(
            time.strftime('%Y-%m-%d %H:%M:%S') + '\n' + message + '\n', encoding='utf-8')
        with (ROOT / 'data/launcher-error.log').open('a', encoding='utf-8') as handle:
            import traceback
            traceback.print_exc(file=handle)
        print('\n' + message, file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
