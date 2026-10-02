"""Durable serial transcription backlog. Review state belongs to the session."""
import copy
import json
import threading
import time
import uuid
from persistence import save_json
from transcription import validate_options


class TranscriptionQueue:
    def __init__(self, workspace, process, available):
        self.path = workspace / 'transcription-queue.json'
        self.process, self.available = process, available
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.stopped = threading.Event()
        self.paused, self.tasks = False, []
        if self.path.exists():
            data = json.loads(self.path.read_text())
            self.paused, self.tasks = data['paused'], data['tasks']
            for task in self.tasks:
                if task['status'] == 'running':
                    task.update(status='pending', detail='Resuming after restart')
        self.save()

    def save(self):
        save_json(self.path, {'paused': self.paused, 'tasks': self.tasks})

    def state(self):
        with self.lock:
            return copy.deepcopy({'paused': self.paused, 'tasks': self.tasks})

    def add(self, project, filename, settings):
        model, language = settings.get('model', 'base'), settings.get('language') or None
        validate_options(model, language)
        start = settings.get('not_before', 0)
        if isinstance(start, bool) or not isinstance(start, (int, float)) or not 0 <= start <= 253402300799:
            raise ValueError('Invalid scheduled start time.')
        align = settings.get('align', False)
        if not isinstance(align, bool):
            raise ValueError('Invalid matching option.')
        with self.lock:
            if any(t['project'] == project and t['status'] in ('pending', 'running') for t in self.tasks):
                raise ValueError('This project is already queued.')
            task = dict(id=uuid.uuid4().hex, project=project, filename=filename,
                        model=model, language=language, align=align, not_before=start, status='pending', progress=0)
            self.tasks.append(task)
            self.save()
            return task['id']

    def action(self, action, identifier=None):
        with self.lock:
            if action in ('pause', 'resume'):
                self.paused = action == 'pause'
                if self.paused:
                    self.cancel.set()
            else:
                task = next((t for t in self.tasks if t['id'] == identifier), None)
                if task is None:
                    raise ValueError('Queue task not found.')
                if task['status'] == 'running':
                    raise ValueError('Pause the queue and wait for the task to stop first.')
                if action == 'remove':
                    self.tasks.remove(task)
                elif action == 'retry':
                    if any(t is not task and t['project'] == task['project'] and t['status'] in ('pending', 'running') for t in self.tasks):
                        raise ValueError('This project already has an active queue task.')
                    task.update(status='pending', detail='', progress=0)
                elif action == 'up':
                    index = self.tasks.index(task)
                    if index:
                        self.tasks[index - 1], self.tasks[index] = task, self.tasks[index - 1]
                else:
                    raise ValueError('Unknown queue action.')
            self.save()

    def tick(self):
        with self.lock:
            if self.paused or any(t['status'] == 'running' for t in self.tasks) or not self.available():
                return
            task = next((t for t in self.tasks if t['status'] == 'pending' and t['not_before'] <= time.time()), None)
            if task is None:
                return
            self.cancel.clear()
            task.update(status='running', detail='Preparing transcription')
            self.save()
        def progress(value, detail):
            with self.lock:
                task.update(progress=value, detail=detail)
        try:
            self.process(task, progress, self.cancel.is_set)
            with self.lock:
                task.update(status='done', progress=100, detail='Transcript saved')
        except InterruptedError:
            with self.lock:
                task.update(status='pending', detail='Paused; completed chunks saved')
        except Exception as error:
            with self.lock:
                task.update(status='error', detail=str(error))
        finally:
            with self.lock:
                self.save()

    def start(self):
        def worker():
            while not self.stopped.is_set():
                self.tick()
                self.stopped.wait(1)
        threading.Thread(target=worker, daemon=True).start()
