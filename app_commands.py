"""Fast app requests and speech aliases, without a planning-model round trip."""
import re

ALIASES={'clawed':'claude','claud':'claude','clod':'claude','claude':'claude','chat gpt':'chatgpt','chat g p t':'chatgpt','chatgpt':'chatgpt','chat gp t':'chatgpt','grock':'grok','grok':'grok','open ai':'openai','openai':'openai'}
APP_PATTERN='(?:'+ '|'.join(sorted((re.escape(k) for k in ALIASES),key=len,reverse=True))+')'

def normalize(name):return ALIASES.get(name.lower().strip(),name.lower().strip())

def route(app,text,source):
    match=re.fullmatch(r'(?:open|launch|start|bring up)(?: (?:the|my))? ('+APP_PATTERN+r'|calculator|notepad|spotify|chrome)(?: (?:desktop )?app| program)?(?: on (?:my |the )?(?:pc|computer))?',text,re.I)
    if not match:
        match=re.fullmatch(r'use (?:my |the )?('+APP_PATTERN+r')(?: desktop)?(?: app| program)(?: on (?:my |the )?(?:pc|computer))?',text,re.I)
    if match:
        from pc_control import PCController
        target=normalize(match[1]);target='chatgpt' if target=='openai' else target
        result=PCController().open(target)
        if result.get('opened'):return target.title()+' is open on your PC.'
        return 'Launching '+target.title()+' on your PC.'
    # Explicit app use is a desktop task, while “ask Claude …” can continue
    # using the subscription transport without requiring a visible window.
    match=re.match(r'^(?:use|open|launch) (?:my |the )?('+APP_PATTERN+r')(?:(?: desktop)? app| program)? (?:to |and |for )(.+)',text,re.I)
    if match and app.pc_agent:
        target=normalize(match[1]);target='chatgpt' if target=='openai' else target
        return app.pc_agent.start('Use the '+target+' app to '+match[2],source)
    if app.pc_agent and re.match(r'^use (?:my |the )?.+?(?: app| program) to .+',text,re.I):return app.pc_agent.start(text,source)
    return None
