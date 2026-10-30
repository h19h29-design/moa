"""Bounded, read-only batches of current confirmed school review snapshots."""
from contextlib import contextmanager
import io
import stat
import zipfile

from . import mobile, school_review as review, school_review_export as export
from . import school_service as svc
from .core import digest, encode
from .web import esc

MAX_NOTICES = 10
MAX_ORIGINAL_BYTES = 64 * 1024 * 1024
MAX_CONTENT_BYTES = 96 * 1024 * 1024


class Limit(ValueError):
    """Reduce the selection; no partial archive or persistent artifact exists."""


@contextmanager
def _snapshot(store):
    store.db.execute('SAVEPOINT confirmed_export')
    try: yield
    finally: store.db.execute('RELEASE confirmed_export')


def _inspect(store, principal, items, expected):
    svc.require_member(store,principal)
    if not isinstance(items,list) or not 1<=len(items)<=MAX_NOTICES:
        raise ValueError('Select one to ten notices')
    pins, summaries, seen, total = [], [], set(), 0
    fields={'id','revision','event'} | ({'snapshot_sha256'} if expected else set())
    for item in items:
        if not isinstance(item,dict) or set(item)!=fields: raise ValueError('Invalid export pins')
        for v in item.values(): review._text(v,160,required=True)
        if item['id'] in seen: raise ValueError('Duplicate notice')
        seen.add(item['id'])
        v=review.view(store,principal,item['id'],revision_id=item['revision'])
        if (v['stale'] or v['migration_required'] or not v['latest'] or
            v['latest']['id']!=item['event'] or v['status']!='confirmed'):
            raise review.Conflict('Confirmed receiving/conversion/review changed')
        saved=export.snapshot(store,principal,item['id'],revision_id=item['revision'],event_id=item['event'])
        fingerprint=digest(encode(saved).encode())
        if expected and item['snapshot_sha256']!=fingerprint:
            raise review.Conflict('Prepared snapshot changed')
        size=0; files=saved['analysis']['files']
        if not 1<=len(files)<=mobile.MAX_FILES: raise review.Conflict('Original file inventory changed')
        for f in files:
            try:
                path=store.object_path(f['sha256'])
                if path.resolve()!=path: raise OSError('Indirect object path')
                st=path.lstat()
                if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=mobile.MAX_FILE_BYTES:
                    raise OSError('Original size/type changed')
                size+=st.st_size
            except (OSError,ValueError): raise review.Conflict('Original inventory unavailable') from None
        if size>mobile.MAX_UPLOAD_BYTES: raise review.Conflict('Original size inventory changed')
        total+=size
        if total>MAX_ORIGINAL_BYTES: raise Limit('선택한 원본 합계가 64MiB를 넘습니다. 문서를 나눠 선택하세요.')
        pins.append({**item,'snapshot_sha256':fingerprint})
        a=saved['analysis']
        summaries.append({'notice_version':item['id'],'conversion_revision':item['revision'],
            'title':saved['title'],'receiving_sequence':saved['receiving_sequence'],
            'review':saved['review'],'snapshot_sha256':fingerprint,'file_count':len(a['files']),
            'original_bytes':size,'analysis_complete':a['complete'],'warnings':a['warnings']})
    return {'pins':pins,'documents':summaries,'original_bytes':total}


def prepare(store, principal, items, *, expected=False):
    """One read snapshot. No model, parser, migration, job, DB or object writes."""
    with _snapshot(store): return _inspect(store,principal,items,expected)


def _index(documents):
    text='<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    text+='<meta http-equiv="Content-Security-Policy" content="'+esc(export.CSP)+'"><title>확인 완료본 묶음</title>'
    text+='<style>body{font:16px/1.7 system-ui;max-width:900px;margin:auto;padding:20px;overflow-wrap:anywhere}article{border:1px solid #ccd;padding:16px;margin:16px 0}</style>'
    text+='<h1>확인 완료본 묶음 · 비공개</h1><p>선택한 수신·변환·검수 시점에 고정된 자료입니다. 이후 최신 상태나 공개 승인을 뜻하지 않습니다. 원래의 경고·미확정 범위를 보존합니다.</p>'
    for d in documents:
        e=d['review']
        text+='<article><h2><a href="'+d['path']+'index.html">'+esc(d['title'])+'</a></h2>'
        text+='<p>수신 #'+str(d['receiving_sequence'])+' · 검수 #'+str(e['sequence'])+' · 담당자 '+esc(e['actor'])+' · '+esc(e['created_at'])+'</p>'
        text+='<p>파일 '+str(d['file_count'])+'개 · 경고 '+str(len(d['warnings']))+'개 · '+('분석기가 보고한 완전성 충족' if d['analysis_complete'] else '일부 미확정·누락 있음')+'</p></article>'
    return (text+'</html>').encode()


def bundle(store, principal, pins):
    """Verify every document/original before returning one deterministic ZIP.

    Reuse individual bundle integrity/filename/link rules. Keep at most one
    individual package in memory, cap all expanded entries, and never extract
    or persist files. A failure returns no partial download.
    """
    with _snapshot(store):
        data=_inspect(store,principal,pins,True)
        out=io.BytesIO();used=0
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as target:
            def add(name,raw):
                nonlocal used
                used+=len(raw)
                if used>MAX_CONTENT_BYTES: raise Limit('HTML·근거를 포함한 묶음이 96MiB를 넘습니다. 문서를 나눠 선택하세요.')
                entry=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
                entry.create_system=3;entry.external_attr=0o100600<<16;entry.compress_type=zipfile.ZIP_DEFLATED
                target.writestr(entry,raw)
            for i,(pin,document) in enumerate(zip(data['pins'],data['documents']),1):
                document['path']=f'documents/{i:02d}/'
                saved=export.snapshot(store,principal,pin['id'],revision_id=pin['revision'],event_id=pin['event'])
                package=export.bundle(store,saved)
                with zipfile.ZipFile(io.BytesIO(package)) as source:
                    for entry in source.infolist():
                        name=entry.filename
                        if (name.startswith('/') or '\\' in name or '..' in name.split('/') or
                            not (name in ('index.html','manifest.json') or name.startswith('originals/'))):
                            raise review.Conflict('Unexpected reviewed archive path')
                        if used+entry.file_size>MAX_CONTENT_BYTES:
                            raise Limit('HTML·근거를 포함한 묶음이 96MiB를 넘습니다. 문서를 나눠 선택하세요.')
                        add(document['path']+name,source.read(entry))
                del package
            index=_index(data['documents'])
            manifest={'format':'moa-school-confirmed-bundle-v1','private':True,'publication_approved':False,
                'documents':data['documents'],'index_sha256':digest(index)}
            add('index.html',index);add('manifest.json',encode(manifest).encode())
        return out.getvalue()
