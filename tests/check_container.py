"""Run against a built image on a machine with Docker; no model download required."""
import json
import subprocess
import sys
import time
from urllib.request import urlopen, Request
from urllib.error import URLError


def check(image):
    container = subprocess.check_output([
        'docker', 'run', '--detach', '--rm', '--init',
        '--publish', '127.0.0.1:18765:8765', '--mount', 'type=volume,destination=/data',
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
                print('Published-port, startup, transcription import, and volume-write checks passed.')
                return
            except (URLError, ConnectionError, TimeoutError):
                time.sleep(1)
        logs = subprocess.check_output(['docker', 'logs', container], text=True, stderr=subprocess.STDOUT)
        raise RuntimeError('Container did not become ready: ' + logs)
    finally:
        subprocess.run(['docker', 'stop', container], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    check(sys.argv[1])
