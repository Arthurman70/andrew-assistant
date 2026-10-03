"""Reproduce the optional expressive voice without changing Andrew's environment."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT=Path(__file__).resolve().parent
COMMIT='5de7a54aa4e5e2baadb0182dde554908b48b85c2'
MODEL_REVISION='71ccd1d0081b430592cea481f4307e764e07bc64'

def main():
    environment=ROOT/'runtime/chatterbox-env'
    python=environment/'Scripts/python.exe'
    if not python.exists():venv.EnvBuilder(with_pip=True).create(environment)
    def install(*args):subprocess.run([str(python),'-m','pip','install',*args],check=True)
    install('torch==2.6.0','torchaudio==2.6.0','--index-url','https://download.pytorch.org/whl/cpu')
    install('git+https://github.com/resemble-ai/chatterbox.git@'+COMMIT)
    # Pin the watermark dependency too: the upstream project references master.
    install('git+https://github.com/resemble-ai/Perth.git@ff1c8ac55a976971245cdd53c18d6131ca00d993')
    code=('from huggingface_hub import snapshot_download; import sys; '
          'snapshot_download(repo_id="ResembleAI/chatterbox-nano",revision=sys.argv[1],'
          'local_dir=sys.argv[2],token=False,allow_patterns=["*.safetensors","*.json","*.txt","*.pt","*.model"])')
    subprocess.run([str(python),'-c',code,MODEL_REVISION,str(ROOT/'runtime/speech/chatterbox-nano')],check=True)
    print('Expressive voice installed. Select it in Andrew Settings, then restart Andrew.')

if __name__=='__main__':main()
