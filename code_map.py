"""Incremental application index and focused source context for small patches."""
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

ROLES={
 'core.py':'SQLite settings/schema, command routing, due scheduler, provider dispatch',
 'feature_timers.py':'timer/alarm naming, selection, edits, pause, snooze, recurrence commands',
 'feature_alarm_recurrence.py':'weekly calendar and alarm occurrence ticks',
 'server.py':'HTTP API, authentication, audio gates, notifications and service startup',
 'pc_audio.py':'PC microphone capture, wake detection, utterance endpointing',
 'pi/agent.py':'Pi microphone, wake detection, relay, local playback',
 'wake_detector.py':'local keyword spotting, assistant name and addressed wake phrase',
 'wake_capture.py':'wake-only recording gates, command capture and speech endpointing',
 'background_guard.py':'background chatter, media rejection, foreground speech',
 'speech_engine.py':'local speech recognition, TTS and voice session pipeline',
 'assets/upgrade.js':'shared PC/Pi UI, alarms/day picker, activities, memory, navigation',
 'app.html':'PC/browser display and improvement UI, refresh and form handlers',
 'assets/browser.js':'browser push-to-talk, local playback, install and widget controls',
 'improvements.py':'planning, small patches, automatic testing/repair/install lifecycle',
 'improvement_installer.py':'isolated regression copy, backup, atomic install, restart, rollback',
 'web_portal.py':'website login, sessions, CSRF and authenticated proxy',
 'satellite_manager.py':'paired Pi deployment, queued updates and recovery',
 'assistant_actions.py':'Andrew persona and conversational command catalog for all models',
 'providers.py':'account/model discovery and selection',
 'code_map.py':'cached function/dependency map and focused context',
}
CODE_EXT={'.py','.html','.js','.css','.ps1','.sh','.cmd','.service','.java','.gradle','.xml','.md'}
DIRECTORIES={'assets','pi','web','installers','android','tests'}


def source_name(name):
    if not isinstance(name,str) or '\\' in name or ':' in name:return False
    path=PurePosixPath(name)
    if path.is_absolute() or any(p in ('.','..') or p.startswith('.') for p in name.split('/')):return False
    if len(path.parts)>1 and path.parts[0] not in DIRECTORIES:return False
    if any(re.fullmatch(r'(?i)(?:con|prn|aux|nul|com[1-9]|lpt[1-9])',PurePosixPath(p).stem) for p in path.parts):return False
    if any(p in {'data','runtime','downloads','build','private','__pycache__'} for p in path.parts):return False
    if not re.fullmatch(r'[A-Za-z0-9_ /().-]+',name):return False
    return (path.suffix.lower() in CODE_EXT or name in
            {'requirements.txt','wake_tuning.json','web/manifest.webmanifest','source_manifest.json'})


def inventory(root, previous=None):
    root=Path(root)
    if previous is None:
        try:previous=json.loads((root/'data/improvements/code-map-cache.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):previous={}
    manifest=root/'source_manifest.json'
    names=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    # Fixtures/old installations have no manifest. App exports always include it.
    if not names:names=[p.relative_to(root).as_posix() for pattern in ('*.py','*.html','wake_tuning.json','pi/*.py','pi/*.html','web/*','assets/*.js','assets/*.css','tests/test_*.py') for p in root.glob(pattern)]
    names += [p.relative_to(root).as_posix() for pattern in ('feature_*.py','tests/test_feature_*.py') for p in root.glob(pattern)]
    for directory in DIRECTORIES:
        for parent,folders,files in os.walk(root/directory,followlinks=False):
            folders[:]=[n for n in folders if not n.startswith('.') and n not in {'build','private','runtime','node_modules','__pycache__','data'}]
            for file in files:
                path=Path(parent)/file;name=path.relative_to(root).as_posix()
                if source_name(name):names.append(name)
    # Newly introduced modules referenced by indexed code join the map on the
    # next pass without crawling runtime or diagnostic scripts.
    for name in list(dict.fromkeys(names)):
        path=root/name
        if source_name(name) and path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and name.endswith('.py'):
            old=previous.get(name,{})
            stat=path.stat();stamp=[stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size]
            if old.get('stamp')==stamp:modules=old.get('imports',[])
            else:
                try:tree=ast.parse(path.read_text(encoding='utf-8-sig'))
                except (SyntaxError,UnicodeError):continue
                modules=[]
                for node in ast.walk(tree):
                    if isinstance(node,ast.ImportFrom) and node.module:modules.append(node.module)
                    elif isinstance(node,ast.Import):modules.extend(a.name for a in node.names)
            for module in modules:
                candidate=str(module).replace('.','/')+'.py'
                if source_name(candidate) and (root/candidate).is_file():names.append(candidate)
    return sorted({n for n in names if source_name(n) and (root/n).is_file() and not (root/n).is_symlink() and (root/n).resolve().is_relative_to(root.resolve())})


def file_index(name, text):
    entry={'role':ROLES.get(name,''),'symbols':{},'imports':[],'size':len(text)}
    if name.endswith('.py'):
        tree=ast.parse(text)
        entry['role']=entry['role'] or (ast.get_docstring(tree) or '').split('\n')[0][:160]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):entry['imports'] += [a.name for a in node.names]
            elif isinstance(node,ast.ImportFrom) and node.module:entry['imports'].append(node.module)
        def walk(nodes,prefix=''):
            for node in nodes:
                if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    symbol=prefix+node.name
                    start=min([node.lineno]+[d.lineno for d in node.decorator_list])
                    entry['symbols'][symbol]=[start,node.end_lineno]
                    if isinstance(node,ast.ClassDef):walk(node.body,symbol+'.')
        walk(tree.body)
    else:
        lines=text.splitlines()
        matches=list(re.finditer(r'(?:async\s+)?function\s+(\w+)\s*\(',text))
        for i,match in enumerate(matches):
            start=text.count('\n',0,match.start())+1
            end=text.count('\n',0,matches[i+1].start()) if i+1<len(matches) else len(lines)
            entry['symbols'][match[1]]=[start,max(start,end)]
    entry['imports']=sorted(set(entry['imports']))
    return entry


def cached_index(root,cache):
    """Only parse files whose content changed; no credential or runtime reads."""
    root,cache=Path(root),Path(cache)
    try:previous=json.loads(cache.read_text(encoding='utf-8'))
    except (OSError,ValueError):previous={}
    result={}
    for name in inventory(root,previous):
        stat=(root/name).stat();stamp=[stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size]
        old=previous.get(name,{})
        if old.get('stamp')==stamp:result[name]=old;continue
        raw=(root/name).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if old.get('hash')==sha:result[name]=dict(old,stamp=stamp);continue
        try:entry=file_index(name,raw.decode('utf-8-sig'))
        except (SyntaxError,UnicodeError):entry={'role':ROLES.get(name,''),'symbols':{},'imports':[],'size':len(raw)}
        result[name]=dict(entry,hash=sha,stamp=stamp)
    cache.parent.mkdir(parents=True,exist_ok=True)
    temporary=cache.with_suffix('.tmp');temporary.write_text(json.dumps(result),encoding='utf-8');temporary.replace(cache)
    return result


def compact(index,request=''):
    words=set(re.findall(r'[a-z]{4,}',request.lower()))-{'yourself','improve','change','please','should','code','andrew'}
    ranked=sorted(index,key=lambda n:sum(w in (n+' '+index[n]['role']).lower() for w in words),reverse=True)
    detailed=set(ranked[:12]) if words else set(index)
    return {name:{'role':v['role'],'symbols':([] if name.startswith('tests/') else list(v['symbols'])[:35 if name in detailed else 5]),
                  'imports':[i for i in v['imports'] if i.replace('.','/')+'.py' in index], 'chars':v['size']}
            for name,v in index.items()}


def explicit_selection(request,index):
    selected=[]
    for name,entry in index.items():
        if not re.search(r'(?<![\w/])'+re.escape(name)+r'(?![\w/])',request):continue
        match=re.search(re.escape(name)+r'::([\w.]+)',request)
        symbol=match[1] if match and match[1] in entry['symbols'] else ''
        selected.append(name+('::'+symbol if symbol else ''))
    return selected if 1<=len(selected)<=3 else []


def context(base,selected,index,request):
    """Provide chosen functions plus imports/schema, rather than whole big files."""
    result={};words=set(re.findall(r'[a-z]{3,}',request.lower()))
    grouped={}
    for selector in selected:
        name,_,symbol=selector.partition('::');grouped.setdefault(name,[])
        if symbol:grouped[name].append(symbol)
    for name,explicit in grouped.items():
        text=(Path(base)/name).read_text(encoding='utf-8-sig')
        if len(text)<=12000:result[name]=text;continue
        info=index.get(name,{});symbols=info.get('symbols',{})
        chosen=list(explicit)
        if not chosen:
            ranked=sorted(symbols,key=lambda s:sum(w in s.lower() for w in words),reverse=True)
            chosen=[s for s in ranked if not ('.' not in s and any(k.startswith(s+'.') for k in symbols))][:3]
        if name=='core.py' and re.search(r'\b(?:alarms?|timers?|scheduler)\b',request,re.I):
            chosen += ['Andrew.__init__','Andrew.due','Andrew.status']
        if name=='feature_timers.py' and not explicit:
            chosen += ['Schedule.__init__','Schedule.route']
        lines=text.splitlines(keepends=True);ranges=[]
        for item in chosen:
            if item in symbols:ranges.append(tuple(symbols[item]))
        if not ranges:result[name]=text;continue
        # Top-level imports/constants are often needed by a focused replacement.
        first=min((v[0] for v in symbols.values()),default=1)
        if name.endswith('.py') and first>1:ranges.insert(0,(1,first-1))
        merged=[]
        for start,end in sorted(set(ranges)):
            if merged and start<=merged[-1][1]+1:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
            else:merged.append((start,end))
        result[name]='\n'.join(f'\n# SOURCE {name} lines {start}-{end}\n'+''.join(lines[start-1:end]) for start,end in merged)
    return result
