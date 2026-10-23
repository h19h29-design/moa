"""Actual parser + staff HTTP + original ZIP. Outputs stay in pytest's data root.

HWPX/PDF are generated synthetic documents. Legacy binary HWP is an optional
already-acquired private fixture; never embed its bytes or text in this repo.
"""
import io
import json
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
            z.writestr('Contents/section0.xml', '<hs:sec xmlns:hs="hs" xmlns:hp="hp"><hp:p><hp:run><hp:ctrl><hp:header><hp:p><hp:run><hp:t>합성학교 가정통신문</hp:t></hp:run></hp:p></hp:header></hp:ctrl><hp:t>합성 행사 안내 2026-10-30 12,000원</hp:t><hp:ctrl><hp:footNote><hp:p><hp:run><hp:t>취소 시 비용 면제</hp:t></hp:run></hp:p></hp:footNote></hp:ctrl></hp:run></hp:p><hp:p><hp:run><hp:t>신청 마감 16:30, 미신청 시 임의 배정.</hp:t></hp:run></hp:p></hs:sec>')
    elif kind == 'docx':
        with zipfile.ZipFile(out,'w') as z:
            z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>Synthetic trip 2026-10-30 12,000 KRW</w:t><w:footnoteReference w:id="1"/></w:r></w:p><w:p><w:r><w:t>Application deadline 16:30.</w:t></w:r></w:p><w:sectPr><w:headerReference r:id="header1"/></w:sectPr></w:body></w:document>')
            z.writestr('word/_rels/document.xml.rels','<Relationships><Relationship Id="header1" Target="header1.xml"/></Relationships>')
            z.writestr('word/header1.xml','<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Synthetic School</w:t></w:r></w:p></w:hdr>')
            z.writestr('word/footnotes.xml','<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>Cancellation fee waived.</w:t></w:r></w:p></w:footnote></w:footnotes>')
    elif kind == 'pdf':
        from reportlab.pdfgen import canvas
        c = canvas.Canvas(out, invariant=1)
        for line in ('Synthetic trip 2026-10-30 12,000 KRW','Deadline 16:30. No application: automatic allocation.'):
            c.drawString(50, 750, 'Synthetic School')
            c.drawString(50, 710, line)
            c.drawString(50, 100, '[1] Cancellation fee waived.')
            c.showPage()
        c.save()
    return out.getvalue()


@pytest.mark.parametrize('kind', ['hwp', 'hwpx', 'pdf', 'docx'])
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
    analysis = svc.structure_for(store,svc.Principal('A','a'),ident)
    assert analysis['version'] == 'moa-school-structure-v1' and analysis['human_approved'] is False
    assert len(analysis['files']) == 2 and analysis['files'][0]['sha256'] == original_sha
    assert analysis['files'][0]['status'] == 'extracted' and analysis['files'][0]['block_count'] > 0
    assert analysis['files'][1]['role'] == 'attachment' and analysis['files'][1]['status'] == 'needs_parser'
    assert analysis['complete'] is False  # The untouched text attachment is not silently declared analyzed.
    assert all(b['sha256'] == original_sha and b['locator']['format'] == kind for b in analysis['blocks'])
    if kind in ('hwpx','docx'):
        assert any(r['kind'] == 'header_for' and r['certainty'] == 'explicit' for r in analysis['relations'])
        assert any(r['kind'] == 'footnote_for' and r['certainty'] == 'explicit' for r in analysis['relations'])
    if kind == 'pdf':
        assert {b['locator']['page'] for b in analysis['blocks']} == {1,2}
        assert analysis['files'][0]['coverage']['native_zones'] is False
    assert fetch(opener,base,'/structure?id='+ident)[0] == 200
    assert fetch(opener,base,'/structure?id='+ident+'&format=json')[0] == 200
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
    # Staff corrections are a separate overlay, including on real HWP parsing.
    # They do not mutate original objects, generated HTML, automatic evidence or
    # research approval. Exercise the real HTTP flow, not only service methods.
    review_url = '/structure-review?id='+ident+'&format=json'
    review_status, review_raw, _ = fetch(opener,base,review_url)
    assert review_status == 200
    review = json.loads(review_raw)
    block = next(b for b in review['analysis']['blocks'] if b['kind']=='body')
    fields = {'csrf':csrf,'id':ident,'revision':version['revision'],
        'base_hash':review['base_hash'],'expected_review':'','action':'block',
        'block_id':block['id'],'block_kind':block['kind'],'text':block['text']+' [합성 검수 메모]',
        'comment':'형식별 격리 시험의 담당자 수정'}
    assert fetch(opener,base,'/structure-review',fields=fields)[0] == 200
    changed = json.loads(fetch(opener,base,review_url)[1])
    assert changed['status']=='draft' and changed['original']==analysis
    assert fetch(opener,base,'/structure-review',fields={'csrf':csrf,'id':ident,'revision':version['revision'],
        'base_hash':review['base_hash'],'expected_review':changed['latest']['id'],'action':'confirm',
        'acknowledge_warnings':'yes','comment':'미확정 첨부 범위를 포함하여 확인'})[0] == 200
    reviewed = json.loads(fetch(opener,base,review_url)[1])
    assert reviewed['status']=='confirmed' and reviewed['analysis']['complete'] is False
    assert svc.structure_for(store,svc.Principal('A','a'),ident)==analysis
    assert fetch(opener,base,'/html?id='+ident)[1]==rendered
    # ZIP container timestamps may differ across downloads; compare exact entry
    # names and bytes, not that volatile container metadata.
    with zipfile.ZipFile(io.BytesIO(fetch(opener,base,'/bundle?id='+ident)[1])) as after_zip, zipfile.ZipFile(io.BytesIO(zipped)) as before_zip:
        assert {n:after_zip.read(n) for n in after_zip.namelist()} == {n:before_zip.read(n) for n in before_zip.namelist()}
    history_status, history_raw, _ = fetch(opener,base,'/structure-history?id='+ident+'&format=json')
    history = json.loads(history_raw)
    assert history_status==200 and [e['action'] for e in history['events']]==['confirm','block']
    from bs4 import BeautifulSoup
    pinned = '?id='+ident+'&revision='+version['revision']+'&event='+reviewed['latest']['id']
    preview_status, preview, _ = fetch(opener,base,'/review-preview'+pinned)
    reviewed_status, reviewed_html, _ = fetch(opener,base,'/review-html'+pinned+'&download=1')
    reviewed_zip_status, reviewed_zip, _ = fetch(opener,base,'/review-bundle'+pinned)
    assert preview_status==reviewed_status==reviewed_zip_status==200
    node = BeautifulSoup(reviewed_html,'html.parser').find(attrs={'data-block-id':block['id']})
    assert node.find('p',class_='block-text').get_text()==fields['text']
    with zipfile.ZipFile(io.BytesIO(reviewed_zip)) as z:
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['review']['id']==reviewed['latest']['id']
        assert manifest['analysis_complete'] is False
        assert manifest['html_sha256']==digest(z.read('index.html'))
        assert original_sha in [digest(z.read(f['path'])) for f in manifest['originals']]
        assert all(digest(z.read(f['path']))==f['sha256'] for f in manifest['originals'])
    # Compare stored automatic/review/history snapshots for each actual parser
    # without rerunning it or changing the original/review/conversion evidence.
    comparison_url='/structure-compare?id='+ident+'&revision='+version['revision']
    pinned_pair=comparison_url+'&left=original&right='+reviewed['latest']['id']
    before_compare=list(store.db.iterdump())
    files_before={str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}
    comparison_status,comparison_html,_=fetch(opener,base,pinned_pair)
    json_status,comparison_raw,_=fetch(opener,base,pinned_pair+'&format=json')
    assert comparison_status==json_status==200
    comparison=json.loads(comparison_raw)
    assert comparison['summary']['changed_blocks']==1 and comparison['complete'] is False
    assert digest(comparison['blocks']['items'][0]['after']['text'].encode())==digest(fields['text'].encode())
    assert comparison['files']==analysis['files'] and comparison['warnings']['items']==analysis['warnings']
    status_only=json.loads(fetch(opener,base,comparison_url+'&left='+changed['latest']['id']+
        '&right='+reviewed['latest']['id']+'&format=json')[1])
    assert status_only['summary']['changed_blocks']==0 and status_only['summary']['status_changed'] is True
    assert list(store.db.iterdump())==before_compare
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    assert not store.db.execute('SELECT 1 FROM notices').fetchone()
    assert not store.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    # Restore a saved reviewed state after another edit; keep the original,
    # automatic conversion, earlier review rows and pinned exports byte-stable.
    old_history=[tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')]
    assert fetch(opener,base,'/structure-review',fields={**fields,
        'expected_review':reviewed['latest']['id'],'text':'합성 임시 수정: 복원 대상으로 되돌립니다.'})[0]==200
    interim=json.loads(fetch(opener,base,review_url)[1])
    restore_url='/structure-restore?id='+ident+'&revision='+version['revision']+'&source='+reviewed['latest']['id']
    restore_preview_status,restore_preview,_=fetch(opener,base,restore_url)
    assert restore_preview_status==200
    restore_status,_,_=fetch(opener,base,'/structure-review',fields={
        'csrf':csrf,'id':ident,'revision':version['revision'],'base_hash':reviewed['base_hash'],
        'expected_review':interim['latest']['id'],'action':'restore','source_event':reviewed['latest']['id'],
        'acknowledge_restore':'yes','comment':'형식별 원본·이력 보존 복원 시험'})
    assert restore_status==200
    restored=json.loads(fetch(opener,base,review_url)[1])
    assert restored['analysis']==reviewed['analysis'] and restored['status']=='draft'
    assert restored['latest']['action']=='restore' and restored['latest']['warnings_acknowledged'] is False
    assert [tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')][:2]==old_history
    restored_pin='?id='+ident+'&revision='+version['revision']+'&event='+restored['latest']['id']
    restore_html_status,restored_html,_=fetch(opener,base,'/review-html'+restored_pin)
    restore_zip_status,restored_zip,_=fetch(opener,base,'/review-bundle'+restored_pin)
    assert restore_html_status==restore_zip_status==200
    restored_node=BeautifulSoup(restored_html,'html.parser').find(attrs={'data-block-id':block['id']})
    assert restored_node.find('p',class_='block-text').get_text()==fields['text']
    assert fetch(opener,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert fetch(opener,base,'/review-bundle'+pinned)[1]==reviewed_zip
    with zipfile.ZipFile(io.BytesIO(restored_zip)) as z:
        restored_manifest=json.loads(z.read('manifest.json'))
        assert restored_manifest['review']['status']=='draft'
        assert all(digest(z.read(f['path']))==f['sha256'] for f in restored_manifest['originals'])
    assert svc.structure_for(store,svc.Principal('A','a'),ident)==analysis
    assert fetch(opener,base,'/html?id='+ident)[1]==rendered
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    evidence = Path(os.environ.get('MOA_FORMAT_EVIDENCE_DIR', store.root/'format-evidence'))/kind
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(evidence/('original.'+kind), raw)
    atomic_write(evidence/'candidate.html', rendered)
    atomic_write(evidence/'originals.zip', zipped)
    atomic_write(evidence/'reviewed.html', reviewed_html)
    atomic_write(evidence/'reviewed-originals.zip', reviewed_zip)
    atomic_write(evidence/'comparison.html',comparison_html)
    atomic_write(evidence/'restore-preview.html',restore_preview)
    atomic_write(evidence/'restored.html',restored_html)
    atomic_write(evidence/'restored-originals.zip',restored_zip)
    write_json(evidence/'restored-review.json',restored)
    write_json(evidence/'comparison.json',comparison)
    write_json(evidence/'structure.json',analysis)
    write_json(evidence/'review.json',reviewed)
    write_json(evidence/'review-history.json',history)
    write_json(evidence/'RESULT.json', {'format':kind, 'fixture':'existing_private' if kind == 'hwp' else 'synthetic',
        'source_sha256':original_sha, 'parser_status':result['sources'][0]['status'],
        'extracted_characters':len(result['sources'][0]['text']), 'blocks':len(result['blocks']),
        'warnings':[w['code'] for w in result['warnings']], 'structure_version':analysis['version'],
        'structure_blocks':len(analysis['blocks']),'structure_relations':len(analysis['relations']),
        'structure_complete':analysis['complete'],'structure_coverage':analysis['files'][0]['coverage'],
        'http_structure':200,'http_review':review_status,'http_history':history_status,
        'staff_review_status':reviewed['status'],'review_events':len(history['events']),
        'http_reviewed_preview':preview_status,'http_reviewed_html':reviewed_status,'http_reviewed_zip':reviewed_zip_status,
        'reviewed_edit_visible':True,'reviewed_zip_originals_sha256_verified':True,
        'http_comparison':comparison_status,'http_comparison_json':json_status,
        'comparison_changed_blocks':comparison['summary']['changed_blocks'],
        'comparison_status_only_distinct':True,'comparison_db_objects_cache_unchanged':True,
        'http_restore_preview':restore_preview_status,'http_restore':restore_status,
        'http_restored_html':restore_html_status,'http_restored_zip':restore_zip_status,
        'restored_content_matches_source':True,'restored_status':'draft',
        'prior_review_rows_and_exports_preserved':True,
        'automatic_analysis_unchanged':True,'conversion_html_and_zip_contents_unchanged':True,
        'http_upload':status,'http_html':html_status,
        'http_zip':zip_status, 'originals_sha256_verified':True, 'private':True,
        'operational_db_used':False, 'human_approval_written':False, 'store':str(store.root)})
