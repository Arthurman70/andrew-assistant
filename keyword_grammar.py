"""Bounded local keyword grammar. Never enable general room transcription."""
from functools import lru_cache
import json
import math
from pathlib import Path
import re

# Dictionary entries such as "tam" share legitimate pronunciations of Tom.
# Use common, distinct names as rejection paths, not arbitrary vowel spellings.
CONFUSABLE_NAMES = frozenset('tom tim dan don ray roy ron rob bob sam jim kim ben ken john joan jean jane joe jay lee lou leo liz max mike mark mary marie anna anne amy ava ella emma'.split())


def model_path(directory):
    directory = Path(directory)
    for path in (directory/'vosk', directory.parent/'vosk-model-small-en-us-0.15'):
        if (path/'am/final.mdl').is_file() and (path/'conf/model.conf').is_file():
            return path
    return None


@lru_cache(maxsize=1)
def load_model(path):
    import vosk
    vosk.SetLogLevel(-1)
    try:return vosk.Model(str(path))
    except Exception as exc:raise RuntimeError('Keyword model is unavailable.') from exc


def confusable_names(name, known):
    """Give nearby vowels their own rejection paths instead of forcing a match."""
    variants = []
    for at, char in enumerate(name):
        if char not in 'aeiou':
            continue
        for vowel in 'aeiou':
            candidate = name[:at]+vowel+name[at+1:]
            if candidate != name and candidate in CONFUSABLE_NAMES and candidate not in variants and all(known(w) for w in candidate.split()):
                variants.append(candidate)
    return variants[:24]


class KeywordGrammar:
    @classmethod
    def available(cls, directory, name, phrases):
        path = model_path(directory)
        if not path or not re.fullmatch(r"[a-z]+(?:[ '-][a-z]+)*", name.lower()):
            return None
        try:
            model = load_model(str(path))
            known = lambda word: model.vosk_model_find_word(word) >= 0
            normalized = [phrase.lower() for phrase in phrases]
            # Vosk otherwise drops unknown words: a missing name must NEVER
            # turn the address into a recognizer for just "hey".
            if not all(known(word) for phrase in normalized for word in phrase.split()):
                return None
            return cls(model, name.lower(), normalized, known)
        except (ImportError, OSError, RuntimeError):
            return None  # Existing small keyword model remains available.

    def __init__(self, model, name, phrases, known):
        self.model = model
        self.targets = [phrase.split() for phrase in phrases]
        rivals = confusable_names(name, known)
        alternatives = []
        for phrase in phrases:
            at = 4 if phrase.startswith('hey '+name) else 0
            alternatives.extend(phrase[:at]+rival+phrase[at+len(name):] for rival in rivals)
        self.grammar = json.dumps(list(dict.fromkeys(phrases+alternatives+['[unk]'])))
        self.reset()

    def reset(self):
        # Capture calls reset for every blocked frame during playback. Defer
        # reconstruction until input resumes instead of falling behind audio.
        self.recognizer = None
        self.samples = 0
        self.last_end = -1

    def prepare(self):
        import vosk
        # A fresh recognizer also resets its timestamps; Reset() keeps its
        # previous time offset, which would break privacy-safe lookback.
        self.recognizer = vosk.KaldiRecognizer(self.model, 16000, self.grammar)
        self.recognizer.SetWords(True)
        self.recognizer.SetPartialWords(True)

    def accept(self, pcm):
        if self.recognizer is None:self.prepare()
        self.samples += len(pcm)//2
        final = self.recognizer.AcceptWaveform(pcm)
        result = json.loads(self.recognizer.Result() if final else self.recognizer.PartialResult())
        words = result.get('result' if final else 'partial_result', [])
        for target in self.targets:
            for at in range(len(words)-len(target)+1):
                match = words[at:at+len(target)]
                if [word.get('word') for word in match] != target:
                    continue
                numbers = [word.get(key, -1) for word in match for key in ('start','end','conf')]
                if not all(isinstance(n,(int,float)) and math.isfinite(n) for n in numbers):
                    continue
                start, end = match[0]['start'], match[-1]['end']
                if start < 0 or not .2 <= end-start <= 4 or end <= self.last_end:
                    continue
                if not 0 <= self.samples/16000-end <= 1.5:
                    continue
                if any(word['conf'] < .9 or word['end'] <= word['start'] for word in match):
                    continue
                if any(not -.03 <= b['start']-a['end'] <= .35 for a,b in zip(match,match[1:])):
                    continue
                self.last_end = end
                # Only sample boundaries escape this class. No transcript,
                # partial text, unknown speech, or rejection phrase is retained.
                return int(start*16000), int(end*16000)
        return None
