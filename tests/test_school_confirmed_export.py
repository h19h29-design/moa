"""Private, read-only, version-pinned batches; no partial or stale downloads."""
import io
import json
import os
import zipfile
from urllib.parse import urlencode

from bs4 import BeautifulSoup
import pytest

from moa import school_confirmed_export as batch, school_review as review
from moa import school_review_export as export, school_service as svc
from moa.core import Store, digest, encode
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save
from test_school_review_check import originals
from test_web_school import school_web, client, fetch, login


def confirmed(s,p,*,key='trip',price='12000',incoming=None):
    if incoming is None: receipt,version=converted(svc,s,p,key=key,price=price)
    else:
        receipt=svc.upload(s,p,incoming);svc.request_conversion(s,p,receipt.version_id)
        assert svc.process_conversions(s)==['done']
        version=svc.get_version(s,p,receipt.version_id)
    event=save(review,s,p,receipt.version_id,{'kind':'confirm','acknowledge_warnings':True})
    return {'id':receipt.version_id,'revision':version['revision'],'event':event}


def http_prepare(a,base,csrf,items):
    fields=[('csrf',csrf),*(('item',encode(x)) for x in items)]
    status,raw,headers=fetch(a,base,'/confirmed-export/prepare',data=urlencode(fields).encode())
    page=BeautifulSoup(raw,'html.parser')
    if status!=200:return status,page,headers,None
    form=page.select_one('[data-export-download]')
    values={e['name']:e.get('value','') for e in form.select('input[name]')}
    values['acknowledge']='yes'
    return status,page,headers,values


def get_url(fields):
    return '/confirmed-export/preview?'+urlencode({'bundle':fields['bundle']})


def verify_zip(raw,s,p,items):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=z.namelist();assert len(names)==len(set(names))
        assert all(not n.startswith('/') and '\\' not in n and '..' not in n.split('/') for n in names)
        root=json.loads(z.read('manifest.json'))
        assert root['format']=='moa-school-confirmed-bundle-v1'
        assert root['private'] is True and root['publication_approved'] is False
        assert root['index_sha256']==digest(z.read('index.html'))
        assert [d['notice_version'] for d in root['documents']]==[pin['id'] for pin in items]
        listing=BeautifulSoup(z.read('index.html'),'html.parser')
        assert not listing.find_all(['script','img','iframe','form','base'])
        assert all(a['href'] in names for a in listing.select('a[href]'))
        for i,(d,pin) in enumerate(zip(root['documents'],items),1):
            prefix=f'documents/{i:02d}/';assert d['path']==prefix
            saved=export.snapshot(s,p,pin['id'],revision_id=pin['revision'],event_id=pin['event'])
            manifest=json.loads(z.read(prefix+'manifest.json'))
            assert d['snapshot_sha256']==manifest['snapshot_sha256']==digest(encode(saved).encode())
            assert d['review']==saved['review'] and d['review']['status']=='confirmed'
            assert d['warnings']==manifest['warnings']==saved['analysis']['warnings']
            assert d['analysis_complete']==manifest['analysis_complete']==saved['analysis']['complete']
            assert manifest['html_sha256']==digest(z.read(prefix+'index.html'))
            doc=BeautifulSoup(z.read(prefix+'index.html'),'html.parser')
            assert all(prefix+a['href'] in names for a in doc.select('[data-original-file]'))
            for f in manifest['originals']:
                raw_original=z.read(prefix+f['path'])
                assert digest(raw_original)==f['sha256'] and len(raw_original)==f['bytes']
                assert raw_original==s.object_path(f['sha256']).read_bytes()
        return root


def test_multi_document_bundle_is_pinned_deterministic_and_preserves_every_row_and_file(schools):
    _,s,p,_=schools
    items=[confirmed(s,p),confirmed(s,p,key='second')]
    old=export.snapshot(s,p,items[0]['id'],revision_id=items[0]['revision'],event_id=items[0]['event'])
    old_html=export.render_html(old);old_zip=export.bundle(s,old)
    rows=list(s.db.iterdump());files=originals(s);changes=s.db.total_changes
    prepared=batch.prepare(s,p,items);raw=batch.bundle(s,p,prepared['pins'])
    verify_zip(raw,s,p,items)
    assert batch.bundle(s,p,prepared['pins'])==raw
    assert list(s.db.iterdump())==rows and originals(s)==files and s.db.total_changes==changes
    assert export.render_html(old)==old_html and export.bundle(s,old)==old_zip
    assert s.db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_unsafe_names_duplicate_originals_and_incomplete_warnings_stay_private(schools):
    _,s,p,_=schools
    incoming=svc.IncomingPost('unsafe','<script>synthetic title</script>','<p>Synthetic body</p>',(
        svc.IncomingFile('../same#?.txt',b'untouched synthetic file','attachment'),
        svc.IncomingFile('same#?.txt',b'another synthetic file','attachment')))
    item=confirmed(s,p,incoming=incoming);prepared=batch.prepare(s,p,[item])
    result=verify_zip(batch.bundle(s,p,prepared['pins']),s,p,[item])
    assert result['documents'][0]['analysis_complete'] is False and result['documents'][0]['warnings']
    assert result['documents'][0]['file_count']==3


@pytest.mark.parametrize('change',['reopen','reconfirm','conversion','correction','pending','failed','hash','title'])
def test_prepared_bundle_rejects_changed_receiving_conversion_review_or_snapshot(schools,change,monkeypatch):
    _,s,p,_=schools;item=confirmed(s,p);pins=batch.prepare(s,p,[item])['pins']
    if change in ('reopen','reconfirm'):
        save(review,s,p,item['id'],{'kind':'reopen'})
        if change=='reconfirm':save(review,s,p,item['id'],{'kind':'confirm','acknowledge_warnings':True})
    elif change in ('conversion','pending','failed'):
        svc.request_conversion(s,p,item['id'])
        if change=='conversion':assert svc.process_conversions(s)==['done']
        if change=='failed':
            s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=?)",(item['id'],));s.db.commit()
    elif change=='correction':converted(svc,s,p,price='15000')
    else:
        original=export.snapshot
        def different(*args,**kwargs):
            saved=original(*args,**kwargs)
            if change=='hash':saved['analysis']['complete']=not saved['analysis']['complete']
            else:saved['title']='Changed metadata'
            return saved
        monkeypatch.setattr(export,'snapshot',different)
    rows=list(s.db.iterdump());files=originals(s)
    with pytest.raises(review.Conflict):batch.bundle(s,p,pins)
    assert list(s.db.iterdump())==rows and originals(s)==files


@pytest.mark.parametrize('damage',['missing','changed','symlink','directory','fifo'])
def test_second_document_original_damage_never_returns_a_partial_archive(schools,damage):
    _,s,p,_=schools;items=[confirmed(s,p),confirmed(s,p,key='two',price='15000')]
    pins=batch.prepare(s,p,items)['pins'];version=svc.get_version(s,p,items[1]['id'])
    path=s.object_path(version['files'][0]['sha256']);raw=path.read_bytes()
    if damage=='changed':path.write_bytes(b'x'*len(raw))
    else:
        path.unlink()
        if damage=='symlink':
            other=s.root/'synthetic-object';other.write_bytes(raw);path.symlink_to(other)
        elif damage=='directory':path.mkdir()
        elif damage=='fifo':os.mkfifo(path)
    rows=list(s.db.iterdump());changes=s.db.total_changes
    with pytest.raises(review.Conflict):batch.bundle(s,p,pins)
    assert list(s.db.iterdump())==rows and s.db.total_changes==changes


@pytest.mark.parametrize('kind',['empty','too_many','duplicate','unknown','extra','missing','wrong_type','null','long'])
def test_invalid_selection_is_rejected_without_writes(schools,kind):
    _,s,p,_=schools;item=confirmed(s,p);items=[item]
    if kind=='empty':items=[]
    elif kind=='too_many':items=[item]*11
    elif kind=='duplicate':items=[item,item]
    elif kind=='unknown':items=[{**item,'id':'unknown'}]
    elif kind=='extra':items=[{**item,'school':'B'}]
    elif kind=='missing':items=[{'id':item['id']}]
    elif kind=='wrong_type':items=[{**item,'event':1}]
    elif kind=='null':items=[None]
    else:items=[{**item,'event':'x'*161}]
    rows=list(s.db.iterdump())
    with pytest.raises((ValueError,LookupError)):batch.prepare(s,p,items)
    assert list(s.db.iterdump())==rows


def test_limits_fail_before_bundle_materialisation_or_entry_read(schools,monkeypatch):
    _,s,p,_=schools;item=confirmed(s,p);data=batch.prepare(s,p,[item]);pins=data['pins']
    original_bundle=export.bundle
    def forbidden(*args,**kwargs):raise AssertionError('Archive should not be created')
    monkeypatch.setattr(batch,'MAX_ORIGINAL_BYTES',data['original_bytes']-1)
    monkeypatch.setattr(export,'bundle',forbidden)
    with pytest.raises(batch.Limit):batch.prepare(s,p,[item])
    with pytest.raises(batch.Limit):batch.bundle(s,p,pins)
    monkeypatch.setattr(batch,'MAX_ORIGINAL_BYTES',64*1024*1024)
    monkeypatch.setattr(export,'bundle',original_bundle)
    with zipfile.ZipFile(io.BytesIO(original_bundle(s,export.snapshot(s,p,item['id'],revision_id=item['revision'],event_id=item['event'])))) as z:
        first_size=z.infolist()[0].file_size
    monkeypatch.setattr(batch,'MAX_CONTENT_BYTES',first_size-1)
    monkeypatch.setattr(zipfile.ZipFile,'read',forbidden)
    before=list(s.db.iterdump())
    with pytest.raises(batch.Limit):batch.bundle(s,p,pins)
    assert list(s.db.iterdump())==before


def test_one_sqlite_read_snapshot_keeps_all_selected_documents_together(schools,monkeypatch):
    _,s,p,_=schools;items=[confirmed(s,p),confirmed(s,p,key='two')];pins=batch.prepare(s,p,items)['pins']
    original=review.view;fired=False
    def interleave(*args,**kwargs):
        nonlocal fired
        result=original(*args,**kwargs)
        if not fired:
            fired=True
            with Store(s.root) as other:
                save(review,other,p,items[1]['id'],{'kind':'reopen'})
        return result
    monkeypatch.setattr(review,'view',interleave)
    raw=batch.bundle(s,p,pins)
    monkeypatch.setattr(review,'view',original)
    verify_zip(raw,s,p,items)  # Archive is the consistent request-start snapshot.
    with pytest.raises(review.Conflict):batch.bundle(s,p,pins)


@pytest.mark.parametrize('state',['unreviewed','draft','wrong_revision','wrong_event','foreign_school'])
def test_only_exact_current_confirmation_can_be_prepared(schools,state):
    _,s,p,other=schools;item=confirmed(s,p)
    if state in ('unreviewed','draft'):
        receipt,version=converted(svc,s,p,key=state)
        event='missing'
        if state=='draft':
            v=review.view(s,p,receipt.version_id);block=next(b for b in v['analysis']['blocks'] if b['kind']=='body')
            event=save(review,s,p,receipt.version_id,{'kind':'block','block_id':block['id'],'block_kind':'body','text':'Saved draft'})
        item={'id':receipt.version_id,'revision':version['revision'],'event':event}
    elif state=='wrong_revision':item['revision']='other-conversion'
    elif state=='wrong_event':item['event']='other-event'
    rows=list(s.db.iterdump())
    with pytest.raises((review.Conflict,LookupError)):
        batch.prepare(s,other if state=='foreign_school' else p,[item])
    assert list(s.db.iterdump())==rows


def test_directory_pagination_search_and_ten_document_boundary(school_web):
    _,s,base=school_web;p=svc.Principal('A','a')
    items=[confirmed(s,p,key=f'document-{i}',price=str(12000+i)) for i in range(21)]
    a=client();csrf=login(a,base);rows=list(s.db.iterdump())
    first=BeautifulSoup(fetch(a,base,'/confirmed-export')[1],'html.parser')
    second=BeautifulSoup(fetch(a,base,'/confirmed-export?page=2')[1],'html.parser')
    assert len(first.select('[name=item]'))==20 and len(second.select('[name=item]'))==1
    first_ids={json.loads(e['value'])['id'] for e in first.select('[name=item]')}
    assert json.loads(second.select_one('[name=item]')['value'])['id'] not in first_ids
    assert first.select_one('nav[aria-label] a')['href']=='/confirmed-export?q=&page=2'
    assert '합성 행사 안내' in fetch(a,base,'/confirmed-export?'+urlencode({'q':'합성 행사 안내'}))[1].decode()
    assert not BeautifulSoup(fetch(a,base,'/confirmed-export?q=%25')[1],'html.parser').select('[name=item]')
    status,_,_,fields=http_prepare(a,base,csrf,items[:10]);assert status==200
    status,raw,_=fetch(a,base,'/confirmed-export/download',fields=fields)
    assert status==200 and len(verify_zip(raw,s,p,items[:10])['documents'])==10
    assert list(s.db.iterdump())==rows


def test_root_listing_and_manifest_are_included_in_expanded_byte_limit(schools,monkeypatch):
    _,s,p,_=schools;item=confirmed(s,p);pins=batch.prepare(s,p,[item])['pins']
    raw=batch.bundle(s,p,pins)
    with zipfile.ZipFile(io.BytesIO(raw)) as z: content=sum(e.file_size for e in z.infolist())
    monkeypatch.setattr(batch,'MAX_CONTENT_BYTES',content)
    assert batch.bundle(s,p,pins)==raw
    monkeypatch.setattr(batch,'MAX_CONTENT_BYTES',content-1)
    with pytest.raises(batch.Limit):batch.bundle(s,p,pins)


def test_http_selection_preview_download_are_read_only_and_use_the_displayed_pins(school_web):
    _,s,base=school_web;p=svc.Principal('A','a');items=[confirmed(s,p),confirmed(s,p,key='two')]
    converted(svc,s,p,key='unreviewed')
    a=client();csrf=login(a,base);rows=list(s.db.iterdump());files=originals(s)
    assert BeautifulSoup(fetch(a,base)[1],'html.parser').select_one('[data-action=confirmed-export]')
    status,raw,_=fetch(a,base,'/confirmed-export');assert status==200
    directory=BeautifulSoup(raw,'html.parser');choices=directory.select('[name=item]')
    assert len(choices)==2 and {json.loads(c['value'])['event'] for c in choices}=={x['event'] for x in items}
    assert fetch(a,base,'/confirmed-export?q=nonexistent')[1].decode().count('name="item"')==0
    status,page,headers,fields=http_prepare(a,base,csrf,items)
    assert status==200 and len(page.select('[data-export-document]'))==2
    ack=page.select_one('[name=acknowledge]');assert ack.has_attr('required') and not ack.has_attr('checked')
    assert 'no-store' in headers['Cache-Control']
    assert all('event=' in link['href'] for link in page.select('[data-export-document] a'))
    status,raw,headers=fetch(a,base,'/confirmed-export/download',fields=fields)
    assert status==200 and headers['Content-Type']=='application/zip'
    assert 'attachment;' in headers['Content-Disposition'] and 'no-store' in headers['Cache-Control']
    verify_zip(raw,s,p,items)
    assert list(s.db.iterdump())==rows and originals(s)==files
    save(review,s,p,items[1]['id'],{'kind':'reopen'})
    before=list(s.db.iterdump())
    assert fetch(a,base,get_url(fields))[0]==409
    status,raw,_=fetch(a,base,'/confirmed-export/download',fields=fields)
    assert status==409 and not raw.startswith(b'PK') and list(s.db.iterdump())==before
    assert http_prepare(a,base,csrf,items)[0]==409


def test_http_school_session_read_permission_revocation_logout_and_prepared_limit(school_web):
    web,s,base=school_web;p=svc.Principal('A','a');item=confirmed(s,p)
    a,b,r,fresh=client(),client(),client(),client()
    ca=login(a,base);cb=login(b,base,'b','synthetic-b-only');cr=login(r,base,'reader','synthetic-reader-only');cf=login(fresh,base)
    fields=http_prepare(a,base,ca,[item])[3];route=get_url(fields)
    assert fetch(b,base,route)[0]==fetch(fresh,base,route)[0]==404
    assert fetch(client(),base,route)[0]==401
    assert http_prepare(b,base,cb,[item])[0]==404
    reader_fields=http_prepare(r,base,cr,[item])[3]
    assert fetch(r,base,'/confirmed-export/download',fields=reader_fields)[0]==200
    assert fetch(b,base,'/confirmed-export')[1].decode().count('name="item"')==0
    for _ in range(4):assert http_prepare(a,base,ca,[item])[0]==200
    assert fetch(a,base,route)[0]==404  # At most four preparations per login.
    fields=http_prepare(a,base,ca,[item])[3]
    assert fetch(a,base,'/logout',fields={'csrf':ca})[0]==200
    login(a,base);assert fetch(a,base,get_url(fields))[0]==404
    svc.grant_member(s,'A','reader','reader',active=False)
    assert fetch(r,base,get_url(reader_fields))[0]==401
    assert fetch(r,base,'/confirmed-export/download',fields=reader_fields)[0]==401
    svc.configure_school(s,'A','Disabled synthetic school',enabled=False)
    assert fetch(a,base,'/confirmed-export')[0]==401


@pytest.mark.parametrize('mutation,status',[
    ('none',400),('extra',400),('blank_extra',400),('blank_item',400),('blank_duplicate',400),
    ('duplicate_csrf',400),('bad_json',400),('null',400),('too_many',400),
    ('csrf',403),('origin',403),('large',413),('bad_utf8',400),('stale',409)])
def test_http_prepare_rejects_ambiguous_unsafe_and_stale_requests(school_web,mutation,status):
    _,s,base=school_web;p=svc.Principal('A','a');item=confirmed(s,p);a=client();csrf=login(a,base)
    fields=[('csrf',csrf),('item',encode(item))];headers={};data=None
    if mutation=='none':fields=fields[:1]
    elif mutation=='extra':fields.append(('school','B'))
    elif mutation=='blank_extra':fields.append(('school',''))
    elif mutation=='blank_item':fields[1]=('item','')
    elif mutation=='blank_duplicate':fields.append(('item',''))
    elif mutation=='duplicate_csrf':fields.append(('csrf',csrf))
    elif mutation=='bad_json':fields[1]=('item','{bad json')
    elif mutation=='null':fields[1]=('item','null')
    elif mutation=='too_many':fields=fields[:1]+fields[1:]*11
    elif mutation=='csrf':fields[0]=('csrf','wrong')
    elif mutation=='origin':headers['Origin']='https://foreign.example.test'
    elif mutation=='large':data=b'x'*16385
    elif mutation=='bad_utf8':data=urlencode(fields).encode()+b'&bad=%FF'
    else:save(review,s,p,item['id'],{'kind':'reopen'})
    rows=list(s.db.iterdump())
    assert fetch(a,base,'/confirmed-export/prepare',data=data if data is not None else urlencode(fields).encode(),headers=headers)[0]==status
    assert list(s.db.iterdump())==rows


@pytest.mark.parametrize('mutation,status',[
    ('none',400),('bad_ack',400),('duplicate',400),('duplicate_blank',400),('blank_extra',400),
    ('extra',400),('csrf',403),('origin',403),('large',413),('bad_key',400),('unknown',404)])
def test_http_download_requires_exact_explicit_acknowledgement(school_web,mutation,status):
    _,s,base=school_web;p=svc.Principal('A','a');item=confirmed(s,p);a=client();csrf=login(a,base)
    fields=http_prepare(a,base,csrf,[item])[3];headers={};data=None
    if mutation=='none':del fields['acknowledge']
    elif mutation=='bad_ack':fields['acknowledge']='no'
    elif mutation=='duplicate':data=(urlencode(fields)+'&acknowledge=yes').encode()
    elif mutation=='duplicate_blank':data=(urlencode(fields)+'&acknowledge=').encode()
    elif mutation=='blank_extra':fields['subject']=''
    elif mutation=='extra':fields['subject']='other'
    elif mutation=='csrf':fields['csrf']='wrong'
    elif mutation=='origin':headers['Origin']='https://foreign.example.test'
    elif mutation=='large':data=b'x'*16385
    elif mutation=='bad_key':fields['bundle']='../invalid'
    else:fields['bundle']='x'*24
    rows=list(s.db.iterdump())
    status_out,raw,_=fetch(a,base,'/confirmed-export/download',fields=fields if data is None else None,data=data,headers=headers)
    assert status_out==status and not raw.startswith(b'PK') and list(s.db.iterdump())==rows


@pytest.mark.parametrize('suffix',['?school=B','?q=%FF','?q=x&q=y','?page=0','?page=1000001','?q='+'x'*201])
def test_http_directory_strict_query_never_writes(school_web,suffix):
    _,s,base=school_web;a=client();login(a,base);rows=list(s.db.iterdump())
    assert fetch(a,base,'/confirmed-export'+suffix)[0]==400 and list(s.db.iterdump())==rows


def test_http_limit_is_clear_and_original_damage_never_sends_zip(school_web,monkeypatch):
    _,s,base=school_web;p=svc.Principal('A','a');item=confirmed(s,p);a=client();csrf=login(a,base)
    monkeypatch.setattr(batch,'MAX_ORIGINAL_BYTES',1)
    status,page,_,_=http_prepare(a,base,csrf,[item]);assert status==400 and '문서를 나눠 선택하세요' in page.get_text()
    monkeypatch.setattr(batch,'MAX_ORIGINAL_BYTES',64*1024*1024)
    fields=http_prepare(a,base,csrf,[item])[3];path=s.object_path(svc.get_version(s,p,item['id'])['files'][0]['sha256'])
    path.write_bytes(b'synthetic damaged bytes');rows=list(s.db.iterdump())
    status,raw,_=fetch(a,base,'/confirmed-export/download',fields=fields)
    assert status==409 and not raw.startswith(b'PK') and list(s.db.iterdump())==rows
