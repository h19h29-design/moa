"""Reviewed output must be private, pinned, escaped and non-mutating."""
import io
import json
import zipfile
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup
import pytest

from moa import mobile, school_review as review, school_service as svc
from moa import school_review_export as export
from moa.core import digest
from test_school_structure import converted
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def reviewed(s, *, incoming=None):
    principal = svc.Principal('A', 'a')
    if incoming is None:
        receipt, version = converted(svc, s, principal)
    else:
        receipt = svc.upload(s, principal, incoming)
        svc.request_conversion(s, principal, receipt.version_id)
        assert svc.process_conversions(s) == ['done']
        version = svc.get_version(s, principal, receipt.version_id)
    v = review.view(s, principal, receipt.version_id)
    block = next(b for b in v['analysis']['blocks'] if b['kind'] == 'body')
    event = save(review, s, principal, receipt.version_id,
        {'kind':'block', 'block_id':block['id'], 'block_kind':'heading', 'text':'담당자가 고친 최종 안내'})
    return principal, receipt.version_id, version, event, block


def url(ident, revision, event, route='/review-html'):
    return f'{route}?id={ident}&revision={revision}&event={event}'


def soup(raw):
    return BeautifulSoup(raw, 'html.parser')


def test_review_preview_and_download_pin_event_and_do_not_write(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    principal, ident, version, event, block = reviewed(s)
    original = mobile.revision(s, version['mobile_id'], school=True)
    originals = {f['sha256']:s.object_path(f['sha256']).read_bytes() for f in version['files']}
    before = s.db.total_changes
    review_page = soup(fetch(a, base, '/structure-review?id='+ident)[1])
    link = review_page.find('a', href=lambda x: x and x.startswith('/review-preview?'))
    assert link, 'Reviewed HTML preview link is missing'
    assert parse_qs(urlsplit(link['href']).query)['event'] == [event]
    status, page, _ = fetch(a, base, link['href'])
    assert status == 200
    frame = soup(page).find('iframe', title='검수 수정본 HTML')
    assert frame is not None and frame.has_attr('sandbox')
    html_status, html, headers = fetch(a, base, frame['src'])
    assert html_status == 200 and 'no-store' in headers['Cache-Control']
    assert headers['Content-Disposition'].startswith('inline;')
    node = soup(html).find(attrs={'data-block-id':block['id']})
    assert node.find('h3').get_text() == '담당자가 고친 최종 안내'
    assert block['text'] not in node.get_text()
    assert '수정 중' in soup(html).get_text()
    status, downloaded, headers = fetch(a, base, frame['src']+'&download=1')
    assert status == 200 and downloaded == html
    assert headers['Content-Type'].startswith('text/html')
    assert headers['Content-Disposition'].startswith('attachment;')
    assert s.db.total_changes == before
    assert mobile.revision(s, version['mobile_id'], school=True) == original
    assert all(s.object_path(sha).read_bytes() == raw for sha,raw in originals.items())
    assert not s.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    assert not list((s.root/'learning').glob('*.jsonl'))


def test_previous_html_and_zip_survive_edits_reconversion_and_receiving_correction(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    principal, ident, version, draft, block = reviewed(s)
    confirmed = save(review, s, principal, ident, {'kind':'confirm','acknowledge_warnings':True})
    old_url = url(ident, version['revision'], confirmed)
    status, old, _ = fetch(a, base, old_url)
    assert status == 200, 'Versioned reviewed HTML route is missing'
    zip_url = url(ident, version['revision'], confirmed, '/review-bundle')
    old_zip = fetch(a, base, zip_url)[1]
    assert '담당자 확인 완료' in soup(old).get_text()
    save(review, s, principal, ident, {'kind':'reopen'})
    save(review, s, principal, ident, {'kind':'block','block_id':block['id'],'block_kind':'body','text':'후속 수정'})
    svc.request_conversion(s, principal, ident)
    assert svc.process_conversions(s) == ['done']
    correction, _ = converted(svc, s, principal, price='17000')
    assert correction.version_id != ident
    assert fetch(a, base, old_url)[1] == old
    assert fetch(a, base, zip_url)[1] == old_zip
    status, old_draft, _ = fetch(a, base, url(ident, version['revision'], draft))
    assert status == 200 and '수정 중' in soup(old_draft).get_text()
    history = soup(fetch(a, base, '/structure-history?id='+ident+'&revision='+version['revision'])[1])
    assert any(parse_qs(urlsplit(x['href']).query).get('event') == [confirmed]
               for x in history.find_all('a', href=lambda x:x and x.startswith('/review-preview?')))


def test_bundle_contains_exact_originals_and_verifiable_snapshot_not_unsafe_names(school_web):
    _, s, base = school_web
    a = client(); login(a, base)
    post = svc.IncomingPost('zip', '<unsafe title>', '<p>Body</p>', (
        svc.IncomingFile('../same#?.html', b'<html><script>never execute original</script><p>one</p></html>', 'attachment'),
        svc.IncomingFile('same#?.html', b'<html><p>two</p></html>', 'attachment')))
    _, ident, version, event, _ = reviewed(s, incoming=post)
    status, raw, headers = fetch(a, base, url(ident, version['revision'], event, '/review-bundle'))
    assert status == 200, 'Reviewed ZIP route is missing'
    assert headers['Content-Type'] == 'application/zip'
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert len(z.namelist()) == len(set(z.namelist()))
        assert {'index.html','manifest.json'} <= set(z.namelist())
        manifest = json.loads(z.read('manifest.json'))
        assert manifest['review']['id'] == event and manifest['review']['status'] == 'draft'
        assert manifest['notice_version'] == ident and manifest['conversion_revision'] == version['revision']
        assert manifest['html_sha256'] == digest(z.read('index.html'))
        assert manifest['private'] is True and manifest['publication_approved'] is False
        assert len(manifest['originals']) == len(version['files'])
        assert {f['sha256'] for f in manifest['originals']} == {f['sha256'] for f in version['files']}
        for f in manifest['originals']:
            assert f['path'].startswith('originals/') and '..' not in f['path']
            assert digest(z.read(f['path'])) == f['sha256']
        links = soup(z.read('index.html')).find_all('a', attrs={'data-original-file':True})
        assert len(links) == len(version['files'])
        assert {a['href'] for a in links} == {f['path'] for f in manifest['originals']}
        assert not soup(z.read('index.html')).find('script')


@pytest.mark.parametrize('route', ['/review-preview','/review-html','/review-bundle'])
def test_exports_enforce_school_membership_and_exact_version_scope(school_web, route):
    _, s, base = school_web
    a, b, reader, anon = client(), client(), client(), client()
    login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    _, ident, version, event, _ = reviewed(s)
    endpoint = url(ident, version['revision'], event, route)
    assert fetch(a,base,endpoint)[0] == 200, 'Reviewed endpoint missing'
    assert fetch(reader,base,endpoint)[0] == 200
    assert fetch(anon,base,endpoint)[0] == 401
    assert fetch(b,base,endpoint)[0] == 404
    other, foreign = converted(svc,s,svc.Principal('A','a'),key='other')
    assert fetch(a,base,url(ident,foreign['revision'],event,route))[0] == 404
    assert fetch(a,base,url(other.version_id,foreign['revision'],event,route))[0] == 404
    svc.grant_member(s,'A','reader','reader',active=False)
    assert fetch(reader,base,endpoint)[0] == 401


@pytest.mark.parametrize('suffix', ['', '&revision=x', '&revision=x&event=y&event=z',
    '&revision=x&event=y&download=anything', '&revision=x&event=y&school=B'])
def test_export_rejects_unpinned_or_ambiguous_requests(school_web, suffix):
    _, s, base = school_web
    a = client(); login(a,base)
    _, ident, _, _, _ = reviewed(s)
    assert fetch(a,base,'/review-html?id='+ident+suffix)[0] == 400


def test_corrected_header_footnote_attachment_and_removed_relations_render(school_web):
    _, s, base = school_web
    a = client(); login(a,base)
    principal, ident, version, _, _ = reviewed(s)
    v = review.view(s,principal,ident)
    edge = next(e for e in v['analysis']['relations'] if e['kind']=='attachment_reference')
    note = next(b for b in v['analysis']['blocks'] if b['kind']=='heading' and b['file_id']=='0')
    save(review,s,principal,ident,{'kind':'block','block_id':note['id'],'block_kind':'footnote','text':'담당자 수정 각주'})
    save(review,s,principal,ident,{'kind':'relation_remove','relation':{k:edge[k] for k in ('kind','source','target')}})
    event = save(review,s,principal,ident,{'kind':'relation_add','relation':{
        'kind':'footnote_for','source':edge['source'],'target':note['id']}})
    status, raw, _ = fetch(a,base,url(ident,version['revision'],event))
    assert status == 200
    doc = soup(raw)
    assert doc.find(attrs={'data-block-id':note['id']}).find('aside',role='doc-footnote').get_text() == '담당자 수정 각주'
    assert doc.find(attrs={'data-relation':'header_for'})
    assert doc.find(attrs={'data-relation':'footnote_for'})
    assert not doc.find(attrs={'data-relation':'attachment_reference'})
    assert len(doc.find_all(attrs={'data-file-id':True})) == 2
    for link in doc.find_all('a',href=lambda x:x and x.startswith('#')):
        assert doc.find(id=link['href'][1:]), 'Dangling relationship anchor'


def test_table_correction_never_reintroduces_original_cell_text_or_removed_header(school_web):
    _,s,base = school_web; a=client(); login(a,base)
    principal,ident,version,_,_ = reviewed(s, incoming=svc.IncomingPost('table','표 시험',
        '<p>Body</p><table><tr><th>Old heading</th><td>Old value</td></tr></table>'))
    v=review.view(s,principal,ident)
    table=next(b for b in v['analysis']['blocks'] if b['kind']=='table')
    edge=next(e for e in v['analysis']['relations'] if e['kind']=='table_header_for')
    event=save(review,s,principal,ident,{'kind':'relation_remove','relation':{k:edge[k] for k in ('kind','source','target')}})
    status,raw,_=fetch(a,base,url(ident,version['revision'],event))
    assert status==200
    node=soup(raw).find(attrs={'data-block-id':table['id']})
    assert not node.find('th') and node.find('td').get_text()=='Old heading'
    event=save(review,s,principal,ident,{'kind':'block','block_id':table['id'],'block_kind':'table','text':'수정된 표 15,000원'})
    doc=soup(fetch(a,base,url(ident,version['revision'],event))[1])
    node=doc.find(attrs={'data-block-id':table['id']})
    assert '수정된 표 15,000원' in node.get_text() and 'Old value' not in node.get_text()
    assert not node.find('table') and '셀' in node.get_text()


def test_untrusted_text_never_becomes_script_network_or_attribute(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    principal,ident,version,_,block=reviewed(s)
    malicious='</style><script>bad()</script><img src="https://bad.example/a" onerror="bad()">'
    event=save(review,s,principal,ident,{'kind':'block','block_id':block['id'],'block_kind':'body','text':malicious},comment=malicious)
    status,raw,_=fetch(a,base,url(ident,version['revision'],event))
    assert status==200
    doc=soup(raw)
    assert doc.find(attrs={'data-block-id':block['id']}).find('p',class_='block-text').get_text()==malicious
    assert not doc.find_all(['script','img','object','embed','iframe','form','base'])
    assert "default-src 'none'" in doc.find('meta',attrs={'http-equiv':'Content-Security-Policy'})['content']
    assert all(not k.lower().startswith('on') for tag in doc.find_all(True) for k in tag.attrs)
    assert all(a['href'].startswith('#') for a in doc.find_all('a',href=True))


@pytest.mark.parametrize('damage', ['missing','changed','symlink'])
def test_bundle_refuses_missing_corrupt_or_symlink_original_without_writing(school_web,damage):
    _,s,base=school_web; a=client(); login(a,base)
    _,ident,version,event,_=reviewed(s)
    path=s.object_path(version['files'][0]['sha256'])
    raw=path.read_bytes()
    if damage=='missing': path.unlink()
    elif damage=='changed': path.write_bytes(b'corrupt synthetic fixture')
    else:
        other=s.root/'untouched-test-original'; other.write_bytes(raw)
        path.unlink(); path.symlink_to(other)
    before=s.db.total_changes
    status,body,_=fetch(a,base,url(ident,version['revision'],event,'/review-bundle'))
    assert status==409 and not body.startswith(b'PK')
    assert s.db.total_changes==before


def test_sparse_tables_share_document_display_budget_and_preserve_text(school_web):
    _,s,base=school_web; a=client(); login(a,base)
    row=('<w:tr><w:trPr><w:gridBefore w:val="199"/></w:trPr>'
         '<w:tc><w:p><w:r><w:t>Synthetic cell</w:t></w:r></w:p></w:tc></w:tr>')
    table='<w:tbl>'+'<w:tr/>'*99+row+'</w:tbl>'
    raw=io.BytesIO()
    with zipfile.ZipFile(raw,'w') as z:
        z.writestr('word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'+table+table.replace('Synthetic cell','Second cell')+'</w:body></w:document>')
    principal,ident,version,_,_=reviewed(s, incoming=svc.IncomingPost('sparse','Sparse tables','<p>Body</p>',(
        svc.IncomingFile('sparse.docx',raw.getvalue(),'convert'),)))
    event=save(review,s,principal,ident,{'kind':'confirm','acknowledge_warnings':True})
    before=s.db.total_changes
    status,html,_=fetch(a,base,url(ident,version['revision'],event))
    assert status==200
    doc=soup(html)
    assert len(doc.find_all(['td','th']))<=20_000, 'Sparse grids must share a document-wide expansion limit'
    assert len(doc.find_all('table'))==1
    assert 'Synthetic cell' in doc.get_text() and 'Second cell' in doc.get_text()
    assert '표 표시량 제한' in doc.get_text()
    assert s.db.total_changes==before


def test_utf8_output_limit_stops_before_rendering_later_tables(school_web,monkeypatch):
    _,s,_=school_web
    principal,ident,version,_,block=reviewed(s, incoming=svc.IncomingPost('limit','HTML limit',
        '<p>Body</p><table><tr><td>Later cell</td></tr></table>'))
    event=save(review,s,principal,ident,{'kind':'block','block_id':block['id'],
        'block_kind':'body','text':'가'*11_000})
    saved=export.snapshot(s,principal,ident,revision_id=version['revision'],event_id=event)
    rendered=[]
    table=export._table
    def counted(*args):
        rendered.append(args[0]['id'])
        return table(*args)
    monkeypatch.setattr(export,'_table',counted)
    monkeypatch.setattr(export,'MAX_HTML_BYTES',32_000)
    before=s.db.total_changes
    with pytest.raises(ValueError,match='Reviewed HTML output limit'):
        export.render_html(saved)
    assert rendered==[], 'Stop at the first over-budget UTF-8 chunk, not after rendering the document'
    assert s.db.total_changes==before
