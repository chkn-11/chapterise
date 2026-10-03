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
from epub_match import match_book, read_epub, resolve, audio_extras, PageText


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
    def test_image_only_contents_entry_is_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_epub(Path(directory) / 'images.epub')
            with ZipFile(path) as z:
                files = {name: z.read(name) for name in z.namelist()}
            files['OPS/text.xhtml'] = b'<html><body><div id="one"><img src="drawing.jpg"/></div><h1 id="two">Second</h1><p>Readable chapter.</p></body></html>'
            with ZipFile(path, 'w') as z:
                for name, data in files.items():
                    z.writestr(name, data)
            book = read_epub(path)
            self.assertEqual([c['title'] for c in book['chapters']], ['First', 'Second'])
            self.assertEqual(book['chapters'][0]['text'], '')
            self.assertEqual(book['chapters'][0]['word_count'], 0)

    def test_leading_epigraph_belongs_to_its_chapter(self):
        quote = 'Before the winter voyage every sailor must learn the names of all the northern stars'
        with tempfile.TemporaryDirectory() as directory:
            for ncx in (False, True):
                path = Path(directory) / 'epigraph.epub'
                with ZipFile(path, 'w') as z:
                    z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
                    nav = '<item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>' if ncx else '<item id="toc" href="nav.xhtml" properties="nav"/>'
                    z.writestr('book.opf', '<package><manifest>' + nav + '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine toc="toc"><itemref idref="a"/><itemref idref="b"/></spine></package>')
                    z.writestr('a.xhtml', '<html><body><h1 id="a">First</h1><p>The previous story ends here.</p></body></html>')
                    z.writestr('b.xhtml', f'<html><head><title>Hidden</title></head><body><blockquote>{quote}</blockquote><p>— A sailor’s handbook</p><h1 id="b">Second</h1><p>A new story begins here.</p></body></html>')
                    z.writestr('nav.xhtml', '<html><nav role="doc-toc"><a href="a.xhtml#a">First</a><a href="b.xhtml#b">Second</a></nav></html>')
                    z.writestr('toc.ncx', '<ncx><navMap><navPoint><navLabel><text>First</text></navLabel><content src="a.xhtml#a"/></navPoint><navPoint><navLabel><text>Second</text></navLabel><content src="b.xhtml#b"/></navPoint></navMap></ncx>')
                book = read_epub(path)
                self.assertNotIn(quote, book['chapters'][0]['text'])
                self.assertTrue(book['chapters'][1]['text'].startswith(quote))
                self.assertIn('1 sections', book['warnings'][0])
                proposal = match_book(book, {'segments': [transcript_passage(quote, 50)]})['proposals'][1]
                self.assertEqual(proposal['start'], 50)

    def test_epigraph_rule_rejects_ordinary_prose_and_prior_sections(self):
        for prefix in ['<p>Ordinary preceding prose.</p>',
                       '<p>Earlier story.</p><blockquote>A quotation.</blockquote>',
                       '<h1>Earlier section</h1><blockquote>A quotation.</blockquote>',
                       '<blockquote>' + 'word ' * 401 + '</blockquote>',
                       '<blockquote>A quotation.</blockquote><p>' + 'prose ' * 81 + '</p>']:
            page = PageText(prefix + '<h1 id="chapter">Chapter</h1>')
            self.assertFalse(page.has_leading_epigraph(page.anchors['chapter']))

    def test_shared_document_epigraph_keeps_explicit_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = make_epub(Path(directory) / 'shared.epub')
            with ZipFile(path) as z:
                files = {name: z.read(name) for name in z.namelist()}
            files['OPS/text.xhtml'] = files['OPS/text.xhtml'].replace(b'<body>', b'<body><blockquote>A quotation before both sections.</blockquote>')
            with ZipFile(path, 'w') as z:
                for name, data in files.items():
                    z.writestr(name, data)
            book = read_epub(path)
            self.assertEqual(book['warnings'], [])
            self.assertNotIn('quotation', book['chapters'][0]['text'])
            self.assertNotIn('Second', book['chapters'][0]['text'])

    def test_missing_anchor_recovers_only_unambiguous_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'broken.epub'
            with ZipFile(path, 'w') as z:
                z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
                z.writestr('book.opf', '<package><manifest><item id="nav" href="nav.xhtml" properties="nav"/><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
                z.writestr('nav.xhtml', '<html><nav role="doc-toc"><a href="a.xhtml#stale">Recovered</a><a href="b.xhtml#first">First</a><a href="b.xhtml#missing">Ambiguous</a></nav></html>')
                z.writestr('a.xhtml', '<html><body><h1><img alt="Recovered"/></h1><p>A chapter whose heading is an image.</p></body></html>')
                z.writestr('b.xhtml', '<html><body><h1 id="first">Next chapter</h1><p>Distinct second passage.</p></body></html>')
            book = read_epub(path)
            self.assertEqual([c['title'] for c in book['chapters']], ['Recovered', 'First'])
            self.assertNotIn('Distinct second', book['chapters'][0]['text'])
            self.assertTrue(any('Recovered contents entry' in w for w in book['warnings']))
            self.assertTrue(any('Could not locate contents anchor: Ambiguous' == w for w in book['warnings']))

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

    def test_source_boundary_preserves_heading_but_not_late_passage(self):
        transcript = {'segments': [transcript_passage(self.passage, 20)]}
        book = self.book([self.passage])
        original = match_book(book, transcript)['proposals'][0]
        self.assertEqual(original['start'], 20)
        adjusted = match_book(book, transcript, existing_chapters=[{'start_time': '15'}])['proposals'][0]
        self.assertEqual(adjusted['start'], 15)
        self.assertEqual(adjusted['text_match_start'], 20)
        self.assertEqual(adjusted['confidence'], 'review')
        late = self.book(['omitted ' * 100 + self.passage])
        self.assertEqual(match_book(late, transcript, existing_chapters=[{'start_time': '15'}])['proposals'][0]['start'], 20)
        far = {'segments': [transcript_passage(self.passage, 100)]}
        self.assertEqual(match_book(book, far, existing_chapters=[{'start_time': '15'}])['proposals'][0]['start'], 100)

    def test_expected_spoken_heading_precedes_prose(self):
        book = self.book([self.passage])
        book['chapters'][0]['title'] = '19'
        speech = {'segments': [transcript_passage('Nineteen.', 15), transcript_passage(self.passage, 20)]}
        p = match_book(book, speech)['proposals'][0]
        self.assertEqual(p['start'], 15)
        self.assertEqual(p['text_match_start'], 20)
        self.assertEqual(p['boundary_evidence'], 'spoken_heading')
        self.assertEqual(p['heading_start'], 15)
        # Hyphenated and numerical headings also work inside mixed segments.
        book['chapters'][0]['title'] = 'Chapter 21: A journey'
        for heading in ('Twenty-one.', '21.', 'Chapter twenty-one.'):
            speech = {'segments': [transcript_passage(heading + ' ' + self.passage, 20)]}
            p = match_book(book, speech)['proposals'][0]
            self.assertEqual(p['start'], 20)
            self.assertEqual(p['boundary_evidence'], 'spoken_heading')

    def test_written_number_headings_use_the_whole_number(self):
        book = self.book([self.passage])
        for title, heading in [('CHAPTER TWENTY-ONE', 'Twenty-one.'),
                               ('PART TWENTY-ONE', 'Part twenty-one.'),
                               ('CHAPTER ONE HUNDRED AND ONE', 'One hundred and one.')]:
            book['chapters'][0]['title'] = title
            announcement = transcript_passage(heading, 10)
            for wi, word in enumerate(announcement['words']):
                word.update(start=10 + wi * .3, end=10 + wi * .3 + .2)
            announcement['end'] = announcement['words'][-1]['end']
            speech = {'segments': [announcement, transcript_passage(self.passage, 20)]}
            self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 10)
        book['chapters'][0]['title'] = 'Chapter 20'
        speech = {'segments': [transcript_passage('Chapter twenty-one.', 10), transcript_passage(self.passage, 20)]}
        self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)

    def test_heading_rejects_wrong_numbers_mentions_and_ambiguous_announcements(self):
        book = self.book([self.passage])
        book['chapters'][0]['title'] = '19'
        for heading in ('Eighteen.', 'He counted nineteen.', 'There were nineteen people',
                        'Chapter nineteen contains a lesson.',
                        'The end of chapter nineteen.'):
            speech = {'segments': [transcript_passage(heading, 10), transcript_passage(self.passage, 20)]}
            self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)
        speech = {'segments': [transcript_passage('Nineteen.', 10), transcript_passage('Nineteen.', 15),
                               transcript_passage(self.passage, 20)]}
        self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)
        # Passage timing cannot establish a heading's word timestamp.
        speech = {'segments': [{'start': 15, 'end': 16, 'text': 'Nineteen.'},
                               transcript_passage(self.passage, 20)]}
        self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)
        # A chapter number after an opening epigraph must not exclude that epigraph.
        speech = {'segments': [transcript_passage(self.passage, 20), transcript_passage('Nineteen.', 45)]}
        self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)

    def test_heading_does_not_cross_previous_chapter_or_trust_broken_word_timing(self):
        book = self.book([self.other, self.passage])
        book['chapters'][1]['title'] = 'Chapter 2'
        speech = {'segments': [transcript_passage('Two.', 10), transcript_passage(self.other, 20),
                               transcript_passage(self.passage, 50)]}
        self.assertEqual(match_book(book, speech)['proposals'][1]['start'], 50)
        book = self.book([self.passage])
        book['chapters'][0]['title'] = 'Chapter 21'
        bad = transcript_passage('Twenty one.', 15)
        bad['words'][1]['start'] = 12
        speech = {'segments': [bad, transcript_passage(self.passage, 20)]}
        self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)
        speech = {'segments': [transcript_passage('Twenty-one.', 1), transcript_passage(self.passage, 20)]}
        self.assertEqual(match_book(book, speech)['proposals'][0]['start'], 20)

    def test_part_heading_and_source_inside_prior_narration(self):
        book = self.book([self.passage])
        book['chapters'][0]['title'] = 'PART FOUR'
        speech = {'segments': [transcript_passage('The previous chapter ends here.', 1),
                               transcript_passage('Part four.', 15), transcript_passage(self.passage, 20)]}
        p = match_book(book, speech, existing_chapters=[{'start_time': '3'}])['proposals'][0]
        self.assertEqual(p['start'], 15)
        self.assertEqual(p['boundary_evidence'], 'spoken_heading')
        speech['segments'].pop(1)
        p = match_book(book, speech, existing_chapters=[{'start_time': '3'}])['proposals'][0]
        self.assertEqual(p['start'], 20)
        self.assertIn('crosses recognised narration', p['reason'])
        intro = {'segments': [transcript_passage('Publisher introduction and dedication.', 0),
                              transcript_passage(self.passage, 20)]}
        self.assertEqual(match_book(book, intro, existing_chapters=[{'start_time': '0'}])['proposals'][0]['start'], 20)

    def test_dense_source_map_is_corroborated_independently_of_track_labels(self):
        book = self.book([self.passage + f' specialword{i}' for i in range(8)])
        # Independent unique opening passages avoid common-text ambiguities.
        for i, chapter in enumerate(book['chapters']):
            chapter['text'] = ' '.join(f'unique{i}word{j}' for j in range(20))
        speech = {'segments': []}
        tracks = []
        for i, chapter in enumerate(book['chapters']):
            start = i * 100 + 20
            speech['segments'].append(transcript_passage('Trailing narration from before the boundary.', start - 7))
            speech['segments'].append(transcript_passage(chapter['text'], start))
            tracks.append({'start_time': str(start - 5), 'tags': {'title': f'Track {i + 91}'}})
        p = match_book(book, speech, existing_chapters=tracks)['proposals'][:8]
        self.assertEqual([v['start'] for v in p], [i * 100 + 15 for i in range(8)])
        self.assertTrue(all(v['boundary_evidence'] == 'source_consistent_map' for v in p))
        # The same overlap is not enough to trust a sparse unrelated track.
        p = match_book(book, speech, existing_chapters=tracks[:1])['proposals'][0]
        self.assertEqual(p['start'], 20)

    def test_illustration_title_needs_full_heading_not_story_mention(self):
        book = self.book([''])
        book['chapters'][0]['title'] = "Shallan’s Sketchbook: Cryptics"
        speech = {'segments': [transcript_passage('She took her sketchbook and began drawing Cryptics', 10),
                               transcript_passage("Shalon's Sketchbook. Cryptics. A description follows", 100)]}
        p = match_book(book, speech)['proposals'][0]
        self.assertEqual(p['start'], 100)
        self.assertEqual(p['confidence'], 'review')
        self.assertTrue(p['title_only'])
        self.assertIsNone(match_book(book, {'segments': speech['segments'][:1]})['proposals'][0]['start'])

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

    def test_internal_production_blocks_are_grouped_and_reviewable(self):
        book = self.book([self.passage, self.other])
        transcript = {'segments': [
            transcript_passage(self.passage, 10),
            transcript_passage('This is a Graphic Audio production', 200),
            transcript_passage('Narrated by Example Reader and performed by a full cast', 220),
            transcript_passage('Production copyright all rights reserved', 260),
            transcript_passage('Graphic Audio presents the next part', 430),
            transcript_passage('Performed by another full cast', 450),
            transcript_passage(self.other, 600),
            transcript_passage('Graphic Audio presents a second intermission', 800),
            transcript_passage('Narrated by Another Reader', 820),
            transcript_passage(self.passage, 1000)]}
        anchors = [{'start': 10}, {'start': 600}, {'start': 1000}]
        extras = audio_extras(book, transcript, anchors)
        internal = [p for p in extras if 'interlude' in p['id']]
        self.assertEqual([p['start'] for p in internal], [200, 800])
        self.assertEqual(len({p['id'] for p in internal}), 2)
        self.assertTrue(all(p['confidence'] == 'review' for p in internal))
        quoted = self.book([' '.join(s['text'] for s in transcript['segments'])])
        self.assertEqual(audio_extras(quoted, transcript, anchors), [])
        sparse = {'segments': [transcript_passage('Graphic Audio presents', 200),
                               transcript_passage('Narrated by Example Reader', 450)]}
        self.assertEqual(audio_extras(book, sparse, anchors), [])


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
