"""Read EPUB chapter structure and find evidence-backed, ordered audio matches."""
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from html.parser import HTMLParser
import hashlib
import posixpath
import re
import unicodedata
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
from zipfile import ZipFile, BadZipFile


def tokens(text):
    return re.findall(r"[^\W_]+", unicodedata.normalize('NFKC', text).casefold(), re.UNICODE)


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def resolve(base, href):
    url = urlsplit(href)
    if url.scheme or url.netloc:
        raise ValueError('External EPUB content is not supported.')
    name = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(url.path))) if url.path else base
    if name.startswith(('../', '/')) or name == '..':
        raise ValueError('Invalid EPUB content path.')
    return name, unquote(url.fragment)


class PageText(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts, self.anchors, self.headings = [], {}, []
        self.hidden = 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'script', 'style', 'head'}:
            self.hidden += 1
        if self.hidden:
            return
        if tag in {'p', 'div', 'br', 'li', 'section', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n')
        for key in ('id', 'name'):
            if attrs.get(key):
                self.anchors.setdefault(attrs[key], len(self.parts))
        if tag in {'h1', 'h2', 'h3'}:
            self.headings.append(len(self.parts))

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'head'} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in {'p', 'div', 'li', 'section', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n')

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def read_epub(path):
    """Use EPUB3 navigation or EPUB2 NCX, including multiple fragments per file."""
    try:
        with ZipFile(path) as archive:
            if len(archive.infolist()) > 10000 or sum(i.file_size for i in archive.infolist()) > 250_000_000:
                raise ValueError('EPUB is too large to parse (250 MB uncompressed limit).')
            def read(name):
                item = archive.getinfo(name)
                if item.file_size > 15_000_000:
                    raise ValueError('An EPUB document exceeds the 15 MB limit.')
                data = archive.read(name)
                if b'<!ENTITY' in data.upper():
                    raise ValueError('EPUB XML entity declarations are not supported.')
                return data
            container = ET.fromstring(read('META-INF/container.xml'))
            rootfile = next(e for e in container.iter() if local_name(e.tag) == 'rootfile')
            package_name = rootfile.attrib['full-path']
            package = ET.fromstring(read(package_name))
            manifest = {e.attrib['id']: e for e in package.iter() if local_name(e.tag) == 'item'}
            spine_element = next(e for e in package.iter() if local_name(e.tag) == 'spine')
            spine = []
            for ref in spine_element:
                item = manifest.get(ref.get('idref'))
                if item is not None and item.get('media-type') in {'application/xhtml+xml', 'text/html'}:
                    spine.append(resolve(package_name, item.attrib['href'])[0])
            if not spine:
                raise ValueError('EPUB contains no readable spine documents.')
            pages = {name: PageText(read(name).decode('utf-8-sig', errors='replace')) for name in spine}
            entries, warnings = [], []
            nav = next((e for e in manifest.values() if 'nav' in e.get('properties', '').split()), None)
            if nav is not None:
                name, _ = resolve(package_name, nav.attrib['href'])
                document = ET.fromstring(read(name))
                toc = next((e for e in document.iter() if local_name(e.tag) == 'nav' and
                            ('toc' in e.get('{http://www.idpf.org/2007/ops}type', '').split() or e.get('role') == 'doc-toc')), None)
                if toc is not None:
                    for link in toc.iter():
                        if local_name(link.tag) == 'a' and link.get('href'):
                            entries.append((' '.join(''.join(link.itertext()).split()), *resolve(name, link.attrib['href'])))
            if not entries:
                ncx = manifest.get(spine_element.get('toc'))
                if ncx is None:
                    ncx = next((e for e in manifest.values() if e.get('media-type') == 'application/x-dtbncx+xml'), None)
                if ncx is not None:
                    name, _ = resolve(package_name, ncx.attrib['href'])
                    for point in ET.fromstring(read(name)).iter():
                        if local_name(point.tag) != 'navPoint':
                            continue
                        content = next((e for e in point if local_name(e.tag) == 'content'), None)
                        label = next((e for e in point if local_name(e.tag) == 'navLabel'), None)
                        if content is not None:
                            title = ' '.join(''.join(label.itertext()).split()) if label is not None else 'Untitled'
                            entries.append((title, *resolve(name, content.attrib['src'])))
            offsets, all_parts = {}, []
            for name in spine:
                offsets[name] = len(all_parts)
                all_parts.extend(pages[name].parts)
                all_parts.append('\n')
            boundaries = {}
            for title, name, fragment in entries:
                if name not in pages:
                    warnings.append(f'Skipped contents entry outside the reading order: {title}')
                    continue
                if fragment and fragment not in pages[name].anchors:
                    warnings.append(f'Could not locate contents anchor: {title}')
                    continue
                position = offsets[name] + (pages[name].anchors[fragment] if fragment else 0)
                # A parent part and its first child may point to the same location.
                boundaries[position] = title or 'Untitled section'
            if not boundaries:
                warnings.append('No usable table of contents; using one section per spine document. Check the chapter list.')
                for index, name in enumerate(spine):
                    text = ''.join(pages[name].parts).strip()
                    boundaries[offsets[name]] = (text.splitlines()[0][:120] if text else f'Section {index + 1}')
            ordered = sorted(boundaries.items())
            chapters = []
            for index, (start, title) in enumerate(ordered):
                end = ordered[index + 1][0] if index + 1 < len(ordered) else len(all_parts)
                text = ' '.join(''.join(all_parts[start:end]).split())
                if not text:
                    continue
                identifier = hashlib.sha256(f'{index}:{title}:{text}'.encode()).hexdigest()[:24]
                chapters.append({'id': identifier, 'title': title[:500], 'text': text,
                                 'word_count': len(tokens(text))})
            if len(chapters) > 2000:
                raise ValueError('EPUB has too many contents entries (maximum 2,000).')
            title = next((e.text for e in package.iter() if local_name(e.tag) == 'title' and e.text), path.stem)
            return {'title': title, 'chapters': chapters, 'warnings': warnings}
    except (BadZipFile, KeyError, StopIteration, ET.ParseError) as error:
        raise ValueError(f'Could not read EPUB structure: {error}') from None


def audio_words(transcript):
    words, times, precise = [], [], []
    for segment in transcript.get('segments', []):
        if segment.get('words'):
            for word in segment['words']:
                normalized = tokens(word['word'])
                words.extend(normalized)
                times.extend([word['start']] * len(normalized))
                precise.extend([True] * len(normalized))
        else:
            normalized = tokens(segment['text'])
            words.extend(normalized)
            # Old transcripts have passage timing only. Never invent word timing.
            times.extend([segment['start']] * len(normalized))
            precise.extend([False] * len(normalized))
    return words, times, precise


def audio_extras(book, transcript, proposals):
    """Conservative production-announcement candidates, never inferred from silence alone."""
    matched = [p['start'] for p in proposals if p['start'] is not None]
    segments = transcript.get('segments', [])
    if not matched or not segments:
        return []
    end = max(s['end'] for s in segments)
    book_phrases = set()
    for chapter in book['chapters']:
        words = tokens(chapter['text'])
        book_phrases.update(tuple(words[i:i + 4]) for i in range(len(words) - 3))

    def cues(segment):
        words = tokens(segment['text'])
        phrases = [tuple(words[i:i + 4]) for i in range(len(words) - 3)]
        if phrases and sum(p in book_phrases for p in phrases) / len(phrases) > .5:
            return set()  # Book dialogue or printed credits are not audio-only evidence.
        text = ' '.join(words)
        patterns = {
            'production': r'graphic ?audio|produced by|directed (?:and adapted )?by|sound design|production copyright|audiobook production',
            'cast': r'narrated by|read by|performed by|performances by|full cast|cast includes',
            'closing': r'(?:you have been|thanks for|thank you for) listening|this concludes',
            'rights': r'copyright|all rights reserved',
        }
        return {kind for kind, pattern in patterns.items() if re.search(pattern, text)}

    def proposal(kind, start, evidence):
        return {'id': f'audio-only-{kind}', 'title': 'Intro' if kind == 'intro' else 'Outro / credits',
                'source': 'audio', 'start': start, 'confidence': 'review', 'book_excerpt': '',
                'audio_excerpt': ' '.join(s['text'] for s in evidence)[:700],
                'reason': ('Production announcements before the first book match suggest an audio-only intro. Confirm where the story begins.'
                           if kind == 'intro' else
                           'Production and cast announcements suggest audio-only closing credits. This timestamp marks spoken evidence; music or the actual credits boundary may begin earlier.')}

    extras = []
    opening = [s for s in segments if s['start'] < min(180, min(matched)) and s['end'] <= min(matched)]
    kinds = set().union(*(cues(s) for s in opening))
    if min(matched) >= 10 and 'cast' in kinds and 'production' in kinds:
        extras.append(proposal('intro', 0, opening))
    # Only look after the last matched chapter and within the recording's tail.
    closing = [(s, cues(s)) for s in segments if s['start'] > max(matched)
               and s['start'] >= max(end * .85, end - 1200)]
    for segment, markers in closing:
        if not markers.intersection({'production', 'cast', 'closing'}):
            continue
        cluster = [(s, c) for s, c in closing if segment['start'] <= s['start'] <= segment['start'] + 90]
        kinds = set().union(*(c for _, c in cluster))
        if 'cast' in kinds and ('production' in kinds or {'closing', 'rights'} <= kinds):
            extras.append(proposal('outro', segment['start'], [s for s, c in cluster if c]))
            break
    return extras


def match_book(book, transcript, progress=None):
    """Search unique phrase anchors, tolerate omissions, then enforce book order.

    Confidence labels describe evidence, not calibrated probability. Later passage
    matches are useful navigation targets, never asserted to be chapter starts.
    """
    words, times, precise = audio_words(transcript)
    index = defaultdict(list)
    for i in range(len(words) - 3):
        index[tuple(words[i:i + 4])].append(i)
    groups = []
    for chapter_index, chapter in enumerate(book['chapters']):
        if progress:
            progress(round(chapter_index / max(1, len(book['chapters'])) * 100), f"Matching {chapter['title']}…")
        book_words = tokens(chapter['text'])
        title_words = tokens(chapter['title'])
        if title_words and book_words[:len(title_words)] == title_words:
            book_words = book_words[len(title_words):]
        candidates = []
        # Start near the opening, then widen the search when adaptations omit it.
        for offset in range(0, min(len(book_words), 2400), 32):
            passage = book_words[offset:offset + 64]
            if len(passage) < 8:
                continue
            votes = Counter()
            for j in range(len(passage) - 3):
                hits = index.get(tuple(passage[j:j + 4]), [])
                if len(hits) > 30:
                    continue  # Common phrases are weak evidence.
                for hit in hits:
                    votes[(hit - j) // 12] += 1 / max(1, len(hits))
            for bucket, _ in votes.most_common(3):
                left = max(0, bucket * 12 - 8)
                right = min(len(words), bucket * 12 + len(passage) + 12)
                blocks = [b for b in SequenceMatcher(None, passage, words[left:right], autojunk=False).get_matching_blocks() if b.size >= 4]
                matched = sum(b.size for b in blocks)
                if matched < 8 or matched / len(passage) < .3:
                    continue
                first = blocks[0]
                audio_index = left + first.b
                book_offset = offset + first.a
                coverage = min(1, matched / len(passage))
                score = coverage * .7 + min(matched / 24, 1) * .3
                candidate = {'start': times[audio_index], 'book_offset': book_offset,
                             'book_excerpt': ' '.join(passage[first.a:first.a + 45]),
                             'audio_excerpt': ' '.join(words[audio_index:audio_index + 45]),
                             'matched_words': matched, 'score': round(score, 3),
                             'word_timing': precise[audio_index]}
                # Prefer early evidence; late matches remain useful but cannot be strong boundaries.
                candidate['weight'] = score - min(.3, book_offset / 2000)
                if score >= .5 and matched >= 16:
                    candidate['weight'] += .6 if book_offset <= 12 else .3 if book_offset <= 64 else 0
                candidates.append(candidate)
        candidates.sort(key=lambda c: c['weight'], reverse=True)
        distinct = []
        for candidate in candidates:
            if all(abs(candidate['start'] - other['start']) > 20 for other in distinct):
                distinct.append(candidate)
            if len(distinct) == 6:
                break
        groups.append(distinct)
    # Maximum-evidence increasing sequence, allowing unmatched or omitted chapters.
    nodes = []
    for chapter_index, candidates in enumerate(groups):
        additions = []
        for candidate in candidates:
            previous = [n for n in nodes if n['candidate']['start'] + 1 < candidate['start']]
            best = max(previous, key=lambda n: n['total'], default=None)
            additions.append({'chapter': chapter_index, 'candidate': candidate,
                              'previous': best, 'total': candidate['weight'] + (best['total'] if best else 0)})
        nodes.extend(additions)
    selected, node = {}, max(nodes, key=lambda n: n['total'], default=None)
    while node:
        selected[node['chapter']] = node['candidate']
        node = node['previous']
    proposals = []
    for i, chapter in enumerate(book['chapters']):
        candidate = selected.get(i)
        proposal = {'id': chapter['id'], 'title': chapter['title'], 'start': None,
                    'source': 'epub', 'confidence': 'unmatched',
                    'reason': 'No reliable phrase match found. This section may be omitted from the adaptation, or recognition may have missed it. Locate it manually or mark it not present after checking.',
                    'book_excerpt': ' '.join(chapter['text'].split()[:60]), 'audio_excerpt': ''}
        if candidate:
            rivals = [c for c in groups[i] if abs(c['start'] - candidate['start']) > 120 and c['weight'] >= candidate['weight'] - .08]
            strong = candidate['book_offset'] <= 12 and candidate['score'] >= .8 and candidate['matched_words'] >= 16 and candidate['word_timing'] and not rivals
            proposal.update({k: v for k, v in candidate.items() if k != 'weight'})
            proposal['confidence'] = 'strong' if strong else 'review'
            reasons = []
            if candidate['book_offset'] > 12:
                reasons.append(f"First match is {candidate['book_offset']} words into the chapter; the actual beginning may be earlier")
            if rivals:
                reasons.append('Similar evidence appears elsewhere in the recording')
            if not candidate['word_timing']:
                reasons.append('This transcript has passage-level timing; check the exact spoken start')
            if not strong and not reasons:
                reasons.append('Partial text match; listen to confirm the boundary')
            proposal['reason'] = '. '.join(reasons) + '.' if reasons else 'Strong opening-text match with word timing; listen before accepting.'
        elif groups[i]:
            proposal['reason'] = 'Matches conflict with the book chapter order. Locate this chapter manually.'
        proposals.append(proposal)
    proposals.extend(audio_extras(book, transcript, proposals))
    return {'proposals': proposals, 'engine': 'phrase-alignment-v2',
            'notice': 'Confidence describes matching evidence, not a probability. GraphicAudio omissions may move the first matched passage past the chapter start.'}
