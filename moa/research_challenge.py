"""Held-out synthetic structures, independent operation gold, raw-only scoring.

Neither a checker nor a model is imported by the authoring/oracle functions.
The private author seed and gold must remain on the evaluator, not the worker.
These eight authored families are not eight validated real-school templates.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter

from .r2_data import fact_state,metrics
from .worker_contract import canonical
from .worker_handoff import questions

VERSION='moa.synthetic-structure-challenge.v1'
OPERATIONS=('identity','reorder','whitespace','context_reorder','date','time','amount',
            'selection','conditions','table_relations','omission','addition','unit','footnote',
            'missing_source','ambiguous_scope')
FAMILIES=('long_list','grade_schedule','merged_header','cross_row','repeated_triples',
          'exceptions','unit_footnote','multi_session')


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def _source(seed,family):
    code=digest({'seed':seed,'family':family})[:8]
    headers=['항목','참여반','활동실'];n=10 if family=='long_list' else 6
    rows=[[f'활동{i+1}',f'{i%3+1}학년',f'탐구실{i+1}'] for i in range(n)]
    if family in ('grade_schedule','multi_session'):
        headers=['회차','대상','일정','장소']
        rows=[[f'{i+1}회',f'{i%3+1}학년',f'2027년 11월 {i+3}일 15:30',f'교실{i+1}'] for i in range(n)]
    if family in ('merged_header','unit_footnote'):
        headers=['과정','오전 참여','오후 참여','재료비']
        rows=[[f'탐구{i+1}',f'{i+2}명',f'{i+3}명',f'{(i+1)*1000}원'] for i in range(n)]
    if family=='cross_row':
        rows=[[f'팀{i+1}',f'연결팀{(i+1)%n+1}',f'순환실{i+1}'] for i in range(n)]
        headers=['팀','공동 팀','모임실']
    if family=='repeated_triples':
        headers=['연번','학과','수업교실']*2
        rows=[[str(i+1),f'연구학과{i+1}',f'실습실{i+1}',str(i+5),f'연구학과{i+5}',f'실습실{i+5}'] for i in range(4)]
    source={'caption':f'별빛학교 {code} 체험 안내','headers':headers,'rows':rows,
        'unit':'참가비: 원, 인원: 명','conditions':['신청 마감: 2027년 11월 21일',
            '활동 시작: 15:30','참가 금액: 12000원','반드시 2개 선택','전학 예정자는 신청 대상에서 제외합니다.'],
        'notes':['준비물은 개인별 지참합니다.','비가 오면 실내 활동으로 변경합니다.'],
        'facts':['신청은 학교 안내 페이지에서 합니다.']}
    if family in ('merged_header','unit_footnote'):source['geometry']=[[0,1,1,2]]
    if family=='exceptions':source['notes']+=['알레르기가 있으면 참가 전 담당 교사에게 알려야 합니다.']
    return source


def _semantic(facts):
    """Author's explicit equivalence: keyed rows, set-like independent clauses.

    Not a production comparison or learned rule. Cell associations, duplicates,
    headers, units and merge semantics remain part of the author's truth.
    """
    result=copy.deepcopy(facts)
    clean=lambda s:re.sub(r'\s+','',s)
    if result['headers']==['연번','학과','수업교실']*2:
        result['headers']=result['headers'][:3]
        result['rows']=[row[o:o+3] for row in result['rows'] for o in (0,3)]
    for k,v in list(result.items()):
        if isinstance(v,str):result[k]=clean(v)
        elif k=='rows':result[k]=sorted([[clean(x) for x in row] for row in v])
        elif k in ('conditions','notes','facts'):result[k]=sorted(clean(x) for x in v)
        elif k=='headers':result[k]=[clean(x) for x in v]
    return result


def _variant(source,op):
    target=copy.deepcopy(source)
    if source['headers']==['연번','학과','수업교실']*2:
        target['headers']=source['headers'][:3]
        target['rows']=[row[o:o+3] for row in source['rows'] for o in (0,3)]
    if op=='reorder':target['rows'].reverse()
    elif op=='whitespace':
        target['conditions']=['  '+x+'  ' for x in target['conditions']]
    elif op=='context_reorder':
        target['conditions'].reverse();target['notes'].reverse()
    elif op in ('date','time','amount','selection'):
        i,old,new={'date':(0,'21일','22일'),'time':(1,'15:30','16:30'),
            'amount':(2,'12000원','21000원'),'selection':(3,'2개','3개')}[op]
        target['conditions'][i]=target['conditions'][i].replace(old,new)
    elif op=='conditions':target['conditions'][-1]='전학 예정자도 신청할 수 있습니다.'
    elif op=='table_relations':target['rows'][0][-1],target['rows'][1][-1]=target['rows'][1][-1],target['rows'][0][-1]
    elif op=='omission':target['rows'].pop()
    elif op=='addition':target['rows'].append(copy.deepcopy(target['rows'][0]))
    elif op=='unit':target['unit']='참가비: 만원, 인원: 명'
    elif op=='footnote':target['notes'].pop()
    state=fact_state(source,target)
    complete=op not in ('missing_source','ambiguous_scope')
    if op=='missing_source':
        state['source_complete']=False;state['source_blocks'][0]['text']='{}'
    elif op=='ambiguous_scope':state['rendered_blocks'][0]['source_refs']=['unprovided_context']
    before,after=_semantic(source),_semantic(target)
    expected='insufficient' if not complete else 'preserved' if before==after else 'changed'
    criterion=('table_relations' if op in ('identity','reorder','table_relations') else
               'omission' if op in ('omission','footnote','whitespace','missing_source') else
               'addition' if op=='addition' else 'conditions')
    return state,criterion,expected,{'name':op,'complete':complete,'before':before,'after':after}


def generate(author_seed):
    if not isinstance(author_seed,str) or not author_seed:raise ValueError('private author seed required')
    out=[];q=questions()
    for family in FAMILIES:
        source=_source(author_seed,family);source_sha=digest(source)
        group=digest({'version':VERSION,'family':family,'source_sha256':source_sha})
        for op in OPERATIONS:
            state,key,gold,operation=_variant(source,op)
            out.append({'case_id':'c-'+digest({'source':source_sha,'operation':op})[:24],
                'state':state,'questions':{key:q[key]},'expected':{key:gold},
                'meta':{'split':'test','evaluation_only':True,'family':family,'original_group_id':group,
                    'source_sha256':source_sha,'authored_source':source,'operation':operation,
                    'error_type':op,'label_origin':'independent_source_and_recorded_operation',
                    'rights_evidence':'self_authored_synthetic_only','forbidden_lineage':[]}})
    return out


def validate(rows,protected_source_hashes):
    ids=set();payloads=set();counts=Counter();groups=set();errors=Counter()
    for row in rows:
        meta=row['meta'];source=meta['authored_source']
        if meta['source_sha256'] in protected_source_hashes:raise ValueError('protected evaluation/training source overlap')
        if (meta['split']!='test' or not meta['evaluation_only'] or
            meta['rights_evidence']!='self_authored_synthetic_only' or meta['forbidden_lineage'] or
            meta['source_sha256']!=digest(source)):raise ValueError('new test provenance')
        state,key,gold,operation=_variant(source,meta['error_type'])
        if (row['state']!=state or row['questions']!={key:questions()[key]} or
            row['expected']!={key:gold} or meta['operation']!=operation):raise ValueError('independent oracle replay mismatch')
        payload=digest({'state':state,'questions':row['questions']})
        if row['case_id'] in ids or payload in payloads:raise ValueError('duplicate challenge input')
        ids.add(row['case_id']);payloads.add(payload);groups.add(meta['original_group_id']);counts[gold]+=1;errors[meta['error_type']]+=1
    return {'version':VERSION,'items':len(rows),'groups':len(groups),'labels':dict(counts),'errors':dict(errors),
            'gold_sha256':digest(rows),'inputs_sha256':digest(sorted(payloads)),'human_gold':0,'real_document_accuracy':None}


def project(rows,freeze):
    if not freeze or not freeze.get('candidate_sha256') or any(
        not re.fullmatch('[0-9a-f]{64}',str(x)) for x in [*freeze['candidate_sha256'].values(),
             freeze.get('settings_sha256'),freeze.get('checker_sha256')]):raise ValueError('candidate/checker/settings freeze required')
    return [{k:copy.deepcopy(row[k]) for k in ('case_id','state','questions')} for row in rows]


def combine(direct,model):
    if direct in ('changed','preserved'):return direct
    return 'changed' if model=='changed' else 'insufficient'


def model_review_reason(direct,model,model_status='completed'):
    """Quality-label priority does not erase a disagreement's review burden."""
    if model_status!='completed':return model_status
    if model not in ('preserved','changed','insufficient'):return 'incomplete'
    if model!=direct:return 'model_direct_conflict'
    return None


def evaluate(rows,predictions,checker):
    indexed={p['case_id']:p for p in predictions}
    if len(indexed)!=len(predictions) or set(indexed)-{r['case_id'] for r in rows}:raise ValueError('prediction identity')
    model=[];direct=[];combined=[];additional=extra_reviews=combined_reviews=0
    for row in rows:
        p=indexed.get(row['case_id'])
        if p and (p.get('prediction_origin')!='raw_laya' or p.get('system_forced_insufficient')):raise ValueError('raw model required')
        label=p.get('prediction') if p else None
        if label not in ('preserved','changed','insufficient',None):raise ValueError('raw label')
        d=checker(row['state'])['quality_label'];gold=next(iter(row['expected'].values()))
        base={'case_id':row['case_id'],'group_id':row['meta']['original_group_id'],
              'error':row['meta']['error_type'],'expected':gold}
        model.append(dict(base,prediction=label));direct.append(dict(base,prediction=d));combined.append(dict(base,prediction=combine(d,label)))
        additional+=gold=='changed' and d!='changed' and label=='changed'
        extra_reviews+=gold=='preserved' and d=='preserved' and label in ('changed','insufficient')
        combined_reviews+=gold=='preserved' and d=='preserved' and bool(
            model_review_reason(d,label,p.get('status','completed') if p else 'missing'))
    return {'model':metrics(model),'direct':metrics(direct),'combined':metrics(combined),
            'additional_errors_caught':additional,'model_only_extra_normal_alerts':extra_reviews,
            'extra_reviews_after_direct_identity_priority':combined_reviews,
            'overflow_items':sum(p.get('status')=='input_overflow' for p in predictions),
            'system_forced_answers_counted_as_model':0,'raw_records':model,'direct_records':direct,'combined_records':combined}
