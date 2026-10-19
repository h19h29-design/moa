"""Conservative correspondence for self-contained Korean notice clauses.

This is deliberately not a sentence bag or a language-model equivalence test.
Only unique named fields, explicit subjects and self-contained conditionals can
move. The entire clause (including polarity, values and exceptions) must match.
Unknown grammar, duplicate subjects and cross-references retain strict order.
"""
from __future__ import annotations

import re

from .verification import content_tokens

VERSION = 'moa.independent-clause.v2'
_DEPENDENT = re.compile(
    r'(?:^\s*\d+[.)]|제\s*\d+\s*[항호단계]|[①-⑳]|'
    r'앞\s*(?:항목|문장|조건)|위\s|위\s*(?:기한|항목|조건|내용)|아래|상기|해당|이때|'
    r'(?:첫|두|세|네|다섯|여섯)\s*번째|(?:조건|항목)\s*\d+\s*번|'
    r'이후|이전|먼저|다음|순서|단계|각각|차례|전항|후항|전술|후술|전자|후자|'
    r'^\s*(?:단[ ,，:]|다만|예외[ :：]))')
_FIELD = re.compile(
    r'^(신청|접수|제출|활동|교육|수업|참가|참여|모집|문의|담당|준비|대상|장소|일시|기간|금액|비용)'
    r'[가-힣 ]{0,22}\s*[:：]\s*\S.+$')
_SELECTION = re.compile(r'^(반드시|각자|학생은|참가자는)\s*\d+\s*(?:개|과목).{0,25}선택(?:합니다\.)?$')
_CONDITIONAL = re.compile(r'^(.{2,45}?(?:면|경우에는|경우에|경우))\s+\S.+(?:합니다|됩니다|바랍니다)\.$')
_SUBJECT = re.compile(r'^([가-힣0-9 ]{2,35}?)(?:은|는|이|가)\s+\S.+(?:합니다|됩니다|바랍니다)\.$')


def has_order_dependency(value):
    """References in another field can constrain an otherwise independent list."""
    if isinstance(value, str): return bool(_DEPENDENT.search(value))
    if isinstance(value, list): return any(has_order_dependency(v) for v in value)
    if isinstance(value, dict): return any(has_order_dependency(v) for v in value.values())
    return False


def _anchor(text):
    if not isinstance(text, str) or len(text) > 1000 or _DEPENDENT.search(text):
        return None
    if _FIELD.fullmatch(text):
        return ('named_field', re.sub(r'\s+', '', re.split(r'[:：]', text, 1)[0]))
    match = _SELECTION.fullmatch(text)
    if match:
        return ('selection', match[1])
    match = _CONDITIONAL.fullmatch(text)
    if match:
        return ('conditional', re.sub(r'\s+', '', match[1]))
    match = _SUBJECT.fullmatch(text)
    if match:
        return ('subject', re.sub(r'\s+', '', match[1]))
    return None


def clause_correspondence(source, dom):
    """Return a full token-preserving bijection, or no reorder permission.

    A clause is never split, reconstructed or replaced with source text. The
    caller can only move actual DOM clauses to their proven source positions.
    """
    if not isinstance(source, list) or not isinstance(dom, list) or len(source) != len(dom):
        return None
    left, right = [_anchor(v) for v in source], [_anchor(v) for v in dom]
    if (not source or None in left or None in right or len(set(left)) != len(left)
            or len(set(right)) != len(right) or set(left) != set(right)):
        return None
    positions = {anchor: i for i, anchor in enumerate(right)}
    mapping = [positions[a] for a in left]
    if any(content_tokens(value) != content_tokens(dom[j]) for value, j in zip(source, mapping)):
        return None
    return {'version': VERSION, 'source_to_dom': mapping,
            'anchors': [list(a) for a in left], 'complete_clause_tokens_equal': True}
