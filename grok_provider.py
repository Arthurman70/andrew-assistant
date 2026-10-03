"""Official Grok subscription client. No API key or UI scraping."""
import json
import os
from pathlib import Path
import re
import subprocess
import time
import threading
import tempfile
import shutil
from contextlib import contextmanager

ROOT=Path(__file__).resolve().parent
EXE=next((p for p in [ROOT/'runtime/grok/grok.exe',Path.home()/'.grok/bin/grok.exe',Path(shutil.which('grok') or 'missing-grok')] if p.is_file()),ROOT/'runtime/grok/grok.exe')
HEALTH_LOCK=threading.Lock()
HEALTH={'state':'unknown','message':''}
CONNECTION={'state':'unknown','message':'Grok connection has not been checked.'}
RESPONSE_ONLY=('You are the response component of Andrew. Return the requested answer or JSON '
    'directly in your final response. No built-in tools are available. Andrew handles device '
    'actions separately; when asked for an action plan, return the requested JSON without '
    'executing it. Do not claim to have performed actions.')
RECOVERY=('The previous attempt ended without a completed answer. Answer the original request '
    'directly, without invoking tools, delegating, or inspecting files. Use the supplied text '
    'and any attached images. Preserve the requested output format, including JSON when '
    'requested. If necessary information is missing, explain that or ask a brief question.\n\n')


class GrokError(RuntimeError):
    """A safe, user-facing failure, never a raw credential-bearing CLI error."""


def health():
    with HEALTH_LOCK: return dict(HEALTH)


def record(state,message,model,started,attempts):
    value={'state':state,'message':message,'model':model or 'account default',
           'at':time.time(),'seconds':round(time.monotonic()-started,2),'attempts':attempts}
    with HEALTH_LOCK: HEALTH.update(value)
    # Metadata only: never prompts, transcripts, answers, stderr, or credentials.
    try: (ROOT/'data/grok-health.json').write_text(json.dumps(value),encoding='utf-8')
    except OSError: pass


def failure_kind(output):
    low=output.lower()
    if any(word in low for word in ('rate limit','rate_limit','too many requests','quota','usage limit','resource_exhausted','429')):
        return 'limit','Your Grok account has reached a usage limit. Wait for it to reset, or ask me to switch to another connected model.'
    if any(word in low for word in ('not authenticated','unauthenticated','unauthorized','401','token expired','invalid token','please log in','login required')):
        return 'auth','Grok needs a fresh account sign-in. Use Connect Grok account, finish signing in, then Refresh.'
    if any(word in low for word in ('model not found','unknown model','invalid model','model_not_found','model is not available')):
        return 'model','That Grok model is unavailable. Choose another Grok model in the app.'
    if 'max turns reached' in low or 'max_turn_requests' in low:
        return 'format','Grok could not complete an answer after an automatic retry. Please ask again or choose another connected model.'
    if any(word in low for word in ('timed out','timeout','connection reset','connection refused','network','temporarily unavailable','service unavailable','502','503','504','overloaded','transport error','connection closed')):
        return 'temporary','Grok is temporarily unreachable. Your account may still be signed in. Please try the question again.'
    return 'failed','Grok could not finish that request. Try again or choose another Grok model; your local commands still work.'

def environment():
    env=os.environ.copy()
    for key in ('XAI_API_KEY','GROK_API_KEY','GROK_DEPLOYMENT_KEY'): env.pop(key,None)
    return env

def status():
    if not EXE.exists():
        CONNECTION.update(state='missing',message='The Grok subscription client is not installed.')
        return False,[]
    try:
        result=subprocess.run([str(EXE),'models'],capture_output=True,text=True,encoding='utf-8',
            env=environment(),timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
        kind,message=failure_kind(result.stdout+'\n'+result.stderr)
        ready=result.returncode==0 and 'Available models:' in result.stdout and kind!='auth'
        models=[{'id':'','label':'Grok account default'}]
        models.extend({'id':m,'label':m} for m in re.findall(r'^\s*[*-]\s+(grok-[\w.-]+)',result.stdout,re.M))
        if ready:CONNECTION.update(state='ready',message='Uses your signed-in Grok subscription')
        else:
            if kind=='failed':kind,message='temporary','Grok model discovery did not finish. Please try switching again; sign-in status is not confirmed.'
            CONNECTION.update(state=kind,message=message)
        return ready,models
    except (OSError,subprocess.TimeoutExpired):
        CONNECTION.update(state='temporary',message='Grok model discovery timed out. Please try switching again; this does not mean you are signed out.')
        return False,[]

def chat(system,prompt,model='',timeout=45,images=None,response_schema=None):
    blocks=None
    if images:
        import base64
        blocks=[{'type':'text','text':prompt}]+[{'type':'image','mimeType':'image/jpeg',
            'data':base64.b64encode(Path(p).read_bytes()).decode()} for p in images]
    return _chat(system,prompt,model,blocks=blocks,timeout=timeout,response_schema=response_schema)


@contextmanager
def prompt_args(system,prompt,blocks,recover):
    """Keep attachments identical on retry; remove all transient prompt files."""
    prefix=RECOVERY if recover else ''
    path=None
    try:
        if blocks is not None:
            content=([{'type':'text','text':prefix}]+blocks) if prefix else blocks
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',
                    prefix='andrew-vision-',dir=ROOT/'data',delete=False) as file:
                path=Path(file.name)
                json.dump(content,file)
        # Native Windows command lines are capped at 32K UTF-16 characters.
        elif len(system)+len(prompt)+len(prefix)>10000:
            with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.txt',
                    prefix='andrew-request-',dir=ROOT/'data',delete=False) as file:
                path=Path(file.name)
                file.write(prefix+prompt)
        yield ['--prompt-file',str(path)] if path else ['-p',prefix+prompt]
    finally:
        if path: path.unlink(missing_ok=True)


def _chat(system,prompt,model='',blocks=None,timeout=45,response_schema=None):
    work=ROOT/'data/ai-workspace';work.mkdir(exist_ok=True)
    system=RESPONSE_ONLY+'\n\n'+system
    # Grok 1.0.44 treats --tools '' as inheritance, and unknown names also
    # restore default tools. Allow a KNOWN tool, then remove it plus the MCP
    # meta-tools. Verified with the real CLI: init.tools == []. Keep deny-all
    # permissions too. Andrew's own bounded PC action planner is unaffected.
    args=[str(EXE),'--cwd',str(work),'--tools','read_file',
          '--disallowed-tools','read_file,search_tool,use_tool','--deny','*',
          '--permission-mode','dontAsk','--no-plan','--no-subagents',
          '--disable-web-search','--max-turns','1','--system-prompt-override',system,
          '--verbatim','--output-format','json']
    if model: args.extend(['--model',model])
    if response_schema is not None:
        args.extend(['--json-schema',json.dumps(response_schema,separators=(',',':'))])
    started=time.monotonic()
    recover=False
    for attempt in (1,2):
        try:
            with prompt_args(system,prompt,blocks,recover) as request_args:
                result=subprocess.run(args+request_args,capture_output=True,text=True,encoding='utf-8',errors='replace',
                    env=environment(),timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                kind,message=failure_kind(result.stderr+'\n'+result.stdout)
            else:
                try:
                    value=json.loads(result.stdout.strip().lstrip('\ufeff'))
                    if not isinstance(value,dict): raise ValueError('Not an answer')
                    if value.get('type')=='error':
                        kind,message=failure_kind(str(value.get('message','')))
                    elif value.get('stopReason')=='max_turn_requests':
                        kind,message=failure_kind('max turns reached')
                    else:
                        if value.get('stopReason') not in (None,'end_turn','refusal'):
                            raise ValueError('Unfinished answer')
                        answer=value['text']
                        if not isinstance(answer,str) or not answer.strip(): raise ValueError('Empty answer')
                        record('ready','Grok answered successfully.',model,started,attempt)
                        return answer.strip()
                except (ValueError,KeyError,TypeError):
                    kind,message='response','Grok returned an incomplete answer. Please try again.'
        except subprocess.TimeoutExpired:
            kind,message='temporary','Grok took too long to answer. Your account is still selected; please try a shorter question.'
        except OSError:
            kind,message='client','The Grok client could not start. Andrew needs its Grok connection repaired.'
        if kind in ('temporary','format') and attempt==1:
            recover=kind=='format'
            if kind=='temporary': time.sleep(.5)
            continue
        record(kind,message,model,started,attempt)
        raise GrokError(message)
