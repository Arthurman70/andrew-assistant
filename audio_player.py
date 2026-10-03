"""Isolated, reusable speaker process. Refresh devices on each playback."""
import base64
import io
import json
import sys
import wave
import sounddevice as sd
from audio_utils import scale_wav


def play(content, desired='auto', volume=100, started=lambda **values:None):
    sd._terminate();sd._initialize()
    devices=sd.query_devices()
    selected=None
    fallback=False
    if desired!='auto':
        selected=next((i for i,d in enumerate(devices)
                       if d['name']==desired and d['max_output_channels']),None)
        fallback=selected is None
    if selected is None:
        selected=int(sd.default.device[1])
        if selected<0 or not devices[selected]['max_output_channels']:
            selected=next((i for i,d in enumerate(devices) if d['max_output_channels']),None)
    if selected is None:raise RuntimeError('No connected speaker output.')
    content=scale_wav(content,volume)
    with wave.open(io.BytesIO(content),'rb') as wav:
        with sd.RawOutputStream(device=selected,samplerate=wav.getframerate(),
                                channels=wav.getnchannels(),dtype='int16') as stream:
            started(device=devices[selected]['name'],fallback=fallback)
            total=0;chunk=max(1,int(wav.getframerate()*.05))
            while True:
                data=wav.readframes(chunk)
                if not data:break
                stream.write(data);total+=len(data)//(wav.getnchannels()*wav.getsampwidth())
                if '--worker' in sys.argv:emit({'progress':total})


def emit(value):
    print(json.dumps(value),flush=True)


def main():
    if '--worker' not in sys.argv:
        play(sys.stdin.buffer.read(),sys.argv[1] if len(sys.argv)>1 else 'auto',
             float(sys.argv[2]) if len(sys.argv)>2 else 100)
        return
    emit({'ready':True})
    for line in sys.stdin:
        try:
            request=json.loads(line)
            play(base64.b64decode(request['audio'],validate=True),request['device'],request['volume'],
                 lambda **values:emit({'started':True,**values}))
            emit({'finished':True})
        except Exception:
            emit({'error':'Speaker playback failed. Connect a speaker or choose another output.'})


if __name__=='__main__':main()
