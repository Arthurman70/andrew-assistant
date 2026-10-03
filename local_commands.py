"""Explicit local commands; model-generated text is never passed here."""
import re
from urllib.parse import quote_plus, urlsplit


def web_url(value):
    value = value.strip()
    if '://' not in value and re.fullmatch(r'[\w.-]+\.[a-zA-Z]{2,}(?:/\S*)?', value):
        value = 'https://' + value
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username or parsed.password or any(ord(c)<33 for c in value) or len(value)>2000):
        raise ValueError('Use a normal website address beginning with https://.')
    return value


def route(app, text, source):
    target = source if source in ('pi','browser') else 'pc'
    match = re.search(r'\s+on (?:the |my )?(pi|raspberry pi|pc|computer)$', text, re.I)
    if match:
        target = 'pi' if 'pi' in match[1].lower() else 'pc'
        text = text[:match.start()]
    low = text.lower()
    voice=re.fullmatch(r'(?:use|switch to|change (?:your )?voice to)(?: the)? (expressive|natural|warm|nano|heart|michael)(?: voice)?',low)
    if voice:
        app.set('voice',{'expressive':'nano','nano':'nano','natural':'af_heart','heart':'af_heart','warm':'am_michael','michael':'am_michael'}[voice[1]])
        return 'Voice changed. Expressive speech takes longer to prepare; the natural and warm voices are faster.'
    low=re.sub(r'^turn (up|down) (yourself|your voice|your volume)$',r'turn \2 \1',low)
    key = target+'_volume'
    match = re.fullmatch(r'(?:set |change )?(?:(?:your|andrew|the|my) )?(?:voice )?volume(?: to)? (\d{1,3})(?: ?percent|%)?', low)
    direction = re.fullmatch(r'(?:turn (?:yourself|your voice|your volume|it) (up|down)|(?:speak|talk) (louder|quieter)|volume (up|down))', low)
    mute = re.fullmatch(r'(mute|unmute)(?: yourself| your voice| andrew)?', low)
    if match or direction or mute:
        current = app.get(key)
        if match:
            value = int(match[1])
            if not 0 <= value <= 100:
                app.request.control_failed=True
                return 'Choose my volume between zero and one hundred percent.'
        elif direction:
            value = max(0,min(100,current+(10 if next(v for v in direction.groups() if v) in ('up','louder') else -10)))
        else:
            if mute[1]=='mute':
                if current: app.set(target+'_unmuted_volume',current)
                value=0
            else: value=app.get(target+'_unmuted_volume') or 80
        app.set(key,value)
        return f'My {"Pi" if target=="pi" else "PC"} voice volume is {value} percent.'
    if low in ('what is your volume', "what's your volume", 'your volume'):
        return f'My {target.upper()} voice volume is {app.get(key)} percent.'
    if low in ('stop listening', 'pause listening', 'turn off your microphone'):
        app.set(target+'_listening',False)
        return f'My {target.upper()} microphone is paused. Use the screen to enable it again.'
    if low in ('close browser','close the browser','close video','close the video','stop video','go home','show your face','show the clock'):
        if target=='pi': return app.display_job('home')
        return None  # Let the PC controller close a PC browser when requested.
    if low in ('pause video','pause the video','resume video','resume the video') and target=='pi':
        return app.display_job('pause' if low.startswith('pause') else 'resume')
    url=None
    if low in ('open browser','open the browser','launch browser','launch the browser'):
        url='https://www.google.com'
    elif low in ('open youtube','open you tube'):
        url='https://www.youtube.com'
    else:
        match=re.fullmatch(r'(?:search (?:the web )?for|google) (.+)',text,re.I)
        video=re.fullmatch(r'(?:find|search for|play) (.+?) (?:on youtube|on you tube)',text,re.I)
        direct=re.fullmatch(r'(?:open (?:website |browser |the browser to )?|play (?:a |the )?video )(.+)',text,re.I)
        if match: url='https://www.google.com/search?q='+quote_plus(match[1])
        elif video: url='https://www.youtube.com/results?search_query='+quote_plus(video[1])
        elif direct and (direct[1].startswith(('http://','https://')) or re.fullmatch(r'[\w.-]+\.[a-zA-Z]{2,}(?:/\S*)?',direct[1])):
            url=web_url(direct[1])
    if url:
        if target=='pi': return app.display_job('open',url)
        import webbrowser
        webbrowser.open(url)
        return 'Opened the browser on your PC.'
    return None
