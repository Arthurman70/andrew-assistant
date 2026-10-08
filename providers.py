"""Subscription-backed provider discovery and validated, persistent selection."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
CACHE = {}

def codex_executable():
    found = shutil.which('codex')
    if found: return found
    candidates = list((Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
    return str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None

def catalog(force=False):
    from core import request_json
    with LOCK:
        if not force and time.time()-CACHE.get('at', 0) < 30: return CACHE['data']
        providers = []
        try:
            tags = request_json('http://127.0.0.1:11434/api/tags', timeout=3)
            local = [{'id': m['name'], 'label': m['name']+(' · fast' if m['name']=='qwen3:0.6b' else '')}
                     for m in tags.get('models', []) if m['name'] != 'qwen3:4b']
            providers.append({'id':'local','label':'On this PC','ready':bool(local),'models':local,'detail':'Local · no per-request charge'})
        except Exception:
            providers.append({'id':'local','label':'On this PC','ready':False,'models':[], 'detail':'Local model service is reconnecting.'})
        exe = codex_executable()
        ready = False
        if exe:
            try:
                check = subprocess.run([exe,'login','status'], capture_output=True,text=True,timeout=15,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                ready = 'using ChatGPT' in check.stdout+check.stderr
            except (OSError, subprocess.TimeoutExpired): pass
        models = [{'id':'','label':'Account default'}]
        cache = Path.home()/'.codex/models_cache.json'
        if cache.exists():
            try:
                models += [{'id':m['slug'],'label':m.get('display_name',m['slug'])}
                           for m in json.loads(cache.read_text(encoding='utf-8-sig')).get('models',[])
                           if m.get('slug') and m['slug'] != 'codex-auto-review']
            except (ValueError, OSError): pass
        providers.append({'id':'openai','label':'OpenAI / ChatGPT','ready':ready,'models':models,
                          'detail':'Uses your ChatGPT account' if ready else 'ChatGPT account sign-in needed.'})
        from claude_provider import status as claude_status, MODELS
        connection = claude_status()
        providers.append({'id':'claude','label':'Claude', **connection, 'models':MODELS,
                          'custom_models':True})
        from grok_provider import status, CONNECTION
        ready,models=status()
        providers.append({'id':'grok','label':'Grok','ready':ready,'models':models,
            'detail':'Uses your signed-in Grok subscription' if ready else CONNECTION['message'],
            'retryable':not ready and CONNECTION['state']=='temporary',
            'url':'https://grok.com'})
        CACHE.update(at=time.time(),data=providers)
        return providers

def resolve(app, provider=None, model=None):
    provider = provider or app.get('provider')
    provider = {'open ai':'openai','chatgpt':'openai','chat gpt':'openai','on this pc':'local'}.get(provider.lower(),provider.lower())
    entry = next((p for p in catalog() if p['id']==provider),None)
    if not entry: raise ValueError('Choose Local, OpenAI, Claude, or Grok.')
    if not entry['ready'] and entry.get('retryable'):
        # Retry a transient discovery failure once when the user selects it.
        # A cached startup timeout must not demand another account sign-in.
        entry = next((p for p in catalog(force=True) if p['id']==provider),entry)
    if not entry['ready']: raise ValueError(entry['detail'])
    if model is not None:
        if provider=='claude':
            from claude_provider import model_id
            model=model_id(model)
        if provider == 'local' and model == 'qwen3:4b': model = 'qwen3:4b-instruct'
        def spoken_key(value):
            for word, digit in {'zero':'0','one':'1','two':'2','three':'3','four':'4','five':'5','six':'6','seven':'7','eight':'8','nine':'9'}.items():
                value = re.sub(r'\b'+word+r'\b',digit,value.lower())
            return re.sub(r'[^a-z0-9]','',value.lower())
        target = spoken_key(model)
        if provider=='grok' and model in ('fast','fast model'):
            target=spoken_key('grok-4.7-build-fast')
        equivalent = next((m['id'] for m in entry['models'] if target in (spoken_key(m['id']),spoken_key(m['label']))),None)
        if equivalent is not None: model = equivalent
        if model and not re.fullmatch(r'[\w.:-]{1,100}',model): raise ValueError('Choose a model identifier.')
        if provider in ('local','openai','grok') and model not in [m['id'] for m in entry['models']]:
            raise ValueError('That model is not available in the connected account or local installation.')
    return provider, (app.get(provider+'_model') if model is None else model) or '', entry


def choose(app, provider, model=None):
    provider, selected, entry = resolve(app,provider,model)
    app.set(provider+'_model',selected)
    app.set('provider',provider)
    selected = selected or 'account default'
    return f'Using {entry["label"]}, {selected}. Everyday commands still run locally.'
