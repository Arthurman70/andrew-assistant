"""Google Voice requests: local contacts, immutable previews, explicit send/call."""
import re
import secrets
import threading
import time

def phone_number(value):
    if not isinstance(value,str) or not re.fullmatch(r'[+\d() .-]{7,30}',value):raise ValueError('Use a full phone number, including area code.')
    digits=re.sub(r'\D','',value)
    if len(digits)==10:return '+1'+digits
    if len(digits)==11 and digits.startswith('1'):return '+'+digits
    if value.startswith('+') and 8<=len(digits)<=15:return '+'+digits
    raise ValueError('Use a full phone number, including country code for numbers outside the US.')

class Communications:
    def __init__(self,app,driver=None):
        self.app=app;self.driver=driver;self.pending={};self.lock=threading.RLock()
        self.result='Connect your Google Voice account to call or text.'
    def status(self):
        with self.lock:
            return {'connection':self.driver.status() if self.driver else {'state':'disconnected','message':'Connect Google Voice'},
                    'contacts':self.app.get('contacts') or {},'pending':{s:p for s,p in self.pending.items() if p['expires']>time.time()},'result':self.result}
    def connect(self):
        if not self.driver:
            from google_voice import GoogleVoice
            self.driver=GoogleVoice()
        self.driver.connect();return 'Opening Google Voice in your regular Chrome browser, using your existing Google session. Keep that tab selected for calls and texts.'
    def contact(self,name,number):
        if not re.fullmatch(r"[\w '\-]{1,60}",name):raise ValueError('Use a short contact name.')
        number=phone_number(number);contacts=self.app.get('contacts') or {};contacts[name.strip().lower()]=number
        self.app.set('contacts',contacts);return 'Saved '+name+' in Andrew contacts.'
    def resolve(self,recipient):
        contacts=self.app.get('contacts') or {}
        if recipient.lower().strip() in contacts:return contacts[recipient.lower().strip()]
        try:return phone_number(recipient)
        except ValueError:raise ValueError('I need a phone number for '+recipient+'. Say “save contact '+recipient+' as” followed by the full number.')
    def draft(self,kind,recipient,message,source):
        if kind not in ('call','text'):raise ValueError('Choose call or text.')
        destination=self.resolve(recipient)
        if kind=='text' and (not isinstance(message,str) or not 1<=len(message.strip())<=1000):raise ValueError('Use a text message between one and one thousand characters.')
        with self.lock:
            self.pending[source]={'id':secrets.token_hex(8),'kind':kind,'recipient':recipient,'number':destination,
                                  'message':message.strip(),'expires':time.time()+120,'person':self.app.memory.current(source)}
        preview=f'Text to {recipient}, {destination}: {message}. Say “confirm text” to send, or “cancel message”.' if kind=='text' else f'Call {recipient}, {destination}, using the PC microphone and speakers? Say “confirm call” or “cancel call”.'
        if not self.driver or self.driver.status()['state']!='ready':preview+=' Connect Google Voice in Connections first.'
        return preview
    def confirm(self,source,kind):
        with self.lock:
            item=self.pending.get(source)
            if not item or item['expires']<time.time() or item['kind']!=kind:return 'There is no current '+kind+' preview on this device. Ask again to prepare it.'
            person=self.app.memory.current(source)
            if item['person'] and not person:
                phrase='confirm sending this text message now please' if kind=='text' else 'confirm placing this phone call now please'
                return 'I could not match your voice from that short request. Say “'+self.app.get('name')+', '+phrase+'”, or confirm in the app.'
            if item['person']!=person:return 'The speaker changed. Please prepare the request again.'
            if not self.driver or self.driver.status()['state']!='ready':return 'Open Google Voice in your regular Chrome browser and select its tab first. Nothing was sent or called.'
            self.pending.pop(source)
        # Consume once before dispatch. A browser failure never auto-retries a
        # send/call: Google may have accepted it even if confirmation was lost.
        result=self.driver.perform(kind,item['number'],item['message'])
        self.result=result;return result
    def route(self,text,source):
        low=text.lower()
        if low.startswith('call yourself '):return None
        if low in ('connect google voice','open google voice'):return self.connect()
        if low in ('confirm text','send that text','confirm send','confirm sending this text message now','confirm sending this text message now please'):return self.confirm(source,'text')
        if low in ('confirm call','place that call','confirm placing this phone call now','confirm placing this phone call now please'):return self.confirm(source,'call')
        if low in ('cancel message','cancel text','cancel call'):
            with self.lock:self.pending.pop(source,None)
            return 'Cancelled the pending request.'
        if low in ('hang up','end call'):
            return self.driver.perform('hangup','','') if self.driver else 'No Google Voice connection is open.'
        m=re.fullmatch(r'(?:save|add) contact (.+?) (?:as|number) ([+\d() .-]+)',text,re.I)
        if m:return self.contact(m[1],m[2])
        m=re.fullmatch(r'(?:text|send (?:a )?text to|send (?:a )?message to) (.+?)(?: saying| that says|:|, )\s*(.+)',text,re.I)
        if m:return self.draft('text',m[1],m[2],source)
        m=re.fullmatch(r'(?:call|phone|dial) (.+)',text,re.I)
        if m:return self.draft('call',m[1],'',source)
        return None
