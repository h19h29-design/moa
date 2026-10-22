"""Actual parser + staff HTTP + original ZIP. Outputs stay in pytest's data root.

HWPX/PDF are generated synthetic documents. Legacy binary HWP is an optional
already-acquired private fixture; never embed its bytes or text in this repo.
"""
import io
import os
from pathlib import Path
import re
import zipfile

import pytest

from moa import mobile, school_service as svc
from moa.core import atomic_write, digest, write_json
from test_mobile import multipart
from test_web_school import school_web, client, fetch, login


def sample(kind):
    if kind == 'hwp':
        path = os.environ.get('MOA_HWP_FIXTURE')
        if not path: pytest.skip('Private already-acquired binary HWP is not configured')
        return Path(path).read_bytes()
    out = io.BytesIO()
    if kind == 'hwpx':
        with zipfile.ZipFile(out, 'w') as z:
            z.writestr('mimetype', 'application/hwp+zip')
            z.writestr('Contents/section0.xml', '<hs:sec xmlns:hs="hs" xmlns:hp="hp"><hp:p><hp:run><hp:t>합성 행사 안내 2026-10-30 12,000원</hp:t></hp:run></hp:p><hp:p><hp:run><hp:t>신청 마감 16:30, 미신청 시 임의 배정.</hp:t></hp:run></hp:p></hs:sec>')
    elif kind == 'pdf':
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(out, invariant=1)
        c.drawString(50, 740, 'Synthetic school notice 2026-10-30 12,000 KRW')
        c.drawString(50, 710, 'Deadline 16:30. No application: automatic allocation.')
        c.save()
    return out.getvalue()


@pytest.mark.parametrize('kind', ['hwp', 'hwpx', 'pdf'])
def test_actual_upload_roles_html_and_original_zip(school_web, kind):
    _, store, base = school_web
    raw = sample(kind); original_sha = digest(raw)
    opener = client(); csrf = login(opener, base)
    body, ctype = multipart({'csrf':csrf, 'post_key':'format-'+kind, 'title':'파일 형식 변환 호환 시험'},
        [('guide', '안내.'+kind, raw), ('attachments', '신청서.txt', b'synthetic untouched form')])
    status, page, _ = fetch(opener, base, '/upload', data=body, headers={'Content-Type':ctype})
    assert status == 200
    ident = re.search(r'data-version="([a-f0-9]+)"', page.decode())[1]
    before = svc.get_version(store, svc.Principal('A','a'), ident)
    assert [f['role'] for f in before['files']] == ['convert', 'attachment']
    assert fetch(opener, base, '/convert', fields={'csrf':csrf,'id':ident,'revision':'','role:0':'convert','role:1':'attachment'})[0] == 200
    assert svc.process_conversions(store) == ['done']
    version = svc.get_version(store, svc.Principal('A','a'), ident)
    revision = mobile.revision(store, version['mobile_id'], school=True)
    result = revision['data']
    assert result['sources'][0]['status'] == 'extracted'
    assert result['sources'][0]['text']
    html_status, rendered, _ = fetch(opener, base, '/html?id='+ident)
    zip_status, zipped, _ = fetch(opener, base, '/bundle?id='+ident)
    assert html_status == zip_status == 200
    with zipfile.ZipFile(io.BytesIO(zipped)) as z:
        originals = [z.read(name) for name in z.namelist() if name.startswith('attachments/')]
        assert original_sha in [digest(data) for data in originals]
        assert b'synthetic untouched form' in originals and 'index.html' in z.namelist()
    if kind == 'hwpx': assert '12,000원' in rendered.decode() and '16:30' in rendered.decode()
    if kind == 'pdf': assert b'12,000' in rendered and b'16:30' in rendered
    if kind == 'hwp': assert digest(Path(os.environ['MOA_HWP_FIXTURE']).read_bytes()) == original_sha
    assert not store.db.execute('SELECT 1 FROM notices').fetchone()
    assert not store.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    evidence = Path(os.environ.get('MOA_FORMAT_EVIDENCE_DIR', store.root/'format-evidence'))/kind
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(evidence/('original.'+kind), raw)
    atomic_write(evidence/'candidate.html', rendered)
    atomic_write(evidence/'originals.zip', zipped)
    write_json(evidence/'RESULT.json', {'format':kind, 'fixture':'existing_private' if kind == 'hwp' else 'synthetic',
        'source_sha256':original_sha, 'parser_status':result['sources'][0]['status'],
        'extracted_characters':len(result['sources'][0]['text']), 'blocks':len(result['blocks']),
        'warnings':[w['code'] for w in result['warnings']], 'http_upload':status,'http_html':html_status,
        'http_zip':zip_status, 'originals_sha256_verified':True, 'private':True,
        'operational_db_used':False, 'human_approval_written':False, 'store':str(store.root)})
