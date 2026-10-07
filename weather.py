"""Local weather intents and cached Open-Meteo daily/hourly forecast data."""
import datetime as dt
import math
import re
import threading
import time
from urllib.parse import urlencode

NUMBERS={'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'thirteen':13,'fourteen':14,'fifteen':15,'sixteen':16,'twenty four':24,'twenty-four':24,'forty eight':48,'forty-eight':48,'seventy two':72,'seventy-two':72}
CONDITIONS={0:('Clear','☀️'),1:('Mostly clear','🌤️'),2:('Partly cloudy','⛅'),3:('Cloudy','☁️'),45:('Fog','🌫️'),48:('Freezing fog','🌫️'),51:('Light drizzle','🌦️'),53:('Drizzle','🌦️'),55:('Heavy drizzle','🌧️'),56:('Freezing drizzle','🌧️'),57:('Freezing drizzle','🌧️'),61:('Light rain','🌦️'),63:('Rain','🌧️'),65:('Heavy rain','🌧️'),66:('Freezing rain','🌧️'),67:('Freezing rain','🌧️'),71:('Light snow','🌨️'),73:('Snow','🌨️'),75:('Heavy snow','❄️'),77:('Snow grains','❄️'),80:('Rain showers','🌦️'),81:('Rain showers','🌧️'),82:('Heavy showers','🌧️'),85:('Snow showers','🌨️'),86:('Heavy snow showers','❄️'),95:('Thunderstorms','⛈️'),96:('Thunderstorms with hail','⛈️'),99:('Thunderstorms with hail','⛈️')}
DAYS=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']

def conditions(code,is_day=True):
    label,icon=CONDITIONS.get(code,('Conditions unavailable','—'))
    if code in (0,1) and not is_day:icon='🌙'
    return {'condition':label,'icon':icon}

def numeric(value):return round(float(value),1) if type(value) in (int,float) and math.isfinite(value) else None
def degrees(value):return str(round(value)) if value is not None else 'unavailable'

def plan(text,detail=None):
    low=text.lower().strip()
    for word,number in sorted(NUMBERS.items(),key=lambda item:-len(item[0])):low=re.sub(r'\b'+re.escape(word)+r'\b',str(number),low)
    mode='hourly' if re.search(r'\bhourly\b|\bhour.by.hour\b|\beach hour\b|\b\d+ hours?\b',low) else 'daily'
    if detail is not None:
        if detail not in ('daily','hourly'):raise ValueError('Choose daily or hourly weather.')
        mode=detail
    result={'days':3,'mode':mode,'offset':0,'hours':None,'day':None,'period':'Next 3 days','explicit':False}
    number=re.search(r'\b(?:next |coming |for |over )?(\d+)\s*(days?|weeks?|hours?)\b',low)
    if number:
        count=int(number[1]);unit=number[2]
        if unit.startswith('hour'):
            if not 1<=count<=360:raise ValueError('Choose 1 to 360 forecast hours.')
            result.update(hours=count,days=min(16,math.ceil(count/24)+1),mode='hourly',period='Next '+str(count)+' hours',explicit=True)
        else:
            count*=7 if unit.startswith('week') else 1
            if not 1<=count<=16:raise ValueError('Forecasts are available for 1 to 16 days.')
            result.update(days=count,period='Next '+str(count)+' days',explicit=True)
    elif re.search(r'\b(?:next|coming|this|the) week\b|\bweekly\b|\ba week\b',low):result.update(days=7,period='Next 7 days',explicit=True)
    if re.search(r'\bday after tomorrow\b',low):result.update(days=1,offset=2,period='Day after tomorrow',explicit=True)
    elif re.search(r'\btomorrow\b',low):result.update(days=1,offset=1,period='Tomorrow',explicit=True)
    elif re.search(r'\btoday\b|\btonight\b',low):result.update(days=1,period='Today',explicit=True)
    else:
        day=next((d for d in DAYS if re.search(r'\b'+d+r'\b',low)),None)
        if day:result.update(days=1,day=day,next_day=bool(re.search(r'\bnext '+day,low)),period=day.title(),explicit=True)
    if mode=='hourly' and not result['explicit']:result.update(hours=24,days=2,period='Next 24 hours')
    unit='celsius' if re.search(r'\bcelsius\b|\bcentigrade\b',low) else ('fahrenheit' if re.search(r'\bfahrenheit\b',low) else None)
    result['units']=unit
    return result

def location_from(text):
    match=re.search(r'\bin\s+(.+)',text,re.I)
    if not match:
        match=re.search(r'\b(?:weather|forecast)\s+for\s+(.+)',text,re.I)
        if match and re.match(r'(?:the |next |coming |today|tomorrow|\d|one |two |three |four |five |six |seven |a week)',match[1],re.I):return None
    if not match:return None
    if re.match(r'(?:the |next |coming |today\b|tomorrow\b|day after tomorrow\b)',match[1],re.I):return None
    city=re.split(r'\s+(?:for\s+)?(?:the\s+)?(?:next|coming|today|tomorrow|tonight|day after tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|hourly|hour.by.hour|in celsius|in fahrenheit)\b',match[1],maxsplit=1,flags=re.I)[0]
    city=re.sub(r'\s+(?:please|by the hour)$','',city,flags=re.I).strip(' ,.!?')
    if city.lower() in ('celsius','fahrenheit'):return None
    return city or None

class Weather:
    def __init__(self,app,fetch=None,clock=time.time):
        self.app=app;self.fetch=fetch;self.clock=clock;self.lock=threading.RLock();self.cache={};self.places={};self.views={};self.context={}
    def json(self,url):
        if self.fetch:return self.fetch(url)
        from core import request_json
        return request_json(url,timeout=10)
    def state(self):
        with self.lock:return dict(self.views)
    def owner(self,source):return (source,self.app.memory.current(source))
    def load(self,city,units):
        key=(city.casefold(),units);now=self.clock()
        cached=self.cache.get(key)
        if cached and now-cached['fetched_at']<600:return cached
        place=self.places.get(city.casefold())
        if not place:
            results=self.json('https://geocoding-api.open-meteo.com/v1/search?'+urlencode({'name':city,'count':1,'language':'en'})).get('results',[])
            if not results:raise ValueError('I could not find that city. Try its city and state or country.')
            place=results[0];self.places[city.casefold()]=place
        result=self.json('https://api.open-meteo.com/v1/forecast?'+urlencode({
            'latitude':place['latitude'],'longitude':place['longitude'],'timezone':'auto','forecast_days':16,
            'temperature_unit':units,'wind_speed_unit':'mph' if units=='fahrenheit' else 'kmh',
            'current':'temperature_2m,apparent_temperature,weather_code,is_day,relative_humidity_2m,wind_speed_10m',
            'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max',
            'hourly':'temperature_2m,apparent_temperature,weather_code,precipitation_probability,wind_speed_10m,is_day'}))
        if not result.get('daily',{}).get('time') or not result.get('hourly',{}).get('time'):raise ValueError('The forecast service did not return daily and hourly weather. Try again shortly.')
        payload={'place':place,'data':result,'fetched_at':now};self.cache[key]=payload
        if len(self.cache)>12:self.cache.pop(next(iter(self.cache)))
        return payload
    def request(self,city=None,period='',detail=None,source='pc',units=None):
        if source not in ('pc','pi','browser'):raise ValueError('Choose a supported Andrew device.')
        if not isinstance(period,str) or len(period)>2000 or (city is not None and not isinstance(city,str)):raise ValueError('Use a city and a short forecast period.')
        selection=plan(period,detail)
        units=units or selection['units'] or self.app.get('weather_units') or 'fahrenheit'
        if units not in ('fahrenheit','celsius'):raise ValueError('Choose Fahrenheit or Celsius.')
        city=(city or self.app.get('weather_city') or '').strip()
        if not city:return 'Tell me a city, such as “weather in Boston for the next three days”.'
        if not 2<=len(city)<=160:raise ValueError('Use a city, optionally with a state or country.')
        with self.lock:
            try:payload=self.load(city,units)
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else 'Weather is temporarily unavailable. Your earlier forecast is still shown with its update time.'
                if source in self.views:self.views[source]=dict(self.views[source],error=message)
                return message
            data=payload['data'];current=data.get('current',{});now=dt.datetime.fromisoformat(current.get('time',data['hourly']['time'][0]))
            now+=dt.timedelta(seconds=max(0,self.clock()-payload['fetched_at']))
            today=now.date();offset=selection['offset']
            if selection['day']:
                offset=(DAYS.index(selection['day'])-today.weekday())%7
                if selection.get('next_day') and offset==0:offset=7
            start=today+dt.timedelta(days=offset);end=start+dt.timedelta(days=selection['days'])
            if (end-today).days>16:raise ValueError('That period is beyond the available 16-day forecast.')
            daily=data['daily'];hourly=data['hourly']
            def value(block,key,index):
                values=block.get(key,[]);return numeric(values[index]) if index<len(values) else None
            def code(block,index):
                values=block.get('weather_code',[]);return values[index] if index<len(values) else None
            days=[]
            for i,date in enumerate(daily['time']):
                if not start.isoformat()<=date<end.isoformat():continue
                label='Today' if date==today.isoformat() else 'Tomorrow' if date==(today+dt.timedelta(days=1)).isoformat() else dt.date.fromisoformat(date).strftime('%A')
                days.append({'date':date,'label':label,'high':value(daily,'temperature_2m_max',i),'low':value(daily,'temperature_2m_min',i),
                             'rain_chance':value(daily,'precipitation_probability_max',i),'wind':value(daily,'wind_speed_10m_max',i),**conditions(code(daily,i))})
            hours=[];first_hour=now.replace(minute=0,second=0,microsecond=0) if start==today else dt.datetime.combine(start,dt.time())
            for i,timestamp in enumerate(hourly['time']):
                moment=dt.datetime.fromisoformat(timestamp)
                if moment<first_hour or (not selection['hours'] and moment.date()>=end):continue
                daylight=hourly.get('is_day',[]);is_day=bool(daylight[i]) if i<len(daylight) else True
                hours.append({'time':timestamp,'date':timestamp[:10],'temperature':value(hourly,'temperature_2m',i),
                              'feels_like':value(hourly,'apparent_temperature',i),'rain_chance':value(hourly,'precipitation_probability',i),'wind':value(hourly,'wind_speed_10m',i),**conditions(code(hourly,i),is_day)})
            if selection['hours']:
                hours=hours[:selection['hours']];covered={h['date'] for h in hours};days=[d for d in days if d['date'] in covered]
            if not days or not hours:raise ValueError('There is no forecast data for that period yet.')
            place=payload['place'];label=', '.join(dict.fromkeys(str(place[k]) for k in ('name','admin1','country') if place.get(k)))
            view={'id':str(self.clock()),'location':label,'city':city,'timezone':data.get('timezone',place.get('timezone','')),
                  'units':units,'unit_label':'°F' if units=='fahrenheit' else '°C','wind_unit':'mph' if units=='fahrenheit' else 'km/h',
                  'period':selection['period'],'mode':selection['mode'],'days':days,'hours':hours,'requested_hours':selection['hours'],
                  'updated_at':payload['fetched_at'],'current':{'temperature':numeric(current.get('temperature_2m')),'feels_like':numeric(current.get('apparent_temperature')),
                  'humidity':numeric(current.get('relative_humidity_2m')),'wind':numeric(current.get('wind_speed_10m')),**conditions(current.get('weather_code'),current.get('is_day',1))},
                  'error':'','attribution':'Open-Meteo','attribution_url':'https://open-meteo.com/'}
            self.views[source]=view;self.context[self.owner(source)]={'at':self.clock(),'city':city,'period':selection['period'],'detail':selection['mode']}
            self.app.set('weather_city',city);self.app.set('weather_units',units)
        self.app.controls.show('home',source,'weatherPanel','Weather')
        return self.summary(view,selection['explicit'])
    def summary(self,view,explicit=False):
        days=view['days'];unit='Fahrenheit' if view['units']=='fahrenheit' else 'Celsius';city=view['location'].split(',')[0]
        if view['mode']=='hourly':
            hours=view['hours'];temps=[h['temperature'] for h in hours if h['temperature'] is not None]
            chance=[h['rain_chance'] for h in hours if h['rain_chance'] is not None]
            result=city+': '+str(len(hours))+' hours of forecast are on screen.'
            if temps:result+=' Temperatures range from '+degrees(min(temps))+' to '+degrees(max(temps))+' degrees '+unit+'.'
            if chance:result+=' Highest precipitation chance is '+degrees(max(chance))+' percent.'
            return result
        if not explicit:
            cur=view['current'];day=days[0]
            return 'In '+city+', '+cur['condition'].lower()+', '+degrees(cur['temperature'])+' degrees '+unit+', feeling like '+degrees(cur['feels_like'])+'. Today’s high is '+degrees(day['high'])+' and low '+degrees(day['low'])+'. The daily and hourly forecast is on screen.'
        parts=[city+', '+view['period'].lower()+', degrees '+unit+'.']
        for day in days[:3]:parts.append(day['label']+': '+day['condition'].lower()+', high '+degrees(day['high'])+', low '+degrees(day['low'])+'.')
        if len(days)>3:
            highs=[d['high'] for d in days if d['high'] is not None];lows=[d['low'] for d in days if d['low'] is not None]
            if highs and lows:parts.append('The period’s highest temperature is '+degrees(max(highs))+' and lowest '+degrees(min(lows))+'.')
            parts.append('All '+str(len(days))+' days are on screen.')
        return ' '.join(parts)
    def route(self,text,source):
        low=text.lower().strip();key=self.owner(source);context=self.context.get(key)
        weather=bool(re.search(r'\bweather\b|\bforecast\b|\bhighs and lows\b',low))
        follow=bool(context and self.clock()-context['at']<300 and re.fullmatch(r'(?:(?:what about|and|show(?: me)?|break (?:it|that) down|make (?:it|that)|for) )?(?:the )?(?:next .+|tomorrow|today|hourly|hour.by.hour|by the hour|daily|in celsius|in fahrenheit)',low))
        if not weather and not follow:
            self.context.pop(key,None);return None
        if low in ('open weather','open weather page'):self.app.controls.show('home',source,'weatherPanel','Weather');return 'Weather is on screen. Ask for a city and forecast period.'
        if weather and not re.match(r'^(?:weather|forecast|hourly|daily|(?:show|give|tell|check|read)(?: me)? (?:the )?(?:weather|forecast|highs|lows)|what(?: is|\x27s| will| are| about).{0,40}(?:weather|forecast)|highs and lows)\b',low):return None
        city=location_from(text) or (context['city'] if follow else None)
        period=text;detail=None
        if follow and re.fullmatch(r'(?:show(?: me)? |break (?:it|that) down |make (?:it|that) )?(?:hourly|hour.by.hour|by the hour|daily)',low):
            detail='daily' if low.endswith('daily') else 'hourly'
            period=context['period']+' '+detail
        elif follow and low in ('in celsius','in fahrenheit'):period=context['period']+' '+context['detail']+' '+low
        return self.request(city,period,detail=detail,source=source)
