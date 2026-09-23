"""Run against a built image on a machine with Docker; no model download required."""
import json
import re
import subprocess
import sys
import time
from urllib.request import urlopen


def check(image):
    container = subprocess.check_output([
        'docker', 'run', '--detach', '--rm', '--init',
        '--publish', '127.0.0.1:18765:8765', '--mount', 'type=volume,destination=/data',
        image, '--host', '0.0.0.0', '--port', '8765', '--workspace', '/data',
        '--public-origin', 'http://127.0.0.1:18765',
    ], text=True).strip()
    try:
        for _ in range(60):
            logs = subprocess.check_output(['docker', 'logs', container], text=True, stderr=subprocess.STDOUT)
            match = re.search(r'Open (http://127\.0\.0\.1:18765/[^/\s]+/)', logs)
            if match:
                with urlopen(match[1] + 'api/state', timeout=5) as response:
                    state = json.load(response)
                assert state['filename'] is None
                assert state['transcription_available'] is True
                subprocess.run(['docker', 'exec', container, 'python', '-c',
                                "from pathlib import Path; Path('/data/write-check').write_text('ok')"], check=True)
                print('Published-port, startup, transcription import, and volume-write checks passed.')
                return
            time.sleep(1)
        raise RuntimeError('Container did not become ready: ' + logs)
    finally:
        subprocess.run(['docker', 'stop', container], check=False, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    check(sys.argv[1])
