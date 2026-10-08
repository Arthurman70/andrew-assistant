"""Local, named profiles. No background recording and no raw voice archive."""
import json
import re
import secrets
import threading
import time
import numpy as np

SENSITIVE=re.compile(r'password|passcode|secret|api.?key|token|credit card|social security|diagnos|religion|politic|sexual|medical',re.I)

class Memory:
    def __init__(self,app):
        self.app=app;self.active={};self.prompted={};self.extractor=None;self.extract_lock=threading.Lock()
        from conversation_memory import ConversationMemory
        self.conversation=ConversationMemory(app)
        self.voice_status='Ready to learn a named speaker';self.pending_enrollment={};self.guests={}
        with app.lock,app.db:
            app.db.executescript('''
            CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,name TEXT UNIQUE COLLATE NOCASE,created REAL);
            CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,person TEXT,topic TEXT,value TEXT,automatic INTEGER,updated REAL,UNIQUE(person,topic));
            CREATE TABLE IF NOT EXISTS speaker_prints(person TEXT,device TEXT,vector TEXT,PRIMARY KEY(person,device));
            ''')

    def current(self,source):
        if getattr(self.app.request,'scheduled_context',False):return getattr(self.app.request,'person',None)
        if self.guests.get(source,0)>time.time():return None
        if getattr(self.app.request,'voice_context',False):return getattr(self.app.request,'person',None)
        entry=self.active.get(source)
        return entry['id'] if entry and time.time()-entry['at']<900 else None

    def name(self,person):
        with self.app.lock:row=self.app.db.execute('SELECT name FROM people WHERE id=?',(person,)).fetchone()
        return row['name'] if row else None

    def identify(self,name,source):
        self.guests.pop(source,None)
        name=name.strip().title()
        if not re.fullmatch(r"[A-Za-z][A-Za-z '\-]{0,39}",name):raise ValueError('Please use a short spoken name.')
        with self.app.lock,self.app.db:
            row=self.app.db.execute('SELECT id FROM people WHERE name=?',(name,)).fetchone()
            person=row['id'] if row else secrets.token_hex(8)
            if not row:self.app.db.execute('INSERT INTO people VALUES (?,?,?)',(person,name,time.time()))
        self.active[source]={'id':person,'name':name,'at':time.time(),'method':'introduced'}
        self.app.request.person=person
        embedding=getattr(self.app.request,'embedding',None)
        if embedding is not None:self.enroll(person,source,embedding)
        else:self.pending_enrollment[source]=(person,time.time()+120)
        return f'Nice to meet you, {name}. I will remember your preferences. To help me recognize you, say my name and then “learn my voice” followed by a full sentence.'

    def enroll(self,person,source,vector):
        with self.app.lock,self.app.db:
            self.app.db.execute('INSERT OR REPLACE INTO speaker_prints VALUES (?,?,?)',(person,source,json.dumps(vector.tolist())))
        self.voice_status='Voice profile saved locally';self.pending_enrollment.pop(source,None)

    def embedding(self,pcm):
        if len(pcm)<51200:return None
        samples=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768
        if float(np.sqrt(np.mean(samples*samples)))<.005:return None
        with self.extract_lock:
            if self.extractor is None:
                from core import ROOT
                model=ROOT/'runtime/speech/speaker-resnet34.onnx'
                if not model.exists():return None
                import sherpa_onnx
                config=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model),num_threads=2,debug=False,provider='cpu')
                self.extractor=sherpa_onnx.SpeakerEmbeddingExtractor(config)
            stream=self.extractor.create_stream();stream.accept_waveform(16000,samples);stream.input_finished()
            if not self.extractor.is_ready(stream):return None
            vector=np.asarray(self.extractor.compute(stream),dtype=np.float32)
            return vector/max(float(np.linalg.norm(vector)),1e-9)

    def begin_voice(self,pcm,source,text=None):
        self.app.request.voice_context=True;self.app.request.person=None;self.app.request.embedding=None
        if self.guests.get(source,0)>time.time():return
        with self.app.lock:rows=self.app.db.execute('SELECT person,device,vector FROM speaker_prints').fetchall()
        if text is not None and not rows and not re.search(r'\b(my name is|call me|this is|i am|i\x27m|learn my voice)\b',text,re.I):
            # Matching nobody costs CPU on every request. Keep the speaker
            # unknown; never inherit the last person's identity from a device.
            self.active.pop(source,None)
            return
        try:vector=self.embedding(pcm)
        except Exception:
            self.voice_status='Voice matching unavailable; introduce yourself';return
        self.app.request.embedding=vector
        if vector is None:return
        scores={}
        for row in rows:
            known=np.asarray(json.loads(row['vector']),dtype=np.float32)
            if known.shape!=vector.shape:continue
            similarity=float(np.dot(known,vector))
            if row['device']!=source:similarity-=.04
            scores[row['person']]=max(scores.get(row['person'],-1),similarity)
        ranked=sorted(scores.items(),key=lambda item:item[1],reverse=True)
        if ranked and ranked[0][1]>=.78 and (len(ranked)==1 or ranked[0][1]-ranked[1][1]>=.12):
            person=ranked[0][0];self.app.request.person=person
            self.active[source]={'id':person,'name':self.name(person),'at':time.time(),'method':'voice match (estimate)'}
        else:self.active.pop(source,None)

    def end_voice(self):
        self.app.request.voice_context=False;self.app.request.person=None;self.app.request.embedding=None

    def put(self,person,topic,value,automatic=False):
        if not person:raise ValueError('Tell me your name first: “my name is …”.')
        topic=topic.strip().lower()[:60];value=value.strip()[:500]
        if not value:raise ValueError('Tell me what to remember.')
        if SENSITIVE.search(topic+' '+value):return 'I do not store passwords or sensitive personal details in assistant memory.'
        with self.app.lock,self.app.db:
            self.app.db.execute('INSERT INTO memories VALUES (?,?,?,?,?,?) ON CONFLICT(person,topic) DO UPDATE SET value=excluded.value,automatic=excluded.automatic,updated=excluded.updated',
                (secrets.token_hex(8),person,topic,value,int(automatic),time.time()))
        return 'I will remember that.'

    def learn(self,text,source):
        if not self.app.get('memory_auto'):return
        person=self.current(source)
        if not person or '?' in text or SENSITIVE.search(text):return
        match=re.fullmatch(r'my (favorite (?:food|drink|music|game|movie|book|color)|preferred (?:temperature|units|voice|volume)) is (.{1,120})',text,re.I)
        if match:self.put(person,match[1],match[2],True)
        match=re.fullmatch(r'I (like|love|prefer|dislike) (.{1,120})',text,re.I)
        if match:self.put(person,'preference: '+match[2].lower(),match[1]+' '+match[2],True)

    def context(self,source):
        person=self.current(source)
        if not person:return 'Speaker is not identified. Do not assume an identity from the microphone device.'
        with self.app.lock:rows=self.app.db.execute('SELECT topic,value FROM memories WHERE person=? ORDER BY updated DESC LIMIT 20',(person,)).fetchall()
        return json.dumps({'name':self.name(person),'preferences':[dict(r) for r in rows],'conversation_summary':self.conversation.summary(person)},ensure_ascii=False)

    def snapshot(self):
        with self.app.lock:
            people=[dict(r) for r in self.app.db.execute('SELECT id,name FROM people ORDER BY name')]
            entries=[dict(r) for r in self.app.db.execute('SELECT id,person,topic,value,automatic FROM memories ORDER BY updated DESC LIMIT 100')]
        return {'people':people,'memories':entries,'active':{s:v for s,v in self.active.items() if time.time()-v['at']<900},
                'automatic':bool(self.app.get('memory_auto')),'voice_status':self.voice_status,
                'conversations':{p['id']:self.conversation.stats(p['id']) for p in people}}

    def route(self,text,source):
        match=re.fullmatch(r'(?:my name is|this is|I am called|call me) ([A-Za-z][A-Za-z \-\']{0,39})',text,re.I)
        if match:return self.identify(match[1],source)
        low=text.lower();person=self.current(source)
        if re.fullmatch(r'(?:compact|summarize|condense|compress) (?:my |your |our |the )?(?:memories|memory|conversations|conversation history)',low):
            return self.conversation.compact(person)
        if low.startswith('learn my voice'):
            pending=self.pending_enrollment.get(source)
            if not person and pending and pending[1]>time.time():person=pending[0]
            vector=getattr(self.app.request,'embedding',None)
            if not person:return 'First say “my name is” and your name.'
            if vector is None:return 'Say my name, then “learn my voice” and a full sentence out loud.'
            self.enroll(person,source,vector);return 'Your voice profile is saved on this PC. I will ask when a match is uncertain.'
        if low in ('who am i','what is my name',"what's my name"):
            return ('I have you as '+self.name(person)+'. Say “my name is” to correct me.') if person else 'What should I call you? Say “my name is” and your name.'
        if low in ('guest mode','forget who is speaking'):
            self.guests[source]=time.time()+900
            self.active.pop(source,None);self.pending_enrollment.pop(source,None);self.app.request.person=None
            return 'Guest mode for fifteen minutes. I will not use or save personal preferences. Introduce yourself to leave guest mode sooner.'
        if low in ('what do you remember about me','show my memories','my memories'):
            return 'Tell me your name first by saying “my name is” followed by your name.' if not person else self.describe(person)
        if low in ('stop learning preferences','disable automatic memory','pause memory'):
            self.app.set('memory_auto',False);return 'Automatic preference learning is off. You can still ask me to remember something.'
        if low in ('learn my preferences','enable automatic memory'):
            self.app.set('memory_auto',True);return 'I will learn preferences from identified speakers.'
        if low in ('forget everything about me','delete my profile'):
            if not person:return 'Tell me which profile is yours first.'
            with self.app.lock,self.app.db:
                for table,column in [('memories','person'),('speaker_prints','person'),('people','id')]:
                    self.app.db.execute(f'DELETE FROM {table} WHERE {column}=?',(person,))
            self.active={s:v for s,v in self.active.items() if v['id']!=person};self.app.request.person=None
            self.conversation.delete(person)
            if self.app.pc_agent:self.app.pc_agent.memory.delete(person)
            self.app.conversations.clear()
            self.app.request.memory_deleted=True
            return 'Your profile, saved preferences, and voice match were deleted.'
        match=re.fullmatch(r'remember (?:that )?(.+)',text,re.I)
        if match:
            fact=match[1];parts=re.split(r'\s+is\s+',fact,maxsplit=1,flags=re.I)
            return self.put(person,parts[0] if len(parts)==2 else fact[:60],parts[-1])
        match=re.fullmatch(r'forget (?:that |my )?(.+)',text,re.I)
        if match:
            if not person:return 'Tell me your name first.'
            with self.app.lock,self.app.db:
                count=self.app.db.execute('DELETE FROM memories WHERE person=? AND (topic LIKE ? OR value LIKE ?)',(person,'%'+match[1]+'%','%'+match[1]+'%')).rowcount
            self.conversation.delete(person);self.app.conversations.clear();self.app.request.memory_deleted=True;return 'Forgot that memory.' if count else 'I could not find that in your memories.'
        return None

    def describe(self,person):
        with self.app.lock:rows=self.app.db.execute('SELECT topic,value FROM memories WHERE person=? ORDER BY updated DESC',(person,)).fetchall()
        summary=self.conversation.summary(person)
        facts='; '.join(r['topic']+': '+r['value'] for r in rows)
        return ('; '.join(part for part in (facts,summary[:2200]) if part) or 'I know your name, but have no saved preferences yet.')
