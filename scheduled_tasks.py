"""Persistent timed commands, independent of countdown alerts and AI timers."""
import datetime as dt
import json
import re
import secrets
import threading
import time
from feature_timers import clock_target,numeric
from feature_alarm_recurrence import DAYS,repeat_clause,next_occurrence

DURATION=r'(?:\d+\s*(?:days?|hours?|minutes?|seconds?)\s*)+'
ACTION=r'(?:open|launch|close|play|pause|resume|stop|cast|read|show|check|turn|dim|set|change|switch|use|ask|tell|run|start|search|find|build|create|write|improve|weather|forecast|news|announce|call|text|take|record|send|add|remove|summarize|compare|research)\b'


def deadline(when,now=None):
    now=time.time() if now is None else now
    if not isinstance(when,str) or len(when)>160:raise ValueError('Use a short task time, such as in ten minutes or tomorrow at 7 pm.')
    text=numeric(re.sub(r'\bzero\b','0',when.strip(),flags=re.I))
    text=re.sub(r'\bhalf (?:an? )?hour\b','30 minutes',text)
    text=re.sub(r'\b(?:a |1 )?quarter(?: of)? (?:an? )?hour\b','15 minutes',text)
    text=re.sub(r'\b(?:a|an) (second|minute|hour|day)\b',r'1 \1',text)
    cadence=re.fullmatch(r'(every|each) (.+?) at (.+)',text)
    if cadence:text=cadence[3]+' '+cadence[1]+' '+cadence[2]
    phrase,days,_=repeat_clause(text)
    if phrase.startswith('in '):
        duration=re.sub(r'\s+and\s+|,\s*',' ',phrase[3:]).strip()
        if days:raise ValueError('Use a clock time for repeating tasks, such as 7 am every weekday.')
        if not re.fullmatch(DURATION,duration):raise ValueError('Use a duration such as in ten minutes.')
        sizes={'day':86400,'hour':3600,'minute':60,'second':1}
        seconds=sum(int(n)*sizes[unit.rstrip('s')] for n,unit in re.findall(r'(\d+)\s*(days?|hours?|minutes?|seconds?)',duration))
        if seconds<=0:raise ValueError('Choose a task time in the future.')
        return now+seconds,[],None
    phrase=phrase.removeprefix('at ').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}t\d{2}:\d{2}(?::\d{2})?',phrase,re.I):
        target=dt.datetime.fromisoformat(phrase)
        if target.timestamp()<=now:raise ValueError('Choose a task time in the future.')
    else:
        weekday=re.search(r'\b(?:next )?('+ '|'.join(DAYS)+r')\b',phrase)
        if weekday:
            selected=DAYS.index(weekday[1]);phrase=(phrase[:weekday.start()]+' '+phrase[weekday.end():]).strip()
            clock=clock_target(phrase,dt.datetime.fromtimestamp(now)).strftime('%H:%M')
            target=dt.datetime.fromtimestamp(next_occurrence(clock,[selected],now))
        else:target=clock_target(phrase,dt.datetime.fromtimestamp(now))
    clock=target.strftime('%H:%M')
    return (next_occurrence(clock,days,now) if days else target.timestamp()),days,clock


def extract(text):
    """Find explicit timed actions before app launching and timer routing."""
    original=text.strip();low=original.lower()
    if re.match(r'^read(?: (?:aloud|word for word|verbatim))? (?:this|the following)(?: text|passage|article|document)?\s*[:\n]',low):return None
    explicit=bool(re.match(r'^schedule\b|^(?:set|create|add) (?:a |an )?(?:scheduled )?task\b',low))
    if not explicit and re.match(r'^(?:remind me|set|start|create)\b',low) and re.search(r'\b(?:timer|alarm|reminder)\b',low):return None
    body=re.sub(r'^(?:schedule(?: (?:a |the |my )?task)?|(?:set|create|add) (?:a |an )?(?:scheduled )?task)(?: to)?\s+','',original,flags=re.I) if explicit else original
    # Prefix times: "at 7 pm open Chrome", "in ten minutes, read the news".
    m=re.fullmatch(r'((?:at |in |tomorrow at |today at |every .+? at |each .+? at ).+?)(?:,\s*|\s+(?:to )?)(('+ACTION+r')[\s\S]+)',body,re.I)
    if m:
        try:deadline(m[1])
        except ValueError:
            if explicit:raise
        else:return m[2].strip(),m[1]
    if not explicit and re.match(r'^(?:weather|forecast)\b',low):return None
    # Suffix times: keep the original task text/casing intact.
    m=re.fullmatch(r'(.+?)\s+((?:in |at |today at |tomorrow at |every .+? at |each .+? at |next (?:'+ '|'.join(DAYS)+r') at |(?:'+ '|'.join(DAYS)+r') at )[^\n]+)',body,re.I)
    if m and (explicit or re.match(ACTION,m[1],re.I)):
        try:deadline(m[2])
        except ValueError:
            # Ignore non-time uses such as "look at this page".
            if explicit or re.match(r'(?:in (?:-?\d|zero|one|two|three|four|five|six|seven|eight|nine|ten|(?:a|an|a few|half an) (?:second|minute|hour|day))|at \d|tomorrow|today|next )',numeric(m[2]),re.I):raise
        else:return m[1].strip(),m[2]
    if explicit:raise ValueError('Say “schedule open Chrome at 7 pm” or “read the news in ten minutes”.')
    return None


class Tasks:
    def __init__(self,app):
        self.app=app;self.thread=None;self.lock=threading.RLock();self.notify=None;self.health={'state':'starting','detail':'Timed tasks are starting.'}
        with app.lock,app.db:
            app.db.execute('''CREATE TABLE IF NOT EXISTS scheduled_tasks(
                id TEXT PRIMARY KEY,number INTEGER UNIQUE,name TEXT,command TEXT,due REAL,status TEXT,
                source TEXT,person TEXT,provider TEXT,model TEXT,repeat_days TEXT,clock TEXT,
                created REAL,started REAL,finished REAL,result TEXT,pc_task_id TEXT)''')
            app.db.execute("UPDATE scheduled_tasks SET status='needs_attention',result='Interrupted by a restart. Review the result before choosing Run now; completed actions will not be repeated automatically.' WHERE status='running'")

    def snapshot(self):
        with self.app.lock:rows=[dict(r) for r in self.app.db.execute('SELECT * FROM scheduled_tasks ORDER BY CASE WHEN status IN (\'scheduled\',\'queued\',\'running\',\'paused\') THEN 0 ELSE 1 END,CASE WHEN status IN (\'scheduled\',\'queued\',\'running\',\'paused\') THEN due ELSE -due END LIMIT 60')]
        for row in rows:
            row['due_display']=dt.datetime.fromtimestamp(row['due']).astimezone().strftime('%a, %b %d · %I:%M %p %Z')
            row['repeat_days']=json.loads(row['repeat_days'])
            row['repeat_label']='One time' if not row['repeat_days'] else 'Every '+', '.join(DAYS[i].title() for i in row['repeat_days'])
            row['person_name']=self.app.memory.name(row['person']) if row['person'] else 'Guest'
        return rows

    def create(self,command,when,name='',source='pc'):
        if source not in ('pc','pi','browser'):raise ValueError('Choose PC, Pi, or browser.')
        if not isinstance(command,str) or not 1<=len(command.strip())<=32000:raise ValueError('Enter the task Andrew should run.')
        if not isinstance(name,str) or len(name)>80:raise ValueError('Use a task name under 80 characters.')
        due,days,clock=deadline(when);identity=secrets.token_hex(8)
        with self.app.lock,self.app.db:
            number=self.app.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM scheduled_tasks').fetchone()[0]
            provider=self.app.get('provider')
            self.app.db.execute('INSERT INTO scheduled_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,number,name.strip() or 'Task '+str(number),command.strip(),due,'scheduled',source,self.app.memory.current(source),provider,self.app.get(provider+'_model') or '',json.dumps(days),clock,time.time(),None,None,'',None))
        row=self.find(identity)
        row['due_display']=dt.datetime.fromtimestamp(due).astimezone().strftime('%a, %b %d · %I:%M %p %Z')
        row['repeat_label']='One time' if not days else 'Every '+', '.join(DAYS[i].title() for i in days)
        self.app.controls.show('home',source,'taskPanel','Scheduled tasks')
        return 'Task '+str(number)+' scheduled for '+row['due_display']+': '+row['command']+'. '+row['repeat_label']+'. The host PC must be awake; overdue queued tasks run when it becomes available.'

    def find(self,target):
        selector=re.sub(r'^(?:scheduled )?task(?: number)?\s*','',str(target).lower()).strip()
        with self.app.lock:
            rows=[dict(r) for r in self.app.db.execute('SELECT * FROM scheduled_tasks')]
        matches=[r for r in rows if r['id']==selector or str(r['number'])==numeric(selector) or r['name'].casefold()==selector]
        if len(matches)!=1:raise ValueError('Use a task number from Scheduled tasks, such as task number 1.')
        return matches[0]

    def modify(self,target,action,when=None):
        row=self.find(target);values={}
        if action=='cancel':values={'status':'cancelled','result':'Cancelled. Actions already completed are kept.'}
        elif row['status']=='running':raise ValueError('That task is running. Cancel it before changing its time.')
        elif action=='pause':values={'status':'paused'}
        elif action=='resume':values={'status':'scheduled','result':''}
        elif action=='run':values={'status':'queued','due':time.time(),'result':'','pc_task_id':None}
        elif action=='time':
            due,days,clock=deadline(when);values={'status':'scheduled','due':due,'repeat_days':json.dumps(days),'clock':clock,'result':'','pc_task_id':None}
        else:raise ValueError('Choose cancel, pause, resume, run, or time.')
        with self.app.lock,self.app.db:
            self.app.db.execute('UPDATE scheduled_tasks SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*values.values(),row['id']])
        if action=='cancel' and row['pc_task_id'] and self.app.pc_agent:
            if self.app.pc_agent.status().get('task_id')==row['pc_task_id']:self.app.pc_agent.cancel()
        return 'Task '+str(row['number'])+': '+({'cancel':'cancelled','pause':'paused','resume':'scheduled','run':'queued to run','time':'rescheduled'}[action])+'.'

    def route(self,text,source):
        low=text.lower().strip()
        if low in ('scheduled tasks','list tasks','list scheduled tasks','show scheduled tasks','show my tasks','what tasks are scheduled','what tasks do i have'):
            self.app.controls.show('home',source,'taskPanel','Scheduled tasks');rows=self.snapshot()
            return '; '.join('Task '+str(r['number'])+' · '+r['name']+': '+r['status']+' · '+r['due_display']+' · '+r['command'] for r in rows[:12]) or 'No tasks are scheduled. Say open Chrome at 7 pm.'
        m=re.fullmatch(r'(cancel|delete|pause|resume|run|retry) (?:the |my )?((?:scheduled )?task(?: number)? .+?)(?: now)?',low)
        if m:return self.modify(m[2],{'delete':'cancel','retry':'run'}.get(m[1],m[1]))
        m=re.fullmatch(r'(?:change|edit|move|reschedule) ((?:scheduled )?task(?: number)? .+?) (?:to|for|at) (.+)',low)
        if m:return self.modify(m[1],'time',m[2])
        intent=extract(text)
        if intent:return self.create(intent[0],intent[1],source=source)
        return None

    def tick(self,now=None):
        now=time.time() if now is None else now
        with self.lock:
            with self.app.lock,self.app.db:self.app.db.execute("UPDATE scheduled_tasks SET status='queued' WHERE status='scheduled' AND due<=?",(now,))
            if self.thread and self.thread.is_alive():return
            agent=self.app.pc_agent
            if (agent and (agent.status()['state'] in ('running','needs_input','paused') or (agent.thread and agent.thread.is_alive()))) or self.app.ai_lock.locked():return
            with self.app.lock,self.app.db:
                row=self.app.db.execute("SELECT * FROM scheduled_tasks WHERE status='queued' ORDER BY due,number LIMIT 1").fetchone()
                if not row:return
                row=dict(row)
                self.app.db.execute("UPDATE scheduled_tasks SET status='running',started=?,finished=NULL,result='',pc_task_id=NULL WHERE id=? AND status='queued'",(now,row['id']))
            self.thread=threading.Thread(target=self.execute,args=(row,),daemon=True);self.thread.start()

    def execute(self,row):
        answer='';status='complete';delegated=False
        try:
            self.app.request.scheduled_context=True;self.app.request.person=row['person']
            self.app.request.scheduled_provider=row['provider'];self.app.request.scheduled_model=row['model']
            self.app.request.scheduled_task_id=row['id']
            with self.app.lock:
                if self.app.db.execute('SELECT status FROM scheduled_tasks WHERE id=?',(row['id'],)).fetchone()[0]!='running':return
            agent=self.app.pc_agent;before=agent.status().get('task_id') if agent else None
            answer=self.app.command(row['command'],row['source'])
            if answer.startswith(('I am already working on a PC task.','I am still answering the previous AI question.')):
                status='queued';answer='Waiting for the current task or AI reply to finish.';return
            if getattr(self.app.request,'control_failed',False):status='failed'
            state=agent.status() if agent else {}
            if state.get('task_id') and state['task_id']!=before and state.get('scheduled_task_id')==row['id']:
                delegated=True;identity=state['task_id']
                with self.app.lock,self.app.db:self.app.db.execute('UPDATE scheduled_tasks SET pc_task_id=?,result=? WHERE id=?',(identity,answer,row['id']))
                while True:
                    state=agent.result(identity)
                    if state and state['state']!='running':break
                    time.sleep(.25)
                status={'complete':'complete','cancelled':'cancelled','needs_input':'needs_attention','paused':'needs_attention'}.get(state['state'],'failed')
                answer=state.get('answer') or state.get('progress') or 'Review the PC task result.'
        except Exception as exc:
            status='failed';answer=str(exc) if isinstance(exc,(ValueError,RuntimeError)) else 'The task could not finish. Its result is saved; choose Run now to retry.'
        finally:
            try:
                now=time.time();values={'status':status,'finished':now,'result':answer}
                days=json.loads(row['repeat_days'])
                if days and status=='complete':values.update(status='scheduled',due=next_occurrence(row['clock'],days,max(now,row['due'])))
                with self.app.lock,self.app.db:
                    self.app.db.execute('UPDATE scheduled_tasks SET '+','.join(k+'=?' for k in values)+" WHERE id=? AND status='running'",[*values.values(),row['id']])
                if answer and not delegated and status!='queued':
                    notification='Scheduled task '+str(row['number'])+': '+answer
                    self.app.event(notification)
                    if self.notify:
                        try:self.notify(notification,row['source'])
                        except Exception:self.app.event('Scheduled task '+str(row['number'])+' finished; its reply is on screen because the speaker or connection was unavailable.')
            finally:
                self.app.request.scheduled_context=False;self.app.request.person=None
                self.app.request.scheduled_provider=None;self.app.request.scheduled_model=None
                self.app.request.scheduled_task_id=None

    def run_loop(self):
        while True:
            try:
                self.tick();self.health={'state':'ready','detail':'Timed tasks are ready.','checked_at':time.time()}
            except Exception:
                self.health={'state':'retrying','detail':'Timed tasks hit a local error and are retrying. Queued work is retained.','checked_at':time.time()}
            time.sleep(.5)
