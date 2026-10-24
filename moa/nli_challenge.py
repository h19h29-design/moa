"""New synthetic source/template families; labels from authored operations only.

No school originals, checker predictions, LAYA answers or NLI outputs are
training/evaluation gold. Generation is separate from scoring and is never
called on held-out families before the evaluator freezes its configuration.
"""
from __future__ import annotations

import copy
import hashlib
import re

from .r2_data import fact_state
from .worker_contract import canonical
from .worker_handoff import questions

VERSION='moa.nli-challenge.v1'
NORMAL=('identity','independent_reorder','row_reorder','semantic_paraphrase')
CHANGED=('date','time','amount','selection','condition','table_relations','omission','duplicate','unit','footnote')
MISSING=('missing_pages','missing_context','missing_header','role_unverified')
FAMILIES={'unit':('unit_craft',),
          'dev':('dev_badge','dev_garden','dev_recycling'),
          'test':('drill_routes','locker_returns','music_rehearsal','club_subsidy','exam_rooms','uniform_lending',
                  'reading_log_long','tool_inventory_long'),
          'independent':('archive_visits','research_subsidy','meal_allergy','laboratory_passes_long')}


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def authored(seed,family):
    if family in FAMILIES['independent']:
        # These are separately authored facts, not the same date/amount/
        # conditions hidden behind a new parent hash. Do not use model/checker
        # predictions to choose them. Actual paired-input overlap is audited
        # separately before an evaluation is dispatched.
        definitions={
          'archive_visits':(['관람 묶음','관람 주제','해설 시작점'],
            [['오전반','지역 지도','전시동 입구'],['오후반','생활 도구','자료동 로비'],['저녁반','마을 사진','영상실 앞']],
            '관람비: 원 / 관람 주제 선택: 개',
            ['예약 마감: 2031년 3월 14일','해설 출발: 10:25','관람비: 6500원','관람 주제를 반드시 3개 선택',
             '비가 오면 야외 해설을 기록관 안으로 변경합니다.'],
            ['관람 확인서는 첨부 원본을 인쇄하여 기록관 안내 창구에 냅니다.'],
            ['단체 예약 취소는 방문 전날 정오까지 알려야 합니다.','음식물은 전시 구역으로 가져갈 수 없습니다.']),
          'research_subsidy':(['연구 분야','구매 허용 범위','필요 증빙','심사 창구'],
            [['토양','배양 재료','구매 명세서','연구동 서쪽'],['빛','차광 도구','실험 일지','연구동 동쪽'],['소리','측정 부품','물품 사진','연구동 중앙']],
            '지원 한도: 원 / 연구 분야 선택: 개',
            ['지원 접수 종료: 2032년 8월 17일','심사 시작: 15:35','지원 한도: 9200원','연구 분야를 반드시 4개 선택',
             '비가 오면 야외 시연을 실험동 강당으로 변경합니다.'],
            ['지급 요청서는 원본 첨부 서식에 서명한 뒤 연구지원실로 제출합니다.'],
            ['실험 종료 후 남은 재료의 수량을 별도로 보고해야 합니다.','증빙이 없는 구입 항목에는 지원금을 지급하지 않습니다.']),
          'meal_allergy':(['식단 구역','제외 재료','대체 음식','배식 창구'],
            [['가구역','우유','두유','분홍 창구'],['나구역','달걀','두부','보라 창구'],['다구역','밀','쌀면','주황 창구']],
            '추가 식비: 원 / 시식 메뉴 선택: 개',
            ['식단 변경 마감: 2033년 11월 22일','시식 시작: 11:55','추가 식비: 3700원','시식 메뉴를 반드시 5개 선택',
             '비가 오면 야외 시식 장소를 급식동 식당으로 변경합니다.'],
            ['알레르기 확인표는 첨부된 원본을 작성하여 영양상담실로 가져옵니다.'],
            ['대체 음식에도 알레르기 재료가 있는지 배식 전에 확인합니다.','식단 변경 사실을 보호자에게 별도로 알립니다.']),
          'laboratory_passes_long':(['안전증 번호','이수 실험','검사 담당실','보호구 보관대'],
            [[str(i+101),'안전실험 '+str(i+1)+'단계','검사실'+str(i%6+11),'보관대'+str(i%7+21)] for i in range(36)],
            '안전증 발급비: 원 / 실험 묶음 선택: 개',
            ['발급 접수 끝: 2034년 2월 12일','안전 점검 시작: 08:45','안전증 발급비: 5600원','실험 묶음을 반드시 6개 선택',
             '비가 오면 외부 안전 점검을 과학동 복도로 변경합니다.'],
            ['실험 동의서는 첨부 원본에 보호자가 서명하여 안전관리실로 보냅니다.'],
            ['보호구가 파손되면 실험 전에 새 보호구로 교환합니다.','안전증 번호와 검사 담당실의 연결을 현장에서 확인합니다.'])}
        h,rows,unit,conditions,facts,notes=definitions[family]
        return {'caption':family+' · 독립 저작 '+digest({'seed':seed,'family':family})[:12],
                'headers':h,'rows':rows,'unit':unit,'conditions':conditions,'facts':facts,'notes':notes}
    layouts={
      'drill_routes':(['대피 구역','대상 반','집결 장소'],[['북계단','1학년','안전지대 가'],['남계단','2학년','안전지대 나'],['중앙계단','3학년','안전지대 다']]),
      'locker_returns':(['물품 구분','반납일','보증금'],[['열쇠','6월 21일','2000원'],['출입카드','6월 22일','4000원'],['보관함','6월 23일','6000원']]),
      'music_rehearsal':(['악기','연습 요일','지도 그룹'],[['바이올린','월요일','현악팀'],['클라리넷','화요일','관악팀'],['팀파니','목요일','타악팀']]),
      'club_subsidy':(['지원 유형','지원 조건','환급액','원본 제출자료'],[['교통','왕복 이용','3000원','승차권'],['재료','제작 완료','5000원','영수증'],['대여','물품 반납','7000원','대여증']]),
      'exam_rooms':(['대상 학년','시작 시각','입실 장소','퇴실 장소'],[['1학년','09:10','가관101','중앙문'],['2학년','09:30','나관202','남문'],['3학년','09:50','다관303','북문']]),
      'uniform_lending':(['구분','대여 크기','대여 수량','반납함'],[['여름복','소','2벌','노랑함'],['겨울복','중','3벌','파랑함'],['체육복','대','1벌','초록함']]),
      'reading_log_long':(['인증 단계','인증 활동','검사 창구'],[[str(i+1),'독서기록 '+str(i+1)+'회 작성','독서창구'+str(i%4+1)] for i in range(32)]),
      'tool_inventory_long':(['장비 번호','교육 대상','대여실','사용 시간'],[[str(i+1),'실습'+str(i+1)+'조','준비실'+str(i%5+1),str(15+i%5*5)+'분'] for i in range(40)]),
      'dev_badge':(['명찰 종류','발급 대상','수령 장소'],[['노랑','신입','창구1'],['초록','재학','창구2'],['파랑','졸업예정','창구3']]),
      'dev_garden':(['구역','심을 작물','필요 도구'],[['동쪽','상추','호미'],['서쪽','감자','삽'],['중앙','고추','물뿌리개']]),
      'dev_recycling':(['분류','수거 요일','배출 장소'],[['종이','화요일','가함'],['플라스틱','수요일','나함'],['캔','금요일','다함']])}
    headers,rows=layouts.get(family,(['재료','수량','보관함'],[['색종이','2장','가함'],['나무판','3개','나함'],['끈','1개','다함']]))
    weather={'drill_routes':'비가 오면 집결 장소를 체육관으로 변경합니다.',
             'locker_returns':'비가 오면 반납 장소를 체육관으로 변경합니다.'}.get(family,
             '비가 오면 활동 장소를 체육관으로 변경합니다.')
    return {'caption':family+' · 독립 저작 '+digest({'seed':seed,'family':family})[:12],
            'headers':headers,'rows':rows,'unit':'금액: 원 / 선택: 개',
            'conditions':['접수 기한: 2029년 6월 20일','교육 시작: 13:40','참가 비용: 4800원','반드시 2개 선택',weather],
            'facts':['신청서는 원본 첨부를 내려받아 담당 부서에 제출합니다.'],
            'notes':['기한을 넘긴 신청은 받지 않습니다.','분실한 물품이 있으면 담당 교사에게 알립니다.']}


def variant(source,op):
    observed,dom=copy.deepcopy(source),copy.deepcopy(source)
    if op=='independent_reorder':dom['conditions']=dom['conditions'][2:]+dom['conditions'][:2]
    elif op=='row_reorder':dom['rows'].reverse()
    elif op=='semantic_paraphrase':dom['conditions'][-1]=dom['conditions'][-1].replace('비가 오면','우천 시').replace('변경합니다.','바꿉니다.')
    elif op in ('date','time','amount','selection'):
        i,a,b={'date':(0,'20일','21일'),'time':(1,'13:40','14:40'),
               'amount':(2,'4800원','8400원'),'selection':(3,'2개','3개')}[op]
        text=dom['conditions'][i]
        if a in text:dom['conditions'][i]=text.replace(a,b)  # preserve v1 replay
        else:
            pattern={'date':r'(\d+)일','time':r'(\d+):(\d+)',
                     'amount':r'(\d+)원','selection':r'(\d+)개'}[op]
            match=re.search(pattern,text)
            if not match:raise ValueError('authored mutation target missing')
            replacement=(str(int(match[1])+1).zfill(2)+':'+match[2] if op=='time' else
                         str(int(match[1])+(1000 if op=='amount' else 1))+{'date':'일','amount':'원','selection':'개'}[op])
            dom['conditions'][i]=text[:match.start()]+replacement+text[match.end():]
    elif op=='condition':dom['conditions'][-1]=dom['conditions'][-1].replace('비가 오면','비가 오지 않으면')
    elif op=='table_relations':dom['rows'][0][-1],dom['rows'][1][-1]=dom['rows'][1][-1],dom['rows'][0][-1]
    elif op=='omission':dom['rows'].pop()
    elif op=='duplicate':dom['rows'].append(copy.deepcopy(dom['rows'][0]))
    elif op=='unit':dom['unit']='금액: 천 원 / 선택: 개'
    elif op=='footnote':dom['notes'].pop()
    sha=digest(source)
    p={'source_revision':sha,'render_revision':digest(dom),
       'file':{'sha256':sha,'integrity_verified':True,'expected_pages':['p1','p2'],'available_pages':['p1','p2']},
       'scope':{'required_context_ids':['b1','h1','n1','policy'],'provided_context_ids':['b1','h1','n1','policy'],'table_inventory_complete':True},
       'tables':[{'id':'t1','source_block_id':'b1','required':{'headers':['h1'],'footnotes':['n1'],'merges':[]},
                  'provided':{'headers':['h1'],'footnotes':['n1'],'merges':[]}}],
       'role':{'value':'notice','confirmed_by':'synthetic_author','source_sha256':sha,'source_revision':sha}}
    complete=True
    if op=='missing_pages':p['file']['available_pages'].pop()
    elif op=='missing_context':
        p['scope']['provided_context_ids'].remove('policy');observed['conditions'].pop();complete=False
    elif op=='missing_header':p['tables'][0]['provided']['headers']=[];observed['headers']=[];complete=False
    elif op=='role_unverified':p['role']['confirmed_by']='parser_hint'
    state=fact_state(observed,dom);state['source_complete']=complete
    key='table_relations' if op in ('identity','row_reorder','table_relations','missing_header') else 'omission' if op in ('omission','footnote') else 'addition' if op=='duplicate' else 'conditions'
    gold='preserved' if op in NORMAL else 'changed' if op in CHANGED else 'insufficient'
    return state,p,key,gold


def generate(seed,split):
    if not seed or split not in FAMILIES:raise ValueError('explicit private author seed and split')
    result=[]
    for family in FAMILIES[split]:
        source=authored(seed,family);sha=digest(source)
        ops=('identity','table_relations','condition','missing_header') if family.endswith('_long') else NORMAL+CHANGED+MISSING
        for op in ops:
            state,p,key,gold=variant(source,op)
            result.append({'case_id':'nli-'+digest({'source':sha,'op':op})[:24], 'state':state,'provenance':p,
               'questions':{key:questions()[key]},'expected':{key:gold},
               'meta':{'family':family,'split':split,'original_group_id':digest({'version':VERSION,'family':family,'source':sha}),
                       'source_sha256':sha,'authored_source':source,'error_type':op,'evaluation_only':True,
                       'label_origin':'independent_source_and_recorded_operation','rights_evidence':'self_authored_synthetic_only'}})
    return result


def validate(rows,protected_hashes,protected_families):
    seen=set();groups=set();counts={}
    for row in rows:
        m=row['meta'];source=m['authored_source'];op=m['error_type'];sha=digest(source)
        if sha in protected_hashes or m['family'] in protected_families:raise ValueError('protected original/template')
        if (sha!=m['source_sha256'] or m['family'] not in FAMILIES[m['split']] or m['evaluation_only'] is not True or
                m['label_origin']!='independent_source_and_recorded_operation' or
                m['rights_evidence']!='self_authored_synthetic_only' or op not in NORMAL+CHANGED+MISSING):
            raise ValueError('author lineage')
        state,p,key,gold=variant(source,op)
        group=digest({'version':VERSION,'family':m['family'],'source':sha})
        if (row['state']!=state or row['provenance']!=p or row['expected']!={key:gold} or
                row['questions']!={key:questions()[key]} or m['original_group_id']!=group or
                row['case_id']!='nli-'+digest({'source':sha,'op':op})[:24]):raise ValueError('authored operation replay')
        if row['case_id'] in seen:raise ValueError('duplicate identity')
        seen.add(row['case_id']);groups.add(group);counts[gold]=counts.get(gold,0)+1
    return {'version':VERSION,'rows':len(rows),'groups':len(groups),'labels':counts,
            'source_sha256':sorted({r['meta']['source_sha256'] for r in rows}),
            'families':sorted({r['meta']['family'] for r in rows})}
