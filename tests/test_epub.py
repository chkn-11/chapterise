import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Session, ThreadingHTTPServer, handler_for, validate_transcript
from epub_match import match_book, read_epub, resolve, audio_extras


def make_epub(path, ncx=False, no_toc=False):
    with ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>')
        nav_item = '' if no_toc else ('<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>' if ncx else '<item id="toc" href="nav.xhtml" properties="nav"/>')
        z.writestr('OPS/book.opf', '<package><metadata><title>Fixture Book</title></metadata><manifest>' + nav_item + '<item id="text" href="text.xhtml" media-type="application/xhtml+xml"/></manifest><spine toc="toc"><itemref idref="text"/></spine></package>')
        z.writestr('OPS/text.xhtml', '<html><head><title>Hidden title</title></head><body><h1 id="one">First</h1><p>Opening text for chapter one.</p><h1 id="two">Second</h1><p>Opening text for chapter two.</p></body></html>')
        if ncx:
            z.writestr('OPS/toc.ncx', '<ncx><navMap>' + ''.join(f'<navPoint><navLabel><text>{title}</text></navLabel><content src="text.xhtml#{anchor}"/></navPoint>' for title, anchor in [('First', 'one'), ('Second', 'two')]) + '</navMap></ncx>')
        else:
            z.writestr('OPS/nav.xhtml', '<html xmlns:epub="http://www.idpf.org/2007/ops"><nav epub:type="toc"><a href="text.xhtml#one">First</a><a href="text.xhtml#two">Second</a></nav></html>')
    return path


class EpubTests(unittest.TestCase):
    def test_navigation_fragments_and_ncx(self):
        with tempfile.TemporaryDirectory() as directory:
            for ncx in (False, True):
                book = read_epub(make_epub(Path(directory) / 'book.epub', ncx))
                self.assertEqual(book['title'], 'Fixture Book')
                self.assertEqual([c['title'] for c in book['chapters']], ['First', 'Second'])
                self.assertNotIn('Second', book['chapters'][0]['text'])
                self.assertNotIn('Hidden', book['chapters'][0]['text'])
                self.assertIn('chapter two', book['chapters'][1]['text'])
                self.assertEqual(book['warnings'], [])

    def test_fallback_and_rejected_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_epub(Path(directory) / 'book.epub', no_toc=True)
            self.assertIn('No usable table', read_epub(path)['warnings'][0])
            with ZipFile(path, 'w') as z:
                z.writestr('META-INF/container.xml', '<!DOCTYPE x [<!ENTITY bad "unsafe">]><container/>')
            with self.assertRaisesRegex(ValueError, 'entity'):
                read_epub(path)
            path.write_bytes(b'not a zip')
            with self.assertRaisesRegex(ValueError, 'EPUB structure'):
                read_epub(path)
        for href in ('../../../outside', 'https://example.com/book', '//example.com/book'):
            with self.assertRaises(ValueError):
                resolve('OPS/book.opf', href)


def transcript_passage(text, start):
    words = text.split()
    return {'start': start, 'end': start + len(words), 'text': text,
            'words': [{'word': word, 'start': start + i, 'end': start + i + 1} for i, word in enumerate(words)]}


class MatchingTests(unittest.TestCase):
    passage = 'Beyond the quiet valley a silver river crossed the empty plain while distant thunder rolled across the mountains'
    other = 'Inside the ancient library she discovered a small wooden box filled with letters written by her missing brother'

    def book(self, texts):
        return {'chapters': [{'id': str(i), 'title': f'Chapter {i}', 'text': text} for i, text in enumerate(texts)]}

    def test_openings_omissions_and_unmatched_sections(self):
        book = self.book([self.passage, 'omitted ' * 80 + self.other, 'Nothing similar appears in this recorded adaptation'])
        transcript = {'segments': [transcript_passage(self.passage, 10), transcript_passage(self.other, 100)]}
        proposals = match_book(book, transcript)['proposals']
        self.assertEqual(proposals[0]['start'], 10)
        self.assertEqual(proposals[0]['confidence'], 'strong')
        self.assertEqual(proposals[1]['start'], 100)
        self.assertEqual(proposals[1]['confidence'], 'review')
        self.assertEqual(proposals[1]['book_offset'], 80)
        self.assertIn('beginning may be earlier', proposals[1]['reason'])
        self.assertIsNone(proposals[2]['start'])

    def test_repetition_and_segment_timing_are_not_strong(self):
        book = self.book([self.passage])
        repeated = {'segments': [transcript_passage(self.passage, 10), transcript_passage(self.passage, 200)]}
        proposal = match_book(book, repeated)['proposals'][0]
        self.assertEqual(proposal['confidence'], 'review')
        self.assertIn('elsewhere', proposal['reason'])
        segment = transcript_passage(self.passage, 10)
        del segment['words']
        proposal = match_book(book, {'segments': [segment]})['proposals'][0]
        self.assertEqual(proposal['confidence'], 'review')
        self.assertEqual(proposal['start'], 10)
        self.assertIn('passage-level', proposal['reason'])

    def test_partial_opening_is_preferred_over_clean_later_passage(self):
        opening = self.passage + ' above the old stone bridge'
        later = ' '.join([self.other] * 4)
        book = self.book([opening + ' omitted' * 104 + ' ' + later])
        transcript = {'segments': [transcript_passage(opening, 10), transcript_passage(later, 100)]}
        proposal = match_book(book, transcript)['proposals'][0]
        self.assertEqual(proposal['start'], 10)
        self.assertEqual(proposal['book_offset'], 0)
        self.assertEqual(proposal['confidence'], 'review')

    def test_matches_respect_order_without_inventing_missing_starts(self):
        book = self.book([self.passage, self.other])
        transcript = {'segments': [transcript_passage(self.other, 10), transcript_passage(self.passage, 100)]}
        proposals = match_book(book, transcript)['proposals']
        self.assertEqual(sum(p['start'] is not None for p in proposals), 1)
        self.assertTrue(any('conflict' in p['reason'] for p in proposals))

    def test_audio_only_intro_and_cast_credits_require_evidence(self):
        book = self.book([self.passage])
        transcript = {'segments': [
            transcript_passage('A Graphic Audio production presents a new story', 1),
            transcript_passage('Narrated by Example Reader with a full cast', 15),
            transcript_passage(self.passage, 100),
            transcript_passage('The story ends here with a final farewell', 880),
            transcript_passage('This is a Graphic Audio production', 920),
            transcript_passage('With performances by Example Actor and Another Actor', 930),
            transcript_passage('Thanks for listening', 990)]}
        proposals = match_book(book, transcript)['proposals']
        extras = [p for p in proposals if p['source'] == 'audio']
        self.assertEqual([(p['title'], p['start']) for p in extras], [('Intro', 0), ('Outro / credits', 920)])
        self.assertTrue(all(p['confidence'] == 'review' for p in extras))
        self.assertIn('earlier', extras[1]['reason'])
        # Audio without production cues does not get invented intro/outro markers.
        self.assertEqual(audio_extras(book, {'segments': [transcript_passage(self.passage, 100),
                             transcript_passage('An ordinary final conversation ends the story', 990)]}, proposals[:1]), [])
        # A story quoting production announcements must not be called audio-only.
        quoted_book = self.book([' '.join(s['text'] for s in transcript['segments'])])
        self.assertEqual(audio_extras(quoted_book, transcript, proposals[:1]), [])

    def test_unmatched_prologue_is_not_automatically_omitted(self):
        book = self.book(['An entirely different opening sequence without any spoken correspondence', self.passage])
        book['chapters'][0]['title'] = 'Prologue'
        proposals = match_book(book, {'segments': [transcript_passage(self.passage, 100)]})['proposals']
        self.assertEqual(proposals[0]['confidence'], 'unmatched')
        self.assertIn('may be omitted', proposals[0]['reason'])
        self.assertIsNone(proposals[0]['start'])


class ProjectTests(unittest.TestCase):
    def test_upload_persistence_invalid_import_and_source_change(self):
        with tempfile.TemporaryDirectory() as directory, patch('app.probe', return_value={'format': {'duration': '100'}}):
            root = Path(directory)
            workspace = root / 'projects'
            session = Session(workspace=workspace)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(session, 'test'))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(route, data, filename=None):
                headers = {'Content-Type': 'application/octet-stream', 'X-File-Name': filename} if filename else {'Content-Type': 'application/json'}
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                connection.request('POST', '/test/api/' + route, data if filename else json.dumps(data), headers)
                response = connection.getresponse()
                status, body = response.status, json.loads(response.read())
                connection.close()
                return status, body
            try:
                self.assertIsNone(session.state()['filename'])
                self.assertEqual(request('upload/audio', b'fixture audio', 'book.m4b')[0], 200)
                self.assertEqual(session.job['status'], 'idle')
                self.assertEqual(request('upload/epub', make_epub(root / 'book.epub').read_bytes(), 'book.epub')[0], 200)
                rows = session.rows + [{'start': 30, 'title': 'Edited', 'selected': True, 'kind': 'epub', 'epubId': 'example'}]
                speech = {'segments': [transcript_passage('a simple spoken phrase', 30)]}
                self.assertEqual(request('review', {'rows': rows, 'transcript': speech, 'omitted_sections': ['section-id']})[0], 200)
                restored = Session(workspace=workspace)
                self.assertEqual(restored.rows, rows)
                self.assertEqual(restored.transcript, speech)
                self.assertEqual(restored.book['title'], 'Fixture Book')
                self.assertEqual(restored.omitted_sections, ['section-id'])
                self.assertEqual(request('review', {'rows': rows, 'omitted_sections': [None]})[0], 400)
                self.assertEqual(session.omitted_sections, ['section-id'])
                self.assertEqual(len(restored.projects()), 1)
                self.assertEqual(request('review', {'rows': rows, 'transcript': {'segments': [{'text': 'bad', 'start': float('nan'), 'end': 4}]}})[0], 400)
                self.assertEqual(session.transcript, speech)
                self.assertEqual(request('export', {'chapters': rows})[0], 400)
                self.assertEqual(request('upload/audio', b'bad', '../escape.m4b')[0], 400)
                session.source.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'source changed'):
                    restored.open_project(session.project.name)
            finally:
                server.shutdown()
                server.server_close()

    def test_invalid_word_timing(self):
        speech = {'segments': [transcript_passage('spoken words', 10)]}
        speech['segments'][0]['words'][0]['start'] = 50
        with self.assertRaises(ValueError):
            validate_transcript(speech, 100)


if __name__ == '__main__':
    unittest.main()
