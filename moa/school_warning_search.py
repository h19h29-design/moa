"""Read-only filters over one school's pinned, immutable analysis warnings."""
import unicodedata
from collections import Counter

from . import school_service as svc, school_review as review, school_warning_web as guidance

PAGE_SIZE = 20


def _fold(value):
    return unicodedata.normalize('NFC', value).casefold()


def select(analysis, ident, *, q='', file='', code='', page=1):
    """Keep analyzer order and duplicates; filters never mark a warning resolved."""
    q = review._text(q, 120)
    if any(ord(c)<32 or ord(c)==127 for c in q): raise ValueError('Invalid warning search')
    q = q.strip()
    if type(page) is not int or not 1 <= page <= 1_000_000:
        raise ValueError('Invalid warning page')
    descriptions = guidance.describe(analysis, ident)
    per_file = Counter(d['file_id'] for d in descriptions)
    files = [{'value':'file:'+f['id'], 'label':'파일 '+str(i)+' · '+f['filename'],
              'count':per_file[f['id']]}
             for i, f in enumerate(analysis['files'], 1)]
    unlocated = per_file['']
    if unlocated:
        files.append({'value':'unlocated', 'label':'문서 묶음 · 파일 위치 미확인', 'count':unlocated})
    counts = {}
    for d in descriptions:
        value = 'code:'+d['code']
        if value not in counts:
            label = d['title']
            if d['code'] not in guidance.GUIDANCE: label += ' · '+(d['code'] or '코드 미기록')
            counts[value] = {'value':value, 'label':label, 'count':0}
        counts[value]['count'] += 1
    codes = list(counts.values())
    if not isinstance(file, str) or (file and file not in {f['value'] for f in files}):
        raise ValueError('Invalid warning file filter')
    if not isinstance(code, str) or (code and code not in counts):
        raise ValueError('Invalid warning type filter')
    needle = _fold(q)
    matches = []
    for i, (warning, d) in enumerate(zip(analysis['warnings'], descriptions), 1):
        source = 'file:'+d['file_id'] if d['file_id'] else 'unlocated'
        if file and file != source: continue
        if code and code != 'code:'+d['code']: continue
        text = ' '.join(d[k] for k in ('title','explanation','action','location','reference','code'))
        if needle and needle not in _fold(text): continue
        matches.append({'number':i, 'warning':warning})
    total = len(matches)
    pages = max(1, (total+PAGE_SIZE-1)//PAGE_SIZE)
    if page>pages: raise ValueError('Warning page out of range')
    return {'analysis':analysis, 'id':ident, 'revision':analysis['conversion_revision'],
        'filters':{'q':q, 'file':file, 'code':code}, 'files':files, 'codes':codes,
        'all_total':len(descriptions), 'total':total, 'page':page, 'pages':pages,
        'rows':matches[(page-1)*PAGE_SIZE:page*PAGE_SIZE]}


def listing(store, principal, ident, revision, **filters):
    # Require both identifiers: a saved URL must never silently select the latest revision.
    review._text(ident, 160, required=True)
    review._text(revision, 160, required=True)
    analysis = svc.structure_for(store, principal, ident, revision_id=revision)
    return select(analysis, ident, **filters)
