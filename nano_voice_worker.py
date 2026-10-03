"""Isolated local Nano runtime. JSON stdin/stdout, no network during use."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import wave

os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
ROOT=Path(__file__).resolve().parent

def main():
    with contextlib.redirect_stdout(sys.stderr):
        import torch
        import numpy as np
        from chatterbox.tts_turbo import ChatterboxTurboTTS
        torch.set_num_threads(6)
        model=ChatterboxTurboTTS.from_local(ROOT/'runtime/speech/chatterbox-nano',device='cpu',nano=True)
        model.generate('Hello. I am ready to help.')
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line);text=request['text']
            if not isinstance(text,str) or not 1<=len(text)<=2400:raise ValueError()
            with contextlib.redirect_stdout(sys.stderr):
                audio=model.generate(text).squeeze().detach().cpu().numpy()
            output=io.BytesIO()
            with wave.open(output,'wb') as wav:
                wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(model.sr)
                wav.writeframes((audio.clip(-1,1)*32767).astype('<i2').tobytes())
            print(json.dumps({'audio':base64.b64encode(output.getvalue()).decode()}),flush=True)
        except Exception:print(json.dumps({'error':'Local Nano voice could not synthesize this sentence.'}),flush=True)

if __name__=='__main__':main()
