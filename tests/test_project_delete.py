import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import Session
from persistence import save_json
from transcription_queue import TranscriptionQueue


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.session = Session(workspace=self.workspace)
        self.session.queue = TranscriptionQueue(self.workspace, lambda *args: None, lambda: True)

    def load(self, source):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'audio')
        with patch('app.probe', return_value={'format': {'duration': '100'}, 'chapters': []}):
            self.session.load_source(source)
        cache = self.session.project / 'chunks' / 'one.json'
        save_json(cache, {'cached': True})
        return self.session.project

    def test_uploaded_files_queue_and_current_state_removed(self):
        source = self.workspace / 'upload-example' / 'book.m4b'
        project = self.load(source)
        self.session.output.write_bytes(b'export')
        self.session.queue.add(project.name, source.name, {})
        self.session.delete_project(project.name)
        self.assertFalse(project.exists())
        self.assertFalse(source.parent.exists())
        self.assertFalse((self.workspace / 'last.json').exists())
        self.assertIsNone(self.session.state()['project_id'])
        self.assertIsNone(self.session.state()['filename'])
        self.assertEqual(self.session.queue.tasks, [])
        restored = TranscriptionQueue(self.workspace, lambda *args: None, lambda: True)
        self.assertEqual(restored.tasks, [])

    def test_external_files_preserved(self):
        source = self.root / 'original.m4b'
        project = self.load(source)
        self.session.output.write_bytes(b'export')
        output = self.session.output
        self.session.delete_project(project.name)
        self.assertTrue(source.exists())
        self.assertTrue(output.exists())
        self.assertFalse(project.exists())

    def test_shared_upload_preserved_until_last_project(self):
        source = self.workspace / 'upload-example' / 'book.m4b'
        project = self.load(source)
        other = self.workspace / ('f' * 24)
        data = json.loads((project / 'project.json').read_text())
        save_json(other / 'project.json', data)
        self.session.delete_project(project.name)
        self.assertTrue(source.exists())
        self.assertTrue(other.exists())
        self.session.delete_project(other.name)
        self.assertFalse(source.parent.exists())

    def test_running_work_blocks_deletion(self):
        source = self.workspace / 'upload-example' / 'book.m4b'
        project = self.load(source)
        self.session.queue.add(project.name, source.name, {})
        self.session.queue.tasks[0]['status'] = 'running'
        with self.assertRaisesRegex(ValueError, 'Pause the queue'):
            self.session.delete_project(project.name)
        self.session.queue.tasks[0]['status'] = 'pending'
        self.session.job['status'] = 'running'
        with self.assertRaisesRegex(ValueError, 'current operation'):
            self.session.delete_project(project.name)
        self.assertTrue(project.exists())
        self.assertTrue(source.exists())

    def test_invalid_identifier_and_symlinks_preserved(self):
        source = self.root / 'original.m4b'
        project = self.load(source)
        for identifier in ('../original.m4b', None, 'missing'):
            with self.assertRaises(ValueError):
                self.session.delete_project(identifier)
        alias = self.workspace / ('f' * 24)
        alias.symlink_to(project, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.session.delete_project(alias.name)
        self.assertTrue(project.exists())
        upload = self.workspace / 'upload-link'
        upload.symlink_to(self.root, target_is_directory=True)
        data = json.loads((project / 'project.json').read_text())
        data['source'] = str(upload / source.name)
        save_json(project / 'project.json', data)
        self.session.delete_project(project.name)
        self.assertTrue(source.exists())
        self.assertTrue(upload.is_symlink())
