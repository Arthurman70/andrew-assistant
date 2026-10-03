"""Durable everyday assistant functions, independent of a language model."""
import ast
import datetime as dt
import json
import operator
import random
import re
import time
from urllib.parse import urlencode

class Daily:
    def __init__(self,app):self.app=app;self.stopwatches={}
    def lists(self):return self.app.get('lists') or {}
    def list_change(self,name,item,remove=False):
        name=name.strip().lower()[:40] or 'shopping';item=item.strip()[:180]
        if not item:raise ValueError('Name an item for the list.')
        with self.app.lock:
            lists=self.lists();values=lists.setdefault(name,[])
            if remove:values[:]=[x for x in values if x.lower()!=item.lower()]
            elif item.lower() not in [x.lower() for x in values]:values.append(item)
            self.app.set('lists',lists)
        return f'{"Removed" if remove else "Added"} {item} {"from" if remove else "to"} your {name} list.'
    def when(self,text):
        now=dt.datetime.now();low=text.lower().strip()
        if low.startswith('in '):return now+dt.timedelta(seconds=self.app.seconds(low[3:]))
        m=re.fullmatch(r'(?:(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday) )?(?:at )?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',low)
        if not m:raise ValueError('Use “in ten minutes” or “tomorrow at 7:30 pm”.')
        day,hour,minute,period=m.groups();hour=int(hour);minute=int(minute or 0)
        if minute>59 or (period and not 1<=hour<=12) or (not period and hour>23):raise ValueError('That is not a valid time.')
        if period:hour=hour%12+(12 if period=='pm' else 0)
        target=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        if day=='tomorrow':target+=dt.timedelta(days=1)
        elif day not in (None,'today'):
            target+=dt.timedelta(days=(['monday','tuesday','wednesday','thursday','friday','saturday','sunday'].index(day)-now.weekday())%7)
            if target<=now:target+=dt.timedelta(days=7)
        elif target<=now:
            if day=='today':raise ValueError('That time has already passed today.')
            target+=dt.timedelta(days=1)
        return target
    def reminder(self,message,when,source):
        target=self.when(when);self.app.request.source=source
        self.app.timer(message[:80],(target-dt.datetime.now()).total_seconds(),'reminder')
        return 'I will remind you to '+message+' '+target.strftime('on %A at %I:%M %p')+'.'
    def weather(self,city=None):
        from core import request_json
        city=city or self.app.get('weather_city')
        if not city:return 'Tell me a city, such as “weather in Boston”.'
        places=request_json('https://geocoding-api.open-meteo.com/v1/search?'+urlencode({'name':city,'count':1,'language':'en'}),timeout=10).get('results',[])
        if not places:return 'I could not find that city. Try the city and state.'
        p=places[0];self.app.set('weather_city',city)
        result=request_json('https://api.open-meteo.com/v1/forecast?'+urlencode({'latitude':p['latitude'],'longitude':p['longitude'],'current':'temperature_2m,apparent_temperature,precipitation','temperature_unit':'fahrenheit','timezone':'auto'}),timeout=10)['current']
        return f'In {p["name"]}, it is {round(result["temperature_2m"])} degrees Fahrenheit and feels like {round(result["apparent_temperature"])}. Current precipitation is {result["precipitation"]} millimeters. Weather from Open-Meteo.'
    def news(self):
        import urllib.request,xml.etree.ElementTree as ET
        with urllib.request.urlopen('https://feeds.bbci.co.uk/news/world/rss.xml',timeout=10) as r:body=r.read(500000)
        root=ET.fromstring(body);titles=[x.findtext('title','') for x in root.findall('./channel/item')[:5]]
        return 'BBC World headlines: '+'; '.join(titles)
    @staticmethod
    def calculate(expression):
        expression=expression.lower().replace('multiplied by','*').replace('times','*').replace('divided by','/').replace('plus','+').replace('minus','-')
        if len(expression)>120:raise ValueError('Use a shorter calculation.')
        ops={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Mod:operator.mod}
        def visit(node):
            if isinstance(node,ast.Constant) and type(node.value) in (int,float) and abs(node.value)<1e12:return node.value
            if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):return visit(node.operand)*(1 if isinstance(node.op,ast.UAdd) else -1)
            if isinstance(node,ast.BinOp) and type(node.op) in ops:return ops[type(node.op)](visit(node.left),visit(node.right))
            raise ValueError('Use numbers with plus, minus, times, or divided by.')
        try:return f'The answer is {visit(ast.parse(expression,mode="eval").body):g}.'
        except (SyntaxError,ZeroDivisionError,OverflowError):raise ValueError('I could not calculate that expression.')
    def snapshot(self):
        return {'lists':self.lists(),'routines':self.app.get('routines') or {},'weather_city':self.app.get('weather_city') or ''}
    def route(self,text,source):
        low=text.lower()
        m=re.fullmatch(r'(?:add|put) (.+?) (?:to|on) (?:my |the )?(shopping|grocery|to do|todo|[\w -]{1,30}) list',text,re.I)
        if m:return self.list_change(m[2],m[1])
        m=re.fullmatch(r'(?:remove|cross off) (.+?) (?:from|on) (?:my |the )?(.+?) list',text,re.I)
        if m:return self.list_change(m[2],m[1],True)
        m=re.fullmatch(r'(?:read|show|what is on|what\'s on)(?: my| the)? (.+?) list',text,re.I)
        if m:return ', '.join(self.lists().get(m[1].lower(),[])) or 'That list is empty.'
        if low in ('show lists','my lists'):return '; '.join(k+': '+', '.join(v) for k,v in self.lists().items()) or 'Your lists are empty.'
        m=re.fullmatch(r'(?:remind me to|reminder to) (.+?) (in .+|(?:today |tomorrow |monday |tuesday |wednesday |thursday |friday |saturday |sunday )?at .+)',text,re.I)
        if m:return self.reminder(m[1],m[2],source)
        m=re.fullmatch(r'(?:schedule|add to (?:my )?calendar) (.+?) ((?:tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday) at .+)',text,re.I)
        if m:return self.reminder(m[1],m[2],source)
        if low in ('my calendar','my reminders','read reminders','what is on my calendar'):
            rows=[r for r in self.app.status()['timers'] if r['kind']=='reminder']
            return '; '.join(r['name']+' '+dt.datetime.fromtimestamp(r['due']).strftime('%A %I:%M %p') for r in rows) or 'You have no upcoming reminders.'
        m=re.fullmatch(r'(?:cancel|delete) (?:the )?(.+?) reminder',text,re.I)
        if m:
            with self.app.lock,self.app.db:self.app.db.execute("UPDATE timers SET status='cancelled' WHERE lower(name)=? AND kind='reminder'",(m[1].lower(),))
            return 'Cancelled that reminder.'
        if low in ('stop','dismiss','stop timers','dismiss alarms'):
            with self.app.lock,self.app.db:changed=self.app.db.execute("UPDATE timers SET status='cancelled' WHERE status='ringing' AND source=?",(source,)).rowcount
            if changed:return 'Dismissed.'
        if low in ('start stopwatch','reset stopwatch'):
            self.stopwatches[source]=time.monotonic();return 'Stopwatch started.'
        if low in ('stop stopwatch','read stopwatch','stopwatch'):
            if source not in self.stopwatches:return 'No stopwatch is running.'
            elapsed=time.monotonic()-self.stopwatches[source]
            if low=='stop stopwatch':self.stopwatches.pop(source)
            return f'{int(elapsed//60)} minutes and {int(elapsed%60)} seconds.'
        m=re.fullmatch(r'(?:what is |what\'s )?(?:the )?weather(?: like)?(?: in (.+))?',text,re.I)
        if m:return self.weather(m[1])
        if low in ('news','read the news','headlines','world news'):return self.news()
        if low in ('daily briefing','good morning'):
            return dt.datetime.now().strftime('Good morning. It is %A, %B %d. ')+self.route('my reminders',source)+' '+self.weather()
        m=re.fullmatch(r'(?:calculate|what is) ([\d\s()+*/.%-]+|\d.+(?:plus|minus|times|divided by).+)',low)
        if m:return self.calculate(m[1])
        m=re.fullmatch(r'convert (-?\d+(?:\.\d+)?) (celsius|fahrenheit|kilometers|miles|kilograms|pounds|meters|feet) to (celsius|fahrenheit|kilometers|miles|kilograms|pounds|meters|feet)',low)
        if m:
            value=float(m[1]);key=(m[2],m[3]);factors={('kilometers','miles'):.621371,('kilograms','pounds'):2.20462,('meters','feet'):3.28084}
            if key==('celsius','fahrenheit'):out=value*1.8+32
            elif key==('fahrenheit','celsius'):out=(value-32)/1.8
            elif key in factors:out=value*factors[key]
            elif key[::-1] in factors:out=value/factors[key[::-1]]
            else:raise ValueError('Those units do not measure the same thing.')
            return f'{out:.2f} {m[3]}.'
        if low in ('flip a coin','toss a coin'):return random.choice(['Heads.','Tails.'])
        dice_text=low
        for word,number in {'four':4,'six':6,'eight':8,'ten':10,'twelve':12,'twenty':20,'hundred':100}.items():dice_text=re.sub(r'\b'+word+r'\b',str(number),dice_text)
        m=re.fullmatch(r'roll (?:a |an )?(?:(\d+) sided )?(?:die|dice)',dice_text)
        if m:return f'You rolled {random.randint(1,max(2,min(100,int(m[1] or 6))))}.'
        m=re.fullmatch(r'announce (.+)',text,re.I)
        if m:
            if not getattr(self.app,'notify',None):return 'Announcements are unavailable while the audio service is stopped.'
            self.app.notify(m[1],'pc')
            if time.time()-self.app.relay_seen<20:self.app.notify(m[1],'pi')
            return 'Announcement queued on the connected speakers.'
        if low in ('list routines','my routines'):return ', '.join((self.app.get('routines') or {}).keys()) or 'No routines yet. Say “create routine focus: set a twenty minute timer; set your volume to 40 percent”.'
        m=re.fullmatch(r'create routine ([\w -]{1,40}):\s*(.+)',text,re.I)
        if m:
            commands=[x.strip() for x in re.split(r';|\s+and then\s+',m[2]) if x.strip()]
            if not 1<=len(commands)<=6:raise ValueError('A routine can contain one to six commands.')
            from assistant_actions import local_routine_command
            if not all(local_routine_command(x) for x in commands):raise ValueError('Routines support timers, assistant volume, time/date, and named Home Assistant lights or thermostats.')
            routines=self.app.get('routines') or {};routines[m[1].lower()]=commands;self.app.set('routines',routines)
            return 'Saved routine '+m[1]+'. Say “run '+m[1]+'”.'
        m=re.fullmatch(r'(?:run|start) (?:routine )?(.+)',low)
        if m and m[1] in (self.app.get('routines') or {}):
            return ' '.join(self.app.command(x,source) for x in self.app.get('routines')[m[1]])
        return None
