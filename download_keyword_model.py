"""Install public local keyword weights; never download anything during listening."""
import argparse
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import zipfile

MODEL = 'vosk-model-small-en-us-0.15'
URL = 'https://alphacephei.com/vosk/models/'+MODEL+'.zip'


def install(directory):
    directory = Path(directory)
    target = directory/'vosk'
    if (target/'am/final.mdl').is_file() and (target/'conf/model.conf').is_file():
        return target
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='keyword-model-', dir=directory) as folder:
        folder = Path(folder)
        archive = folder/'model.zip'
        legacy = directory.parent/MODEL
        if (legacy/'am/final.mdl').is_file() and (legacy/'conf/model.conf').is_file():
            shutil.copytree(legacy, folder/MODEL)
        else:
            urllib.request.urlretrieve(URL, archive)
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    path = PurePosixPath(member.filename)
                    if '\\' in member.filename or ':' in member.filename or path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != MODEL:
                        raise ValueError('Unexpected keyword model archive path.')
                    if member.is_dir():continue
                    output = folder.joinpath(*path.parts)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as src, output.open('wb') as dst:shutil.copyfileobj(src,dst)
        extracted = folder/MODEL
        if not (extracted/'am/final.mdl').is_file() or not (extracted/'conf/model.conf').is_file():
            raise ValueError('Keyword model download is incomplete.')
        # Merge a repaired download only inside the dedicated model directory.
        shutil.copytree(extracted, target, dirs_exist_ok=True)
    return target


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory', default=str(Path(__file__).resolve().parent/'runtime/wake'))
    args=parser.parse_args()
    print('Local keyword model ready: '+str(install(args.directory)))
