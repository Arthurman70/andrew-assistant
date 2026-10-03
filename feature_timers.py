"""One local scheduler interface for voice, screens and every AI provider."""
import datetime as dt
import math
import re
import secrets
import time
import json
from feature_alarm_recurrence import repeat_clause, weekdays, repeat_label, next_occurrence

LIVE = ('active','ringing','paused')
NUMBERS = dict(zip(('one','two','three','four','five','six','seven','eight','nine','ten',
    'eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen',
    'twenty'),range(1,21))) | {'thirty':30,'forty':40,'fifty':50,'sixty':60}


def numeric(text):
    text=re.sub(r'\b('+ '|'.join(NUMBERS)+r')\b',lambda m:str(NUMBERS[m[0]]),text.lower())
    return re.sub(r'\b(20|30|40|50)[ -]+([1-9])\b',lambda m:str(int(m[1])+int(m[2])),text)


def duration(seconds):
    seconds=max(0,math.ceil(seconds));parts=[]
    for unit,size in (('day',86400),('hour',3600),('minute',60),('second',1)):
        count,seconds=divmod(seconds,size)
        if count:parts.append(f'{count} {unit}'+('s' if count!=1 else ''))
    return ' '.join(parts) or '0 seconds'


def clock_target(text,now=None):
    now=now or dt.datetime.now()
    text=numeric(text.strip())
    text=re.sub(r'\b(in the morning|in the afternoon|in the evening|at night)\b',
        lambda m:'am' if m[1]=='in the morning' else 'pm',text)
    text=re.sub(r'(?<=\d)\s*([ap])\.?\s?m\b\.?',r' \1m',text)
    text=re.sub(r"\s*o'?clock\b",'',text)
    text=text.replace('noon','12 pm').replace('midnight','12 am')
    date_match=re.search(r'\b(\d{4}-\d{2}-\d{2}|today|tomorrow)\b',text)
    date=None
    if date_match:
        token=date_match[1]
        date=now.date()+dt.timedelta(days=token=='tomorrow') if token in ('today','tomorrow') else dt.date.fromisoformat(token)
        text=(text[:date_match.start()]+' '+text[date_match.end():]).strip()
    text=re.sub(r'^(?:at|for)\s+','',text)
    text=re.sub(r'\b(\d{1,2})\s+(?:oh|o|zero)\s+([1-9])\b',r'\1:0\2',text)
    match=re.fullmatch(r'(\d{1,2})(?:[: ](\d{2}))?\s*(am|pm)?',text.strip())
    if not match:raise ValueError('Use a time such as 7:30 am, tomorrow at 7 am, or 19:30.')
    hour,minute,period=match.groups();hour=int(hour);minute=int(minute or 0)
    if minute>59 or (period and not 1<=hour<=12) or (not period and not 0<=hour<=23):
        raise ValueError('Use a valid clock time such as 7:30 am.')
    if period:hour=hour%12+(12 if period=='pm' else 0)
    target=dt.datetime.combine(date or now.date(),dt.time(hour,minute))
    if date is None and target<=now:target+=dt.timedelta(days=1)
    if target<=now:raise ValueError('That alarm time has passed. Choose a future time.')
    return target


class Schedule:
    def __init__(self,app):
        self.app=app;self.recent={}
        with app.lock,app.db:
            columns={r[1] for r in app.db.execute('PRAGMA table_info(timers)')}
            for name,type_ in (('number','INTEGER'),('duration','REAL'),('remaining','REAL'),('created_at','REAL'),
                               ('repeat_days','TEXT'),('alarm_time','TEXT'),('next_due','REAL')):
                if name not in columns:app.db.execute(f'ALTER TABLE timers ADD COLUMN {name} {type_}')
            # Preserve every deadline/status while assigning durable identities.
            for kind in ('timer','alarm','reminder'):
                number=app.db.execute('SELECT COALESCE(MAX(number),0) FROM timers WHERE kind=?',(kind,)).fetchone()[0]
                for row in app.db.execute('SELECT * FROM timers WHERE kind=? AND number IS NULL ORDER BY rowid',(kind,)).fetchall():
                    number+=1
                    name=f'{kind.title()} {number}' if row['name'] in ('default','',None) else row['name']
                    app.db.execute('UPDATE timers SET number=?,name=?,duration=?,created_at=? WHERE id=?',
                        (number,name,row['duration'],time.time(),row['id']))

    def rows(self,kind=None,source=None):
        with self.app.lock:
            rows=[dict(r) for r in self.app.db.execute("SELECT * FROM timers WHERE status IN ('active','ringing','paused') ORDER BY due,number")]
        return [r for r in rows if (not kind or r['kind']==kind) and (not source or r['source']==source)]

    def label(self,row):
        alias=f'{row["kind"].title()} {row["number"]}'
        return alias if row['name'].lower()==alias.lower() else f'{row["name"]} ({alias.lower()})'

    def remember(self,row,source):
        self.recent[source]=(time.monotonic()+120,row['id'],self.app.memory.current(source))

    def context(self,source):
        recent=self.recent.get(source)
        if recent and recent[0]>time.monotonic() and recent[2]==self.app.memory.current(source):
            return next((r for r in self.rows() if r['id']==recent[1]),None)
        return None

    def create(self,name,seconds,kind='timer',source=None,due=None,repeat_days=None):
        if kind not in ('timer','alarm','reminder'):raise ValueError('Unknown schedule type.')
        if (type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0 or
                (kind=='timer' and seconds>604800)):
            raise ValueError('Use a positive duration; countdown timers can run up to seven days.')
        source=source or getattr(self.app.request,'source','pc')
        name=str(name).strip()
        if len(name)>80:raise ValueError('Use a name under 80 characters.')
        with self.app.lock,self.app.db:
            number=self.app.db.execute('SELECT COALESCE(MAX(number),0)+1 FROM timers WHERE kind=?',(kind,)).fetchone()[0]
            name=name if name and name.lower()!='default' else f'{kind.title()} {number}'
            identity=secrets.token_hex(5);now=time.time()
            deadline=due or now+seconds
            clock=dt.datetime.fromtimestamp(deadline).strftime('%H:%M') if kind=='alarm' else None
            if repeat_days:
                if kind!='alarm':raise ValueError('Only clock alarms can repeat.')
                deadline=next_occurrence(clock,repeat_days,now)
            self.app.db.execute('INSERT INTO timers(id,name,due,status,kind,source,number,duration,created_at,repeat_days,alarm_time) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (identity,name,deadline,'active',kind,source,number,seconds,now,json.dumps(repeat_days) if repeat_days else None,clock))
        row=next(r for r in self.rows() if r['id']==identity);self.remember(row,source)
        return row

    def describe(self,row):
        if row['status']=='ringing':detail='ringing' if row['kind']=='alarm' else 'finished'
        elif row['status']=='paused':detail='paused with '+duration(row['remaining'] or 0)+' left'
        elif row['kind']=='alarm':detail=dt.datetime.fromtimestamp(row['due']).strftime('%A %I:%M %p')
        else:detail=duration(row['due']-time.time())+' left'
        device={'pc':'PC','pi':'Pi','browser':'browser'}.get(row['source'],row['source'])
        repeat=', '+repeat_label(row) if weekdays(row) else ''
        return f'{self.label(row)}: {detail}{repeat}, on {device}'

    def resolve(self,selector,kind,source,scope=None,ringing=False,allow_recent=True):
        rows=self.rows(kind,scope)
        if ringing:rows=[r for r in rows if r['status']=='ringing']
        original=selector.lower().strip()
        selector=re.sub(r'^(?:the |my |a |an )','',original)
        selector=re.sub(r'^(?:timer|alarm)\s+(?:called|named)\s+','',selector)
        selector=re.sub(r'^(?:timer|alarm)\s*','',selector)
        selector=re.sub(r'\s*(?:timer|alarm)$','',selector).strip()
        match=re.fullmatch(r'(?:number\s+|#)?(\d+)',numeric(selector))
        if match:rows=[r for r in rows if r['number']==int(match[1])]
        elif selector in ('it','that','this','that one','this one',''):
            contextual=self.context(source)
            if contextual and (selector or allow_recent) and any(r['id']==contextual['id'] for r in rows):rows=[contextual]
        elif selector in ('latest','last','newest','most recent'):
            if rows:rows=[max(rows,key=lambda r:r['created_at'] or 0)]
        elif selector in ('next','first','earliest'):
            if rows:rows=[min(rows,key=lambda r:r['due'])]
        elif selector in ('longest','shortest') and kind=='timer':
            if rows:rows=[(max if selector=='longest' else min)(rows,key=lambda r:r['remaining'] if r['status']=='paused' else r['due']-time.time())]
        elif selector:
            exact=[r for r in rows if r['name'].lower()==selector]
            if exact:rows=exact
            elif kind=='alarm':
                try:
                    target=clock_target(selector)
                    rows=[r for r in rows if dt.datetime.fromtimestamp(r['due']).time().replace(second=0,microsecond=0)==target.time()]
                except ValueError:rows=[]
            else:rows=[]
        if len(rows)>1 and not scope:
            local=[r for r in rows if r['source']==source]
            if len(local)==1:rows=local
        if len(rows)==1:self.remember(rows[0],source);return rows[0],None
        self.app.request.control_failed=True
        if not rows:return None,f'No matching {kind or "timer or alarm"}. Say list {kind+"s" if kind else "timers and alarms"} to see the current names and numbers.'
        options='; '.join(self.describe(r) for r in rows[:6])
        return None,f'You have several {kind+"s" if kind else "timers and alarms"}: {options}. Use the name or number, for example {rows[0]["kind"]} number {rows[0]["number"]}.'

    def modify(self,selector,kind,action,source,value=None,scope=None):
        with self.app.lock:
            return self._modify(selector,kind,action,source,value,scope)

    def _modify(self,selector,kind,action,source,value=None,scope=None):
        ringing=action=='dismiss'
        row,error=self.resolve(selector,kind,source,scope,ringing,action not in ('cancel','dismiss'))
        if error:
            if ringing and not self.rows(kind,scope):return f'No {kind} is ringing.'
            if ringing and not any(r['status']=='ringing' for r in self.rows(kind,scope)):return f'No {kind} is ringing.'
            return error
        kind=row['kind']
        label=self.label(row);now=time.time();updates={}
        if action=='dismiss' and kind=='alarm' and weekdays(row):
            next_due=row['next_due']
            if not next_due or next_due<=now:next_due=next_occurrence(row['alarm_time'],weekdays(row),now)
            updates={'status':'active','due':next_due,'next_due':None}
            answer=f'Dismissed {label}. Next: '+dt.datetime.fromtimestamp(next_due).strftime('%A %I:%M %p')+'.'
        elif action in ('cancel','dismiss'):updates={'status':'cancelled','next_due':None};answer=f'Cancelled {label}.'
        elif action=='rename':
            name=value.strip()
            if not name or len(name)>80:raise ValueError('Use a name under 80 characters.')
            updates={'name':name};answer=f'{label} is now {name} ({kind} {row["number"]}).'
        elif action=='pause':
            if row['kind']!='timer':return 'Only countdown timers can be paused. You can change or snooze an alarm.'
            if row['status']=='paused':return label+' is already paused.'
            if row['status']=='ringing' or row['due']<=now:return label+' has finished. Restart it or dismiss it.'
            updates={'status':'paused','remaining':max(0,row['due']-now)};answer=label+' paused.'
        elif action=='resume':
            if row['status']!='paused':return label+' is not paused.'
            updates={'status':'active','due':now+(row['remaining'] or 1),'remaining':None};answer=label+' resumed.'
        elif action=='restart':
            if kind!='timer':return 'Give the alarm a clock time, such as change alarm number 1 to 7:30 am.'
            if not row['duration']:return label+' predates saved durations. Say change timer number '+str(row['number'])+' to five minutes, or choose its new duration on screen.'
            updates={'status':'active','due':now+(row['duration'] or 1),'remaining':None};answer=label+' restarted for '+duration(row['duration'] or 1)+'.'
        elif action=='reset':
            if kind=='alarm':
                clock,days,specified=repeat_clause(value)
                if not specified:days=weekdays(row)
                target=clock_target(clock)
                alarm_time=target.strftime('%H:%M')
                deadline=next_occurrence(alarm_time,days,now) if days else target.timestamp()
                updates={'due':deadline,'status':'active','remaining':None,'alarm_time':alarm_time,
                         'repeat_days':json.dumps(days) if days else None,'next_due':None}
                answer=f'{label} set for '+dt.datetime.fromtimestamp(deadline).strftime('%A %I:%M %p')+('. '+repeat_label(updates)+'.' if days else '.')
            else:
                seconds=self.app.seconds(value)
                updates=({'remaining':seconds,'duration':seconds} if row['status']=='paused' else
                         {'due':now+seconds,'duration':seconds,'status':'active','remaining':None})
                answer=f'{label} reset to {duration(seconds)}.'+(' Still paused.' if row['status']=='paused' else '')
        elif action=='repeat':
            if kind!='alarm':return 'Only clock alarms can repeat.'
            days=value
            clock=row['alarm_time'] or dt.datetime.fromtimestamp(row['due']).strftime('%H:%M')
            deadline=next_occurrence(clock,days,now) if days else clock_target(clock).timestamp()
            updates={'repeat_days':json.dumps(days) if days else None,'alarm_time':clock,'due':deadline,'status':'active','next_due':None}
            answer=label+': '+repeat_label(updates)+'.'
        elif action in ('add','subtract','snooze'):
            if action=='snooze' and kind=='alarm' and row['status']!='ringing':
                return label+' is not ringing. Change its clock time to reschedule it.'
            seconds=self.app.seconds(value)
            current=row['remaining'] if row['status']=='paused' else max(0,row['due']-now)
            remaining=seconds if action=='snooze' else current+(seconds if action=='add' else -seconds)
            if remaining<=0 or (kind=='timer' and remaining>604800):raise ValueError('That would leave no time or exceed the countdown limit. Nothing was changed.')
            updates=({'remaining':remaining} if row['status']=='paused' else {'due':now+remaining,'status':'active'})
            if kind=='alarm' and weekdays(row) and not row['next_due']:
                updates['next_due']=next_occurrence(row['alarm_time'],weekdays(row),now)
            if kind=='timer' and row['status']=='ringing':updates['duration']=remaining
            answer=f'{label} '+('snoozed for ' if action=='snooze' else 'now has ')+duration(remaining)+(' left.' if action!='snooze' else '.')
        else:raise ValueError('Unsupported schedule action.')
        with self.app.lock,self.app.db:
            # A scheduler tick or concurrent command cannot resurrect cancelled work.
            actual=self.app.db.execute('SELECT status FROM timers WHERE id=?',(row['id'],)).fetchone()
            if not actual or actual['status'] not in LIVE:return label+' was already cancelled. Nothing changed.'
            self.app.db.execute('UPDATE timers SET '+','.join(k+'=?' for k in updates)+' WHERE id=?',(*updates.values(),row['id']))
        return answer

    def route(self,text,source):
        low=text.lower().strip().replace('’',"'");scope=None
        prefix=re.match(r'^(?:the |that )?one on (?:the |my )?(pc|computer|pi|raspberry pi|browser)[, ]+(.+)$',low)
        if prefix:
            scope={'computer':'pc','raspberry pi':'pi'}.get(prefix[1],prefix[1]);low=prefix[2].strip()
        suffix=re.search(r'\s+on (?:the |my )?(pc|computer|pi|raspberry pi|browser)$',low)
        if suffix:
            scope={'computer':'pc','raspberry pi':'pi'}.get(suffix[1],suffix[1]);low=low[:suffix.start()]
        m=re.fullmatch(r'(?:make|set|change) (.*?alarm(?: number \w+)?) (?:to )?repeat (.+)',low)
        if not m:m=re.fullmatch(r'repeat (.*?alarm(?: number \w+)?) (.+)',low)
        if m:
            _,days,specified=repeat_clause(m[2])
            if not specified:raise ValueError('Say repeat alarm number 1 every weekday, or every Monday and Friday.')
            return self.modify(m[1],'alarm','repeat',source,days,scope)
        m=re.fullmatch(r'(?:stop repeating|remove repetition from|make one time) (.*?alarm(?: number \w+)?)',low)
        if m:return self.modify(m[1],'alarm','repeat',source,[],scope)
        if low in ('list timers','what timers are running','timers','show my timers','list my timers',
                   'list alarms','alarms','what alarms are set','what alarms do i have','list my alarms',
                   'list timers and alarms','what timers and alarms do i have'):
            kind=None if 'timers and alarms' in low else ('alarm' if 'alarm' in low else 'timer')
            rows=self.rows(kind,scope)
            if kind is None:rows=[r for r in rows if r['kind'] in ('timer','alarm')]
            if len(rows)==1:self.remember(rows[0],source)
            return '; '.join(self.describe(r) for r in rows) or ('No alarms are set.' if kind=='alarm' else 'No active timers.' if kind=='timer' else 'No timers or alarms are set.')
        m=re.fullmatch(r'(cancel|delete|stop|dismiss|silence|turn off) (?:all (?:my |the )?)(timers|alarms|timers and alarms)',low)
        if m:
            kinds=('timer','alarm') if m[2]=='timers and alarms' else (m[2][:-1],)
            rows=[r for r in self.rows(source=scope) if r['kind'] in kinds]
            if m[1] not in ('cancel','delete'):
                rows=[r for r in rows if r['status']=='ringing']
                for row in rows:self.modify(str(row['number']),row['kind'],'dismiss',source,scope=row['source'])
                return f'Dismissed {len(rows)} ringing '+m[2]+'.'
            with self.app.lock,self.app.db:
                for r in rows:self.app.db.execute("UPDATE timers SET status='cancelled' WHERE id=?",(r['id'],))
            return f'Cancelled {len(rows)} '+m[2]+'.'
        m=re.fullmatch(r'(?:add|extend(?: by)?) (.+?) (?:to|on) (.*?(?:timer|alarm)(?: (?:number )?\w+)?)',low)
        if m:return self.modify(m[2],'alarm' if re.search(r'\balarm\b',m[2]) else 'timer','add',source,m[1],scope)
        m=re.fullmatch(r'extend (.*?(?:timer|alarm)(?: number \w+)?) by (.+)',low)
        if m:return self.modify(m[1],'alarm' if re.search(r'\balarm\b',m[1]) else 'timer','add',source,m[2],scope)
        m=re.fullmatch(r'(?:subtract|remove|take off) (.+?) (?:from|off) (.*?(?:timer|alarm)(?: number \w+)?)',low)
        if m:return self.modify(m[2],'alarm' if re.search(r'\balarm\b',m[2]) else 'timer','subtract',source,m[1],scope)
        m=re.fullmatch(r'(?:rename|name|call) (.*?(?:timer|alarm)(?: (?:number )?\w+)?) (?:to|as|called|named) (.+)',low)
        if m:return self.modify(m[1],'alarm' if 'alarm' in m[1] else 'timer','rename',source,m[2],scope)
        m=re.fullmatch(r'(?:change|edit|reset|move|reschedule) (.+?) (?:to|for|at) (.+)',low)
        if not m:m=re.fullmatch(r'set (.+?) to (.+)',low)
        if m:
            kind='alarm' if re.search(r'\balarm\b',m[1]) else 'timer' if re.search(r'\btimer\b',m[1]) else None
            existing=any(r['name'].lower()==re.sub(r'^(?:the |my )','',m[1]) for r in self.rows(source=scope))
            if kind or existing:
                if not kind and re.search(r'\b(?:seconds?|minutes?|hours?)\b',m[2]):kind='timer'
                return self.modify(m[1],kind,'reset',source,m[2],scope)
        m=re.fullmatch(r'(?:make it|change it to|reset it to|make that) (.+)',low)
        if m:
            row=self.context(source)
            if row:return self.modify('it',row['kind'],'reset',source,m[1],scope)
            return 'Name the timer or alarm, for example change timer number 1 to ten minutes.'
        m=re.fullmatch(r'(pause|resume|restart|cancel|dismiss|snooze) (?:it|that)(?: for (.+))?',low)
        if m:
            row=self.context(source)
            if row:return self.modify('it',row['kind'],m[1],source,m[2] or ('ten minutes' if m[1]=='snooze' else None),scope)
            return 'Name the timer or alarm, for example pause timer number 1.'
        m=re.fullmatch(r'(cancel|delete|pause|resume|restart) (.+)',low)
        if m and any(r['name'].lower()==re.sub(r'^(?:the |my )','',m[2]) for r in self.rows(source=scope)):
            return self.modify(m[2],None,'cancel' if m[1]=='delete' else m[1],source,scope=scope)
        m=re.fullmatch(r'(?:add|give it) (.+?)(?: more)?',low)
        if m and re.search(r'\b(?:seconds?|minutes?|hours?)\b',m[1]):
            amount=re.sub(r'\s+(?:to|on) (?:it|that|the one|that one)$','',m[1])
            if re.search(r'\b(?:to|on|from)\b',amount):return None
            contextual=self.context(source)
            return self.modify('it',contextual['kind'] if contextual and (not scope or contextual['source']==scope) else 'timer','add',source,amount,scope)
        m=re.fullmatch(r'(cancel|delete|stop|dismiss|silence|turn off|pause|resume|restart|snooze) (.*?(?:timer|alarm)(?: (?:number |called |named )?[^ ]+)?)(?: for (.+))?',low)
        if m:
            action,selector,value=m.groups();kind='alarm' if re.search(r'\balarm\b',selector) else 'timer'
            action={'delete':'cancel','silence':'dismiss','turn off':'dismiss'}.get(action,action)
            if action=='stop':action='dismiss' if kind=='alarm' else 'cancel'
            if action=='snooze':value=value or 'ten minutes'
            return self.modify(selector,kind,action,source,value,scope)
        m=re.fullmatch(r'(?:how much time is (?:left|remaining)(?: on)?|time left on|when does|when will) (.*?timer(?: number \w+)?)(?: (?:finish|end))?',low)
        if m:
            row,error=self.resolve(m[1],'timer',source,scope)
            return error or self.describe(row)+'.'
        m=re.fullmatch(r'(?:set|start|create) (?:a |an |my |the )?(?:(.*?) )?timer for (.+?)(?: (?:called|named) (.+))?',low)
        if m:
            name,amount,trailing=m.groups();row=self.create(trailing or name or '',self.app.seconds(amount),source=scope or source)
            return f'{self.label(row)} set for {duration(row["duration"])}.'
        m=re.fullmatch(r'(?:set|start|create) (?:a |an )?(.+?) timer(?: (?:called|named) (.+))?',low)
        if m:
            row=self.create(m[2] or '',self.app.seconds(m[1]),source=scope or source)
            return f'{self.label(row)} set for {duration(row["duration"])}.'
        alarm=re.sub(r'^wake me(?: up)? (?:at|by)\s+','set alarm for ',low)
        m=re.fullmatch(r'(?:set|create|start) (?:a |an |my |the )?(?:(.*?) )?alarm(?: (?:called|named) (.+?))? (?:for|at) (.+?)(?: (?:called|named) (.+))?',alarm)
        if m:
            before,named,clock,after=m.groups();clock,days,_=repeat_clause(clock);target=clock_target(clock)
            row=self.create(after or named or before or '',(target-dt.datetime.now()).total_seconds(),'alarm',scope or source,target.timestamp(),days)
            return 'Alarm set for '+dt.datetime.fromtimestamp(row['due']).strftime('%A %I:%M %p')+f': {self.label(row)}.'+(' '+repeat_label(row)+'.' if days else '')+' The PC must remain awake.'
        if re.fullmatch(r'(?:set|start|create|change|edit|reset) (?:a |an |my |the )?(?:timer|alarm)',low):
            return ('Say set a timer for five minutes, or change timer number 1 to ten minutes.' if 'timer' in low else
                    'Say set an alarm for 7:30 am, or change alarm number 1 to 8 am.')
        return None
