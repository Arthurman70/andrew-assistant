"""Shared, bounded speech turns and conservative conversation handoffs."""
import re

# Capture includes the wake address; recognition allows a little extra padding.
MAX_CAPTURE_SECONDS = 60
MAX_AUDIO_SECONDS = 65
MIN_COMMAND_FRAMES = 8  # 160 ms permits a short yes/no, but not a single click.


def end_silence_frames(voiced):
    # A thinking pause should not split a thought into unrelated commands.
    return 120 if voiced >= 250 else 90  # 2.4s for longer thoughts, otherwise 1.8s.


def empty_utterance(text, followup=False):
    words = re.sub(r'[^\w\s\']', ' ', str(text or '').lower()).split()
    if not words or all(w in {'um', 'uh', 'uhm', 'erm', 'hmm', 'hm', 'ah', 'er'} for w in words):
        return True
    normalized = ' '.join(words)
    if normalized in ('silence', 'noise', 'background noise', 'inaudible', 'unintelligible', 'music'):
        return True
    # Common silence/music hallucinations must not open a new room conversation.
    return followup and normalized in ('thank you for watching', 'thanks for watching',
                                      'subscribe to my channel', 'please subscribe')


def followup_allowed(request, answer, pending=False, notification=False):
    if pending or not str(answer or '').strip():
        return False
    request = str(request or '').lower().strip().rstrip('.!?')
    if re.fullmatch(r'(?:no(?: thanks| thank you)?|thanks(?: a lot)?|thank you(?: very much)?|'
                    r'goodbye|bye|that(?: is|\'s) all|never mind|nevermind|end conversation|'
                    r'stop listening|shut up|be quiet|stop talking)', request):
        return False
    if '?' in answer:
        return True  # A real question deserves an answer without another wake phrase.
    if notification:
        return False  # Background task completion is not consent to listen again.
    if re.match(r'^(?:what(?: is|\'s)? (?:the )?(?:time|date|day|volume|current model)|'
                r'what model|weather\b|forecast\b|list (?:timers|alarms|scheduled tasks)|'
                r'how (?:much|long).*(?:timer|alarm))', request):
        return False
    # Short device confirmations close the turn; open-ended conversation continues.
    return not re.match(r'^(?:(?:please|can you|could you)\s+)*(?:set|start|stop|cancel|dismiss|'
                        r'snooze|pause|resume|turn|open|close|switch|use|rename|call yourself|'
                        r'change|add|remove|delete|save|install|update|improve|take|record|cast|play)\b', request)
