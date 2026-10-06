"""Verified task recipes and resumable checkpoints, scoped to a speaker."""
import json
import re
import time
from conversation_memory import clean,clean_data

class TaskMemory:
    def __init__(self,app):
        self.app=app
        with app.lock,app.db:
            app.db.executescript("""
            CREATE TABLE IF NOT EXISTS task_recipes(id INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT,request TEXT,steps TEXT,result TEXT,used REAL);
            CREATE TABLE IF NOT EXISTS task_checkpoints(source TEXT PRIMARY KEY,owner TEXT,request TEXT,history TEXT,steps TEXT,provider TEXT,model TEXT,status TEXT,updated REAL);
            """)

    def owner(self,person,source):return person or 'guest:'+source

    def recipes(self,person,source,request):
        tokens=set(re.findall(r'[a-z]{3,}',request.lower()))-{'please','your','andrew','could','would','open','launch','use','create','build','save','click','then','the','app'}
        with self.app.lock:rows=[dict(r) for r in self.app.db.execute('SELECT request,steps,result FROM task_recipes WHERE owner=? ORDER BY used DESC LIMIT 30',(self.owner(person,source),))]
        ranked=sorted(rows,key=lambda r:len(tokens&set(re.findall(r'[a-z]{3,}',r['request'].lower()))),reverse=True)
        return [dict(r,steps=json.loads(r['steps'])) for r in ranked[:3] if tokens&set(re.findall(r'[a-z]{3,}',r['request'].lower()))]

    def success(self,person,source,request,history,answer):
        steps=[]
        for index,entry in enumerate(history):
            action=entry.get('proposed_action')
            if index+1>=len(history) or history[index+1].get('tool_result',{}).get('error'):continue
            if not action or action.get('action') in ('finish','wait'):continue
            # UI handles and references never survive into reusable recipes.
            args={k:v for k,v in action.get('args',{}).items() if k not in ('window','ref')}
            steps.append({'action':action['action'],'args':args,'purpose':action.get('progress','')})
        with self.app.lock,self.app.db:
            self.app.db.execute('DELETE FROM task_recipes WHERE owner=? AND request=?',(self.owner(person,source),clean(request)))
            self.app.db.execute('INSERT INTO task_recipes(owner,request,steps,result,used) VALUES(?,?,?,?,?)',(self.owner(person,source),clean(request),json.dumps(clean_data(steps)),clean(answer),time.time()))

    def save(self,source,person,request,history,steps,provider,model,status):
        # Keep the latest observation plus logical action outcomes. No audio.
        payload=json.dumps(clean_data(history),ensure_ascii=False)
        with self.app.lock,self.app.db:
            self.app.db.execute('INSERT OR REPLACE INTO task_checkpoints VALUES(?,?,?,?,?,?,?,?,?)',(source,self.owner(person,source),clean(request),payload,json.dumps(steps),provider,model,status,time.time()))

    def load(self,source,person):
        with self.app.lock:row=self.app.db.execute('SELECT * FROM task_checkpoints WHERE source=? AND owner=?',(source,self.owner(person,source))).fetchone()
        if not row or row['status'] not in ('running','paused','needs_input','failed') or time.time()-row['updated']>86400:return None
        item=dict(row);item['history']=json.loads(item['history']);item['steps']=json.loads(item['steps']);return item

    def delete(self,person):
        with self.app.lock,self.app.db:
            self.app.db.execute('DELETE FROM task_recipes WHERE owner=?',(person,));self.app.db.execute('DELETE FROM task_checkpoints WHERE owner=?',(person,))
