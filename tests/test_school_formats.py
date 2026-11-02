"""Actual parser + staff HTTP + original ZIP. Outputs stay in pytest's data root.

HWPX/PDF are generated synthetic documents. Legacy binary HWP is an optional
already-acquired private fixture; never embed its bytes or text in this repo.
"""
import csv
import io
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
import zipfile

import pytest

from moa import mobile, school_service as svc, school_review as structure_review
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
    # Explicit unfinished input save/recovery uses the same real-format form,
    # but is not a review event and never changes conversion HTML or originals.
    from bs4 import BeautifulSoup
    draft_status,draft_html,_=fetch(opener,base,'/structure-draft/save',fields={**fields,'expected_draft':'','comment':''})
    assert draft_status==200 and not store.db.execute('SELECT 1 FROM school_structure_reviews').fetchone()
    draft_id=store.db.execute('SELECT id FROM school_review_drafts').fetchone()[0]
    draft_comparison=fetch(opener,base,'/structure-draft/compare?id='+draft_id)
    assert draft_comparison[0]==200
    comparison_page=BeautifulSoup(draft_comparison[1],'html.parser')
    assert comparison_page.select_one('[data-draft-recover]')
    assert comparison_page.select_one('[data-draft-compare-saved] pre').get_text()==fields['text']
    assert fetch(opener,base,'/logout',fields={'csrf':csrf})[0]==200
    opener=client(); csrf=login(opener,base); fields['csrf']=csrf
    before_input_recovery=list(store.db.iterdump())
    recovery_status,recovery_html,_=fetch(opener,base,'/structure-draft?id='+draft_id)
    recovered_page=BeautifulSoup(recovery_html,'html.parser')
    recovered_form=next(f for f in recovered_page.select('form[action="/structure-review"]')
        if f.select_one('input[name=block_id][value="'+block['id']+'"]'))
    assert recovery_status==200 and recovered_page.select_one('#draft-restored')
    assert recovered_form.select_one('textarea[name=text]').get_text()==fields['text']
    assert recovered_form.select_one('textarea[name=comment]').get_text()==''
    assert not recovered_page.select('input[type=checkbox][checked]')
    assert list(store.db.iterdump())==before_input_recovery
    assert json.loads(fetch(opener,base,review_url)[1])==review
    assert fetch(opener,base,'/html?id='+ident)[1]==rendered
    assert fetch(opener,base,'/structure-review',fields=fields)[0] == 200
    compared=BeautifulSoup(fetch(opener,base,'/structure-draft/compare?id='+draft_id)[1],'html.parser')
    assert compared.select_one('[data-draft-reason="review_changed"]')
    assert not compared.select_one('[data-draft-recover]')
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
    # The school overview reflects the restored draft and links that exact
    # saved HTML/history, without rerunning extraction or changing any audit.
    before_overview=list(store.db.iterdump())
    overview_status,overview_html,_=fetch(opener,base,'/?status=draft')
    overview_page=BeautifulSoup(overview_html,'html.parser')
    overview_row=overview_page.select_one('article[data-notice-version="'+ident+'"]')
    assert overview_status==200 and overview_row is not None
    assert overview_row['data-status']=='draft'
    overview_html_url=overview_row.select_one('a[data-action=html]')['href']
    assert restored['latest']['id'] in overview_html_url
    for action in ('review','html','history'):
        assert fetch(opener,base,overview_row.select_one('a[data-action='+action+']')['href'])[0]==200
    # Start the bounded sequential list through the actual overview link. The
    # receiving/conversion IDs and existing restored audit remain unchanged.
    queue_start=overview_page.select_one('a[data-action=queue-start]')['href']
    queue_status,queue_html,_=fetch(opener,base,queue_start)
    queue_page=BeautifulSoup(queue_html,'html.parser')
    assert queue_status==200 and '1 / 1' in queue_page.select_one('#review-queue').get_text()
    assert queue_page.select_one('input[name=id]')['value']==ident
    assert queue_page.select_one('input[name=revision]')['value']==version['revision']
    assert fetch(opener,base,queue_page.select_one('[data-queue=list]')['href'])[0]==200
    assert not BeautifulSoup(fetch(opener,base,'/?status=confirmed')[1],'html.parser').select('article[data-notice-version]')
    assert list(store.db.iterdump())==before_overview
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Persist the frozen list, then lose the login memory and resume from SQLite.
    save_form=queue_page.select_one('form[action="/review-queue/save"]')
    save_fields={x['name']:x.get('value','') for x in save_form.select('input[name]')}
    save_fields['name']='형식별 합성 재개 목록'
    saved_status,saved_html,_=fetch(opener,base,'/review-queue/save',fields=save_fields)
    assert saved_status==200
    resume_url=BeautifulSoup(saved_html,'html.parser').select_one('[data-queue=current]')['href']
    assert fetch(opener,base,'/logout',fields={'csrf':csrf})[0]==200
    resumed_client=client(); resumed_csrf=login(resumed_client,base)
    before_resume=list(store.db.iterdump())
    resume_status,resumed_html,_=fetch(resumed_client,base,resume_url)
    resumed_page=BeautifulSoup(resumed_html,'html.parser')
    assert resume_status==200 and '저장한 목록' in resumed_page.get_text()
    assert resumed_page.select_one('input[name=id]')['value']==ident
    assert resumed_page.select_one('input[name=revision]')['value']==version['revision']
    assert list(store.db.iterdump())==before_resume
    # The two list tables are the only extra DB writes; original/review history
    # and conversion rows remain exactly as before saving the list.
    without_lists=lambda rows:[line for line in rows if not line.startswith(
        ('INSERT INTO "school_review_lists"','INSERT INTO "school_review_list_items"'))]
    assert without_lists(list(store.db.iterdump()))==without_lists(before_overview)
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Immutable private-input history remains available across formal reviews.
    from test_school_review_drafts import draft_save
    newer_draft = draft_save(store,svc.Principal('A','a'),ident,expected=draft_id,
        action={'kind':'block','block_id':block['id'],'block_kind':block['kind'],'text':'다음 임시 입력'},
        comment='형식별 이전 입력 비교')
    before_draft_history=list(store.db.iterdump())
    history_status,history_html,_=fetch(resumed_client,base,'/structure-draft/history?id='+newer_draft)
    assert history_status==200 and len(BeautifulSoup(history_html,'html.parser').select('[data-draft-history-row]'))==2
    pair_status,pair_html,_=fetch(resumed_client,base,'/structure-draft/diff?before='+draft_id+'&after='+newer_draft)
    pair_page=BeautifulSoup(pair_html,'html.parser')
    assert pair_status==200 and pair_page.select_one('[data-draft-base-changed]')
    assert pair_page.select_one('[data-draft-before] [data-draft-field=text]').get_text()==fields['text']
    assert pair_page.select_one('[data-draft-after] [data-draft-field=text]').get_text()=='다음 임시 입력'
    assert not pair_page.select_one('[data-draft-recover]')
    assert list(store.db.iterdump())==before_draft_history
    assert fetch(resumed_client,base,'/html?id='+ident)[1]==rendered
    assert fetch(resumed_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Reuse only an older private input on the unchanged current formal base.
    blocked=BeautifulSoup(fetch(resumed_client,base,'/structure-draft/restore?id='+draft_id)[1],'html.parser')
    assert blocked.select_one('[data-restore-reason="review_changed"]')
    assert not blocked.select_one('[data-draft-restore-form]')
    head=draft_save(store,svc.Principal('A','a'),ident,expected=newer_draft,
        action={'kind':'block','block_id':block['id'],'block_kind':block['kind'],'text':'비교할 최신 임시 입력'})
    restore_status,restore_preview,_=fetch(resumed_client,base,'/structure-draft/restore?id='+newer_draft)
    assert restore_status==200
    restore_form=BeautifulSoup(restore_preview,'html.parser').select_one('[data-draft-restore-form]')
    restore_fields={e['name']:e.get('value','') for e in restore_form.select('input[name]')}
    old_inputs=[tuple(r) for r in store.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]
    before_reuse=list(store.db.iterdump())
    assert fetch(resumed_client,base,'/structure-draft/restore',fields=restore_fields)[0]==200
    latest_input=store.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence DESC LIMIT 1').fetchone()
    assert latest_input['previous_id']==head and latest_input['sequence']==4
    assert store.db.execute('SELECT source_id FROM school_review_draft_restores WHERE draft_id=?',
        (latest_input['id'],)).fetchone()[0]==newer_draft
    assert [tuple(r) for r in store.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')][:3]==old_inputs
    non_inputs=lambda rows:[line for line in rows if not line.startswith(
        ('INSERT INTO "school_review_drafts"','INSERT INTO "school_review_draft_restores"'))]
    assert non_inputs(before_reuse)==non_inputs(list(store.db.iterdump()))
    assert fetch(resumed_client,base,'/html?id='+ident)[1]==rendered
    assert fetch(resumed_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Explicitly submit the immutable recovered input; keep every old event and
    # original/export while appending exactly one formal event and private link.
    handoff_status,handoff_html,_=fetch(resumed_client,base,'/structure-draft/submit?id='+latest_input['id'])
    assert handoff_status==200
    handoff_form=BeautifulSoup(handoff_html,'html.parser').select_one('[data-draft-submit-form]')
    assert handoff_form is not None
    handoff_fields={e['name']:e.get('value','') for e in handoff_form.select('input[name]')}
    private_before=[tuple(r) for r in store.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]
    events_before=[tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')]
    handoff_before=list(store.db.iterdump())
    status,handoff_receipt,_=fetch(resumed_client,base,'/structure-draft/submit',fields=handoff_fields)
    assert status==200 and BeautifulSoup(handoff_receipt,'html.parser').select_one('[data-draft-handoff-receipt]')
    event=store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence DESC LIMIT 1').fetchone()
    assert event['status']=='draft' and event['warnings_acknowledged']==0
    assert store.db.execute('SELECT draft_id FROM school_review_draft_handoffs WHERE review_id=?',(event['id'],)).fetchone()[0]==latest_input['id']
    assert [tuple(r) for r in store.db.execute('SELECT * FROM school_review_drafts ORDER BY sequence')]==private_before
    assert [tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')][:-1]==events_before
    non_handoff=lambda rows:[line for line in rows if not line.startswith(
        ('INSERT INTO "school_structure_reviews"','INSERT INTO "school_review_draft_handoffs"'))]
    assert non_handoff(handoff_before)==non_handoff(list(store.db.iterdump()))
    new_pin='?id='+ident+'&revision='+version['revision']+'&event='+event['id']
    status,submitted_html,_=fetch(resumed_client,base,'/review-html'+new_pin)
    assert status==200 and '다음 임시 입력' in submitted_html.decode()
    assert latest_input['id'].encode() not in submitted_html
    assert latest_input['id'].encode() not in fetch(resumed_client,base,'/structure-review'+new_pin+'&format=json')[1]
    assert fetch(resumed_client,base,'/html?id='+ident)[1]==rendered
    assert fetch(resumed_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert fetch(resumed_client,base,'/review-bundle'+pinned)[1]==reviewed_zip
    # Final review uses the same saved HTML and appends only confirmation.
    final_url='/structure-confirm'+new_pin
    final_status,final_preview,_=fetch(resumed_client,base,final_url)
    final_form=BeautifulSoup(final_preview,'html.parser').select_one('[data-final-confirm]')
    assert final_status==200 and final_form is not None
    final_fields={e['name']:e.get('value','') for e in final_form.select('input[name]')}
    final_fields.update(acknowledge='yes',comment='형식별 HTML·구역·경고 대조 완료')
    final_before=list(store.db.iterdump());final_events=[tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')]
    assert fetch(resumed_client,base,'/review-html'+new_pin)[1]==submitted_html
    final_status,final_receipt,_=fetch(resumed_client,base,'/structure-confirm',fields=final_fields)
    assert final_status==200 and BeautifulSoup(final_receipt,'html.parser').select_one('[data-final-reason=already_confirmed]')
    current=structure_review.view(store,svc.Principal('A','a'),ident)
    assert current['status']=='confirmed' and current['latest']['previous_id']==event['id']
    assert current['analysis']['warnings']==analysis['warnings'] and current['analysis']['complete'] is False
    assert current['latest']['changes']==[{'target':'status','before':'draft','after':'confirmed'}]
    assert [tuple(r) for r in store.db.execute('SELECT * FROM school_structure_reviews ORDER BY sequence')][:-1]==final_events
    stable_final=lambda rows:[r for r in rows if not r.startswith('INSERT INTO "school_structure_reviews"')]
    assert stable_final(final_before)==stable_final(list(store.db.iterdump()))
    assert fetch(resumed_client,base,'/review-html'+new_pin)[1]==submitted_html
    assert fetch(resumed_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert fetch(resumed_client,base,'/review-bundle'+pinned)[1]==reviewed_zip
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Stage 17 downloads the exact current confirmation without changing rows,
    # old exports, automatic analysis, originals or the prior review audit.
    batch_before=list(store.db.iterdump())
    batch_item={'id':ident,'revision':version['revision'],'event':current['latest']['id']}
    batch_status,batch_preview,_=fetch(resumed_client,base,'/confirmed-export/prepare',fields={
        'csrf':resumed_csrf,'item':json.dumps(batch_item)})
    batch_form=BeautifulSoup(batch_preview,'html.parser').select_one('[data-export-download]')
    assert batch_status==200 and batch_form is not None
    batch_fields={e['name']:e.get('value','') for e in batch_form.select('input[name]')}
    batch_fields['acknowledge']='yes'
    batch_zip_status,batch_zip,batch_headers=fetch(resumed_client,base,'/confirmed-export/download',fields=batch_fields)
    assert batch_zip_status==200 and batch_headers['Content-Type']=='application/zip'
    with zipfile.ZipFile(io.BytesIO(batch_zip)) as z:
        root_manifest=json.loads(z.read('manifest.json'));assert len(root_manifest['documents'])==1
        document=root_manifest['documents'][0];prefix=document['path']
        assert document['review']['id']==batch_item['event'] and document['review']['status']=='confirmed'
        assert document['warnings']==analysis['warnings'] and document['analysis_complete'] is False
        assert root_manifest['private'] is True and root_manifest['publication_approved'] is False
        assert root_manifest['index_sha256']==digest(z.read('index.html'))
        document_manifest=json.loads(z.read(prefix+'manifest.json'))
        assert document_manifest['html_sha256']==digest(z.read(prefix+'index.html'))
        assert '다음 임시 입력' in z.read(prefix+'index.html').decode()
        assert original_sha in {f['sha256'] for f in document_manifest['originals']}
        for f in document_manifest['originals']:assert digest(z.read(prefix+f['path']))==f['sha256']
    assert list(store.db.iterdump())==batch_before
    assert fetch(resumed_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert fetch(resumed_client,base,'/review-bundle'+pinned)[1]==reviewed_zip
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Stage 18 persists only the pinned list. Logout/fresh-login recovery must
    # download byte-identical confirmed content without changing prior rows.
    export_save=BeautifulSoup(batch_preview,'html.parser').select_one('[data-export-save]')
    assert export_save is not None
    export_fields={e['name']:e.get('value','') for e in export_save.select('input[name]')}
    export_fields['name']='형식별 확인 완료본 묶음'
    saved_export_status,saved_export_preview,_=fetch(resumed_client,base,'/confirmed-export/save',fields=export_fields)
    assert saved_export_status==200
    saved_form=BeautifulSoup(saved_export_preview,'html.parser').select_one('[data-export-download]')
    saved_ident=saved_form.select_one('[name=id]')['value']
    exclude_exports=lambda rows:[r for r in rows if not r.startswith((
        'INSERT INTO "school_confirmed_export_lists"','INSERT INTO "school_confirmed_export_items"',
        'INSERT INTO "school_confirmed_export_downloads"'))]
    assert exclude_exports(list(store.db.iterdump()))==exclude_exports(batch_before)
    saved_rows=exclude_exports(list(store.db.iterdump()))
    assert fetch(resumed_client,base,'/logout',fields={'csrf':resumed_csrf})[0]==200
    export_client=client();export_csrf=login(export_client,base)
    saved_directory=BeautifulSoup(fetch(export_client,base,'/confirmed-export/saved')[1],'html.parser')
    saved_link=saved_directory.select_one('[data-export-saved-resume]')['href']
    assert saved_ident in saved_link
    resume_export_status,resume_export_preview,_=fetch(export_client,base,saved_link)
    assert resume_export_status==200
    resume_form=BeautifulSoup(resume_export_preview,'html.parser').select_one('[data-export-download]')
    assert not resume_form.select_one('[name=acknowledge]').has_attr('checked')
    resume_fields={e['name']:e.get('value','') for e in resume_form.select('input[name]')}
    resume_fields['acknowledge']='yes';assert resume_fields['csrf']==export_csrf
    resume_zip_status,resume_zip,_=fetch(export_client,base,'/confirmed-export/saved/download',fields=resume_fields)
    assert resume_zip_status==200 and resume_zip==batch_zip and exclude_exports(list(store.db.iterdump()))==saved_rows
    assert fetch(export_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert fetch(export_client,base,'/review-bundle'+pinned)[1]==reviewed_zip
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    # Continue the same actual-parser journey through the receipt search UI.
    # The stored receipt must describe the bytes actually downloaded above.
    receipt_baseline=list(store.db.iterdump())
    receipt_page=BeautifulSoup(fetch(export_client,base,'/confirmed-export/receipts?'+urlencode({'q':'형식별'}))[1],'html.parser')
    csv_url=receipt_page.select_one('[data-receipt-overview-csv]')['href']
    receipt_status,receipt_csv,_=fetch(export_client,base,csv_url)
    csv_rows=list(csv.reader(io.StringIO(receipt_csv.decode('utf-8-sig'))))
    assert receipt_status==200 and len(csv_rows)==2
    assert csv_rows[1][0]==saved_ident and csv_rows[1][4:7]==['1',str(len(resume_zip)),digest(resume_zip)]
    search_form=receipt_page.select_one('[data-receipt-search-save]')
    search_fields={e['name']:e.get('value','') for e in search_form.select('input[name]')}
    search_fields['name']='형식별 통합 검증 영수증'
    search_status,search_html,_=fetch(export_client,base,'/confirmed-export/receipt-searches/save',fields=search_fields)
    search_card=BeautifulSoup(search_html,'html.parser').select_one('[data-receipt-search-card]')
    assert search_status==200 and search_card is not None
    search_url=search_card.select_one('[data-receipt-search-open]')['href']
    search_csv_url=search_card.select_one('[data-receipt-search-csv]')['href']
    without_searches=lambda rows:[r for r in rows if not r.startswith('INSERT INTO "school_receipt_searches"')]
    assert without_searches(list(store.db.iterdump()))==without_searches(receipt_baseline)
    assert fetch(export_client,base,'/logout',fields={'csrf':export_csrf})[0]==200
    report_client=client();report_csrf=login(report_client,base)
    before_reports=list(store.db.iterdump())
    loaded_status,loaded_search,_=fetch(report_client,base,search_url)
    assert loaded_status==200
    loaded_page=BeautifulSoup(loaded_search,'html.parser')
    assert loaded_page.select_one('[name=q]')['value']=='형식별'
    assert fetch(report_client,base,search_csv_url)[1]==receipt_csv
    other_client=client();login(other_client,base,user='b',password='synthetic-b-only')
    assert fetch(other_client,base,search_url)[0]==404
    assert fetch(other_client,base,saved_link)[0]==404
    assert list(store.db.iterdump())==before_reports
    # A later formal reopening invalidates the entire saved ZIP, while the
    # earlier confirmation, original bytes and receipt remain available.
    current=structure_review.view(store,svc.Principal('A','a'),ident)
    reopen_status,_,_=fetch(report_client,base,'/structure-review',fields={
        'csrf':report_csrf,'id':ident,'revision':version['revision'],'base_hash':current['base_hash'],
        'expected_review':current['latest']['id'],'action':'reopen','comment':'통합 검증 후 추가 검수'})
    assert reopen_status==200
    after_reopen=list(store.db.iterdump())
    blocked_status,_,blocked_headers=fetch(report_client,base,'/confirmed-export/saved/download',fields={
        **resume_fields,'csrf':report_csrf})
    assert blocked_status==409 and 'Content-Disposition' not in blocked_headers
    assert fetch(report_client,base,search_csv_url)[1]==receipt_csv
    assert list(store.db.iterdump())==after_reopen
    assert fetch(report_client,base,'/review-html'+pinned+'&download=1')[1]==reviewed_html
    assert fetch(report_client,base,'/review-bundle'+pinned)[1]==reviewed_zip
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes()))
        for folder in ('objects','extracted') for p in (store.root/folder).rglob('*') if p.is_file()}==files_before
    evidence = Path(os.environ.get('MOA_FORMAT_EVIDENCE_DIR', store.root/'format-evidence'))/kind
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(evidence/('original.'+kind), raw)
    atomic_write(evidence/'candidate.html', rendered)
    atomic_write(evidence/'originals.zip', zipped)
    atomic_write(evidence/'reviewed.html', reviewed_html)
    atomic_write(evidence/'input-recovery.html', recovery_html)
    atomic_write(evidence/'reviewed-originals.zip', reviewed_zip)
    atomic_write(evidence/'final-check.html',final_preview)
    atomic_write(evidence/'final-confirmed.html',final_receipt)
    atomic_write(evidence/'confirmed-batch-preview.html',batch_preview)
    atomic_write(evidence/'confirmed-batch.zip',batch_zip)
    atomic_write(evidence/'saved-batch-preview.html',saved_export_preview)
    atomic_write(evidence/'resumed-batch-preview.html',resume_export_preview)
    atomic_write(evidence/'resumed-batch.zip',resume_zip)
    atomic_write(evidence/'saved-search-relogin.html',loaded_search)
    atomic_write(evidence/'download-receipts.csv',receipt_csv)
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
        'http_input_draft':draft_status,'http_input_recovery':recovery_status,
        'input_draft_logout_recovery_without_review_or_approval':True,
        'staff_review_status':reviewed['status'],'review_events':len(history['events']),
        'http_reviewed_preview':preview_status,'http_reviewed_html':reviewed_status,'http_reviewed_zip':reviewed_zip_status,
        'http_confirmed_batch_prepare':batch_status,'http_confirmed_batch_zip':batch_zip_status,
        'confirmed_batch_pins_originals_and_warnings_verified':True,'confirmed_batch_db_and_files_unchanged':True,
        'http_saved_export':saved_export_status,'http_saved_export_resume':resume_export_status,
        'http_saved_export_zip':resume_zip_status,'saved_export_logout_recovery_byte_identical':True,
        'saved_export_only_list_rows_appended':True,'saved_export_originals_history_preserved':True,
        'receipt_matches_actual_download_bytes':True,'saved_search_relogin_csv_identical':True,
        'saved_search_and_batch_other_school_denied':True,'later_review_blocks_saved_zip':blocked_status,
        'blocked_download_preserves_originals_old_exports_and_receipts':True,
        'reviewed_edit_visible':True,'reviewed_zip_originals_sha256_verified':True,
        'http_comparison':comparison_status,'http_comparison_json':json_status,
        'comparison_changed_blocks':comparison['summary']['changed_blocks'],
        'comparison_status_only_distinct':True,'comparison_db_objects_cache_unchanged':True,
        'http_restore_preview':restore_preview_status,'http_restore':restore_status,
        'http_restored_html':restore_html_status,'http_restored_zip':restore_zip_status,
        'restored_content_matches_source':True,'restored_status':'draft',
        'http_overview':overview_status,'overview_draft_and_pinned_links_verified':True,
        'overview_db_objects_cache_unchanged':True,
        'http_saved_list':saved_status,'http_saved_resume':resume_status,
        'saved_list_logout_resume_pins_preserved':True,
        'prior_review_rows_and_exports_preserved':True,
        'automatic_analysis_unchanged':True,'conversion_html_and_zip_contents_unchanged':True,
        'http_upload':status,'http_html':html_status,
        'http_zip':zip_status, 'originals_sha256_verified':True, 'private':True,
        'operational_db_used':False, 'human_approval_written':False, 'store':str(store.root)})
