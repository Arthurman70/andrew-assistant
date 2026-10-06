"""Shared limits and lossless chunks for task-sized text and spoken readings."""
import io
import re
import wave

MAX_TEXT=32000
READ_CHUNK=12000

def needs_detail(text):
    return bool(re.search(r'\b(?:read|verbatim|entire|full text|word for word|in detail|detailed|thorough|comprehensive|review|research|analyze|analyse|compare)\b',str(text),re.I))

def speech_chunks(text,limit=1200):
    text=str(text).strip()
    while len(text)>limit:
        end=text.rfind(' ',0,limit+1)
        if end<limit//3:end=limit
        yield text[:end]
        text=text[end:].lstrip()
    if text:yield text

def combine_wavs(parts):
    output=io.BytesIO();params=None
    with wave.open(output,'wb') as destination:
        for content in parts:
            with wave.open(io.BytesIO(content),'rb') as source:
                current=(source.getnchannels(),source.getsampwidth(),source.getframerate(),source.getcomptype(),source.getcompname())
                if params is None:
                    params=current;destination.setparams(source.getparams())
                elif current!=params:raise ValueError('Speech chunks used incompatible audio formats.')
                destination.writeframes(source.readframes(source.getnframes()))
    return output.getvalue()
