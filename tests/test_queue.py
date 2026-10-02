import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transcription_queue import TranscriptionQueue
from app import Session
from persistence import save_json


class QueueTests(unittest.TestCase):
    def test_schedule_order_failure_and_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def process(task, progress, cancelled):
                calls.append(task['project'])
                if task['project'] == 'bad':
                    raise ValueError('decoder failure')
                progress(70, 'working')
            queue = TranscriptionQueue(Path(directory), process, lambda: True)
            queue.add('later', 'later.m4b', {'not_before': time.time() + 3600})
            bad = queue.add('bad', 'bad.m4b', {})
            queue.add('good', 'good.m4b', {})
            queue.tick(); queue.tick()
            self.assertEqual(calls, ['bad', 'good'])
            self.assertEqual([t['status'] for t in queue.tasks], ['pending', 'error', 'done'])
            queue.action('retry', bad)
            self.assertEqual(queue.tasks[1]['status'], 'pending')
            with self.assertRaises(ValueError):
                queue.add('bad', 'duplicate', {})
            for start in (float('nan'), float('inf'), -1, 'tomorrow'):
                with self.assertRaises(ValueError):
                    queue.add('invalid', 'invalid', {'not_before': start})

    def test_pause_restart_and_reordering(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = TranscriptionQueue(Path(directory), lambda *args: None, lambda: True)
            first = queue.add('a', 'a.m4b', {})
            second = queue.add('b', 'b.m4b', {})
            queue.action('up', second)
            self.assertEqual(queue.tasks[0]['id'], second)
            queue.action('pause'); queue.tick()
            self.assertTrue(queue.cancel.is_set())
            self.assertEqual(queue.tasks[0]['status'], 'pending')
            queue.tasks[0]['status'] = 'running'; queue.save()
            restored = TranscriptionQueue(Path(directory), lambda *args: None, lambda: True)
            self.assertTrue(restored.paused)
            self.assertEqual(restored.tasks[0]['status'], 'pending')
            restored.action('resume'); restored.tick()
            self.assertEqual(restored.tasks[0]['status'], 'done')
            restored.action('remove', first)
            self.assertEqual(len(restored.tasks), 1)

    def test_pause_during_work_keeps_task_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            entered, finish = threading.Event(), threading.Event()
            def process(task, progress, cancelled):
                entered.set(); finish.wait(5)
                if cancelled():
                    raise InterruptedError()
            queue = TranscriptionQueue(Path(directory), process, lambda: True)
            queue.add('a', 'a', {})
            worker = threading.Thread(target=queue.tick); worker.start()
            self.assertTrue(entered.wait(5))
            queue.action('pause'); finish.set(); worker.join(5)
            self.assertEqual(queue.tasks[0]['status'], 'pending')

    def test_background_completion_preserves_live_review(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / 'book.m4b'; source.write_bytes(b'audio')
            project = workspace / ('a' * 24)
            stat = source.stat()
            original = dict(source=str(source), fingerprint=[stat.st_size, stat.st_mtime_ns, stat.st_ino],
                            duration=30, rows=[{'start': 0, 'title': 'old'}], transcript=None, alignment={'old': True})
            save_json(project / 'project.json', original)
            session = Session(workspace=workspace)
            session.project = project
            session.rows = [{'start': 0, 'title': 'edited'}]
            def transcribe(*args, **kwargs):
                current = json.loads((project / 'project.json').read_text())
                current['rows'] = session.rows
                current['omitted_sections'] = ['manual-omission']
                save_json(project / 'project.json', current)
                return {'segments': []}
            with patch('app.transcribe_audio', transcribe):
                session.queued_transcription({'project': project.name, 'model': 'base', 'language': None}, lambda *args: None, lambda: False)
            saved = json.loads((project / 'project.json').read_text())
            self.assertEqual(saved['rows'][0]['title'], 'edited')
            self.assertEqual(saved['omitted_sections'], ['manual-omission'])
            self.assertEqual(session.transcript, {'segments': []})
            self.assertIsNone(saved['alignment'])
            current = dict(saved, book={'title': 'Book', 'chapters': []})
            save_json(project / 'project.json', current)
            result = {'proposals': [], 'engine': 'test'}
            with patch('app.transcribe_audio', return_value={'segments': []}), patch('app.probe', return_value={'chapters': []}), patch('app.match_book', return_value=result) as matching:
                session.queued_transcription({'project': project.name, 'model': 'base', 'language': None, 'align': True}, lambda *args: None, lambda: False)
                matching.assert_called_once()
            self.assertEqual(json.loads((project / 'project.json').read_text())['alignment'], result)
            self.assertEqual(session.alignment, result)
            source.write_bytes(b'changed audio')
            with patch('app.transcribe_audio') as speech:
                with self.assertRaisesRegex(ValueError, 'source file changed'):
                    session.queued_transcription({'project': project.name, 'model': 'base', 'language': None}, lambda *args: None, lambda: False)
                speech.assert_not_called()

