"""Real bounded school worker, revision provenance and private HTTP access."""
import json

import pytest

from moa import document_structure as structure, mobile
from moa.core import Store, digest
from test_school_service import schools
from test_web_school import school_web, client, fetch, login


def incoming(m, *, key='trip', price='12000'):
    return m.IncomingPost(key, '합성 행사 안내', '<header>합성학교 가정통신문</header><p>비용 '+price+'원</p><p>붙임 1. 참가동의서 1부.</p>', (
        m.IncomingFile('참가동의서.html', '<html><h1>참가동의서</h1><p>보호자 서명</p></html>'.encode(), 'attachment'),))


def converted(m, s, a, *, key='trip', price='12000'):
    receipt = m.upload(s, a, incoming(m, key=key, price=price))
    m.request_conversion(s, a, receipt.version_id)
    assert m.process_conversions(s) == ['done']
    return receipt, m.get_version(s, a, receipt.version_id)


def test_school_worker_stores_all_file_structure_but_preserves_originals_roles_and_research(schools):
    m, s, a, _ = schools
    receipt, version = converted(m,s,a)
    data = mobile.revision(s, version['mobile_id'], school=True)['data']
    assert 'structure' in data, 'School conversion did not persist structural evidence'
    analysis = data['structure']
    assert len(analysis['files']) == 2
    assert analysis['files'][1]['role'] == 'attachment' and analysis['files'][1]['block_count'] > 0
    assert any(r['kind'] == 'attachment_reference' and r['target'] == 'file:1' for r in analysis['relations'])
    assert all(digest(s.object_path(f['sha256']).read_bytes()) == f['sha256'] for f in version['files'])
    assert not s.db.execute('SELECT 1 FROM notices').fetchone()
    assert not s.db.execute('SELECT 1 FROM mobile_reviews').fetchone()
    assert not list((s.root/'learning').glob('*.jsonl'))
    assert s.db.execute('PRAGMA user_version').fetchone()[0] == 5


def test_structure_accessor_is_versioned_scoped_and_read_only(schools):
    m, s, a, b = schools
    old, v1 = converted(m,s,a)
    new, v2 = converted(m,s,a,price='15000')
    assert hasattr(m, 'structure_for'), 'Scoped structure accessor is missing'
    one = m.structure_for(s,a,old.version_id)
    two = m.structure_for(s,a,new.version_id)
    assert one['conversion_revision'] == v1['revision'] and two['conversion_revision'] == v2['revision']
    assert one['notice_version'] != two['notice_version']
    assert '12000' in json.dumps(one, ensure_ascii=False) and '15000' not in json.dumps(one, ensure_ascii=False)
    with pytest.raises(LookupError): m.structure_for(s,b,old.version_id)
    with pytest.raises(LookupError): m.structure_for(s,a,old.version_id,revision_id=v2['revision'])
    counts = s.db.execute('SELECT count(*) FROM mobile_revisions').fetchone()[0]
    assert m.structure_for(s,a,old.version_id) == one
    assert s.db.execute('SELECT count(*) FROM mobile_revisions').fetchone()[0] == counts


def test_separate_cache_and_bounded_subprocess_are_reused_without_research_parser_changes(tmp_path):
    assert hasattr(structure,'extract_asset'), 'Bounded school-only structure worker is missing'
    with Store(tmp_path) as s:
        f = s.object(b'<html><header>School</header><p>body</p></html>', 'notice.html')
        f.update(id='0',role='convert',kind='html')
        result = structure.extract_asset(s,f)
        assert result['status'] == 'extracted' and result['source_sha256'] == f['sha256']
        caches = list((s.root/'extracted').glob('*'+structure.VERSION+'*.json'))
        assert len(caches) == 1
        before = caches[0].stat().st_mtime_ns
        assert structure.extract_asset(s,f) == result
        assert caches[0].stat().st_mtime_ns == before
        assert not list((s.root/'extracted').glob('*'+mobile.PARSER_VERSION+'*.json'))
        assert not s.db.execute('SELECT 1 FROM jobs').fetchone()


def test_legacy_research_conversion_does_not_run_or_export_school_analysis(tmp_path):
    with Store(tmp_path) as s:
        ident = mobile.create_upload(s,[('notice.html', b'<html><p>research original</p></html>')])
        result = mobile.build_conversion(s,ident)
        assert 'structure' not in result
        assert not list((s.root/'extracted').glob('*'+structure.VERSION+'*.json'))


def test_private_structure_html_json_and_revision_cannot_cross_school(school_web):
    from moa import school_service as m
    _, s, base = school_web
    a,b,anon,reader = client(),client(),client(),client()
    login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    receipt, version = converted(m,s,m.Principal('A','a'))
    path = '/structure?id='+receipt.version_id
    assert fetch(anon,base,path)[0] == 401
    assert fetch(b,base,path)[0] == 404
    status, body, headers = fetch(a,base,path)
    assert status == 200, 'Private structure viewer is missing'
    assert '문서 구조 분석' in body.decode() and '미검수' in body.decode()
    assert 'no-store' in headers['Cache-Control']
    assert fetch(reader,base,path)[0] == 200
    status, raw, headers = fetch(a,base,path+'&format=json')
    assert status == 200 and headers['Content-Type'].startswith('application/json')
    data = json.loads(raw)
    assert data['notice_version'] == receipt.version_id and data['conversion_revision'] == version['revision']
    assert data['human_approved'] is False
    assert '/structure?id=' in fetch(a,base,'/notice?id='+receipt.version_id)[1].decode()
    assert fetch(a,base,path+'&revision=unrelated')[0] == 404
    m.grant_member(s,'A','a','uploader',active=False)
    assert fetch(a,base,path)[0] == 401


def test_structure_view_escapes_source_text_and_never_serves_original_markup(school_web):
    from moa import school_service as m
    _, s, base = school_web
    a = client(); login(a,base)
    post = m.IncomingPost('xss','<script>synthetic title</script>', '<p>&lt;img src=x onerror=alert(1)&gt;</p>')
    receipt = m.upload(s,m.Principal('A','a'),post)
    m.request_conversion(s,m.Principal('A','a'),receipt.version_id)
    assert m.process_conversions(s) == ['done']
    status, raw, _ = fetch(a,base,'/structure?id='+receipt.version_id)
    assert status == 200
    assert b'<img src=x' not in raw and b'<script>synthetic title' not in raw
    assert b'&lt;img' in raw


def test_old_revision_without_structure_stays_read_only_until_explicit_reconversion(schools):
    m,s,a,_ = schools
    receipt = m.upload(s,a,incoming(m))
    version = m.get_version(s,a,receipt.version_id)
    legacy = mobile.build_conversion(s,version['mobile_id'],use_approved_rules=False,school=True)
    mobile.save_revision(s,version['mobile_id'],legacy,export_rules=False,school=True)
    assert hasattr(m,'structure_for'), 'Scoped structure accessor is missing'
    with pytest.raises(LookupError): m.structure_for(s,a,receipt.version_id)
    assert not s.db.execute('SELECT 1 FROM jobs').fetchone()


@pytest.mark.parametrize('cached', [[], {'version':structure.VERSION,'status':'extracted','blocks':'not-a-list'},
    {'version':structure.VERSION,'status':'extracted','blocks':[{'kind':'body'}]}])
def test_malformed_structure_cache_is_rebuilt_not_served_or_fatal(tmp_path, cached):
    from moa.core import write_json
    with Store(tmp_path) as s:
        f = s.object(b'<html><p>safe original</p></html>', 'notice.html')
        f.update(id='0',role='convert',kind='html')
        if isinstance(cached,dict): cached.update(source_sha256=f['sha256'],kind='html')
        path = s.root/'extracted'/f"{f['sha256']}-{structure.VERSION}-html.json"
        write_json(path,cached)
        result = structure.extract_asset(s,f)
        assert result['status'] == 'extracted' and result['blocks'][0]['text'] == 'safe original'


def test_structure_worker_detects_original_hash_damage_and_sanitizes_failure(tmp_path):
    from moa.core import atomic_write
    with Store(tmp_path) as s:
        f = s.object(b'<html><p>original</p></html>', 'notice.html')
        f.update(id='0',role='convert',kind='html')
        atomic_write(s.object_path(f['sha256']),b'<html><p>changed synthetic bytes</p></html>')
        result = structure.extract_asset(s,f)
        assert result['status'] == 'parse_error' and result['blocks'] == []
        assert 'changed synthetic bytes' not in json.dumps(result)


def test_notice_size_limit_preserves_originals_with_explicit_incomplete_result(tmp_path):
    with Store(tmp_path) as s:
        files = []
        for i in range(2):
            raw = ('<html>'+('<p>body '+str(i)+'</p>')*1001+'</html>').encode()
            f = s.object(raw,str(i)+'.html'); f.update(id=str(i),role='convert',kind='html'); files.append(f)
        analysis = structure.build_analysis(s,files)
        assert analysis['complete'] is False and analysis['blocks'] == []
        assert analysis['warnings'] == [{'code':'notice_structure_limit'}]
        assert len(analysis['files']) == 2
        assert all(digest(s.object_path(f['sha256']).read_bytes()) == f['sha256'] for f in files)


def test_structure_parser_failure_is_unconfirmed_and_only_allowed_environment_is_inherited(tmp_path, monkeypatch):
    import subprocess
    def failure(args, **kwargs):
        assert kwargs['timeout'] == 55 and kwargs['preexec_fn']
        assert kwargs['env']['TMPDIR'] == str(tmp_path/'temporary')
        assert 'MOA_SYNTHETIC_FORBIDDEN_ENV' not in kwargs['env']
        raise subprocess.TimeoutExpired(args,kwargs['timeout'])
    monkeypatch.setenv('TMPDIR',str(tmp_path/'temporary'))
    monkeypatch.setenv('MOA_SYNTHETIC_FORBIDDEN_ENV','must-not-propagate')
    monkeypatch.setattr(structure.subprocess,'run',failure)
    with Store(tmp_path/'store') as s:
        f = s.object(b'<html><p>body</p></html>', 'notice.html'); f.update(id='0',role='convert',kind='html')
        analysis = structure.build_analysis(s,[f])
        assert analysis['complete'] is False and analysis['blocks'] == []
        assert {w['code'] for w in analysis['warnings']} == {'unreadable_file','structure_parser_failed'}
