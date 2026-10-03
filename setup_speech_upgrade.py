"""Download public local speech models; verify the published ASR checksum."""
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parent

def download(url,path,sha=None):
    if path.exists() and (not sha or hashlib.sha256(path.read_bytes()).hexdigest()==sha):return
    part=path.with_suffix(path.suffix+'.part')
    with urllib.request.urlopen(url,timeout=90) as response,part.open('wb') as out:
        while chunk:=response.read(1024*1024):out.write(chunk)
    if sha and hashlib.sha256(part.read_bytes()).hexdigest()!=sha:
        raise RuntimeError('Downloaded model checksum did not match.')
    part.replace(path)

if __name__=='__main__':
    dest=ROOT/'runtime/speech';dest.mkdir(parents=True,exist_ok=True)
    name='sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8'
    archive=ROOT/'downloads'/f'{name}.tar.bz2'
    archive.parent.mkdir(parents=True,exist_ok=True)
    download('https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'+archive.name,
             archive,'5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf')
    with tarfile.open(archive) as bundle:
        for member in bundle:
            if not (dest/member.name).resolve().is_relative_to(dest.resolve()):raise ValueError('Unexpected model path')
        bundle.extractall(dest,filter='data')
    print('Parakeet installed.',flush=True)
    download('https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx',dest/'speaker-resnet34.onnx')
    print('Local speaker matching model installed.',flush=True)
