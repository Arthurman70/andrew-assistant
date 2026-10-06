"""Per-assistant audio gain. Never changes a device's master volume."""
import io
import wave
import numpy as np

def scale_wav(content,percent):
    with wave.open(io.BytesIO(content),'rb') as source:
        if source.getsampwidth()!=2: raise ValueError('Expected 16-bit voice audio.')
        params=source.getparams()
        samples=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype(np.float32)
    samples=(samples*max(0,min(100,float(percent)))/100).clip(-32768,32767).astype('<i2')
    target=io.BytesIO()
    with wave.open(target,'wb') as output:
        output.setparams(params);output.writeframes(samples.tobytes())
    return target.getvalue()


def playback_timeout(content):
    """Permit the complete clip instead of cutting long readings at two minutes."""
    with wave.open(io.BytesIO(content),'rb') as source:
        duration=source.getnframes()/source.getframerate()
    return max(120,duration+30)
