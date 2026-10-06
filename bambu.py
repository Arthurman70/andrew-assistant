"""Bambu Studio projects, local slicing, and observed desktop printer tasks.

No cloud impersonation, account-token extraction, or printer commands in the CLI.
"""
import json
import hashlib
from functools import lru_cache
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
import zipfile

MODEL_EXT={'.3mf','.stl','.obj','.step','.stp','.amf'}
SETTING_KEYS=('printer_model','printer_settings_id','print_settings_id','filament_settings_id',
              'filament_type','layer_height','sparse_infill_density','enable_support','wall_loops','curr_bed_type')
ALIASES=r'(?:bambu(?: lab(?:s)?)?(?: studio)?|bamboo(?: lab(?:s)?)?(?: studio)?)'

@lru_cache(maxsize=1)
def studio_executable():
    candidates=[]
    if os.name=='nt':
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
            for base in ('Software/Microsoft/Windows/CurrentVersion/Uninstall','Software/WOW6432Node/Microsoft/Windows/CurrentVersion/Uninstall'):
                try:
                    with winreg.OpenKey(hive,base.replace('/','\\')) as keys:
                        for i in range(winreg.QueryInfoKey(keys)[0]):
                            try:
                                with winreg.OpenKey(keys,winreg.EnumKey(keys,i)) as entry:
                                    if winreg.QueryValueEx(entry,'DisplayName')[0]!='Bambu Studio':continue
                                    icon=winreg.QueryValueEx(entry,'DisplayIcon')[0].split(',')[0].strip().strip('"')
                                    candidates.append(Path(icon))
                            except OSError:continue
                except OSError:continue
    candidates += [Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Bambu Studio/bambu-studio.exe',
                   Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Bambu Studio/bambu-studio.exe']
    return next((p for p in candidates if p.is_file() and p.name.lower()=='bambu-studio.exe'),None)

def studio_config(path):
    # Studio's JSON config can have a checksum footer. Read the first object only.
    try:
        if path.stat().st_size>4_000_000:return {}
        value=json.JSONDecoder().raw_decode(path.read_text(encoding='utf-8-sig').lstrip())[0]
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,UnicodeError):return {}

def project_settings(path):
    if path.suffix.lower()!='.3mf':return {}
    with zipfile.ZipFile(path) as archive:
        try:info=archive.getinfo('Metadata/project_settings.config')
        except KeyError:return {}
        if info.file_size>2_000_000:raise ValueError('Project settings are too large to inspect.')
        value=json.loads(archive.read(info))
        if not isinstance(value,dict):raise ValueError('Project settings are not a JSON object.')
        return value

class Bambu:
    def __init__(self,app,config_root=None,executable=None):
        self.app=app;self.config_root=Path(config_root) if config_root else Path(os.environ.get('APPDATA',str(Path.home()/'AppData/Roaming')))/'BambuStudio'
        self.exe=Path(executable) if executable else studio_executable()
        self.lock=threading.RLock();self.selected={};self.projects={};self.proc=None;self.notify=None;self.running=False
        self.directory=app.directory/'bambu';self.directory.mkdir(exist_ok=True)
        self.job=studio_config(self.directory/'job.json') or {'state':'idle'}
        if self.job.get('state')=='running':self.job.update(state='interrupted',message='Slicing was interrupted. The source project is unchanged; start it again when ready.')
        self.cached_at=0;self.cached={}
    def config(self):return studio_config(self.config_root/'BambuStudio.conf')
    def recent(self):
        value=self.config().get('recent_projects',{});paths=list(value.values()) if isinstance(value,dict) else value if isinstance(value,list) else []
        result=[]
        for value in paths[:40]:
            if not isinstance(value,str):continue
            path=Path(value)
            if not path.is_file() or path.suffix.lower() not in MODEL_EXT:continue
            path=path.resolve();id=hashlib.sha256(str(path).encode()).hexdigest()[:16]
            self.projects[id]=path;result.append({'id':id,'number':len(result)+1,'name':path.name,'path':str(path)})
        return result
    def status(self):
        with self.lock:
            if time.monotonic()-self.cached_at<3:return dict(self.cached,job=dict(self.job))
            cfg=self.config();presets=cfg.get('presets',{});presets=presets if isinstance(presets,dict) else {}
            selected={k:presets[k] for k in ('machine','process','filaments') if k in presets}
            self.cached={'installed':bool(self.exe and self.exe.is_file()),'studio_path':str(self.exe) if self.exe else '',
                         'profiles':selected,'projects':self.recent(),'printer_status_source':'Bambu Studio Device tab',
                         'live_printer_connected':False,'note':'Saved profiles are not live printer status. Existing Studio login stays in Studio.'}
            self.cached_at=time.monotonic();return dict(self.cached,job=dict(self.job))
    def resolve(self,query,source='pc'):
        query=str(query or 'latest').strip().strip('"');rows=self.recent()
        if query=='selected' and source in self.selected:return self.selected[source]
        if query.lower() in ('latest','last','latest project','last project'):
            if not rows:raise ValueError('Open or save a project in Bambu Studio first, or give me its file path.')
            return self.projects[rows[0]['id']]
        if query in self.projects:return self.projects[query]
        number=re.fullmatch(r'(?:project(?: number)? )?(\d{1,2})',query,re.I)
        if number:
            index=int(number[1])-1
            if 0<=index<len(rows):return self.projects[rows[index]['id']]
            raise ValueError('That recent project number is not listed. Ask for Bambu projects.')
        path=Path(query).expanduser()
        if path.is_absolute():
            if path.suffix.lower() not in MODEL_EXT or not path.is_file():raise ValueError('Choose an existing 3MF, STL, OBJ, STEP or AMF model file.')
            return path.resolve()
        needle=query.lower();matches=[r for r in rows if r['name'].lower()==needle or Path(r['name']).stem.lower()==needle]
        if not matches:matches=[r for r in rows if needle in r['name'].lower()]
        if len(matches)==1:return self.projects[matches[0]['id']]
        if matches:raise ValueError('Several projects match: '+', '.join('project '+str(r['number'])+' '+r['name'] for r in matches[:6])+'. Use its number.')
        raise ValueError('That project is not in Studio’s recent list. Give me its file path or ask for Bambu projects.')
    def open(self,query=None,source='pc'):
        if not self.exe or not self.exe.is_file():raise ValueError('Install Bambu Studio on the host PC first.')
        if query is None:
            from pc_control import PCController
            result=PCController(request='Open Bambu Studio.').open('bambu')
            return 'Bambu Studio is open on your PC.' if result.get('opened') else 'Opening Bambu Studio on your PC.'
        args=[str(self.exe)]
        if query:
            path=self.resolve(query,source);args.append(str(path));self.selected[source]=path
        subprocess.Popen(args,close_fds=True)
        return 'Opening '+(path.name+' in Bambu Studio.' if query else 'Bambu Studio on your PC.')
    def describe(self,query='latest',source='pc'):
        path=self.resolve(query,source);self.selected[source]=path;settings=project_settings(path)
        summary={k:settings[k] for k in SETTING_KEYS if k in settings}
        sliced=[]
        if path.suffix.lower()=='.3mf':
            with zipfile.ZipFile(path) as archive:
                sliced=[n for n in archive.namelist() if re.fullmatch(r'Metadata/plate_\d+\.gcode',n)]
        return {'name':path.name,'path':str(path),'settings':summary,'sliced_plates':len(sliced),'has_embedded_settings':bool(settings)}
    def describe_text(self,query='latest',source='pc'):
        result=self.describe(query,source);s=result['settings'];parts=[result['name']+'.']
        for key,label in [('printer_settings_id','Printer profile'),('print_settings_id','Process'),('layer_height','Layer height'),('sparse_infill_density','Infill'),('wall_loops','Walls')]:
            if key in s:parts.append(label+': '+str(s[key])+(' mm' if key=='layer_height' else '')+'.')
        if 'filament_type' in s:parts.append('Filament: '+', '.join(dict.fromkeys(s['filament_type']))+'.')
        if 'enable_support' in s:parts.append('Supports '+('on.' if str(s['enable_support']) in ('1','true','True') else 'off.'))
        parts.append(str(result['sliced_plates'])+' plates already contain sliced G-code.' if result['sliced_plates'] else 'No sliced G-code is present.')
        return ' '.join(parts)
    def slice(self,query='latest',source='pc',plate=0,overrides=None):
        path=self.resolve(query,source)
        if not self.exe or not self.exe.is_file():raise ValueError('Bambu Studio is not installed on this host.')
        if path.suffix.lower()!='.3mf':return self.desktop('Open '+str(path)+' in Bambu Studio, choose the existing printer and filament profiles, slice it and save a new 3MF copy. Do not print.',source)
        config=project_settings(path)
        if not config.get('printer_settings_id'):raise ValueError('Save this 3MF as a Bambu Studio project with printer and filament settings first.')
        if config.get('post_process'):raise ValueError('This project contains post-processing scripts. Review them in Studio before slicing; no script was executed.')
        if type(plate)!=int or not 0<=plate<=100:raise ValueError('Use plate zero for all plates, or a plate number from 1 to 100.')
        args=[]
        for key,value in (overrides or {}).items():
            if key=='layer_height':
                if type(value) not in (int,float) or not .08<=value<=.6:raise ValueError('Use a layer height from 0.08 to 0.6 mm.')
                args+=['--layer-height',str(value)]
            elif key=='infill':
                if type(value) not in (int,float) or not 0<=value<=100:raise ValueError('Use infill from 0 to 100 percent.')
                args+=['--sparse-infill-density',str(value)+'%']
            elif key=='supports':
                if type(value)!=bool:raise ValueError('Supports must be on or off.')
                args+=['--enable-support',str(int(value))]
            elif key=='walls':
                if type(value)!=int or not 1<=value<=12:raise ValueError('Use 1 to 12 walls.')
                args+=['--wall-loops',str(value)]
            else:raise ValueError('Unsupported slicing setting.')
        with self.lock:
            if self.running:return 'Bambu is already slicing. Ask for slicing status or cancel Bambu slicing.'
            id=time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3);directory=self.directory/'jobs'/id;directory.mkdir(parents=True)
            output=directory/(path.stem+'-sliced.3mf')
            self.job={'id':id,'state':'running','source':source,'name':path.name,'plate':plate,'output':str(output),'started_at':time.time(),'message':'Slicing '+path.name+'. No print is being sent.'}
            self.save_job();self.selected[source]=path;self.running=True
            command=[str(self.exe),'--debug','1','--mstpp','300','--slice',str(plate),*args,'--export-3mf',str(output),str(path)]
            threading.Thread(target=self.slice_worker,args=(command,directory,output,source),daemon=True).start()
        return 'Slicing '+path.name+(' on plate '+str(plate) if plate else ' on all plates')+'. I will tell you when it finishes. The original project stays unchanged; this does not start a print.'
    def save_job(self):
        temporary=self.directory/'job.tmp';temporary.write_text(json.dumps(self.job),encoding='utf-8');temporary.replace(self.directory/'job.json')
    def slice_worker(self,command,directory,output,source):
        try:
            with (directory/'slicer.log').open('wb') as log:
                with self.lock:
                    if self.job['state']=='cancelled':return
                    self.proc=subprocess.Popen(command,cwd=self.exe.parent,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    process=self.proc
                code=process.wait(timeout=900)
            with self.lock:
                if self.job['state']=='cancelled':return
                if code!=0 or not output.is_file():raise ValueError('Bambu Studio could not finish slicing (exit '+str(code)+'). The local slicing log has details.')
                with zipfile.ZipFile(output) as archive:
                    if not any(re.fullmatch(r'Metadata/plate_\d+\.gcode',entry.filename) and entry.file_size>0 for entry in archive.infolist()):raise ValueError('The exported project has no sliced G-code. It has not been marked ready to print.')
                self.job.update(state='complete',finished_at=time.time(),message='Slicing finished: '+output.name+'. Saved a new 3MF copy; nothing was sent to the printer.')
        except Exception as exc:
            with self.lock:
                if self.proc and self.proc.poll() is None:self.proc.terminate()
                if self.job['state']!='cancelled':self.job.update(state='failed',message=str(exc) if isinstance(exc,ValueError) else 'Slicing failed. The original project is unchanged; check the local slicing log.')
        finally:
            with self.lock:self.proc=None;self.running=False;self.save_job();answer=self.job['message']
            if self.notify:
                try:self.notify(answer,source)
                except Exception:pass
    def cancel_slice(self):
        with self.lock:
            if self.job.get('state')!='running':return 'No Andrew slicing job is running.'
            self.job.update(state='cancelled',message='Bambu slicing cancelled. The original project and printer are unchanged.')
            if self.proc and self.proc.poll() is None:self.proc.terminate()
            self.save_job();return self.job['message']
    def desktop(self,request,source):
        if not self.app.pc_agent:return 'The PC task controller is unavailable. Open Bambu Studio directly.'
        return self.app.pc_agent.start('In the existing Bambu Studio app, '+request,source)
    def live_status(self):
        from pc_control import PCController
        controller=PCController(request='Read Bambu printer status.');windows=[w for w in controller.windows() if w['app']=='bambu-studio.exe']
        main=[w for w in windows if re.search(r'\bBambuStudio\s*$',w.get('title',''),re.I)]
        if len(main)==1:windows=main
        if len(windows)!=1:raise ValueError('Open one intended Bambu Studio window to read its selected printer. Observed '+str(len(windows))+' Studio windows.')
        view=controller.select(windows[0]['window']);tabs=[n for n in view.get('elements',[]) if n['name']=='Device' and n['actionable']]
        if not tabs:raise ValueError('The Studio Device tab is not available in the observed window.')
        if not any(n['name']=='Printing Progress' for n in view.get('elements',[])):view=controller.click(tabs[0]['ref'])
        text=[n['name'].strip() for n in view.get('elements',[]) if n['role']=='Text']
        state=next((t for t in text if re.fullmatch(r'Finished|Printing|Paused|Idle|Failed|Offline|Disconnected|Connecting',t,re.I)),None)
        if not state:raise ValueError('Studio’s visible Device page has not supplied a readable printer state.')
        result={'observed_at':time.time(),'source':'Studio Device tab','state':state,
                'file':next((t for t in text if re.search(r'\.(?:stl|3mf|gcode)$',t,re.I)),''),
                'layer':next((t for t in text if re.match(r'Layer:',t,re.I)),''),
                'percent':next((text[i-1] for i,t in enumerate(text) if t=='%' and i and re.fullmatch(r'\d+(?:\.\d+)?',text[i-1])),None)}
        self.cached_at=0;self.last_device=result
        (self.directory/'device-reader-error.txt').unlink(missing_ok=True)
        return result
    def printer_status(self):
        try:
            state=self.live_status();parts=['Bambu Studio shows '+(state['file']+' ' if state['file'] else '')+state['state'].lower()+'.']
            if state['percent'] is not None:parts.append(state['percent']+' percent.')
            if state['layer']:parts.append(state['layer'].replace('/',' of ')+'.')
            return ' '.join(parts)
        except Exception as exc:
            (self.directory/'device-reader-error.txt').write_text(type(exc).__name__+': '+str(exc)[:1200],encoding='utf-8')
            return None
    def perform(self,args,source='pc'):
        action=args.get('action');query=args.get('project','latest')
        if action=='status':return self.status()
        if action=='printer_status':return self.live_status()
        if action=='projects':return {'projects':self.recent()}
        if action=='inspect':return self.describe(query,source)
        if action=='open':return {'answer':self.open(args.get('project'),source)}
        if action=='slice':return {'answer':self.slice(query,source,args.get('plate',0),args.get('settings',{}))}
        if action=='slice_status':return dict(self.job)
        if action=='cancel_slice':return {'answer':self.cancel_slice()}
        if action=='open_output':
            if self.job.get('state')!='complete':raise ValueError('No completed sliced output is available yet.')
            return {'answer':self.open(self.job['output'],source)}
        raise ValueError('Unsupported Bambu action.')
    def route(self,text,source):
        low=text.lower().strip().rstrip('.!?')
        low=re.sub(ALIASES,'bambu',low)
        if low in ('bambu projects','list bambu projects','recent bambu projects','list recent bambu projects','show bambu projects'):
            rows=self.recent();return ('Recent Bambu projects: '+ '; '.join('project '+str(r['number'])+': '+r['name'] for r in rows[:10])) if rows else 'Bambu Studio has no recent model projects yet.'
        if low in ('bambu settings','bambu profiles','show bambu settings','show bambu profiles'):
            s=self.status()['profiles'];return 'Studio’s selected profiles: '+', '.join(k+': '+(', '.join(v) if isinstance(v,list) else str(v)) for k,v in s.items())+'.' if s else 'Choose printer, process and filament profiles in Bambu Studio first.'
        if low in ('bambu status','bambu connection','bambu studio status'):
            s=self.status();return ('Bambu Studio is installed. '+self.route('bambu profiles',source)+' '+self.job.get('message','No slicing job is running.')) if s['installed'] else 'Bambu Studio was not found on the host PC.'
        if low in ('printer status','bambu printer status','how is my print doing','how much longer is my print','show printer status'):
            return self.printer_status() or self.desktop(text,source)
        if low in ('bambu slicing status','slicing status','print preparation status'):return self.job.get('message','No Andrew slicing job is running.')
        if low in ('cancel bambu slicing','stop bambu slicing','cancel slicing'):return self.cancel_slice()
        if low in ('open bambu','launch bambu','start bambu','open bambu app'):return self.open(source=source)
        if low in ('open sliced project','open bambu sliced project','open sliced bambu project'):return self.perform({'action':'open_output'},source)['answer']
        match=re.fullmatch(r'(?:slice|prepare)(?: the)? (?:latest|last|selected)(?: bambu)? project(?: (?:on )?plate (\d+))?',low)
        if match:return self.slice('selected' if 'selected' in low else 'latest',source,int(match[1] or 0))
        match=re.fullmatch(r'(?:inspect|describe|check|read)(?: the)? (?:bambu )?(?:latest |last |selected )?(?:bambu )?project(?: settings)?',low)
        if match and ('bambu' in low or source in self.selected):return self.describe_text('selected' if 'selected' in low else 'latest',source)
        match=re.fullmatch(r'(?:open|inspect|describe|check|slice|prepare)(?: the)?(?: bambu)? (?:project )?(.+?)(?: in bambu| with bambu| for bambu)?',text.strip().rstrip('.!?'),re.I)
        if match and (re.search(ALIASES,text,re.I) or re.search(r'\bproject (?:number )?\d+\b',low) or source in self.selected or Path(match[1].strip('"')).suffix.lower() in MODEL_EXT):
            verb=text.split()[0].lower();query=match[1];query=re.sub(r' (?:in|with|for) '+ALIASES+'$','',query,flags=re.I)
            plate=0;plate_match=re.search(r' (?:on )?plate (\d+)$',query,re.I)
            if plate_match:plate=int(plate_match[1]);query=query[:plate_match.start()]
            if verb=='open':return self.open(query,source)
            if verb in ('slice','prepare'):return self.slice(query,source,plate)
            return self.describe_text(query,source)
        if re.search(r'\b(?:printer|print|ams|filament)\b',low) and (re.search(r'\bbambu\b',low) or re.fullmatch(r'(?:pause|resume|stop|cancel) (?:my |the )?(?:printer|print)',low) or low in ('printer status','how is my print doing','how much longer is my print','show printer status')):
            return self.desktop(text,source)
        return None
