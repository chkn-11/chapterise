"""Run against a built image on a machine with Docker; no model download required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen, Request
from urllib.error import URLError


def check(image, mounts, user=None):
    user_args = ['--user', user] if user else []
    container = subprocess.check_output([
        'docker', 'run', '--detach', '--rm', '--init',
        '--publish', '127.0.0.1:18765:8765', *mounts, *user_args,
        image,
    ], text=True).strip()
    try:
        for _ in range(60):
            base = 'http://127.0.0.1:18765'
            try:
                with urlopen(base + '/', timeout=5) as response:
                    assert b'Chapterise' in response.read()
                with urlopen(base + '/api/state', timeout=5) as response:
                    state = json.load(response)
                assert state['filename'] is None
                assert state['transcription_available'] is True
                with urlopen(Request(base + '/api/cancel', data=b'{}', headers={
                        'Content-Type': 'application/json', 'Origin': base}), timeout=5) as response:
                    assert response.status == 200
                subprocess.run(['docker', 'exec', container, 'python', '-c',
                                "from pathlib import Path; Path('/data/write-check').write_text('ok')"], check=True)
                break
            except (URLError, ConnectionError, TimeoutError):
                time.sleep(1)
        else:
            logs = subprocess.check_output(['docker', 'logs', container], text=True, stderr=subprocess.STDOUT)
            raise RuntimeError('Container did not become ready: ' + logs)
        if user:
            identity = subprocess.check_output(['docker', 'exec', container, 'python', '-c',
                'import os; print(f"{os.getuid()}:{os.getgid()}")'], text=True).strip()
            assert identity == user, identity
            subprocess.run(['docker', 'exec', container, 'python', '-c',
                "from pathlib import Path; from huggingface_hub.constants import HF_HUB_CACHE; "
                "cache = Path(HF_HUB_CACHE); assert cache.is_relative_to('/models'); "
                "cache.mkdir(parents=True, exist_ok=True); (cache / 'write-check').write_text('ok')"], check=True)
            subprocess.run(['docker', 'exec', container, 'ffmpeg', '-v', 'error',
                '-f', 'lavfi', '-i', 'anullsrc=r=16000:cl=mono', '-t', '1',
                '-c:a', 'aac', '/tmp/fixture.m4b'], check=True)
            audio = subprocess.check_output(['docker', 'exec', container, 'cat', '/tmp/fixture.m4b'])
            with urlopen(Request(base + '/api/upload/audio', data=audio, headers={
                    'Content-Type': 'application/octet-stream', 'X-File-Name': 'fixture.m4b',
                    'Origin': base}), timeout=30) as response:
                assert response.status == 200
            with urlopen(base + '/api/state', timeout=5) as response:
                assert json.load(response)['filename'] == 'fixture.m4b'
            print(f'Custom user {user}: audio upload and model-cache write passed.')
        print('Published-port, startup, transcription import, and volume-write checks passed.')
    finally:
        subprocess.run(['docker', 'stop', container], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    if '--custom-user' in sys.argv[2:]:
        uid, gid = os.getuid(), os.getgid()
        if uid in (0, 10001):
            raise SystemExit('Run custom-user checks as a non-root host user other than UID 10001.')
        with tempfile.TemporaryDirectory(prefix='chapterise-container-') as directory:
            mounts = []
            for name in ('data', 'models'):
                folder = Path(directory) / name
                folder.mkdir()
                mounts.extend(['--mount', f'type=bind,source={folder},destination=/{name}'])
            check(sys.argv[1], mounts, f'{uid}:{gid}')
            assert (Path(directory) / 'data' / 'last.json').is_file()
            assert (Path(directory) / 'models' / 'hub' / 'write-check').read_text() == 'ok'
    else:
        check(sys.argv[1], ['--mount', 'type=volume,destination=/data'])
