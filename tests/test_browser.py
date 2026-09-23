"""Optional browser integration test; speech output is deterministic and requires no model download."""
import base64
import http.client
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_app as fixtures
import test_epub as epub_fixtures
from app import Session, ThreadingHTTPServer, handler_for, probe

@unittest.skipUnless(os.environ.get("CHAPTERISE_TEST_BROWSER"), "Set CHAPTERISE_TEST_BROWSER to a Chromium browser binary")
class BrowserSmoke(unittest.TestCase):
    def test_review_scan_and_export(self):
        fixtures.AudioTests.setUpClass()
        class BrowserSession(Session):
            def state(self):
                return {**super().state(), 'transcription_available': True}
        speech = {'model': 'base', 'languages': ['en'], 'segments': [
            {'start': 1, 'end': 2, 'text': 'Opening words.'},
            {'start': 9, 'end': 10, 'text': 'The missing lighthouse chapter.'},
            {'start': 19, 'end': 20, 'text': '<img src=x> Closing chapter.'},
        ]}
        speech_mock = patch('app.transcribe_audio', return_value=speech)
        speech_mock.start()
        self.addCleanup(speech_mock.stop)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(
            BrowserSession(fixtures.AudioTests.source, fixtures.AudioTests.directory / 'browser.m4a',
                           fixtures.AudioTests.directory / 'projects')))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        browser = None
        wire = None
        with tempfile.TemporaryDirectory(prefix='chapterise-browser-') as profile:
            try:
                url = f'http://127.0.0.1:{server.server_port}/'
                browser = subprocess.Popen([os.environ['CHAPTERISE_TEST_BROWSER'], '--headless', '--no-sandbox',
                    '--disable-gpu', '--disable-background-networking', '--no-first-run',
                    '--no-default-browser-check', '--remote-debugging-port=0',
                    '--remote-allow-origins=*', f'--user-data-dir={profile}', url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                portfile = Path(profile) / 'DevToolsActivePort'
                deadline = time.monotonic() + 20
                while not portfile.exists() and time.monotonic() < deadline:
                    time.sleep(.1)
                port = int(portfile.read_text().splitlines()[0])
                connection = http.client.HTTPConnection('127.0.0.1', port)
                connection.request('GET', '/json/list')
                pages = json.loads(connection.getresponse().read())
                connection.close()
                target = next(p for p in pages if p['type'] == 'page')
                ws = urlsplit(target['webSocketDebuggerUrl'])
                wire = socket.create_connection((ws.hostname, ws.port), timeout=10)
                key = base64.b64encode(os.urandom(16)).decode()
                wire.sendall(f'GET {ws.path} HTTP/1.1\r\nHost: {ws.netloc}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'.encode())
                response = b''
                while not response.endswith(b'\r\n\r\n'):
                    response += wire.recv(1)
                self.assertIn(b'HTTP/1.1 101', response)
                serial = 0
                def exact(n):
                    data = b''
                    while len(data) < n:
                        block = wire.recv(n-len(data))
                        if not block: raise RuntimeError('Browser connection closed')
                        data += block
                    return data
                def call(method, params=None):
                    nonlocal serial
                    serial += 1
                    payload = json.dumps({'id': serial, 'method': method, 'params': params or {}}).encode()
                    mask = os.urandom(4)
                    length = len(payload)
                    header = (bytes([0x81, 0x80 | length]) if length < 126 else
                              bytes([0x81, 0xfe]) + struct.pack('!H', length) if length < 65536 else
                              bytes([0x81, 0xff]) + struct.pack('!Q', length))
                    wire.sendall(header + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))
                    while True:
                        first, second = exact(2)
                        size = second & 127
                        if size == 126: size = struct.unpack('!H', exact(2))[0]
                        elif size == 127: size = struct.unpack('!Q', exact(8))[0]
                        incoming = json.loads(exact(size))
                        if incoming.get('id') == serial:
                            if 'error' in incoming: raise RuntimeError(incoming)
                            return incoming['result']
                def evaluate(expression):
                    result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True, 'awaitPromise': True, 'userGesture': True})
                    if 'exceptionDetails' in result: raise RuntimeError(result)
                    return result['result'].get('value')
                def until(expression):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if evaluate(expression): return
                        time.sleep(.1)
                    raise AssertionError('Timed out: ' + expression + '\n' + str(evaluate('document.body.innerText')))
                until("document.getElementById('filename')?.textContent === 'original.m4a'")
                self.assertTrue(evaluate("document.getElementById('export').disabled"))
                evaluate("document.getElementById('spacing').value = '0'; document.getElementById('scan').click()")
                until("document.getElementById('status').textContent.startsWith('Found 2')")
                self.assertEqual(evaluate("document.querySelectorAll('#rows tr').length"), 3)
                start = evaluate("document.querySelector('#rows tr:nth-child(2) button').click(); audio.currentTime")
                self.assertAlmostEqual(start, evaluate("rows[1].start"), places=3)
                self.assertAlmostEqual(evaluate("previewEnd"), start + 10, places=3)
                until("document.getElementById('audio').currentTime > 0")
                # A preview near EOF stops at the audio end, not beyond it.
                evaluate("document.querySelector('#rows tr:nth-child(3) button').click()")
                self.assertAlmostEqual(evaluate("previewEnd"), 22, places=3)
                evaluate("document.getElementById('audio').pause(); document.getElementById('approved').click()")
                self.assertFalse(evaluate("document.getElementById('export').disabled"))
                self.assertTrue(evaluate("document.querySelector('#rows input[type=range]').disabled"))
                def move_slider(row_index, offset):
                    evaluate(f"(() => {{ const slider = document.querySelector('#rows tr:nth-child({row_index}) input[type=range]'); slider.focus(); slider.value = '{offset}'; slider.dispatchEvent(new Event('input')); slider.dispatchEvent(new Event('change')); }})()")
                # Moving past another chapter sorts the rows but retains the original anchor.
                move_slider(2, 15000)
                self.assertTrue(evaluate("document.getElementById('export').disabled"))
                self.assertAlmostEqual(evaluate("rows[2].start - rows[2].originalStart"), 15, places=3)
                self.assertTrue(evaluate("document.activeElement.type === 'range'"))
                move_slider(3, 1250)
                adjusted = evaluate("rows[1].start")
                self.assertAlmostEqual(evaluate("rows[1].start - rows[1].originalStart"), 1.25, places=3)
                self.assertEqual(evaluate("document.querySelector('#rows tr:nth-child(2) input').type"), 'checkbox')
                self.assertIn('+1.250s', evaluate("document.querySelector('#rows tr:nth-child(2) output').textContent"))
                self.assertEqual(evaluate("document.querySelector('#rows tr:nth-child(2) input[aria-label=\"Chapter start time\"]').value"), evaluate("timestamp(rows[1].start)"))
                # The source file ends inside this marker's +15 second window.
                self.assertLess(evaluate("rows[2].originalStart * 1000 + Number(document.querySelector('#rows tr:nth-child(3) input[type=range]').max)"), 22000)
                # Prepare the gap without creating a marker, then preview and explicitly add it.
                evaluate("document.querySelector('#rows tr:nth-child(2) button:nth-child(2)').click()")
                self.assertEqual(evaluate("rows.length"), 3)
                self.assertEqual(evaluate("document.getElementById('manual-time').value"), '00:00:11.625')
                start = evaluate("document.getElementById('manual-time').value = '00:00:11.000'; document.getElementById('manual-title').value = 'Missing chapter'; document.getElementById('preview-manual').click(); audio.currentTime")
                self.assertAlmostEqual(start, 11, places=3)
                self.assertAlmostEqual(evaluate("previewEnd"), 21, places=3)
                self.assertEqual(evaluate("document.getElementById('manual-time').value"), '00:00:11.000')
                evaluate("document.getElementById('audio').pause(); document.getElementById('approved').click(); document.getElementById('add').click()")
                self.assertEqual(evaluate("rows.length"), 4)
                self.assertTrue(evaluate("document.getElementById('export').disabled"))
                self.assertEqual(evaluate("rows[2]"), {'start': 11, 'originalStart': 11, 'title': 'Missing chapter', 'kind': 'manual', 'selected': True, 'pause': None})
                self.assertTrue(evaluate("document.querySelector('#rows tr:nth-child(3)').classList.contains('new-marker')"))
                # Reject duplicates, invalid timestamps, and the beginning/end.
                for invalid in ['11', '0', '22', 'not a time']:
                    evaluate(f"document.getElementById('manual-time').value = '{invalid}'; document.getElementById('add').click()")
                    self.assertEqual(evaluate("rows.length"), 4)
                    self.assertTrue(evaluate("document.getElementById('manual-status').classList.contains('error')"))
                # Capture a manually located position and add a second missing chapter.
                evaluate("document.getElementById('audio').currentTime = 18; document.getElementById('use-playhead').click()")
                self.assertEqual(evaluate("document.getElementById('manual-time').value"), '00:00:18.000')
                evaluate("document.getElementById('add').click()")
                self.assertEqual(evaluate("rows.length"), 5)
                self.assertEqual(evaluate("rows[4].start"), 18)
                # Transcription is a background API job and must preserve manual edits.
                evaluate("document.getElementById('transcribe').click()")
                until("document.getElementById('status').textContent.startsWith('Transcript ready:')")
                self.assertEqual(evaluate("rows.length"), 5)
                self.assertEqual(evaluate("rows[2].title"), 'Missing chapter')
                self.assertIn('lighthouse', evaluate("document.querySelector('#rows tr:nth-child(2) .snippet').textContent"))
                self.assertNotIn('Opening', evaluate("document.querySelector('#rows tr:nth-child(2) .snippet').textContent"))
                move_slider(5, -15000)
                self.assertNotIn('Closing', evaluate("document.querySelector('#rows tr:nth-child(2) .snippet').textContent"))
                move_slider(2, 0)
                self.assertIn('Closing', evaluate("document.querySelector('#rows tr:nth-child(5) .snippet').textContent"))
                evaluate("document.getElementById('transcript-query').value = 'LIGHTHOUSE'; document.getElementById('transcript-query').dispatchEvent(new Event('input'))")
                self.assertEqual(evaluate("document.querySelectorAll('.transcript-result').length"), 1)
                start = evaluate("document.querySelector('.transcript-result').click(); audio.currentTime")
                self.assertAlmostEqual(start, 9, places=3)
                self.assertEqual(evaluate("document.getElementById('manual-time').value"), '00:00:09.000')
                self.assertAlmostEqual(evaluate("previewEnd"), 19)
                evaluate("document.getElementById('audio').pause(); document.getElementById('transcript-query').value = 'nonexistent'; document.getElementById('transcript-query').dispatchEvent(new Event('input'))")
                self.assertEqual(evaluate("document.querySelectorAll('.transcript-result').length"), 0)
                evaluate("document.getElementById('transcript-query').value = '<img'; document.getElementById('transcript-query').dispatchEvent(new Event('input'))")
                self.assertEqual(evaluate("document.querySelectorAll('.transcript-result').length"), 1)
                self.assertEqual(evaluate("document.querySelectorAll('#transcript-results img').length"), 0)
                # Removing and undoing a chapter changes approval but preserves its content.
                self.assertTrue(evaluate("document.querySelector('#rows .remove-marker').disabled"))
                evaluate("document.getElementById('approved').click(); document.querySelector('#rows tr:nth-child(3) .remove-marker').click()")
                self.assertEqual(evaluate("rows.length"), 4)
                self.assertTrue(evaluate("document.getElementById('export').disabled"))
                evaluate("document.getElementById('undo-remove').click()")
                self.assertEqual(evaluate("rows.length"), 5)
                self.assertEqual(evaluate("rows[2].title"), 'Missing chapter')
                evaluate("document.querySelector('#rows tr:nth-child(5) .remove-marker').click()")
                self.assertEqual(evaluate("rows.length"), 4)
                # A saved review must preserve both the adjusted position and the original anchor.
                evaluate("(() => { const data = {version: 1, filename: state.filename, duration: state.duration, rows, transcript}; const transfer = new DataTransfer(); transfer.items.add(new File([JSON.stringify(data)], 'review.json', {type: 'application/json'})); const input = document.getElementById('load'); input.files = transfer.files; input.dispatchEvent(new Event('change')); })()")
                until("document.getElementById('status').textContent.startsWith('Saved review loaded')")
                self.assertAlmostEqual(evaluate("rows[1].start - rows[1].originalStart"), 1.25, places=3)
                start = evaluate("document.querySelector('#rows tr:nth-child(2) button').click(); audio.currentTime")
                self.assertAlmostEqual(start, adjusted, places=3)
                self.assertAlmostEqual(evaluate("previewEnd"), adjusted + 10, places=3)
                evaluate("document.getElementById('audio').pause(); document.getElementById('approved').click(); document.getElementById('export').click()")
                until("document.getElementById('status').textContent.startsWith('Export complete:')")
                exported = probe(fixtures.AudioTests.directory / 'browser.m4a')['chapters']
                self.assertEqual(len(exported), 4)
                self.assertAlmostEqual(float(exported[2]['start_time']), 11, places=3)
                self.assertEqual(exported[2]['tags']['title'], 'Missing chapter')
                self.assertNotIn(18, [float(c['start_time']) for c in exported])
                self.assertEqual(evaluate('transcript.segments.length'), 3)
                self.assertAlmostEqual(float(exported[1]['start_time']), adjusted, places=3)
                # A new upload can be matched against an EPUB and restored after refresh.
                audio_data = base64.b64encode(fixtures.AudioTests.source.read_bytes()).decode()
                evaluate(f"uploadFile('audio', new File([Uint8Array.from(atob('{audio_data}'), c => c.charCodeAt(0))], 'uploaded.m4b'))")
                self.assertEqual(evaluate('state.filename'), 'uploaded.m4b')
                self.assertEqual(evaluate('rows.length'), 1)
                book_file = epub_fixtures.make_epub(fixtures.AudioTests.directory / 'fixture.epub')
                # Replace chapter one's text with our deterministic recognition fixture.
                from zipfile import ZipFile
                with ZipFile(book_file) as archive:
                    files = {name: archive.read(name) for name in archive.namelist()}
                files['OPS/text.xhtml'] = files['OPS/text.xhtml'].replace(b'Opening text for chapter one.', epub_fixtures.MatchingTests.passage.encode())
                with ZipFile(book_file, 'w') as archive:
                    for name, content in files.items(): archive.writestr(name, content)
                epub_data = base64.b64encode(book_file.read_bytes()).decode()
                evaluate(f"uploadFile('epub', new File([Uint8Array.from(atob('{epub_data}'), c => c.charCodeAt(0))], 'fixture.epub'))")
                speech['segments'] = [epub_fixtures.transcript_passage(epub_fixtures.MatchingTests.passage, 1)]
                evaluate("document.getElementById('align').click()")
                until("document.getElementById('status').textContent.startsWith('EPUB matching finished')")
                self.assertEqual(evaluate("document.querySelectorAll('.proposal').length"), 2)
                self.assertEqual(evaluate('rows.length'), 1)  # No automatic acceptance.
                self.assertEqual(evaluate('alignment.proposals[0].start'), 1)
                self.assertIsNone(evaluate('alignment.proposals[1].start'))
                evaluate("document.querySelector('.proposal button').click()")
                self.assertAlmostEqual(evaluate('audio.currentTime'), 1, places=3)
                self.assertAlmostEqual(evaluate('previewEnd'), 11, places=3)
                evaluate('audio.pause()')
                evaluate("document.querySelector('.proposal button:nth-child(2)').click()")
                self.assertEqual(evaluate('rows.length'), 2)
                self.assertEqual(evaluate('rows[1].kind'), 'epub')
                self.assertTrue(evaluate("document.getElementById('export').disabled"))
                evaluate("document.querySelector('#rows tr:nth-child(2) .remove-marker').click(); document.getElementById('undo-remove').click()")
                self.assertEqual(evaluate('rows.length'), 2)
                self.assertTrue(evaluate("document.querySelector('.proposal button:nth-child(2)').disabled"))
                evaluate("document.querySelector('.omit-section').click()")
                self.assertEqual(evaluate('omittedSections.length'), 1)
                self.assertIn('Marked not present', evaluate("document.querySelectorAll('.proposal .confidence')[1].textContent"))
                evaluate('persistReview()')
                evaluate('window.__chapteriseBeforeReload = true')
                call('Page.reload')
                until("!window.__chapteriseBeforeReload && typeof alignment !== 'undefined' && alignment?.proposals.length === 2 && rows.length === 2")
                self.assertFalse(evaluate("document.getElementById('approved').checked"))
                self.assertEqual(evaluate('rows[1].title'), 'First')
                self.assertEqual(evaluate('omittedSections.length'), 1)
                self.assertEqual(evaluate("document.querySelector('.omit-section').textContent"), 'Reconsider section')
                evaluate("document.querySelector('.omit-section').click()")
                self.assertEqual(evaluate('omittedSections.length'), 0)
                evaluate('persistReview()')
                self.assertEqual(evaluate('transcript.segments.length'), 1)
                if os.environ.get('CHAPTERISE_TEST_SCREENSHOT'):
                    screenshot = call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})
                    Path(os.environ['CHAPTERISE_TEST_SCREENSHOT']).write_bytes(base64.b64decode(screenshot['data']))
            finally:
                if wire: wire.close()
                if browser:
                    browser.terminate()
                    browser.wait(timeout=10)
                server.shutdown()
                server.server_close()
                fixtures.AudioTests.tearDownClass()

if __name__ == '__main__':
    unittest.main()
