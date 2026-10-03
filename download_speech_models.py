"""Fetch public open-source speech models; no account tokens or cloud inference."""
import os
import tarfile
import urllib.request
from pathlib import Path
from huggingface_hub import snapshot_download, hf_hub_download

root = Path(__file__).resolve().parent / 'runtime/speech'
root.mkdir(parents=True, exist_ok=True)
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
print('Downloading Whisper small.en for local recognition...', flush=True)
snapshot_download('Systran/faster-whisper-small.en', token=False,
                  local_dir=root / 'whisper-small.en',
                  allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt'])
print('Downloading Piper Ryan voice for local speech...', flush=True)
for filename in ['en_US-ryan-high.onnx', 'en_US-ryan-high.onnx.json', 'MODEL_CARD']:
    path = hf_hub_download('rhasspy/piper-voices', 'en/en_US/ryan/high/' + filename,
                          token=False, local_dir=root / 'piper-download')
    (root / filename).write_bytes(Path(path).read_bytes())
print('Local recognition and natural voice models ready.', flush=True)
for filename in ('kokoro-v1.0.onnx','voices-v1.0.bin'):
    if not (root/filename).exists():
        urllib.request.urlretrieve('https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/'+filename,root/filename)
wake=root.parent/'wake';wake.mkdir(exist_ok=True)
if not (wake/'bpe.model').exists():
    archive=root.parent/'wake-download.tar.bz2'
    urllib.request.urlretrieve('https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2',archive)
    with tarfile.open(archive,'r:bz2') as bundle:
        for member in bundle.getmembers():
            name=Path(member.name).name
            if member.isfile() and (name in ('bpe.model','tokens.txt') or name.endswith('.int8.onnx')):
                (wake/name).write_bytes(bundle.extractfile(member).read())
    archive.unlink()
print('Kokoro natural voice and keyword-only wake models ready.',flush=True)
