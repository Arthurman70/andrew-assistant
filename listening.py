"""Local sleep/feedback commands. No AI, background transcripts, or recordings."""
import re
import time


def route(app, text, source):
    low=text.lower().strip().replace('’',"'")
    targets=['pc','pi']
    suffix=re.search(r'\s+(?:on |for )(?:(?:the|my) )?(pc|computer|pi|raspberry pi|this device|this microphone|both devices|all devices|everywhere)$',low)
    if suffix:
        target=suffix[1]
        if target in ('pc','computer'): targets=['pc']
        elif target in ('pi','raspberry pi'): targets=['pi']
        elif target in ('this device','this microphone'): targets=[source]
        low=low[:suffix.start()]
    elif low.endswith(' everywhere'): low=low[:-11]
    if low in ('wake up','cancel snooze','stop snoozing','resume listening','end sleep mode'):
        for target in targets: app.set(target+'_snooze_until',0)
        return 'Snooze ended. Enabled microphones are listening again.'
    match=re.fullmatch(r'(?:snooze(?: yourself)?|(?:go to )?sleep|sleep mode|take a break|be quiet|stop listening|pause listening)(?: for (.+))?',low)
    if match and (match[1] or low not in ('stop listening','pause listening')):
        if not match[1]: return 'For how long? Say, snooze for thirty minutes.'
        duration=app.seconds(match[1])
        deadline=time.time()+duration
        for target in targets: app.set(target+'_snooze_until',deadline)
        scope='Both microphones' if len(targets)==2 else 'My '+targets[0].upper()+' microphone'
        return f'{scope} will sleep for {match[1]}. I will resume automatically. Timers and alarms still ring.'
    if low in ('snooze status','sleep status','are you asleep'):
        parts=[]
        for target in targets:
            remaining=app.snooze_remaining(target)
            parts.append(target.upper()+(': sleeping for '+str(max(1,round(remaining)))+' more seconds' if remaining else ': not snoozed'))
        return '; '.join(parts)+'.'
    corrections=("that wasn't for you",'that was not for you','that was the tv','that was the video',
                 'that was background noise','false alarm','you heard the tv','you heard the video')
    if low in corrections:
        # An explicit correction is durable preference feedback, not voice enrollment.
        affected=targets if suffix else [source]
        for target in affected: app.set(target+'_wake_mode','strict')
        return 'Understood. I will be more selective here and wait for a clearer voice above the background. No background speech was saved for training.'
    if low in ('normal wake sensitivity','reset wake sensitivity','listen normally'):
        for target in (targets if suffix else [source]): app.set(target+'_wake_mode','adaptive')
        return 'Adaptive wake detection restored. I will still filter background sound.'
    if low in ('be less sensitive','reduce wake sensitivity'):
        for target in (targets if suffix else [source]): app.set(target+'_wake_mode','strict')
        return 'I will require a clearer voice above the background here.'
    return None
