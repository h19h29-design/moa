"""Synthetic-only persistence and exclusion regression tests."""
import json
import pytest
from moa.core import Store, write_json
from moa.extract import PARSER_VERSION
from moa import corpus, mobile
from moa.learn import learn, review, search_cases, export_learning
from test_moa import school

GUIDE='<!doctype html><html><body><h1>행사 안내</h1><p>대상은 2학년이며 신청 마감까지 준비물을 확인하고 참여해 주세요.</p><table><tr><th>대상</th><th>금액</th></tr><tr><td>2학년</td><td>12,000원</td></tr></table></body></html>'

def notice(store, name='신청서.txt', data=b'original form', incomplete=False):
    asset=store.object(data,name)
    ident,_=store.save_notice(school(),'https://example.org/notice','합성 행사 안내',GUIDE,[asset], '2026-09-30',failed_assets=[{'reason':'synthetic'}] if incomplete else None)
    return ident,asset

def test_existing_notice_is_classified_without_collection_inflation_or_approval(tmp_path):
    with Store(tmp_path) as s:
        ident,asset=notice(s)
        r=corpus.classify_notice(s,ident)
        assert r['use']=='candidate'
        assert len(r['files'])==2
        assert r['files'][0]['effective']['use']=='candidate'
        assert r['files'][1]['effective']['use']=='attachment'
        assert s.db.execute('SELECT count(*) FROM notices').fetchone()[0]==1
        assert corpus.stats(s)['classified']==1
        assert corpus.stats(s)['approved_eligible']==0
        assert s.object_path(asset['sha256']).read_bytes()==b'original form'

def test_auto_reclassification_preserves_human_exclusion_and_history(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s)
        r=corpus.classify_notice(s,ident)
        corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','excluded','합성 검수자','권리 확인 필요')
        newer=corpus.classify_notice(s,ident)
        assert newer['use']=='excluded'
        assert newer['files'][0]['effective']['use']=='excluded'
        assert len(s.db.execute('SELECT * FROM corpus_reviews').fetchall())==1
        assert newer['files'][0]['override']['reviewer']=='합성 검수자'

def test_stale_decision_cannot_overwrite_newer_human_decision(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s); r=corpus.classify_notice(s,ident)
        corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','held','검수자')
        with pytest.raises(ValueError):
            corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','candidate','검수자')

def test_explicit_exclusion_removes_approved_case_from_search_and_export(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s); learn(s,ident)
        s.db.execute("UPDATE cases SET split='train' WHERE notice_id=?",(ident,));s.db.commit()
        r=corpus.classify_notice(s,ident)
        row=s.db.execute('SELECT * FROM cases WHERE notice_id=?',(ident,)).fetchone()
        review(s,row['id'],'approved',layout='grade_cards',reviewer='합성 검수자',rights_reviewed=True,privacy_reviewed=True)
        assert search_cases(s,'12,000')
        corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','excluded','합성 검수자')
        assert search_cases(s,'12,000')==[]
        export_learning(s)
        assert (tmp_path/'learning/approved.jsonl').read_text()==''
        assert s.db.execute('SELECT approved FROM cases WHERE id=?',(row['id'],)).fetchone()[0]==1


def test_excluded_unreviewed_source_is_not_exported_as_a_learning_candidate(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s);learn(s,ident)
        s.db.execute("UPDATE cases SET split='train' WHERE notice_id=?",(ident,));s.db.commit()
        r=corpus.classify_notice(s,ident)
        export_learning(s)
        assert (tmp_path/'learning/candidates.jsonl').read_text()
        corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','excluded','합성 검수자')
        assert (tmp_path/'learning/candidates.jsonl').read_text()==''


def test_missing_original_cannot_be_manually_relabelled_as_available(tmp_path):
    with Store(tmp_path) as s:
        ident,asset=notice(s)
        s.object_path(asset['sha256']).unlink()  # disposable synthetic fixture
        r=corpus.classify_notice(s,ident)
        assert r['use']=='held'
        with pytest.raises(ValueError):corpus.decide_file(s,ident,'0',r['fingerprint'],'attachment','attachment','검수자')


def test_unclassified_legacy_approval_is_archived_but_not_used_until_curation(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s);learn(s,ident)
        s.db.execute("UPDATE cases SET split='train' WHERE notice_id=?",(ident,));s.db.commit()
        row=s.db.execute('SELECT * FROM cases WHERE notice_id=?',(ident,)).fetchone()
        review(s,row['id'],'approved',layout='grade_cards',reviewer='합성 검수자',rights_reviewed=True,privacy_reviewed=True)
        s.db.execute('DELETE FROM corpus_notices WHERE notice_id=?',(ident,));s.db.commit()
        assert search_cases(s,'12,000')==[]
        assert s.db.execute('SELECT approved FROM cases WHERE id=?',(row['id'],)).fetchone()[0]==1
        corpus.classify_notice(s,ident)
        assert search_cases(s,'12,000')

def test_role_change_keeps_human_revision_and_invalidates_current_approval(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s); r=corpus.classify_notice(s,ident)
        mid=s.db.execute('SELECT id FROM mobile_notices WHERE origin_notice_id=?',(ident,)).fetchone()[0]
        rev=mobile.generate(s,mid)
        mobile.decide(s,mid,rev['id'],'approved','합성 검수자',compared=True,rights=True,privacy=True)
        r=corpus.classify_notice(s,ident)
        assert mobile.approved_rules(s)
        corpus.decide_file(s,ident,'body',r['fingerprint'],'attachment','attachment','합성 검수자')
        n=mobile.notice_row(s,mid)
        assert n['approved_revision'] is None
        assert mobile.approved_rules(s)=={}
        assert mobile.revision(s,mid,rev['id'])['data']==rev['data']
        assert s.db.execute('SELECT action FROM mobile_reviews').fetchone()[0]=='approved'

def test_inherited_evaluation_cannot_be_relabelled_for_training(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s); learn(s,ident)
        s.db.execute("UPDATE cases SET split='eval' WHERE notice_id=?",(ident,));s.db.commit()
        r=corpus.classify_notice(s,ident)
        assert r['use']=='evaluation'
        with pytest.raises(ValueError):corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','candidate','검수자')
        assert corpus.stats(s)['approved_eligible']==0

def test_stale_asset_parser_and_incomplete_capture_are_held(tmp_path):
    with Store(tmp_path) as s:
        ident,asset=notice(s,'안내.pdf',b'%PDF-1.4 synthetic',incomplete=True)
        write_json(tmp_path/'extracted'/f"{asset['sha256']}-moa-local-v3.json",{'status':'extracted','text':'일정과 신청 안내가 있는 과거 파서 추출 내용','tables':[],'parser_version':'moa-local-v3'})
        r=corpus.classify_notice(s,ident)
        assert r['use']=='held'
        assert 'parser_stale' in r['files'][1]['effective']['reasons']
        assert 'incomplete' in r['files'][0]['effective']['reasons']

def test_queue_restart_continues_and_does_not_approve(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s)
        assert corpus.enqueue_missing(s)==1
    with Store(tmp_path) as s:
        assert corpus.drain(s,1)==['done']
        assert corpus.stats(s)['classified']==1
        assert corpus.enqueue_missing(s)==0
        assert corpus.stats(s)['approved_eligible']==0

@pytest.mark.parametrize('role,use',[('invalid','candidate'),('convert','approved'),('attachment','candidate')])
def test_invalid_classification_cannot_be_submitted(tmp_path,role,use):
    with Store(tmp_path) as s:
        ident,_=notice(s);r=corpus.classify_notice(s,ident)
        with pytest.raises(ValueError):corpus.decide_file(s,ident,'body',r['fingerprint'],role,use,'검수자')


def test_excluded_approved_pattern_cannot_suggest_layout_for_next_notice(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s);learn(s,ident)
        s.db.execute("UPDATE cases SET split='train' WHERE notice_id=?",(ident,));s.db.commit()
        row=s.db.execute('SELECT * FROM cases WHERE notice_id=?',(ident,)).fetchone()
        review(s,row['id'],'approved',layout='timeline',reviewer='합성 검수자',rights_reviewed=True,privacy_reviewed=True)
        r=corpus.classify_notice(s,ident)
        corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','excluded','검수자')
        second,_=s.save_notice(school(),'https://example.org/new','다른 합성 안내',GUIDE.replace('12,000','88,000'),[],'2026-09-30')
        learn(s,second)
        new=s.db.execute('SELECT payload FROM cases WHERE notice_id=?',(second,)).fetchone()
        assert json.loads(new['payload'])['suggestion'].get('method')!='approved-pattern-suggestion'


def test_operator_evaluation_cannot_be_relabelled_into_examples(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s);r=corpus.classify_notice(s,ident)
        r=corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','evaluation','검수자')
        with pytest.raises(ValueError):corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','candidate','검수자')


def test_same_physical_evaluation_file_cannot_leak_via_another_notice(tmp_path):
    with Store(tmp_path) as s:
        first,_=notice(s);r=corpus.classify_notice(s,first)
        second,_=s.save_notice(school(),'https://example.org/alias','다른 합성 제목',GUIDE,[],'2026-09-30')
        alias=corpus.classify_notice(s,second)
        rev=mobile.generate(s,alias['mobile_id'])
        mobile.decide(s,alias['mobile_id'],rev['id'],'approved','합성 검수자',compared=True,rights=True,privacy=True)
        assert mobile.approved_rules(s)
        corpus.decide_file(s,first,'body',r['fingerprint'],'convert','evaluation','검수자')
        assert mobile.approved_rules(s)=={}
        assert corpus.classify_notice(s,second)['use']=='evaluation'


def test_evaluation_original_cannot_leak_between_uploads_and_collected_records(tmp_path):
    with Store(tmp_path) as s:
        ident,_=notice(s);learn(s,ident)
        s.db.execute("UPDATE cases SET split='eval' WHERE notice_id=?",(ident,));s.db.commit()
        corpus.classify_notice(s,ident)
        upload=mobile.create_upload(s,[('합성 안내.html',GUIDE.encode())])
        rev=mobile.generate(s,upload)
        mobile.decide(s,upload,rev['id'],'approved','합성 검수자',compared=True,rights=True,privacy=True)
        assert mobile.approved_rules(s)=={}
        # Explicit evaluation uploads also reserve identical collected files.
        other,_=s.save_notice(school(),'https://example.org/evalupload','다른 안내',GUIDE.replace('12,000','13,000'),[],'2026-09-30')
        mobile.create_upload(s,[('평가 안내.html',GUIDE.replace('12,000','13,000').encode())],evaluation=True)
        assert corpus.classify_notice(s,other)['use']=='evaluation'


def test_human_classification_privacy_acknowledgement_is_audited_not_approval(tmp_path):
    with Store(tmp_path) as s:
        ident,_=s.save_notice(school(),'https://example.org/privacy','합성 안내',GUIDE.replace('대상은','문의 synthetic@example.org 대상은'),[],'2026-09-30')
        r=corpus.classify_notice(s,ident)
        assert r['use']=='held'
        with pytest.raises(ValueError):corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','candidate','검수자')
        with pytest.raises(ValueError):corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','candidate','검수자',privacy_checked=True)
        r=corpus.decide_file(s,ident,'body',r['fingerprint'],'convert','candidate','검수자','합성 공개 문의처 대조',privacy_checked=True)
        assert r['use']=='candidate' and r['files'][0]['override']['privacy_checked']
        assert corpus.stats(s)['approved_eligible']==0


@pytest.mark.parametrize('body,incomplete',[
    ('<table><tr><td>장소</td><td>운동장</td></tr></table>',True),
    ('<table><tr><td>문의</td><td>synthetic@example.org</td></tr></table>',False),
])
def test_legacy_human_table_approval_cannot_bypass_strong_hold_gates(tmp_path,body,incomplete):
    with Store(tmp_path) as s:
        ident,_=s.save_notice(school(),'https://example.org/hold','합성 안내',body,[],'2026-09-30',failed_assets=[{'reason':'synthetic'}] if incomplete else None)
        learn(s,ident)
        s.db.execute("UPDATE cases SET split='train' WHERE notice_id=?",(ident,));s.db.commit()
        row=s.db.execute('SELECT * FROM cases WHERE notice_id=?',(ident,)).fetchone()
        review(s,row['id'],'approved',layout='key_value_cards',reviewer='검수자',rights_reviewed=True,privacy_reviewed=True)
        assert not corpus.case_allowed(s,s.db.execute('SELECT * FROM cases WHERE id=?',(row['id'],)).fetchone())
