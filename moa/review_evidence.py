"""Authenticated, read-only explanations and a future human-data firewall.

Sidecars are private runtime files, not authority to approve, publish or train.
No original text or model output is loaded until the caller authenticated and
selected its existing document/revision. No arbitrary filesystem path is used.
"""
from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
from html import escape
import json
import re

from .worker_contract import canonical

VERSION = 'moa.review-evidence.v1'
MAX_BYTES = 2_000_000
LABELS = {'preserved': '내용 일치', 'changed': '내용 차이', 'insufficient': '근거 부족'}
REASONS = {
    'file_role_unverified': '안내문/서식 역할 확인 필요',
    'unconfirmed_file_role': '안내문/서식 역할 확인 필요',
    'direct_mismatch': '원문과 모바일의 내용·관계 차이',
    'insufficient_evidence': '판단할 원문 문맥 부족',
    'unknown_file_pages': '원본 전체 페이지 수 미확인',
    'missing_file_pages': '원본 페이지 일부 누락',
    'missing_scope_context': '관련 문맥 일부 누락',
    'unknown_table_context': '표 머리글·각주·병합 관계 미확인',
    'missing_table_context': '표 머리글·각주·병합 근거 누락',
    'new_type': '새 양식', 'model_direct_conflict': '직접 검사와 모델 판단 불일치',
    'browser_not_verified': '브라우저 화면 확인 필요',
    'historical_input_not_reinferred': '이전 입력 결과 · 재추론 아님',
    'input_overflow': '모델 입력 한도 초과', 'model_not_run': '모델 미실행',
}
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _text(value, limit=160):
    return isinstance(value, str) and 0 < len(value) <= limit


def _sha(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _strings(value):
    if not isinstance(value, list) or len(value) > 10000 or any(not _text(v) for v in value):
        raise ValueError('bounded list of lineage or evidence identities required')
    if len(set(value)) != len(value): raise ValueError('duplicate evidence identity')
    return set(value)


def _current_model(model, section, doc):
    """Source/render revisions alone cannot certify a newly inferred result."""
    versions, checkpoints = doc.get('versions', {}), doc.get('models_sha256', {})
    if not isinstance(versions, dict) or not isinstance(checkpoints, dict): return False
    return (all(_sha(v) for v in (section.get('model_input_sha256'), model.get('ordered_input_sha256'),
                                  checkpoints.get(model['name']), model.get('model_sha256'))) and
            section['model_input_sha256'] == model['ordered_input_sha256'] and
            checkpoints[model['name']] == model['model_sha256'] and
            all(_text(versions.get(v)) and versions[v] == model.get(k) for k, v in
                (('input_revision', 'input_revision'), ('checker_version', 'checker'))))


def load(store, ident, revision, files):
    path = store.root / 'reports/review-evidence-v1.json'
    if not path.exists() and not path.is_symlink():
        return {'status': 'missing'}
    try:
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise ValueError('private bounded regular sidecar only')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES: raise ValueError('sidecar size')
        value = json.loads(raw)
        if value['version'] != VERSION or not isinstance(value['documents'], list):
            raise ValueError('sidecar schema')
        matches = [d for d in value['documents'] if d.get('mobile_id') == ident]
        if not matches: return {'status': 'missing'}
        if len(matches) != 1: raise ValueError('duplicate document evidence')
        doc = matches[0]
        if (doc['render_revision'] != revision['id'] or doc['html_sha256'] != revision['html_sha'] or
                doc['files_sha256'] != hashlib.sha256(canonical(files)).hexdigest()):
            return {'status': 'stale'}
        sections = doc['sections']
        if not isinstance(sections, list) or len(sections) > 100:
            raise ValueError('bounded sections')
        for section in sections:
            for field in ('source_id', 'origin_location', 'source_excerpt', 'dom_excerpt'):
                if not isinstance(section[field], str) or len(section[field]) > 30_000:
                    raise ValueError('bounded text only')
            if section['direct_label'] not in LABELS or section['final_label'] not in LABELS:
                raise ValueError('known decisions only')
            if not isinstance(section['reasons'], list) or len(section['reasons']) > 30:
                raise ValueError('bounded reasons')
            for reason in section['reasons']:
                if not isinstance(reason, str) or len(reason) > 160: raise ValueError('reason identifier')
            evidence = section.get('evidence', {})
            if not isinstance(evidence, dict) or any(
                    evidence.get(k) is not None and type(evidence[k]) is not bool for k in
                    ('file_complete', 'scope_complete', 'table_context_complete', 'role_confirmed')):
                raise ValueError('four evidence availability dimensions required')
            if not isinstance(section['models'], list) or len(section['models']) > 10:
                raise ValueError('bounded raw-model records')
            for model in section['models']:
                if (not isinstance(model['name'], str) or len(model['name']) > 160 or
                        model.get('prediction') not in (*LABELS, None) or
                        model['source_revision'] != doc['source_revision'] or model['render_revision'] != revision['id']):
                    raise ValueError('raw-model revision mismatch')
                if not _text(model.get('status', 'model_not_run')): raise ValueError('bounded model status')
                if 'nli' in model:
                    nli=model['nli']
                    if (not isinstance(nli,dict) or nli.get('semantic_relation') not in
                            ('equivalent','contradiction','not_entailed','incomplete') or
                            not isinstance(nli.get('direction_counts'),dict) or
                            set(nli['direction_counts'])!={'source_to_dom','dom_to_source'}):
                        raise ValueError('explicit raw NLI directions')
                    for counts in nli['direction_counts'].values():
                        if (not isinstance(counts,dict) or set(counts)!={'entailment','neutral','contradiction'} or
                                any(type(v) is not int or not 0<=v<=2000 for v in counts.values())):
                            raise ValueError('bounded original NLI label counts')
                if model.get('status') == 'completed' and not _current_model(model, section, doc):
                    model['status'] = 'historical_input_not_reinferred'
        return dict(doc, status='current', human_approved=False, training_allowed=False)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'status': 'invalid'}


def render(report):
    if report['status'] == 'missing': return ''
    if report['status'] != 'current':
        return '<section class="card"><h2>원문과 모바일 · 확인할 부분</h2><p class="warning">검사 설명이 현재 파일·변환 버전과 맞지 않거나 읽을 수 없습니다. 새 검사가 필요합니다.</p></section>'
    esc = lambda value: escape(str(value), quote=True)
    content = '<section class="card evidence-review"><h2>원문과 모바일 · 확인할 부분</h2><p>시험용 검사 설명입니다. 자동 승인·공개·학습하지 않습니다. 아래 차이와 파일 역할부터 확인하세요.</p>'
    for section in report['sections']:
        reasons = ' · '.join(REASONS.get(r, r) for r in section['reasons']) or '해당 구역의 자동 대조 완료 (사람 승인 아님)'
        content += '<article class="files evidence-section"><h3>'+esc(section['origin_location'])+'</h3>'
        content += '<p>규칙: '+LABELS[section['direct_label']]+' · 종합: '+LABELS[section['final_label']]+'</p>'
        for model in section['models']:
            status = model.get('status', 'model_not_run')
            unknown='의미 일치 미확정' if 'nli' in model else '미완료'
            content += '<p>'+esc(model['name'])+' 원판정: '+LABELS.get(model.get('prediction'), unknown)+' · '+esc(REASONS.get(status, status))+'</p>'
            if 'nli' in model:
                for direction,counts in model['nli']['direction_counts'].items():
                    content+='<p class="muted">'+esc(direction)+': '+esc(' / '.join(k+' '+str(v) for k,v in counts.items()))+'</p>'
                content+='<p class="muted">neutral은 원문 부족 판정이 아닙니다. contradiction도 모든 문서 오류를 뜻하지 않습니다. 근거 확인·사람 승인과 별개인 시험 결과입니다.</p>'
        content += '<p class="warning">'+esc(reasons)+'</p>'
        evidence = section.get('evidence', {})
        states = lambda v: '확보' if v is True else '부족' if v is False else '미확인'
        content += '<p class="muted">근거: '+esc(' · '.join(name+': '+states(evidence.get(key)) for name,key in (
            ('전체 페이지','file_complete'),('해당 구역','scope_complete'),('표 문맥','table_context_complete'),('파일 역할','role_confirmed'))))+'</p>'
        left, right = _difference(section['source_excerpt'], section['dom_excerpt'],
                                  section['direct_label'] == 'changed')
        content += ('<div class="evidence-comparison"><div data-side="source"><h4>원문 추출 구역</h4><pre>'+left+
                    '</pre></div><div data-side="dom"><h4>실제 HTML 구역</h4><pre>'+right+'</pre></div></div>'
                    '<p class="muted">노란색은 이 구역의 문자 차이입니다. 의미 오류 확정이나 사람 승인이 아닙니다. '
                    '추출 내용만으로 전체 원문을 보증하지 않으므로 아래 원본 파일도 확인하세요.</p></article>')
    return content+'</section>'


def _difference(source, dom, highlight):
    """Escaped display-only differences, retaining every character on both sides.

    The bounded fallback marks whole texts, not a silently shortened excerpt.
    Neither matching nor display can repair/approve the rendered document.
    """
    if not highlight or source == dom:
        return escape(source), escape(dom)
    tokenize = lambda text: re.findall(r'\s+|[가-힣A-Za-z_]+|\d+|[^\s]', text)
    left, right = tokenize(source), tokenize(dom)
    if len(left) + len(right) > 6000:
        return '<mark class="evidence-diff">'+escape(source)+'</mark>', '<mark class="evidence-diff">'+escape(dom)+'</mark>'
    result = [[], []]
    for tag, a, b, c, d in SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        for side, value in enumerate((''.join(left[a:b]), ''.join(right[c:d]))):
            safe = escape(value)
            result[side].append('<mark class="evidence-diff">'+safe+'</mark>' if tag != 'equal' and value else safe)
    return tuple(''.join(parts) for parts in result)


def suggest_hold(report):
    """An editable UI suggestion only; unknown evidence is not a proven error."""
    if report.get('status') != 'current': return 'unconfirmed', ''
    sections = report.get('sections', [])
    reasons = {r for s in sections for r in s['reasons']}
    if 'missing_file_pages' in reasons:
        return 'incomplete', '확보하지 못한 원본 페이지가 있습니다.'
    if any(s['direct_label'] == 'changed' for s in sections):
        findings = set()
        for section in sections:
            direct = section.get('direct_report')
            if isinstance(direct, dict) and isinstance(direct.get('findings'), list):
                findings.update(f for f in direct['findings'] if isinstance(f, str))
        if findings & {'row_relation_mismatch', 'merge_relation_mismatch', 'headers_mismatch', 'position_dependent_row_order'}:
            return 'table', '원문과 모바일 표의 연결·머리글·순서를 확인하세요.'
        return 'other', '원문과 모바일의 날짜·금액·조건 또는 내용 차이를 확인하세요.'
    if reasons:
        return 'unconfirmed', ' · '.join(REASONS.get(r, r) for r in sorted(reasons))[:1200]
    return 'unconfirmed', ''


def learning_eligibility(record, *, protected_hashes, protected_groups):
    """Future contract: explicit rights + human evidence + lineage firewall.

    This pure audit does not enqueue/export anything. Production reviews must
    first be bound to their authenticated actor and exact reviewed revision.
    Holds and auto-verification are never negative or positive training gold.
    """
    reasons = []
    try:
        human, versions = record['human'], record['versions']
        lineage = _strings(record['lineage'])
        if (not _sha(record['source_sha256']) or not _text(record['source_group']) or
                not isinstance(human, dict) or not isinstance(versions, dict) or not isinstance(record['rights'], dict)):
            raise ValueError('invalid human evidence identity')
        if (record['evaluation_only'] is not False or record['source_sha256'] in protected_hashes or
                record['source_group'] in protected_groups or
                lineage & (set(protected_hashes) | set(protected_groups))):
            reasons.append('protected_evaluation_lineage')
        if (human['origin'] != 'authenticated_human' or human['action'] != 'approved' or
                human['content_compared'] is not True or human['label'] not in LABELS or not _text(human['review_revision'])):
            reasons.append('not_human_content_gold')
        if (not isinstance(human['errors'], list) or len(human['errors']) > 1000 or
                not isinstance(human['corrected_blocks'], list) or len(human['corrected_blocks']) > 1000):
            raise ValueError('bounded error or correction record required')
        if (not isinstance(record['role'], dict) or record['role'].get('value') != 'notice' or
                record['role'].get('confirmed') is not True):
            reasons.append('unconfirmed_notice_role')
        if not all(_text(versions.get(k)) for k in ('source','render','review','model','renderer')):
            reasons.append('unbound_source_dom_or_versions')
        source, dom = record['source_sections'], record['dom_sections']
        if (not isinstance(source, list) or not source or len(source) > 1000 or
                not isinstance(dom, list) or not dom or len(dom) > 1000):
            raise ValueError('bounded source and DOM sections required')
        source_ids = _strings([s['id'] for s in source])
        if any(not _text(s['evidence'], 30000) for s in source): raise ValueError('source evidence required')
        for block in dom:
            if not _strings(block['source_refs']) <= source_ids or not block['source_refs'] or not _text(block['visible_text'], 30000):
                raise ValueError('DOM source correspondence required')
        for error in human['errors']:
            if error['source_id'] not in source_ids or not _text(error['kind']) or not _text(error['detail'], 30000):
                raise ValueError('bound human error location required')
        for block in human['corrected_blocks']:
            if not _strings(block['source_refs']) <= source_ids or not block['source_refs'] or not _text(block['visible_text'], 30000):
                raise ValueError('bound corrected DOM block required')
        rights = record['rights']
    except (KeyError, TypeError, AttributeError, ValueError):
        rights = {}; reasons.append('invalid_human_evidence_contract')
    return {'version': VERSION, 'rule_example': not reasons and rights.get('rule_examples') is True,
            'model_training': not reasons and rights.get('model_training') is True,
            'negative_example': False, 'reasons': reasons,
            'automatic_queue_started': False, 'operating_model_changed': False}
