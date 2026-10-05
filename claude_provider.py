"""Claude subscription transport; no API-key fallback or model tool execution."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent
CREATE_HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
LOGIN_LOCK = threading.Lock()
LOGIN_PROCESS = None
AUTH_CACHE={'at':0,'value':None}
MODELS = [{'id': '', 'label': 'Claude account default'},
          {'id': 'haiku', 'label': 'Haiku · quicker replies'},
          {'id': 'sonnet', 'label': 'Sonnet · balanced'},
          {'id': 'opus', 'label': 'Opus · complex work'}]


class ClaudeError(ValueError):
    pass


def executable():
    bundled = ROOT / 'runtime/claude-cli/node_modules/@anthropic-ai/claude-code-win32-x64/claude.exe'
    return str(bundled) if bundled.is_file() else shutil.which('claude')


def environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(('ANTHROPIC_', 'CLAUDE_CODE_USE_')) or key in (
                'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR'):
            env.pop(key, None)
    env['CLAUDE_CODE_SKIP_PROMPT_HISTORY'] = '1'
    return env


def status(refresh=False):
    if not refresh and AUTH_CACHE['value'] and time.monotonic()-AUTH_CACHE['at']<60:return dict(AUTH_CACHE['value'])
    exe = executable()
    if not exe:
        return {'ready': False, 'state': 'missing', 'detail': 'Claude Code needs to be installed.'}
    try:
        result = subprocess.run([exe, 'auth', 'status'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', env=environment(), timeout=15, creationflags=CREATE_HIDDEN)
        auth = json.loads(result.stdout.lstrip('\ufeff'))
        ready = bool(auth.get('loggedIn') and auth.get('authMethod') == 'claude.ai')
        if ready:
            result={'ready': True, 'state': 'ready', 'detail': 'Connected through your Claude subscription.'}
            AUTH_CACHE.update(at=time.monotonic(),value=result);return result
        return {'ready': False, 'state': 'sign_in',
                'detail': 'Connect your Claude subscription. Signing into the Claude desktop app alone does not connect Andrew.'}
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {'ready': False, 'state': 'unreachable',
                'detail': 'The Claude client could not report its connection. Try Refresh; this is not necessarily a sign-in problem.'}


def connect():
    global LOGIN_PROCESS
    with LOGIN_LOCK:
        AUTH_CACHE.update(at=0,value=None)
        if LOGIN_PROCESS is not None and LOGIN_PROCESS.poll() is None:
            return 'Claude sign-in is already open. Finish it in your browser, then refresh the connection.'
        exe = executable()
        if not exe:
            raise ClaudeError('Claude Code is not installed.')
        # The official client opens the browser. No OAuth codes or URLs are logged by Andrew.
        LOGIN_PROCESS = subprocess.Popen([exe, 'auth', 'login', '--claudeai'], env=environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_HIDDEN)
        return 'Finish the official Claude subscription sign-in in your browser, then refresh the connection.'


def failure_kind(text):
    low = text.lower()
    if any(s in low for s in ('429', 'rate limit', 'usage limit', 'limit reached', 'hit your limit', 'quota')):
        return 'quota', 'Claude has reached an account usage limit. Wait for it to reset or choose another connected model.'
    if any(s in low for s in ('401', 'not logged in', 'authentication', 'unauthorized', 'token expired', 'please run /login')):
        return 'auth', 'Claude needs a fresh subscription sign-in. Use Connect Claude account in the app.'
    if any(s in low for s in ('model_not_found', 'invalid model', 'model is not available', 'does not exist', 'access to model')):
        return 'model', 'That Claude model is unavailable to your account. Try Haiku, Sonnet, Opus, or the account default.'
    if any(s in low for s in ('503', '502', '504', 'overloaded', 'timed out', 'timeout', 'network', 'connection', 'temporarily')):
        return 'temporary', 'Claude is temporarily unreachable. Your account may still be signed in; please try again.'
    return 'failed', 'Claude could not finish this request. Try again or select another model. No API billing fallback was used.'


def record(kind, detail, model):
    try:
        (ROOT / 'data/claude-health.json').write_text(json.dumps({
            'state': kind, 'detail': detail, 'model': model, 'at': time.time()}), encoding='utf-8')
    except OSError:
        pass


def chat(system, prompt, model='', timeout=150, images=None, retry=True):
    connection = status()
    if not connection['ready']:
        raise ClaudeError(connection['detail'])
    work = ROOT / 'data/ai-workspace'
    work.mkdir(parents=True, exist_ok=True)
    args = [executable(), '-p', '--output-format', 'json', '--tools', '',
            '--setting-sources', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--no-session-persistence', '--max-turns', '1', '--system-prompt', system]
    if model:
        args += ['--model', model]
    if images:
        import base64
        args[args.index('--output-format')+1]='stream-json'
        args += ['--input-format','stream-json','--verbose']
        content=[{'type':'text','text':prompt}]+[{'type':'image','source':{'type':'base64',
            'media_type':'image/jpeg','data':base64.b64encode(Path(p).read_bytes()).decode()}} for p in images]
        prompt=json.dumps({'type':'user','message':{'role':'user','content':content}})+'\n'
    for attempt in ((1,2) if retry else (1,)):
        try:
            proc = subprocess.run(args, input=prompt, cwd=work, env=environment(),
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
                creationflags=CREATE_HIDDEN)
            try:
                if images:
                    events=[json.loads(line) for line in proc.stdout.strip().lstrip('\ufeff').splitlines() if line.strip()]
                    payload=next((event for event in reversed(events) if event.get('type')=='result'),{})
                else:
                    payload = json.loads(proc.stdout.strip().lstrip('\ufeff'))
            except ValueError:
                payload = {}
            if proc.returncode or payload.get('is_error'):
                kind, detail = failure_kind(proc.stderr + '\n' + proc.stdout)
            elif isinstance(payload.get('result'), str) and payload['result'].strip():
                record('ready', 'Claude answered successfully.', model)
                return payload['result'].strip()
            else:
                kind, detail = 'response', 'Claude returned an incomplete answer. Please try again.'
        except subprocess.TimeoutExpired:
            kind, detail = 'temporary', 'Claude took too long to answer. Try a smaller request or another model.'
        except OSError:
            kind, detail = 'client', 'The Claude client could not start. Its connection needs repair.'
        if retry and kind == 'temporary' and attempt == 1:
            time.sleep(.5)
            continue
        if kind=='auth':AUTH_CACHE.update(at=0,value=None)
        record(kind, detail, model)
        raise ClaudeError(detail)
