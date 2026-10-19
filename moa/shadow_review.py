"""Notice-only scoped service adapter. No persistence, approval or publication.

Callers supply independently extracted source / actual DOM facts, not content
repaired from a previous case. Human review remains bound to its own revision.
"""
from __future__ import annotations

import copy
import hashlib

from .scoped_verification import compare_scoped_facts
from .research_challenge import combine,model_review_reason
from .worker_contract import canonical
from .r2_data import fact_state

VERSION='moa.scoped-shadow-review.v1'


def native_dom_state(source,dom,*,source_complete):
    """Original parser rows versus observed DOM, with no source-to-DOM repair.

    A rectangular first row is merely a table boundary here. Semantic repeated
    columns still require the scoped checker's explicit header proof. Ragged
    tables are retained as text evidence but cannot produce a quality pass.
    """
    complete=bool(source_complete)
    def facts(block,left):
        nonlocal complete
        rows=block.get('original_table_rows',block.get('table_rows')) if left else block.get('table_rows')
        if rows is None:return {'facts':[block.get('text' if left else 'visible_text','')]}
        valid=(isinstance(rows,list) and len(rows)>=2 and isinstance(rows[0],list) and
               bool(rows[0]) and all(isinstance(row,list) and len(row)==len(rows[0]) and
                                    all(isinstance(c,str) for c in row) for row in rows))
        if not valid:
            complete=False
            return {'facts':[block.get('text' if left else 'visible_text','')]}
        result={'headers':copy.deepcopy(rows[0]),'rows':copy.deepcopy(rows[1:]),
            'caption':block.get('original_caption',block.get('caption','')) if left else block.get('caption',''),
            'notes':[block.get('note','')]}
        if block.get('table_geometry'):result['geometry']=copy.deepcopy(block['table_geometry'])
        return result
    left,right=facts(source,True),facts(dom,False)
    state=fact_state(left,right);state['source_complete']=complete
    state['file_role']=source.get('role') or 'unknown'
    return state


def assess(state,model_report,*,source_revision,render_revision,browser_ok,
           role_verified=False,new_type=False):
    role=state.get('file_role');model=copy.deepcopy(model_report or {})
    model_status='completed'
    if (model.get('prediction_origin')!='raw_laya' or model.get('system_forced_insufficient')):
        model_status='not_raw_model'
    elif model.get('source_revision')!=source_revision or model.get('render_revision')!=render_revision:
        model_status='stale_revision'
    elif model.get('status')!='completed' or model.get('prediction') not in ('preserved','changed','insufficient'):
        model_status='incomplete'
    direct=compare_scoped_facts(state) if role=='notice' else {
        'quality_label':'insufficient','findings':['not_a_notice'],'mode':'shadow_only'}
    reasons=[]
    if new_type:reasons.append('new_type')
    model_reason=model_review_reason(direct['quality_label'],model.get('prediction'),model_status)
    if model_reason=='model_direct_conflict':reasons.append(model_reason)
    if direct['quality_label']=='changed':reasons.append('direct_mismatch')
    if direct['quality_label']=='insufficient':reasons.append('insufficient_evidence')
    if not role_verified:reasons.append('file_role_unverified')
    if not browser_ok:reasons.append('browser_not_verified')
    if model_status!='completed':reasons.append(model_status)
    final=combine(direct['quality_label'],model.get('prediction'))
    if role!='notice' or not role_verified or not browser_ok or model_status!='completed':final='insufficient'
    return {'version':VERSION,'source_revision':source_revision,'render_revision':render_revision,
        'source_sha256':hashlib.sha256(canonical(state.get('source_blocks'))).hexdigest(),
        'dom_sha256':hashlib.sha256(canonical(state.get('rendered_blocks'))).hexdigest(),
        'direct':direct,'model':model,'model_status':model_status,'model_accuracy_eligible':model_status=='completed',
        'final':{'quality_label':final,'mode':'shadow_only'},'priority_reasons':reasons,
        'file_action':'mobile_notice_candidate' if role=='notice' else 'original_attachment_only',
        'human_review_required':bool(reasons),'human_approved':False,'publish_allowed':False,
        'operating_model_changed':False,'whole_original_accuracy':None}
