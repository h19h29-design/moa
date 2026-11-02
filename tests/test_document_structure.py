"""Structure evidence on synthetic originals; never models or operational data."""
import importlib
import importlib.util
import io
import zipfile

import pytest

from moa.core import digest


def module():
    assert importlib.util.find_spec('moa.document_structure') is not None, 'School document structure analysis is missing'
    return importlib.import_module('moa.document_structure')


def archive(parts):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, value in parts.items(): z.writestr(name, value)
    return out.getvalue()


def file_meta(ident, name, raw, role='convert'):
    return {'id':ident, 'filename':name, 'sha256':digest(raw), 'role':role}


def test_html_native_zones_and_referenced_note_keep_source_locations():
    m = module()
    raw = b'''<html><header><p>Synthetic School</p></header><h1>Trip</h1>
      <p>Cost 12000<sup><a href="#n1" role="doc-noteref">1</a></sup></p>
      <aside id="n1" role="doc-footnote">1 Fee waived on cancellation.</aside>
      <footer>Contact office</footer><script>not evidence</script></html>'''
    doc = m.parse_document(raw, 'html')
    assert [(b['kind'], b['text']) for b in doc['blocks']] == [
        ('header','Synthetic School'), ('heading','Trip'), ('body','Cost 12000 1'),
        ('footnote','1 Fee waived on cancellation.'), ('footer','Contact office')]
    assert all(b['locator']['format'] == 'html' and b['locator']['path'] for b in doc['blocks'])
    f = file_meta('0', 'notice.html', raw)
    result = m.analyze_bundle([f], {'0':doc})
    notes = [r for r in result['relations'] if r['kind'] == 'footnote_for']
    assert len(notes) == 1 and notes[0]['basis'] == 'explicit_reference'
    assert notes[0]['source'] == '0/b2' and notes[0]['target'] == '0/b3'
    assert result['human_approved'] is False


def test_table_headers_and_note_reference_are_not_flattened_into_prose():
    m = module()
    raw = '<html><table><tr><th>대상</th><th>비용</th></tr><tr><td>1학년</td><td>12000원<a href="#n">※</a></td></tr></table><p role="doc-footnote" id="n">※ 취소 시 면제</p></html>'.encode()
    doc = m.parse_document(raw, 'html')
    table = doc['blocks'][0]
    assert table['kind'] == 'table' and table['table']['rows'] == 2
    assert [c['text'] for c in table['table']['cells'] if c['header']] == ['대상','비용']
    result = m.analyze_bundle([file_meta('g','notice.html',raw)], {'g':doc})
    assert len([r for r in result['relations'] if r['kind'] == 'table_header_for']) == 2
    assert any(r['kind'] == 'footnote_for' and r['target'] == 'g/b1' for r in result['relations'])


def test_hwpx_control_note_is_separate_from_its_body_and_reference_is_explicit():
    m = module()
    raw = archive({'Contents/section0.xml':'''<hs:sec xmlns:hs="hs" xmlns:hp="hp">
      <hp:p><hp:run><hp:ctrl><hp:header><hp:p><hp:run><hp:t>가정통신문</hp:t></hp:run></hp:p></hp:header></hp:ctrl></hp:run></hp:p>
      <hp:p><hp:run><hp:t>비용 12000원</hp:t><hp:ctrl><hp:footNote number="1"><hp:p><hp:run><hp:t>취소 시 면제</hp:t></hp:run></hp:p></hp:footNote></hp:ctrl></hp:run></hp:p>
      </hs:sec>'''})
    doc = m.parse_document(raw, 'hwpx')
    body = next(b for b in doc['blocks'] if b['kind'] == 'body')
    note = next(b for b in doc['blocks'] if b['kind'] == 'footnote')
    assert body['text'] == '비용 12000원' and note['text'] == '취소 시 면제'
    assert body['locator']['part'] == 'Contents/section0.xml'
    assert 'page' not in body['locator']
    result = m.analyze_bundle([file_meta('0','notice.hwpx',raw)], {'0':doc})
    assert any(r['kind'] == 'header_for' and r['certainty'] == 'explicit' for r in result['relations'])
    assert any(r['kind'] == 'footnote_for' and r['certainty'] == 'explicit' for r in result['relations'])


def test_docx_only_referenced_header_and_footnote_parts_are_in_inventory():
    m = module()
    raw = archive({
        'word/document.xml':'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>Apply by Friday</w:t><w:footnoteReference w:id="2"/></w:r></w:p><w:sectPr><w:headerReference r:id="h1"/></w:sectPr></w:body></w:document>''',
        'word/_rels/document.xml.rels':'<Relationships><Relationship Id="h1" Type="header" Target="header1.xml"/></Relationships>',
        'word/header1.xml':'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Synthetic School</w:t></w:r></w:p></w:hdr>',
        'word/header2.xml':'<orphan>must not appear</orphan>',
        'word/footnotes.xml':'<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="2"><w:p><w:r><w:t>Except holidays</w:t></w:r></w:p></w:footnote><w:footnote w:id="3"><w:p><w:r><w:t>Orphan note</w:t></w:r></w:p></w:footnote></w:footnotes>'})
    doc = m.parse_document(raw, 'docx')
    assert {b['text'] for b in doc['blocks']} == {'Apply by Friday','Synthetic School','Except holidays'}
    result = m.analyze_bundle([file_meta('0','notice.docx',raw)], {'0':doc})
    assert len([r for r in result['relations'] if r['kind'] == 'footnote_for']) == 1


def test_multiple_attachment_references_match_names_not_upload_order_or_roles():
    m = module()
    raw = '<html><p>붙임 1. 참가동의서 1부.</p><p>붙임 2. 준비물 안내 1부.</p></html>'.encode()
    files = [file_meta('body','본문.html',raw), file_meta('b','준비물 안내.html',b'b','attachment'),
             file_meta('a','참가동의서.hwpx',b'a','attachment')]
    docs = {'body':m.parse_document(raw,'html'), 'a':{'status':'needs_parser','blocks':[],'coverage':{}},
            'b':{'status':'needs_parser','blocks':[],'coverage':{}}}
    result = m.analyze_bundle(files, docs)
    links = [r for r in result['relations'] if r['kind'] == 'attachment_reference']
    assert [(r['source'],r['target']) for r in links] == [('body/b0','file:a'),('body/b1','file:b')]
    assert all(r['certainty'] == 'candidate' for r in links)
    assert [f['role'] for f in files] == ['convert','attachment','attachment']


def test_missing_and_ambiguous_attachments_are_unresolved_without_guessing():
    m = module()
    raw = '<html><p>붙임 1. 동의서 1부.</p><p>붙임 2. 미제공 안내 1부.</p></html>'.encode()
    files = [file_meta('0','본문.html',raw), file_meta('1','동의서.pdf',b'a','attachment'), file_meta('2','동의서.hwpx',b'b','attachment')]
    result = m.analyze_bundle(files, {'0':m.parse_document(raw,'html')})
    assert not [r for r in result['relations'] if r['kind'] == 'attachment_reference']
    assert {'ambiguous_attachment','unresolved_attachment'} <= {w['code'] for w in result['warnings']}
    assert len(result['files']) == 3 and result['complete'] is False


def test_unlinked_note_marker_does_not_silently_attach_to_nearest_table():
    m = module()
    raw = '<html><table><tr><td>안내</td></tr></table><p>※ 예외 사항은 담당자 문의</p></html>'.encode()
    doc = m.parse_document(raw, 'html')
    note = next(b for b in doc['blocks'] if b['kind'] == 'footnote')
    assert note['certainty'] == 'candidate'
    result = m.analyze_bundle([file_meta('0','notice.html',raw)], {'0':doc})
    assert not [r for r in result['relations'] if r['kind'] == 'footnote_for']
    assert 'unlinked_footnote' in {w['code'] for w in result['warnings']}


def test_text_pdf_tracks_actual_pages_and_does_not_claim_native_footnote_zones():
    from reportlab.pdfgen import canvas
    m = module(); out = io.BytesIO()
    c = canvas.Canvas(out, invariant=1)
    for text in ('First page body', 'Second page body'):
        c.drawString(40,750,'Synthetic School'); c.drawString(40,700,text); c.showPage()
    c.save()
    doc = m.parse_document(out.getvalue(), 'pdf')
    assert {b['locator']['page'] for b in doc['blocks']} == {1,2}
    assert any(b['kind'] == 'header' and b['certainty'] == 'candidate' for b in doc['blocks'])
    assert doc['coverage']['native_zones'] is False


@pytest.mark.parametrize('kind,raw', [('image', b'not executed'), ('unsupported', b'plain original')])
def test_unreadable_content_remains_unconfirmed_not_empty_success(kind, raw):
    m = module()
    doc = m.parse_document(raw, kind)
    result = m.analyze_bundle([file_meta('0','original.bin',raw,'attachment')], {'0':doc})
    assert result['complete'] is False and result['blocks'] == []
    assert result['files'][0]['status'] != 'extracted'


def test_archive_limits_and_xml_entities_fail_closed():
    m = module()
    raw = archive({'Contents/section0.xml':'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///not-a-real-private-file">]><x>&leak;</x>'})
    with pytest.raises(ValueError): m.parse_document(raw, 'hwpx')


def test_structure_size_limit_rejects_instead_of_silently_truncating():
    m = module()
    raw = ('<html>' + '<p>body</p>' * 2001 + '</html>').encode()
    with pytest.raises(ValueError): m.parse_document(raw, 'html')


def test_docx_keeps_table_position_without_duplicate_cell_prose():
    m = module()
    raw = archive({'word/document.xml':'''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Before</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Audience</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Fee</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>After</w:t></w:r></w:p></w:body></w:document>'''})
    doc = m.parse_document(raw, 'docx')
    assert [(b['kind'],b['text']) for b in doc['blocks']] == [('body','Before'),('table','Audience\nFee'),('body','After')]
    result = m.analyze_bundle([file_meta('0','notice.docx',raw)], {'0':doc})
    assert all(r['certainty'] == 'candidate' for r in result['relations'] if r['kind'] == 'table_header_for')


def test_hwpx_table_is_not_repeated_in_parent_paragraph_text():
    m = module()
    raw = archive({'Contents/section0.xml':'''<s xmlns:hp="hp"><hp:p><hp:run><hp:t>Before</hp:t><hp:tbl><hp:tr><hp:tc><hp:subList><hp:p><hp:run><hp:t>Cell only</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></s>'''})
    doc = m.parse_document(raw, 'hwpx')
    assert [(b['kind'],b['text']) for b in doc['blocks']] == [('body','Before'),('table','Cell only')]


def test_native_note_id_is_scoped_to_its_own_file_not_another_attachment():
    m = module()
    first = b'<html><p><a role="doc-noteref" href="#missing">1</a> body</p></html>'
    second = b'<html><p id="missing" role="doc-footnote">other file note</p></html>'
    files = [file_meta('a','a.html',first),file_meta('b','b.html',second,'attachment')]
    result = m.analyze_bundle(files, {'a':m.parse_document(first,'html'),'b':m.parse_document(second,'html')})
    assert not [r for r in result['relations'] if r['kind'] == 'footnote_for']
    assert {'unresolved_footnote','unlinked_footnote'} <= {w['code'] for w in result['warnings']}


def test_html_metadata_is_not_body_and_empty_document_is_not_complete():
    m = module()
    doc = m.parse_document(b'<!DOCTYPE html><html><head><title>Metadata only</title></head><body></body></html>', 'html')
    assert doc['blocks'] == [] and doc['status'] == 'needs_parser'
    assert not m.analyze_bundle([file_meta('0','empty.html',b'')], {'0':doc})['complete']


def test_hwpx_inline_table_preserves_before_table_after_sequence():
    m = module()
    raw = archive({'Contents/section0.xml':'<s xmlns:hp="hp"><hp:p><hp:run><hp:t>Before</hp:t><hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>Cell</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl><hp:t>After</hp:t></hp:run></hp:p></s>'})
    assert [(b['kind'],b['text']) for b in m.parse_document(raw,'hwpx')['blocks']] == [
        ('body','Before'),('table','Cell'),('body','After')]


def test_hwpx_note_inside_table_is_separate_and_linked_without_polluting_cell_text():
    m = module()
    raw = archive({'Contents/section0.xml':'<s xmlns:hp="hp"><hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>12000원</hp:t><hp:ctrl><hp:footNote><hp:p><hp:run><hp:t>취소 시 면제</hp:t></hp:run></hp:p></hp:footNote></hp:ctrl></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></s>'})
    doc = m.parse_document(raw,'hwpx')
    assert [(b['kind'],b['text']) for b in doc['blocks']] == [('table','12000원'),('footnote','취소 시 면제')]
    result = m.analyze_bundle([file_meta('0','notice.hwpx',raw)], {'0':doc})
    assert any(r['kind'] == 'footnote_for' and r['source'] == '0/b0' and r['target'] == '0/b1' for r in result['relations'])


def test_hwpx_header_table_does_not_shift_the_following_body_table_inventory():
    m = module()
    raw = archive({'Contents/section0.xml':'<s xmlns:hp="hp"><hp:p><hp:run><hp:ctrl><hp:header><hp:p><hp:run><hp:t>School</hp:t><hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>Header cell</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hp:header></hp:ctrl><hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>Body cell</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></s>'})
    doc = m.parse_document(raw,'hwpx')
    tables = [b for b in doc['blocks'] if b['kind'] == 'table']
    assert [b['text'] for b in tables] == ['Header cell','Body cell']
    assert tables[0]['zone'] == 'header' and tables[1].get('zone') != 'header'


@pytest.mark.parametrize('first_row,headers', [('대상|비용',2),('1학년|12000원',0)])
def test_native_table_headers_are_semantic_candidates_not_every_first_row(first_row, headers):
    m = module()
    cells = ''.join('<hp:tc><hp:p><hp:run><hp:t>'+text+'</hp:t></hp:run></hp:p></hp:tc>' for text in first_row.split('|'))
    raw = archive({'Contents/section0.xml':'<s xmlns:hp="hp"><hp:tbl><hp:tr>'+cells+'</hp:tr></hp:tbl></s>'})
    doc = m.parse_document(raw,'hwpx')
    result = m.analyze_bundle([file_meta('0','notice.hwpx',raw)], {'0':doc})
    links = [r for r in result['relations'] if r['kind'] == 'table_header_for']
    assert len(links) == headers and all(r['certainty'] == 'candidate' for r in links)


def test_empty_docx_table_does_not_hide_other_content_or_shift_table_locations():
    m = module()
    raw = archive({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl/><w:p><w:r><w:t>Readable body</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Actual cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
    doc = m.parse_document(raw,'docx')
    assert doc['status'] == 'extracted'
    assert [b['text'] for b in doc['blocks']] == ['Readable body','Actual cell']
    assert doc['blocks'][1]['locator']['path'].endswith('/tbl[2]')


def test_html_header_table_and_nested_table_keep_distinct_source_inventory():
    m = module()
    raw = b'<html><header><p>School</p><table><tr><td>Masthead</td></tr></table></header><table><tr><td>Outer<table><tr><td>Inner</td></tr></table></td></tr></table></html>'
    doc = m.parse_document(raw,'html')
    tables = [b for b in doc['blocks'] if b['kind'] == 'table']
    assert [b['text'] for b in tables] == ['Masthead','Outer','Inner']
    assert tables[0]['zone'] == 'header' and tables[1]['table']['nested'] is True
    assert len({b['locator']['path'] for b in tables}) == 3


def test_relation_and_warning_budgets_stop_oversized_graphs_without_partial_success(monkeypatch):
    m = module()
    raw = b'<html><header>School</header><p>body</p></html>'
    files = [file_meta('0','notice.html',raw)]
    monkeypatch.setattr(m,'MAX_RELATIONS',1,raising=False)
    with pytest.raises(ValueError): m.analyze_bundle(files,{'0':m.parse_document(raw,'html')})
    monkeypatch.setattr(m,'MAX_RELATIONS',4000)
    monkeypatch.setattr(m,'MAX_WARNINGS',1,raising=False)
    raw = b'<html><p>Attachment</p><p role="doc-noteref"><a role="doc-noteref" href="#missing">1</a></p><p role="doc-footnote">Unlinked note</p></html>'
    with pytest.raises(ValueError): m.analyze_bundle(files,{'0':m.parse_document(raw,'html')})


def test_docx_tables_in_referenced_header_and_note_have_explicit_zones_and_containment():
    m = module()
    table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    raw = archive({
        'word/document.xml':'<w:document '+ns+' xmlns:r="r"><w:body><w:p><w:r><w:t>Body</w:t><w:footnoteReference w:id="1"/></w:r></w:p><w:sectPr><w:headerReference r:id="h"/></w:sectPr></w:body></w:document>',
        'word/_rels/document.xml.rels':'<Relationships><Relationship Id="h" Target="header1.xml"/></Relationships>',
        'word/header1.xml':'<w:hdr '+ns+'>'+table.format('Header table')+'</w:hdr>',
        'word/footnotes.xml':'<w:footnotes '+ns+'><w:footnote w:id="1">'+table.format('Note table')+'</w:footnote></w:footnotes>'})
    doc = m.parse_document(raw,'docx')
    result = m.analyze_bundle([file_meta('0','notice.docx',raw)],{'0':doc})
    assert [(b['text'],b.get('zone')) for b in doc['blocks'] if b['kind'] == 'table'] == [('Header table','header'),('Note table','footnote')]
    assert any(r['kind'] == 'header_for' and r['certainty'] == 'explicit' for r in result['relations'])
    assert any(r['kind'] == 'footnote_for' for r in result['relations'])
    assert any(r['kind'] == 'part_of' for r in result['relations'])
    assert result['complete'] is True


def test_docx_nested_table_uses_only_direct_rows_cells_and_text_once():
    m = module()
    raw = archive({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Outer</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Inner</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr></w:tbl></w:body></w:document>'})
    tables = [b for b in m.parse_document(raw,'docx')['blocks'] if b['kind'] == 'table']
    assert [(t['text'],t['table']['rows'],t['table']['cols']) for t in tables] == [('Outer',1,1),('Inner',1,1)]
    assert len({t['locator']['path'] for t in tables}) == 2


@pytest.mark.parametrize('flag,explicit', [('',True),('true',True),('1',True),('on',True),('false',False),('0',False),('off',False)])
def test_docx_native_row_header_flag_is_not_replaced_by_a_label_dictionary(flag,explicit):
    m = module()
    marker = '<w:tblHeader'+(' w:val="'+flag+'"' if flag else '')+'/>'
    raw = archive({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:trPr>'+marker+'</w:trPr><w:tc><w:p><w:r><w:t>구분</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>진행 내용</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
    result = m.analyze_bundle([file_meta('0','notice.docx',raw)],{'0':m.parse_document(raw,'docx')})
    links = [r for r in result['relations'] if r['kind'] == 'table_header_for']
    assert len(links) == (2 if explicit else 0)
    assert all(r['certainty'] == 'explicit' and r['basis'] == 'native_row_header' for r in links)


def test_graph_byte_budget_stops_before_processing_the_remaining_blocks(monkeypatch):
    m = module()
    doc = m.parse_document(('<html><p>'+'x'*2000+'</p></html>').encode(),'html')
    doc['blocks'].append({})  # The tail must not be touched after the first block exhausts the budget.
    monkeypatch.setattr(m,'MAX_RESULT_BYTES',1000)
    with pytest.raises(ValueError): m.analyze_bundle([file_meta('0','notice.html',b'')],{'0':doc})


@pytest.mark.parametrize('kind', ['html','hwpx'])
def test_table_only_native_note_remains_a_linked_container(kind):
    m = module()
    raw = (b'<html><p>Body<a role="doc-noteref" href="#n">1</a></p><aside role="doc-footnote" id="n"><table><tr><td>Note table</td></tr></table></aside></html>'
        if kind == 'html' else archive({'Contents/section0.xml':'<s xmlns:hp="hp"><hp:p><hp:run><hp:t>Body</hp:t><hp:ctrl><hp:footNote><hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:p><hp:run><hp:t>Note table</hp:t></hp:run></hp:p></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hp:footNote></hp:ctrl></hp:run></hp:p></s>'}))
    doc = m.parse_document(raw,kind)
    result = m.analyze_bundle([file_meta('0','notice.'+kind,raw)],{'0':doc})
    note = next(b for b in doc['blocks'] if b['kind'] == 'footnote')
    table = next(b for b in doc['blocks'] if b['kind'] == 'table')
    assert note['text'] == '' and table['text'] == 'Note table'
    assert table['container_id'] == note['id'] and table['zone'] == 'footnote'
    assert {r['kind'] for r in result['relations']} >= {'footnote_for','part_of'}
    assert result['complete'] is True


@pytest.mark.parametrize('container', ['table','header'])
def test_html_note_inside_cell_or_header_keeps_its_own_text_and_link(container):
    m = module()
    content = 'Cost<a role="doc-noteref" href="#n">1</a><aside role="doc-footnote" id="n">Fee waived</aside>'
    raw = ('<html>'+('<table><tr><td>'+content+'</td></tr></table>' if container == 'table' else '<header>'+content+'</header>')+'</html>').encode()
    doc = m.parse_document(raw,'html')
    assert [(b['kind'],b['text']) for b in doc['blocks']] == [(container,'Cost 1'),('footnote','Fee waived')]
    result = m.analyze_bundle([file_meta('0','notice.html',raw)],{'0':doc})
    assert any(r['kind'] == 'footnote_for' and r['source'] == '0/b0' and r['target'] == '0/b1' for r in result['relations'])
    assert result['complete'] is True


@pytest.mark.parametrize('wrapper', ['row','cell'])
def test_docx_content_control_wrappers_keep_owned_rows_cells_without_nested_duplicates(wrapper):
    m = module()
    cell = '<w:tc><w:p><w:r><w:t>Wrapped cell</w:t></w:r></w:p></w:tc>'
    wrap = lambda value:'<w:sdt><w:sdtContent>'+value+'</w:sdtContent></w:sdt>'
    row = '<w:tr>'+(wrap(cell) if wrapper == 'cell' else cell)+'</w:tr>'
    raw = archive({'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Body</w:t></w:r></w:p><w:tbl>'+(wrap(row) if wrapper == 'row' else row)+'</w:tbl></w:body></w:document>'})
    doc = m.parse_document(raw,'docx')
    result = m.analyze_bundle([file_meta('0','notice.docx',raw)],{'0':doc})
    assert [(b['kind'],b['text']) for b in doc['blocks']] == [('body','Body'),('table','Wrapped cell')]
    assert doc['blocks'][1]['table']['rows'] == doc['blocks'][1]['table']['cols'] == 1
    assert result['complete'] is True
