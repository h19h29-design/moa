"""Local-only curation of existing originals; classification is not approval."""
from __future__ import annotations

import copy
import json
from datetime import timedelta

from .core import Store, digest, encode, kst_now
from .extract import PARSER_VERSION, html_document
from .corpus_rules import classify_file, summarise

VERSION = 'local-corpus-v1:'+PARSER_VERSION
USES = {'candidate':'활용 후보 · 미승인', 'attachment':'원본 첨부 전용',
        'held':'보류 · 확인 필요', 'excluded':'활용 제외', 'evaluation':'평가 전용'}
REASONS = {'rights_unreviewed':'이용 권한·정확성 검수 전', 'original_form':'작성용 서식',
    'original_reference':'참고자료', 'boundary_unconfirmed':'안내·서식 경계 미확인',
    'insufficient_text':'안내 내용 부족', 'parser_stale':'이전 파서 결과 · 최신 변환 필요',
    'incomplete':'원문·첨부 일부 누락', 'privacy_review':'개인정보 대조 필요',
    'status_unusable':'미분석·파싱 실패', 'needs_vision':'이미지 분석 필요',
    'needs_parser':'지원 파서 필요', 'evaluation':'평가 자료 · 추천 제외',
    'rights_denied':'이용 권한 문제', 'unrelated':'안내와 무관',
    'operator_decision':'사용자 분류', 'role_review':'변환 역할 대조 필요',
    'original_missing':'실제 원본 파일 없음 · 복구 확인 필요'}


def get_record(store, notice_id):
    row=store.db.execute('SELECT * FROM corpus_notices WHERE notice_id=?',(notice_id,)).fetchone()
    return {**dict(row),'files':json.loads(row['files'])} if row else None


def _cached_result(store, file):
    path=store.root/'extracted'/f"{file['sha256']}-{PARSER_VERSION}.json"
    if not path.exists():
        candidates=list((store.root/'extracted').glob(file['sha256']+'-moa-local-v*.json'))
        path=max(candidates,key=lambda p:p.stat().st_mtime) if candidates else None
    if path and path.stat().st_size<=30*1024*1024:
        try:
            result=json.loads(path.read_text())
            if isinstance(result,dict): return result
        except (ValueError,OSError): pass
    return {'status':'needs_vision' if file.get('kind')=='image' else 'needs_parser',
            'text':'','parser_version':''}


def _descriptor(store, doc, file, evaluation):
    from .learn import PII
    exists=store.object_path(file['sha256']).is_file()
    if file['id']=='body' and exists:
        body=doc.get('body_html','')
        result=html_document(body[:500000])
        result['parser_version']=PARSER_VERSION
    else:
        result=_cached_result(store,file) if exists else {'status':'missing','text':''}
    text=result.get('text','')
    if not isinstance(text,str):text=''
    incomplete=doc.get('capture')=='partial_capture' or bool(file.get('incomplete'))
    if file['id']=='body' and len(doc.get('body_html',''))>500000:incomplete=True
    flags={'filename':file.get('filename',''), 'kind':file.get('kind','unsupported'),
           'status':result.get('status','parse_error'),
           'parser_current':result.get('parser_version')==PARSER_VERSION,
           'evaluation':evaluation or _file_evaluation(store,file['sha256']), 'incomplete':incomplete,
           'privacy_flag':bool(PII.search(text)), 'text':text}
    return flags, result.get('parser_version','')


def _seal(data):
    data['fingerprint']=digest(encode({'files':data['files'],'version':VERSION}).encode())
    return data


def _persist(store, data):
    data=_seal(data)
    store.db.execute('INSERT INTO corpus_notices VALUES(?,?,?,?,?,?,?)'
        ' ON CONFLICT(notice_id) DO UPDATE SET mobile_id=excluded.mobile_id,use=excluded.use,'
        ' files=excluded.files,fingerprint=excluded.fingerprint,version=excluded.version,updated_at=excluded.updated_at',
        (data['notice_id'],data['mobile_id'],data['use'],encode(data['files']),data['fingerprint'],VERSION,kst_now().isoformat()))
    return data


def _evaluation(store, notice_id):
    return bool(store.db.execute("SELECT 1 FROM cases WHERE notice_id=? AND split='eval' LIMIT 1",(notice_id,)).fetchone() or
                store.db.execute("SELECT 1 FROM corpus_reviews WHERE notice_id=? AND action='operator_decision' AND json_extract(payload,'$.use')='evaluation' LIMIT 1",(notice_id,)).fetchone())


def _file_evaluation(store, sha):
    # A different post pointing at the same physical file must not smuggle an
    # explicitly reserved evaluation original into recommendations.
    return bool(store.db.execute("SELECT 1 FROM corpus_reviews r JOIN corpus_notices c ON c.notice_id=r.notice_id,json_each(c.files) j WHERE r.action='operator_decision' AND json_extract(r.payload,'$.use')='evaluation' AND json_extract(j.value,'$.file_id')=r.file_id AND json_extract(j.value,'$.sha256')=? LIMIT 1",(sha,)).fetchone() or
                store.db.execute("SELECT 1 FROM mobile_notices n,json_each(n.files) f WHERE n.origin_notice_id IS NULL AND n.split='eval' AND json_extract(f.value,'$.sha256')=? LIMIT 1",(sha,)).fetchone())


def classify_notice(store, notice_id):
    from . import mobile
    doc=store.notice(notice_id)
    mid=mobile.register_collected(store,doc)
    store.db.execute('BEGIN IMMEDIATE')
    try:
        old=get_record(store,notice_id)
        previous={f['file_id']:f for f in old['files']} if old else {}
        row=mobile.notice_row(store,mid)
        source_files=json.loads(row['files'])
        evaluation=_evaluation(store,notice_id)
        files=[]
        for source in source_files:
            d,parser=_descriptor(store,doc,source,evaluation)
            automatic=classify_file(d)
            # A verified mixed boundary is supplied by the existing mobile UI,
            # not guessed by this classifier or an external model.
            if automatic['content_kind']=='mixed' and source.get('boundary_confirmed') and source.get('form_start') is not None:
                automatic['reasons']=[r for r in automatic['reasons'] if r!='boundary_unconfirmed']
                if automatic['reasons']==[] and d['status']=='extracted' and d['parser_current'] and not (d['incomplete'] or d['privacy_flag']):
                    automatic['use']='candidate';automatic['reasons']=['rights_unreviewed']
            flags={k:v for k,v in d.items() if k not in ('text','filename')}
            stamp=digest(encode({'sha':source['sha256'],'flags':flags,'parser':parser,'version':VERSION,
                                'boundary':[source.get('scope'),source.get('form_start'),source.get('boundary_confirmed')]}).encode())
            before=previous.get(source['id'])
            override=before.get('override') if before and before['source_fingerprint']==stamp else None
            effective=copy.deepcopy(override['result'] if override else automatic)
            if evaluation:effective['use']='evaluation'
            f={'file_id':source['id'],'filename':source['filename'],'sha256':source['sha256'],
               'kind':source.get('kind','unsupported'),'source_status':d['status'],'parser_version':parser,
               'flags':flags,'source_fingerprint':stamp,'automatic':automatic,'effective':effective,'override':override}
            if before and before.get('override') and not override:
                store.db.execute('INSERT INTO corpus_reviews(notice_id,file_id,fingerprint,action,payload,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (notice_id,source['id'],old['fingerprint'],'source_changed',encode(before),'local-rule','',kst_now().isoformat()))
            if not row['latest_revision'] and row['state']!='processing':
                source['role']='attachment' if effective['role']=='attachment' else 'convert'
            files.append(f)
        data={'notice_id':notice_id,'mobile_id':mid,'files':files,'use':summarise([f['effective'] for f in files])['use']}
        _seal(data)
        if not old or old['fingerprint']!=data['fingerprint']:
            _persist(store,data)
        if not row['latest_revision'] and row['state']!='processing':
            store.db.execute('UPDATE mobile_notices SET files=? WHERE id=?',(encode(source_files),mid))
        store.db.commit()
        return data
    except Exception:
        store.db.rollback();raise


def decide_file(store, notice_id, file_id, fingerprint, role, use, reviewer, note='', privacy_checked=False):
    from . import mobile
    if role not in ('convert','attachment') or use not in USES or not reviewer.strip() or len(note)>2000:
        raise ValueError('파일 역할·분류·검수자·메모를 확인하세요.')
    if role=='attachment' and use=='candidate' or role=='convert' and use=='attachment':
        raise ValueError('활용 후보는 안내 변환 파일이어야 합니다.')
    store.db.execute('BEGIN IMMEDIATE')
    try:
        data=get_record(store,notice_id)
        if not data or data['fingerprint']!=fingerprint:raise ValueError('분류가 변경됐습니다. 최신 화면을 여세요.')
        file=next((f for f in data['files'] if f['file_id']==file_id),None)
        if not file:raise ValueError('파일이 없습니다.')
        if use in ('candidate','attachment') and not store.object_path(file['sha256']).is_file():
            raise ValueError('실제 원본 파일이 없습니다. 먼저 복구 여부를 확인하세요.')
        if (file['flags']['evaluation'] or _evaluation(store,notice_id)) and use!='evaluation':raise ValueError('평가 자료는 활용 후보로 변경할 수 없습니다.')
        if use=='candidate' and (file['source_status']!='extracted' or not file['flags']['parser_current'] or file['flags']['incomplete'] or file['kind'] not in ('html','pdf','hwp','hwpx','docx')):
            raise ValueError('최신 파서·전체 원문을 확인한 뒤 후보로 지정하세요.')
        row=mobile.notice_row(store,data['mobile_id'])
        if row['state']=='processing':raise ValueError('변환 중입니다. 완료 후 다시 분류하세요.')
        source_files=json.loads(row['files'])
        source=next(f for f in source_files if f['id']==file_id)
        old_role=source['role']
        result={'role':role,'use':use,'content_kind':file['automatic']['content_kind'],
                'reasons':['operator_decision']+(['rights_unreviewed'] if use=='candidate' else [])}
        # Classification acknowledgement is not content approval. Privacy and
        # boundary flags must still be cleared by a separate source comparison.
        if use=='candidate' and file['flags']['privacy_flag'] and not (privacy_checked and note.strip()):
            raise ValueError('원문 개인정보를 확인하고 확인 결과를 메모에 남기세요.')
        if use=='candidate' and file['automatic']['content_kind']=='mixed' and not source.get('boundary_confirmed'):
            raise ValueError('안내·서식 경계를 먼저 확인하세요.')
        file['override']={'result':result,'reviewer':reviewer.strip()[:80],'note':note,
                          'privacy_checked':bool(privacy_checked),'at':kst_now().isoformat()}
        file['effective']=result
        data['use']=summarise([f['effective'] for f in data['files']])['use']
        old_fingerprint=data['fingerprint']
        _persist(store,data)
        store.db.execute('INSERT INTO corpus_reviews(notice_id,file_id,fingerprint,action,payload,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (notice_id,file_id,old_fingerprint,'operator_decision',encode({**result,'privacy_checked':bool(privacy_checked)}),reviewer.strip()[:80],note,kst_now().isoformat()))
        source['role']=role
        if use=='evaluation':store.db.execute("UPDATE mobile_notices SET split='eval' WHERE id=?",(data['mobile_id'],))
        if use=='evaluation':
            aliases=store.db.execute("SELECT DISTINCT c.notice_id FROM corpus_notices c,json_each(c.files) j WHERE json_extract(j.value,'$.sha256')=? AND c.notice_id!=?",(file['sha256'],notice_id)).fetchall()
            for alias in aliases:
                store.db.execute("INSERT INTO jobs(kind,ref_id,status,updated) VALUES('curate',?,'pending',?) ON CONFLICT(kind,ref_id) DO UPDATE SET status='pending',attempts=0,next_attempt=NULL,updated=excluded.updated WHERE jobs.status IN ('done','error')",(alias[0],kst_now().isoformat()))
        store.db.execute('UPDATE mobile_notices SET files=? WHERE id=?',(encode(source_files),data['mobile_id']))
        # Role changes create a queued new candidate, never modify old human HTML.
        if old_role!=role and row['latest_revision']:
            store.db.execute("UPDATE mobile_notices SET approved_revision=NULL,state='candidate' WHERE id=?",(row['id'],))
        store.db.commit()
        if old_role!=role and row['latest_revision']:
            edits={f['id']:{k:f.get(k) for k in ('role','scope','form_start','boundary_confirmed','incomplete')} for f in source_files}
            mobile.queue_conversion(store,row['id'],row['latest_revision'],edits,row['title'])
        mobile.export_mobile(store)
        from .learn import export_learning
        export_learning(store)
        return get_record(store,notice_id)
    except Exception:
        store.db.rollback();raise


def case_allowed(store, row, table_candidate=False):
    """Existing approval stays archived; excluded sources no longer feed reuse."""
    if row['split']=='eval':return False
    data=get_record(store,row['notice_id'])
    if not data:return False  # preserve approval history, wait for source curation
    if _evaluation(store,row['notice_id']) or data['use']=='excluded':return False
    payload=json.loads(row['payload'])
    source=payload.get('source','body')
    matching=[f for f in data['files'] if f['file_id']==source or f['sha256']==source]
    def allowed(file):
        result=file['effective']
        if _file_evaluation(store,file['sha256']):return False
        # Legacy case partitions may be revised by the existing parser/review
        # workflow. Current case splits and permanent operator eval decisions
        # are authoritative, not a stale automatic curation snapshot.
        if file['flags']['evaluation']:
            file=copy.deepcopy(file)
            doc=store.notice(row['notice_id'])
            original=next(f for f in json.loads(store.db.execute('SELECT files FROM mobile_notices WHERE id=?',(data['mobile_id'],)).fetchone()[0]) if f['id']==file['file_id'])
            descriptor,_=_descriptor(store,doc,original,False)
            result=classify_file(descriptor)
            file['flags']['evaluation']=False
        if result['role']=='convert' and result['use']=='candidate':return True
        # A short, already human-approved table can be a useful structure case
        # even when it is insufficient as a complete mobile guide. Never use
        # this exception for an override, missing text, stale parser or PII.
        flags=file['flags']
        return bool((row['approved'] and row['review_status']=='approved' or table_candidate) and not file.get('override')
                    and result['use']=='held' and result['reasons']==['insufficient_text']
                    and file['source_status']=='extracted' and flags['parser_current']
                    and not (flags['incomplete'] or flags['privacy_flag'] or flags['evaluation']))
    return bool(matching) and all(allowed(f) for f in matching)


def mobile_allowed(store, row):
    files=json.loads(store.db.execute('SELECT files FROM mobile_notices WHERE id=?',(row['id'],)).fetchone()[0])
    hashes={f['sha256'] for f in files}
    if any(_file_evaluation(store,sha) for sha in hashes):return False
    if not row['origin_notice_id']:
        for r in store.db.execute("SELECT notice_id,files FROM corpus_notices WHERE use='evaluation'"):
            if hashes & {f['sha256'] for f in json.loads(r['files'])} and _evaluation(store,r['notice_id']):return False
        return True
    data=get_record(store,row['origin_notice_id'])
    if not data:return False
    return data['use']=='candidate'


def stats(store):
    uses={r[0]:r[1] for r in store.db.execute('SELECT use,count(*) FROM corpus_notices GROUP BY use')}
    files={r[0]:r[1] for r in store.db.execute("SELECT json_extract(j.value,'$.effective.use'),count(*) FROM corpus_notices c,json_each(c.files) j GROUP BY 1")}
    return {'notices':store.db.execute('SELECT count(*) FROM notices').fetchone()[0],
            'classified':sum(uses.values()),'uses':uses,'files':files,
            'approved_eligible':sum(mobile_allowed(store,r) for r in store.db.execute("SELECT n.* FROM corpus_notices c JOIN mobile_notices n ON n.id=c.mobile_id WHERE c.use='candidate' AND n.state='approved' AND n.approved_revision=n.latest_revision AND n.split!='eval'")),
            'jobs':{r[0]:r[1] for r in store.db.execute("SELECT status,count(*) FROM jobs WHERE kind='curate' GROUP BY status")}}


def enqueue_missing(store):
    ids=[r[0] for r in store.db.execute('SELECT n.id FROM notices n LEFT JOIN corpus_notices c ON c.notice_id=n.id WHERE c.notice_id IS NULL OR c.version!=?',(VERSION,))]
    changed=0
    for ident in ids:
        cur=store.db.execute("INSERT INTO jobs(kind,ref_id,status,updated) VALUES('curate',?,'pending',?) ON CONFLICT(kind,ref_id) DO UPDATE SET status='pending',attempts=0,next_attempt=NULL,updated=excluded.updated WHERE jobs.status IN ('done','error')",(ident,kst_now().isoformat()))
        changed+=cur.rowcount
    store.db.commit()
    return changed


def drain(store, limit=2):
    outcomes=[]
    # Cross-process atomic claims, unlike a select/update without a write lock.
    for _ in range(limit):
        store.db.execute('BEGIN IMMEDIATE')
        jobs=store.claim_jobs('curate',1,'local-corpus')
        if not jobs:break
        job=jobs[0]
        try:
            classify_notice(store,job['ref_id'])
            store.finish_job(job['id']);outcomes.append('done')
        except Exception as exc:
            store.db.rollback()
            store.fail_job(job['id'],type(exc).__name__,max_attempts=1);outcomes.append('error')
    return outcomes


def requeue_stale(store):
    cutoff=(kst_now()-timedelta(minutes=2)).isoformat()
    store.db.execute("UPDATE jobs SET status='pending',owner=NULL WHERE kind='curate' AND status='running' AND updated<?",(cutoff,))
    store.db.commit()
