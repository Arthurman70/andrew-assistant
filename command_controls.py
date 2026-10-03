"""Deterministic app controls, bounded clarifications, and ordered local commands.

Quoted messages, improvement specifications and reminders are opaque payloads.
Only user commands can install code; AI actions cannot enter the updater.
"""
import re
import secrets
import time


PROVIDERS = r'(?:grok|grock|claude|clawed|openai|open ai|chatgpt|chat gpt|local)'
MODEL_NAMES = r'(?:haiku|sonnet|opus)'
PAGES = {
    'home': ('home', '', 'Home'), 'your face': ('home', '', 'Home'),
    'settings': ('settings', '', 'Settings'),
    'audio settings': ('settings', 'speechQuality', 'Audio settings'),
    'microphone settings': ('settings', '', 'Microphones and speakers'),
    'voice settings': ('settings', 'speechQuality', 'Speech quality'),
    'activities': ('activities', '', 'Activities'), 'games': ('activities', 'gamePanel', 'Games'),
    'memory': ('people', 'peoplePanel', 'People and memory'),
    'people': ('people', 'peoplePanel', 'People and memory'),
    'people and memory': ('people', 'peoplePanel', 'People and memory'),
    'connections': ('connections', '', 'Connections'),
    'models': ('connections', '', 'AI models'), 'model settings': ('connections', '', 'AI models'),
    'improvements': ('connections', 'improvementList', 'Improve Andrew'),
    'self improvements': ('connections', 'improvementList', 'Improve Andrew'),
    'self improvement': ('connections', 'improvementList', 'Improve Andrew'),
    'latest improvement': ('connections', 'improvementList', 'Latest improvement'),
    'timers': ('home', 'timers', 'Timers'), 'alarms': ('home', 'timers', 'Timers and alarms'),
    'camera page': ('camera', '', 'Camera'), 'camera tab': ('camera', '', 'Camera'),
    'camera settings': ('camera', '', 'Camera'),
    'calls and texts': ('connections', 'communicationsPanel', 'Calls and texts'),
}


def model_target(text):
    """Return a target only for an imperative, never a mention or a question."""
    low = text.lower().strip()
    match = re.fullmatch(r'(?:switch|change)(?: (?:your|the|my))?(?: ai)? (?:model|provider|ai)(?: over)? to (.+)', low)
    if not match:
        match = re.fullmatch(r'(?:set|switch|change)(?: (?:your|the|my))? (?:ai |ai assistant |assistant )?model to (.+)', low)
    if not match:
        match = re.fullmatch(r'switch from '+PROVIDERS+r' to (.+)', low)
    if not match:
        match = re.fullmatch(r'(?:use|switch(?: over)? to|change to) (.+)', low)
        if match and not (re.match(r'^(?:the )?(?:'+PROVIDERS+'|'+MODEL_NAMES+r')\b', match[1]) or match[1].endswith(' model')):
            return None
    if not match: return None
    return re.sub(r'^(?:the )|(?: model)$', '', match[1]).strip()


def navigation_target(text):
    low = text.lower().strip()
    if low in ('go home', 'show your face', 'show the clock'): return PAGES['home']
    match = re.fullmatch(r'(?:open|show me|show|go to|take me to|navigate to|bring up|review)(?: (?:your|the|my))? (.+)', low)
    if not match: return None
    destination = match[1].replace('self-improvement', 'self improvement')
    if destination in PAGES: return PAGES[destination]
    destination = re.sub(r' (?:page|tab|screen)$', '', destination)
    return PAGES.get(destination)


def install_intent(text):
    return bool(re.fullmatch(
        r'(?:(?:save|test|check|review)(?:\s*,?\s*and(?: then)?| then) )?'
        r'(?:install|apply)(?: and save)? (?:the |my |that |this )?'
        r'(?:(?:latest|last|new|ready|saved) )?(?:self[ -])?(?:improvement|update|change|proposal|draft)(?:s)?(?: now)?', text, re.I))


def chain_parts(text):
    # Do not reinterpret the body of a message, memory, reminder or code request.
    if re.match(r'^(?:text|send|call|phone|dial|remember|remind|announce|create|improve|self[ -]improve|ask)\b', text, re.I): return None
    if install_intent(text): return None  # "Save and install" is one operation.
    parts = re.split(r'\s*(?:;|,?\s+and then|,?\s+then|,?\s+and)\s+(?=(?:please )?(?:use|switch|change|set|start|open|show|go|take|install|apply|save|test|snooze|sleep|what|list|stop|cancel|play|improve)\b)', text, flags=re.I)
    # Preserve a code request's specification, including any quoted commands.
    for index, part in enumerate(parts):
        if re.match(r'^(?:improve yourself|self[ -]improve)\b',part,re.I):
            offset=text.find(part)
            parts=parts[:index]+[text[offset:]]
            break
    return parts if len(parts)>1 else None


def local_clause(text):
    text = re.sub(r'^please\s+', '', text, flags=re.I)
    return bool(model_target(text) is not None or navigation_target(text) or install_intent(text) or
        re.fullmatch(r'(?:what time is it|what is the date|list timers|current model|list models|improvement status)', text, re.I) or
        re.fullmatch(r'(?:set|start|change|edit|reset|cancel|stop) .+ (?:timer|alarm)(?: .+)?', text, re.I) or
        re.fullmatch(r'(?:set|change) (?:your |my |the )?volume(?: to)? \d{1,3}(?: percent|%)?(?: on (?:the )?(?:PC|Pi))?', text, re.I) or
        re.fullmatch(r'(?:snooze|sleep) for .+', text, re.I) or
        re.fullmatch(r'(?:open|launch) (?:the )?(?:browser|youtube|calculator|notepad|spotify|grok|claude|chatgpt)', text, re.I) or
        re.fullmatch(r'(?:play|start) (?:chess|trivia|tic tac toe|guess the number)', text, re.I) or
        re.match(r'^(?:improve yourself|self[ -]improve)\b', text, re.I))


class Controls:
    def __init__(self, app):
        self.app = app
        self.pending = {}
        self.navigation = {}
        self.improvement_references = {}

    def snapshot(self):
        with self.app.lock: return dict(self.navigation)

    def show(self, page, source, section='', label=None):
        if page not in {item[0] for item in PAGES.values()}: raise ValueError('Unknown Andrew page.')
        if source=='pi' and page not in ('home','activities','camera'):
            self.app.request.control_failed=True
            return 'That page is available in Andrew on the PC. You can still give its commands here.'
        with self.app.lock:
            self.navigation[source] = {'id': secrets.token_hex(8), 'page':page, 'section':section, 'at':time.time()}
        if source=='pi' and page=='home':return self.app.display_job('home')
        return 'Showing '+(label or page.title())+'.'

    def remember_improvement(self, source, job_id):
        with self.app.lock:
            self.improvement_references[source]=(time.monotonic()+180,job_id,self.app.memory.current(source))

    def improvement_reference(self, source):
        with self.app.lock: item=self.improvement_references.get(source)
        if item and item[0]>time.monotonic() and item[2]==self.app.memory.current(source):return item[1]
        return None

    def ask(self, kind, source, message):
        with self.app.lock:
            self.pending[source] = (time.monotonic()+90, kind, self.app.memory.current(source))
        self.app.request.control_failed = True  # Pause any remaining chained steps.
        return message

    def select(self, target, source):
        from providers import choose
        target = target.lower().strip()
        provider = self.app.get('provider')
        model = target
        match = re.match(r'^('+PROVIDERS+r')(?:\s+(.+))?$', target)
        if match:
            provider = {'grock':'grok','clawed':'claude','open ai':'openai','chatgpt':'openai','chat gpt':'openai'}.get(match[1], match[1])
            model = re.sub(r'^model\s+', '', match[2]) if match[2] else None
            # Spoken model numbers need the provider prefix for catalog matching.
            if model and provider=='grok' and re.match(r'^\d|^(?:four|five)\b', model): model = 'grok '+model
        elif re.fullmatch(MODEL_NAMES, target): provider='claude'
        if model in ('default','account default','default model'): model=''
        try:
            if provider=='claude' and model and not re.fullmatch(MODEL_NAMES+r'|claude-[\w.:-]+',model):
                raise ValueError('Choose Haiku, Sonnet, Opus, or an exact Claude model identifier.')
            result = choose(self.app, provider, model)
            with self.app.lock: self.pending.pop(source, None)
            return result
        except ValueError as exc:
            self.app.request.control_failed=True
            return str(exc)+(' Keeping your current provider and model.' if match else ' Keeping your current model.')

    def chain(self, text, source):
        if getattr(self.app.request,'in_chain',False): return None
        parts=chain_parts(text)
        if not parts or not all(local_clause(part) for part in parts): return None
        if len(parts)>6: return 'Please give me up to six commands at a time.'
        answers=[]
        self.app.request.in_chain=True
        try:
            for index, part in enumerate(parts):
                if index and install_intent(part) and any(re.match(r'^(?:improve yourself|self[ -]improve)\b', p, re.I) for p in parts[:index]):
                    answers.append('The new improvement is still being prepared. I will tell you when it is ready to review and install.')
                    break
                self.app.request.control_failed=False
                try: answer=self.app.command(part,source)
                except (ValueError,RuntimeError) as exc:
                    answer=str(exc);self.app.request.control_failed=True
                answers.append(answer)
                if self.app.request.control_failed or install_intent(part):
                    if index<len(parts)-1: answers.append('The remaining steps have not run.')
                    break
        finally: self.app.request.in_chain=False
        return ' '.join(answers)

    def route(self, text, source):
        low=text.lower().strip()
        answer=self.chain(text,source)
        if answer is not None:return answer
        target=model_target(text)
        if target is not None:return self.select(target,source)
        nav=navigation_target(text)
        if nav:
            if nav[1]=='improvementList':
                from improvements import manager
                rows=manager(self.app).items()
                if rows:self.remember_improvement(source,rows[0]['id'])
            return self.show(nav[0],source,nav[1],nav[2])
        if re.fullmatch(r'(?:switch|change)(?: (?:your|the|my))?(?: ai)? (?:model|provider|ai)',low):
            return self.ask('model',source,'Which model? Say Hey '+self.app.get('name')+', followed by Grok, Claude Sonnet, Claude Opus, OpenAI, or local.')
        with self.app.lock: pending=self.pending.get(source)
        if pending and (pending[0]<time.monotonic() or pending[2]!=self.app.memory.current(source)):
            with self.app.lock:self.pending.pop(source,None)
            pending=None
        if pending and pending[1]=='model' and re.fullmatch(r'(?:'+PROVIDERS+'|'+MODEL_NAMES+r')(?: [\w. -]+)?',low):
            return self.select(low,source)
        if low in ('cancel','never mind','nevermind') and pending:
            with self.app.lock:self.pending.pop(source,None)
            return 'Cancelled. Your model is unchanged.'
        if low in ('what model are you using','which model are you using','current model','what ai are you using'):
            provider=self.app.get('provider')
            return f'I am using {provider}, {self.app.get(provider+"_model") or "the account default"}.'
        if low in ('list models','what models are available','which models are available','what models can i use','which models can you use'):
            from providers import catalog
            provider=self.app.get('provider')
            entry=next((p for p in catalog() if p['id']==provider),{})
            names=[m['label'] for m in entry.get('models',[]) if m.get('id')]
            return ('Available '+provider+' models: '+', '.join(names[:8])+'. Say switch model to, followed by a name.') if names else 'No other models were found for '+provider+'.'
        # An unrelated command ends a clarification, rather than hijacking a later utterance.
        with self.app.lock:self.pending.pop(source,None)
        return None
