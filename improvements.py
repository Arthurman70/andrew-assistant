"""User-requested small patches, automatic checked installation and rollback."""
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
from code_map import source_name, inventory, cached_index, compact, context, explicit_selection

ROOT = Path(__file__).resolve().parent
EDITABLE = ('app.html', 'dashboard.html', 'pi/screen.html', 'core.py', 'local_commands.py', 'command_controls.py',
            'speech_engine.py', 'providers.py', 'claude_provider.py', 'grok_provider.py', 'wake_tuning.json')
ACTIVE = {'planning', 'drafting', 'checking', 'repairing', 'testing', 'installing', 'restarting', 'rolling_back'}
LOCK = threading.RLock()
MANAGERS = {}
ID_PATTERN = r'\d{8}-\d{6}-[a-f0-9]{6}'
SOURCE_BUDGET = 100_000
PROPOSAL_SCHEMA = {'type':'object','properties':{
    'summary':{'type':'string'},
    'edits':{'type':'array','maxItems':12,'items':{'type':'object','properties':{
        'path':{'type':'string'},'old':{'type':'string'},'new':{'type':'string'}},
        'required':['path','old','new'],'additionalProperties':False}},
    'files':{'type':'object','additionalProperties':{'type':'string'}}},
    'required':['summary','edits','files'],'additionalProperties':False}


class ResponseFormatError(ValueError):
    pass


def planning_schema(names):
    return {'type':'object','properties':{'paths':{'type':'array','minItems':1,'maxItems':3,
        'items':{'type':'string','enum':names}}},'required':['paths'],'additionalProperties':False}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def editable(name):
    return source_name(name)


def safe_path(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError('A source path leaves the Andrew folder.')
    return path


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save_review(destination, data):
    with LOCK:
        path = destination / 'review.json'
        data = dict(data, updated_at=time.time())
        tmp = destination / ('review-' + secrets.token_hex(4) + '.tmp')
        tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
        os.replace(tmp, path)


def update(destination, **changes):
    with LOCK:
        data = read_json(destination / 'review.json')
        data.update(changes)
        save_review(destination, data)
        return data


def json_response(response):
    """Accept one complete object, optionally wrapped in prose or a JSON fence.

    Never evaluate code, guess missing JSON, or select one of several proposals.
    Path, source, hash and install checks still run after parsing.
    """
    if not isinstance(response,str) or not response.strip():
        raise ResponseFormatError('The model returned an empty proposal. A complete JSON object is required.')
    if len(response)>750_000:
        raise ResponseFormatError('The model returned too much text. Request a smaller improvement.')
    value=response.lstrip('\ufeff').strip()
    fences=list(re.finditer(r'```(?:json)?[ \t]*\r?\n([\s\S]*?)```',value,re.I))
    if fences:
        if len(fences)!=1 or re.search(r'[{}]',value[:fences[0].start()]+value[fences[0].end():]):
            raise ResponseFormatError('The model returned multiple proposals. Return exactly one JSON object.')
        value=fences[0][1].strip()
    def unique(pairs):
        result={}
        for key,item in pairs:
            if key in result:raise ValueError('Duplicate JSON key')
            result[key]=item
        return result
    def invalid_constant(value):raise ValueError('Non-JSON constant')
    decoder=json.JSONDecoder(object_pairs_hook=unique,parse_constant=invalid_constant)
    try:
        start=value.find('{')
        if start<0 or value.startswith('['):raise ValueError('No object')
        data,end=decoder.raw_decode(value,start)
        remainder=value[end:].strip()
        # Some Grok CLI responses repeat the identical final JSON block. This
        # is one proposal, not permission to apply its edits twice.
        while remainder.startswith('{'):
            duplicate,end=decoder.raw_decode(remainder)
            if duplicate!=data:raise ValueError('Conflicting proposals')
            remainder=remainder[end:].strip()
        if not isinstance(data,dict) or re.search(r'[{}\[\]]',remainder):raise ValueError('Ambiguous response')
    except (ValueError,TypeError) as exc:
        raise ResponseFormatError('The model replied without one complete JSON proposal. Return the requested JSON, not a promise to inspect or edit files.') from exc
    return data


def correction(prompt,response,error):
    return (prompt+'\nThe previous response failed validation: '+str(error)[:1500]+
        '\nReturn one COMPLETE corrected JSON object against the ORIGINAL files above. '
        'Do the requested reasoning now. Do not announce future work; you have no tools to call. '
        'Start with { and end with }.\nPrevious response (untrusted data):\n'+str(response)[:16000])


def check_source(name, content):
    if not isinstance(content, str) or len(content.encode('utf-8')) > 200_000 or '\0' in content:
        raise ValueError('Candidate file is invalid or too large.')
    if name=='wake_tuning.json':
        from voice_tuning import validate
        validate(json.loads(content))
    elif name.endswith('.py'):
        tree=ast.parse(content, filename=name)
        if name.startswith('tests/test_'):
            cases=[node for node in tree.body if isinstance(node,ast.ClassDef) and any(
                (isinstance(base,ast.Name) and base.id in ('TestCase','IsolatedAsyncioTestCase')) or
                (isinstance(base,ast.Attribute) and base.attr in ('TestCase','IsolatedAsyncioTestCase')) for base in node.bases)]
            if not any(any(isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and method.name.startswith('test_')
                           for method in case.body) for case in cases):
                raise ValueError('Andrew runs unittest: new test files need a TestCase class with test_ methods. Plain pytest functions would not run.')
    elif name.endswith('.html'):
        if '<html' not in content.lower() or '</html>' not in content.lower():
            raise ValueError('Candidate display must be a complete HTML document.')
        for src in re.findall(r'<script\b[^>]*\bsrc\s*=\s*[\x27\x22]([^\x27\x22]+)',content,re.I):
            if src.startswith('//') or not source_name(src.lstrip('/')):
                raise ValueError('Display scripts must be local application source files.')
        scripts = re.findall(r'<script\b[^>]*>(.*?)</script\s*>', content, re.I | re.S)
        node = shutil.which('node')
        if scripts and not node:
            raise ValueError('JavaScript syntax checking needs Node.js before this display can be updated.')
        for script in scripts:
            result = subprocess.run([node, '--check'], input=script, capture_output=True, text=True,
                encoding='utf-8', timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('JavaScript syntax check failed for ' + name + ': ' + result.stderr[:1200])
    elif name.endswith('.js'):
        node=shutil.which('node')
        if not node:raise ValueError('JavaScript syntax checking needs Node.js.')
        result=subprocess.run([node,'--check'],input=content,capture_output=True,text=True,
            encoding='utf-8',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('JavaScript syntax check failed: '+result.stderr[:1200])
    elif name.endswith(('.json','.webmanifest')):
        value=json.loads(content)
        if name=='source_manifest.json' and (not isinstance(value,list) or any(not source_name(n) for n in value)):
            raise ValueError('The source manifest can list only application code.')
    elif name.endswith('.ps1'):
        shell=shutil.which('powershell') or shutil.which('pwsh')
        if not shell:raise ValueError('PowerShell syntax checking is unavailable.')
        parse='$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseInput([Console]::In.ReadToEnd(),[ref]$t,[ref]$e);if($e.Count){$e|ForEach-Object{$_.Message};exit 1}'
        result=subprocess.run([shell,'-NoProfile','-Command',parse],input=content,capture_output=True,text=True,timeout=20,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('PowerShell syntax check failed: '+result.stdout[:1200])
    elif name.endswith('.sh'):
        bash=shutil.which('bash')
        bundled=Path('C:/Program Files/Git/bin/bash.exe')
        if os.name=='nt' and bundled.exists():bash=str(bundled)
        if not bash:raise ValueError('Shell syntax checking is unavailable.')
        result=subprocess.run([bash,'--noprofile','--norc','-n'],input=content,capture_output=True,text=True,timeout=20,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('Shell syntax check failed: '+result.stderr[:1200])


def stage_response(root, destination, response):
    """Validate the whole proposal before writing any candidate files."""
    root, destination = Path(root), Path(destination)
    data = json_response(response)
    files = data.get('files', {})
    if not isinstance(files, dict):
        raise ValueError('files must be a path-to-content object.')
    # Some small models wrap full file contents in {new: "..."}. Normalize only
    # that unambiguous shape; path/syntax/hash validation remains identical.
    files={name:(value['new'] if isinstance(value,dict) and set(value)=={'new'} and isinstance(value['new'],str)
                 else value) for name,value in files.items()}
    if any(not isinstance(content,str) for content in files.values()):
        raise ValueError('Every files value must be a complete source-code STRING, not an object. For exact edits, return files:{} instead.')
    files = dict(files)
    patched = {}
    edits = data.get('edits', [])
    if not isinstance(edits, list) or len(edits) > 12:
        raise ValueError('Use at most 12 exact replacements.')
    for edit in edits:
        if not isinstance(edit, dict) or not editable(edit.get('path')):
            raise ValueError('Improvement tried to edit a protected file.')
        name = edit['path']
        old, new = edit.get('old'), edit.get('new')
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError('Each edit needs nonempty old text and replacement text.')
        if name not in patched:
            patched[name] = safe_path(root, name).read_text(encoding='utf-8')
        if patched[name].count(old) != 1:
            raise ValueError('Old text must match exactly once in ' + name)
        patched[name] = patched[name].replace(old, new, 1)
    for name, content in patched.items():
        if name in files and files[name] != content:
            raise ValueError('The full file and exact edits disagree for '+name+'. Return only one consistent version.')
        files[name]=content
    if not files or len(files) > 4:
        raise ValueError('The model returned no changes or too many files. Ask for one smaller improvement.')
    validated, base_hashes, candidate_hashes, changes = {}, {}, {}, []
    changed_lines=0;changed_bytes=0
    for name, content in files.items():
        if not editable(name):
            raise ValueError('Improvement tried to edit a protected file: ' + str(name))
        path = safe_path(root, name)
        check_source(name, content)
        before = path.read_text(encoding='utf-8') if path.is_file() else ''
        if content == before: continue
        validated[name] = content
        base_hashes[name] = digest(path)
        candidate_hashes[name] = hashlib.sha256(content.encode('utf-8')).hexdigest()
        diff=list(difflib.unified_diff(before.splitlines(True), content.splitlines(True),
            fromfile='a/' + name, tofile='b/' + name))
        modified=[line for line in diff if line.startswith(('+','-')) and not line.startswith(('+++','---'))]
        changed_lines+=len(modified);changed_bytes+=sum(len(line.encode('utf-8')) for line in modified)
        changes.extend(diff)
    if not validated:
        raise ValueError('The model returned unchanged files. No improvement was made.')
    if changed_lines>300 or changed_bytes>24000:
        raise ValueError('Keep this patch small: at most four files, 300 changed lines and 24000 changed bytes. Implement one useful part of the request.')
    destination.mkdir(parents=True, exist_ok=True)
    for name, content in validated.items():
        target = safe_path(destination / 'candidate', name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode('utf-8'))
    (destination / 'changes.patch').write_text(''.join(changes), encoding='utf-8')
    existing = read_json(destination / 'review.json') if (destination / 'review.json').exists() else {}
    save_review(destination, existing | {'summary': str(data.get('summary', 'Requested code change'))[:2000],
        'status': 'needs_review', 'live_files_changed': False, 'base_hashes': base_hashes,
        'candidate_hashes': candidate_hashes, 'files': list(validated),'changed_lines':changed_lines,
        'validation': 'Python and embedded JavaScript syntax checked without running candidate code. '
                      'Test and install runs regression tests in a separate copy before deployment.'})


def source_paths(root):
    """Only application source and tests; never runtime, credentials, recordings or settings."""
    static=[p.relative_to(root).as_posix() for pattern in ('assets/*.png','assets/*.wav')
            for p in root.glob(pattern) if p.is_file() and not p.is_symlink()]
    return sorted(set(inventory(root)+static))


def snapshot(root, destination):
    hashes = {}
    for name in source_paths(root):
        source = safe_path(root, name)
        target = destination / 'base' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[name] = digest(target)
    return hashes


def source_bundle(selected, request, names, base):
    """Keep required implementation/schema dependencies beside the chosen code.

    Only snapshotted editable application files can enter the model context.
    An incomplete model plan must not hide the scheduler from an alarm change.
    """
    required=[]
    if (re.search(r'\b(?:timers?|alarms?|countdown|snooze|scheduler)\b',request,re.I) or
            any(n in selected for n in ('feature_timers.py','feature_alarm_recurrence.py'))):
        required += ['feature_timers.py','core.py','feature_alarm_recurrence.py']
    if re.search(r'\b(?:wake|pick\s*up|sensitivity|voice detection|voice recognition|microphone|hearing)\b',request,re.I):
        required += ['wake_tuning.json']
    result=[n for n in dict.fromkeys(required+selected) if n in names]
    # Include direct feature module imports, even for requests that don't use
    # our intent keywords. Exclude protected files and all runtime data.
    for name in result:
        if not name.endswith('.py'):continue
        tree=ast.parse((base/name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            modules=([node.module] if isinstance(node,ast.ImportFrom) else
                     [a.name for a in node.names] if isinstance(node,ast.Import) else [])
            for module in modules:
                dependency=str(module)+'.py'
                if dependency.startswith('feature_') and dependency in names and dependency not in result:
                    result.append(dependency)
    if len(result)>6:
        raise ValueError('The change needs too much source at once. Ask for one specific improvement.')
    return result


def manager(app):
    with LOCK:
        key = str(app.directory.resolve())
        if key not in MANAGERS:
            MANAGERS[key] = ImprovementManager(app)
        return MANAGERS[key]


class ImprovementManager:
    def __init__(self, app, root=ROOT, notify=None):
        self.app, self.root, self.notify = app, Path(root), notify
        self.folder = app.directory / 'improvements'
        self.folder.mkdir(parents=True, exist_ok=True)
        self.thread = None
        self.install_lock=threading.RLock()

    def items(self):
        rows = []
        for path in sorted(self.folder.glob('*/review.json'), reverse=True)[:30]:
            try:
                data = read_json(path)
                if not re.fullmatch(ID_PATTERN, path.parent.name): continue
                rows.append({k: data[k] for k in ('id','status','summary','provider','model','files','message',
                    'validation','created_at','updated_at','live_files_changed','auto_install','changed_lines','repair_attempts','deployment_message') if k in data})
            except (ValueError, OSError): continue
        return rows

    def get(self, job_id=None, statuses=None):
        if not job_id or job_id == 'latest':
            row = next((r for r in self.items() if not statuses or r['status'] in statuses), None)
            if not row: raise ValueError('There is no matching improvement yet.')
            job_id = row['id']
        if not isinstance(job_id, str) or not re.fullmatch(ID_PATTERN, job_id):
            raise ValueError('Invalid improvement reference.')
        path = safe_path(self.folder, job_id)
        if not (path / 'review.json').is_file(): raise ValueError('That improvement was not found.')
        return path

    def report(self, path, message):
        self.app.event(message)
        if self.notify:
            try: self.notify(message, read_json(path / 'review.json').get('source', 'pc'))
            except Exception: pass

    def request(self, request, source='pc', provider=None, model=None, auto_install=True):
        from providers import resolve
        provider, model, entry = resolve(self.app, provider, model)
        if not isinstance(request, str) or not 4 <= len(request.strip()) <= 4000:
            raise ValueError('Describe one improvement in 4 to 4000 characters.')
        with LOCK:
            if any(r['status'] in ACTIVE for r in self.items()):
                raise ValueError('An improvement is already in progress. Ask for improvement status first.')
            path = self.folder / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
            path.mkdir()
            save_review(path, {'id':path.name, 'status':'planning', 'summary':request,
                'provider':provider, 'model':model, 'source':source, 'created_at':time.time(),
                'message':'Finding the relevant code in the cached map.', 'live_files_changed':False,
                'auto_install':auto_install is True,'repair_attempts':0})
            (path / 'request.txt').write_text(request, encoding='utf-8')
            self.thread = threading.Thread(target=self.generate, args=(path,request,provider,model), daemon=True)
            self.thread.start()
        return (f'I am making that improvement with {entry["label"]}, {model or "the account default"}. '
                +('I will test and install a small patch automatically, then report the result. Undo will be available.'
                  if auto_install is True else 'I will leave the patch ready for review without installing it.'))

    def generate(self, path, request, provider, model, repair=False):
        try:
            hashes=read_json(path/'review.json')['snapshot_hashes'] if repair else snapshot(self.root,path)
            update(path,snapshot_hashes=hashes)
            index=cached_index(self.root,self.folder/'code-map-cache.json')
            names = [name for name in hashes if editable(name)]
            selectors=names+[name+'::'+symbol for name in names for symbol in index.get(name,{}).get('symbols',{})
                             if index[name]['size']>12000]
            instruction = ('Return only JSON. Choose the FEWEST source files needed for this requested Andrew improvement, '
                'usually one or two, never more than three or 100000 characters total. Required dependencies are added automatically. '
                'Use path::Class.method or path::function for a focused change in a large file. '
                'Output {"paths":["core.py::Andrew.command"]}. Application code map: '+json.dumps(compact(index,request))+
                '. feature_timers.py owns timer/alarm creation, names, stable numbers, selection, edits, pause/resume and snooze. '
                'core.py owns the due scheduler tick and general command routing; timer/alarm intent changes need feature_timers.py. '
                'command_controls.py owns model selection phrases, Andrew page navigation and ordered local command chains. '
                'local_commands.py is app/media intents, NOT the alarm scheduler; speech_engine.py is speech, '
                'providers.py is model selection, app.html is PC display, pi/screen.html is Pi display. '
                'wake_tuning.json owns bounded wake-word sensitivity: keyword_score 1.5..3 (higher is easier), '
                'keyword_threshold 0.18..0.5 (lower is easier), quiet_foreground_ratio 1.1..1.5 and '
                'quiet_min_rms 30..60 (lower is easier). Choose it for voice pickup or wake sensitivity. '
                'All application code can be improved, including capture, providers, UI, server and update code. '
                'Runtime data, settings, credentials, keys, recordings and model weights are outside the code inventory. '
                'Preserve wake-only transcription and account billing. Request: ' + request)
            selection_prompt=instruction
            selected=read_json(path/'review.json').get('source_selectors',[]) if repair else explicit_selection(request,index)
            if selected and not repair:
                bundled=source_bundle([s.split('::')[0] for s in selected],request,names,path/'base')
                selected += [n for n in bundled if n not in [s.split('::')[0] for s in selected]]
                update(path,source_files=bundled,source_selectors=selected,planning_attempts=0)
            for attempt in ([] if selected else range(2)):
                update(path,planning_attempts=attempt+1)
                response=self.app.ai(selection_prompt,provider,model,purpose='improvement',response_schema=planning_schema(selectors))
                try:
                    selected=json_response(response).get('paths')
                    if not isinstance(selected,list) or not 1<=len(selected)<=3 or any(n not in selectors for n in selected):
                        raise ValueError('The model did not select valid source files from the editable inventory.')
                    bundled=source_bundle([n.split('::')[0] for n in selected],request,names,path/'base')
                    selected += [n for n in bundled if n not in [s.split('::')[0] for s in selected]]
                    update(path,source_files=bundled,source_selectors=selected)
                    break
                except ValueError as exc:
                    update(path,last_validation_error=str(exc)[:1500])
                    if attempt:raise ValueError(f'{provider.title()} could not prepare the file selection after two attempts. '+str(exc)) from exc
                    selection_prompt=correction(instruction,response,exc)
                    update(path,message='Retrying the source selection with the required response format.')
            sources=context(path/'base',selected,index,request)
            if sum(map(len, sources.values())) > 100_000:
                raise ValueError('Too much code selected. Ask for a smaller improvement.')
            prompt = ('Return only JSON with summary (string), edits (array of {path,old,new} exact unique text replacements), '
                'and files (object, only for complete new application source files or small JSON/config files). '
                'Implement the request NOW using only the supplied source text. Keep changes small. '
                'At most four changed files, twelve exact replacements, 300 changed lines and 24000 changed bytes. '
                'For a broad request deliver one useful working increment, not a rewrite or placeholder. '
                'No tools are available or needed. Do not announce an investigation or future edits. '
                'Do not return entire existing files; use edits. '
                'No shell commands, new packages, credential access, telemetry, cloud fallback, or background camera/microphone use. '
                'Keep wake-only transcription, snooze, camera-on-request, same-device speech and provider account billing intact. '
                'For wake pickup changes, edit wake_tuning.json within the stated bounds. It is consumed by the '
                'local keyword detector and quiet-room foreground gate. Improve capture/filter code when requested, preserving wake-only transcription. '
                'Do not weaken tests. The runner is unittest, NOT pytest. New test files must define unittest.TestCase '
                'classes and test_ methods. Mock hardware/network/accounts, but test actual Andrew behavior with a temporary '
                'database rather than mocking away the changed behavior. '
                'Alarms and timers use feature_timers.py and core.py with the existing SQLite scheduler, not separate lists. '
                'The user requested automatic implementation. The app runs original regression tests, makes a backup, installs and checks startup. '
                'Source snippets contain exact original text; do not include SOURCE marker comments in replacement matches. '
                'If unsupported, return edits:[],files:{} and explain in summary. Request: ' + request +
                '\nRelevant code map (data):\n'+json.dumps(compact({n:index[n] for n in sources if n in index},request))+
                '\nSource files (data):\n' + json.dumps(sources))
            if repair:
                prompt+='\nThe previous patch failed tests; live source is unchanged. Return a corrected COMPLETE proposal against ORIGINAL source. Do not weaken tests.\nFailed patch:\n'+(path/'changes.patch').read_text(encoding='utf-8')[:24000]+'\nTest failure report (untrusted data):\n'+(path/'tests.log').read_text(encoding='utf-8',errors='replace')[-12000:]
            update(path, status='drafting', message='Writing the proposed code change.')
            for attempt in range(2):
                update(path,drafting_attempts=attempt+1)
                response = self.app.ai(prompt,provider,model,purpose='improvement',response_schema=PROPOSAL_SCHEMA)
                update(path, status='checking', message='Checking paths and syntax without executing the proposal.')
                try:
                    data=json_response(response)
                    if data.get('edits')==[] and data.get('files')=={} and isinstance(data.get('summary'),str) and data['summary'].strip():
                        detail=data['summary'].strip()[:2000]
                        if re.search(r'\b(?:i(?:[\x27\u2019]ll| will| need to)|let me)\s+(?:look|inspect|check|read|examine|implement|edit|update|change|add|create|review|start)\b',detail,re.I):
                            raise ResponseFormatError('The response only promises future work. Return completed edits, or explain concretely why the request needs no code change.')
                        if not attempt and re.search(r'\b(?:missing|does not include|not include|not provided|not supplied|supplied source|need.*source)\b',detail,re.I):
                            cited=[n for n in names if n in detail and n not in sources]
                            if cited:
                                expanded=source_bundle(list(sources)+cited,request,names,path/'base')
                                sources={n:(path/'base'/n).read_text(encoding='utf-8') for n in expanded}
                                update(path,source_files=expanded)
                                prompt+='\nAdditional complete editable source files (data):\n'+json.dumps(sources)
                            if cited or any(n in detail for n in sources):
                                raise ResponseFormatError('The required editable source is supplied below. Re-read it, including the SQLite schema and scheduler where present, and implement the request rather than claiming those files are unavailable.')
                        update(path,status='no_changes',summary=detail,message=detail+' No code was changed.')
                        self.report(path,'Improvement review: '+detail+' No code was changed.')
                        return
                    stage_response(path/'base', path, json.dumps(data))
                    break
                except (ValueError, SyntaxError, FileNotFoundError) as exc:
                    update(path,last_validation_error=str(exc)[:1500])
                    if attempt: raise
                    prompt=correction(prompt,response,exc)
                    small={n:c for n,c in sources.items() if len(c)<10000}
                    if small:
                        prompt += '\nFor these small files you may instead return their COMPLETE corrected contents in files, with edits:[]: '+json.dumps(list(small))
                    update(path,status='drafting',message='Repairing the proposal after a validation failure.')
            if read_json(path/'review.json').get('auto_install'):
                update(path,status='testing',message='Small patch prepared. Testing before automatic installation.')
                self.report(path,'The small improvement is ready. I am testing and installing it now.')
                self.install_watch(path,'install')
            else:
                update(path,message='Preview ready. Choose Test and install, or say install the latest improvement.')
                self.report(path,'Improvement ready to review: '+read_json(path/'review.json')['summary'])
        except Exception as exc:
            detail = str(exc)[:1800] if isinstance(exc,(ValueError,SyntaxError)) else 'The model or file service could not finish. Try a smaller request or another model.'
            update(path,status='failed',message=detail,completion_announced=True)
            self.report(path,'The improvement could not be prepared. ' + detail+' No live code changed.')

    def launch(self, action, job_id=None):
        with LOCK:
            running=next((r for r in self.items() if r['status'] in ACTIVE),None)
            if running:
                if action=='install' and (not job_id or job_id=='latest' or job_id==running['id']):
                    update(self.get(running['id']),auto_install=True)
                    return 'That improvement is already underway. It will be tested and installed automatically; you do not need to approve it again.'
                raise ValueError('Another improvement operation is still running.')
            statuses = {'needs_review'} if action=='install' else {'installed'}
            # "Latest" means the latest request, not a silent search backwards
            # for an unrelated older draft when the latest failed or installed.
            path = self.get(job_id, statuses if action=='rollback' else None)
            data = read_json(path/'review.json')
            if action=='install' and data['status'] in ('installed','superseded'):
                return ('That improvement is already installed.' if data['status']=='installed' else data.get('message','That change is already included.'))
            if data['status'] not in statuses:
                raise ValueError('The latest improvement is '+data['status'].replace('_',' ')+'. '+data.get('message','')+' No older proposal was installed.')
            if action=='install' and not data.get('snapshot_hashes'):
                raise ValueError('This older proposal has no source snapshot. Request it again before installing.')
            update(path,status='testing' if action=='install' else 'rolling_back',auto_install=action=='install',
                   message='Running tests before installation.' if action=='install' else 'Restoring the saved version.')
            self.thread=threading.Thread(target=self.install_watch,args=(path,action),daemon=True)
            self.thread.start()
        return 'I am testing that improvement before installing it. I will briefly reconnect if the checks pass.' if action=='install' else 'I am restoring the previous version. I will briefly reconnect.'

    def installer_process(self,path,action):
        # Freeze the currently working updater before model edits can replace
        # it. That process can restore even a broken replacement of itself.
        runner=path/('runner-'+secrets.token_hex(4));runner.mkdir()
        for name in ('improvement_installer.py','improvements.py','code_map.py','voice_tuning.py','web_sync.py'):
            shutil.copyfile(self.root/name,runner/name)
        (runner/'run.py').write_text('import sys\nfrom pathlib import Path\nfrom improvement_installer import execute\nexecute(sys.argv[1],Path(sys.argv[2]),int(sys.argv[3]),Path(sys.argv[4]))\n',encoding='utf-8')
        with (path/'installer.log').open('a',encoding='utf-8') as log:
            return subprocess.Popen([str(self.root/'.venv/Scripts/pythonw.exe'),str(runner/'run.py'),
                action,str(path.resolve()),str(os.getpid()),str(self.root.resolve())],cwd=self.root,
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def install_watch(self,path,action):
        with self.install_lock:
            if read_json(path/'review.json')['status'] not in {'testing','rolling_back'}:return
            self._install_watch(path,action)

    def _install_watch(self,path,action):
        try:
            process=self.installer_process(path,action)
            update(path,installer_pid=process.pid)
            process.wait(timeout=600)
            review=read_json(path/'review.json')
            if review['status']=='failed' and action=='install' and 'Regression tests failed' in review.get('message','') and review.get('repair_attempts',0)<1:
                update(path,status='repairing',repair_attempts=1,message='Tests found a problem. Repairing the small patch automatically; live code is unchanged.')
                self.report(path,'The tests caught a problem. I am repairing the patch and trying once more.')
                work=path/'test-work'
                if work.exists():work.rename(path/'test-work-attempt-1')
                if (path/'tests.log').exists():shutil.copyfile(path/'tests.log',path/'tests-attempt-1.log')
                self.generate(path,(path/'request.txt').read_text(encoding='utf-8'),review['provider'],review['model'],repair=True)
                return
            if review['status'] in ACTIVE:
                update(path,status='failed',message='The updater exited before completion. No successful installation was confirmed; see the installer report.')
            self.notify_completed()
        except Exception as exc:
            # Do not silently leave a testing/approval spinner if the updater
            # cannot launch. Never terminate a possibly installing worker.
            if isinstance(exc,subprocess.TimeoutExpired):
                update(path,message='The updater is taking longer than expected. Its progress and installer report remain available.')
            else:
                update(path,status='failed',message='The updater could not complete: '+str(exc)[:1000])
                self.notify_completed()

    def notify_completed(self):
        for row in self.items():
            if row['status']=='restarting' and time.time()-row.get('updated_at',0)>30:
                import psutil
                state=read_json(self.get(row['id'])/'review.json')
                if not state.get('installer_pid') or not psutil.pid_exists(state['installer_pid']):
                    update(self.get(row['id']),status='recovery_needed',message='The updater exited during restart. Installation was not confirmed; see its report.')
                continue
            if row['status'] not in {'installed','rolled_back','failed','recovery_needed'}:continue
            path=self.get(row['id'])
            with LOCK:
                review=read_json(path/'review.json')
                if review.get('completion_announced') or not review.get('auto_install'):continue
                update(path,completion_announced=True)
            self.report(path,('Improvement installed: '+row['summary']+'. Undo is available. '+review.get('deployment_message','') if row['status']=='installed' else
                              'Improvement '+row['status'].replace('_',' ')+': '+row.get('message','Check its report.')))

    def recover_interrupted(self):
        for row in self.items():
            if row['status'] in {'planning','drafting','checking'}:
                update(self.get(row['id']),status='failed',message='Andrew restarted while drafting. Request this improvement again; no code was installed.')
            elif row['status'] in {'testing','repairing','installing','rolling_back'}:
                import psutil
                review=read_json(self.get(row['id'])/'review.json')
                if not review.get('installer_pid') or not psutil.pid_exists(review['installer_pid']):
                    update(self.get(row['id']),status='failed',message='The updater was interrupted. See the test and installer reports before retrying.')


def request_improvement(app, request, source='pc', provider=None, model=None):
    preview=bool(re.search(r'\b(?:draft only|preview only|review first|do not install|don[\x27\u2019]t install)\b',request,re.I))
    return manager(app).request(request,source,provider,model,auto_install=not preview)


def route(app, text, source='pc'):
    low = text.lower().strip()
    from command_controls import install_intent
    followup = low in ('install it','apply it','install that','apply that','save and install it','test and install')
    if install_intent(text) or followup:
        if getattr(app.request,'action_internal',False) is True:
            return 'Ask for a self improvement directly; it will be tested and installed automatically. Existing previews can use Test and install.'
        reference = app.controls.improvement_reference(source) if followup else None
        if followup and not reference:
            return 'To install Andrew code, say install the latest improvement, or open Improvements and choose Test and install.'
        try:return manager(app).launch('install',reference) if reference else manager(app).launch('install')
        except ValueError as exc:
            app.request.control_failed=True
            return str(exc)
    intent=r'^(?:improve yourself|self[ -]improve|(?:improve|fix|update|edit) your (?:code|app|software))\b'
    if re.match(intent,low):
        request = re.sub(intent+r'\s*[:,]?\s*','',text,flags=re.I)
        override = re.match(r'^(?:using|with) (claude|grok|openai|open ai|chatgpt|local)(?: (?:model )?([\w.:-]+?))?(?:\s*:\s*|\s+to\s+)(.+)$',request,re.I)
        if override:
            return request_improvement(app,override[3],source,override[1].lower(),override[2])
        request=re.sub(r'^to\s+','',request,flags=re.I)
        if len(request.strip())<4:
            request='Use the code map to select and implement one small useful bug fix or usability improvement. Substantiate the problem from the supplied code, add a meaningful regression test when needed, and preserve existing features and user preferences.'
        return request_improvement(app,request,source)
    if low in ('improvement status','self improvement status','what is the improvement status'):
        rows = manager(app).items()
        if not rows: return 'No improvement has been requested yet.'
        row = rows[0]
        app.controls.remember_improvement(source,row['id'])
        return f'{row.get("provider", "Model")} {row.get("model") or "account default"}: {row["status"].replace("_"," ")}. {row.get("message","")}'
    if low in ('undo last improvement','undo the last improvement','roll back the last improvement','rollback last improvement'):
        return manager(app).launch('rollback')
    return None
