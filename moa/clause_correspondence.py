"""Conservative correspondence for self-contained Korean notice clauses.

This is deliberately not a sentence bag or a language-model equivalence test.
Only unique named fields, explicit subjects and self-contained conditionals can
move. Whole clauses must match after bounded, versioned presentation rules;
values, actors, objects, polarity and obligations are never discarded. Unknown
paraphrases, duplicate subjects and cross-references retain strict comparison.
"""
from __future__ import annotations

import re

from .verification import content_tokens

VERSION = 'moa.independent-clause.v13'
# Do not enumerate Korean endings after former/latter: an unseen ending can
# still bind another field's order. Without a lexical parser, compounds such
# as '전자공학' conservatively remain strict too. A left lexical boundary
# avoids '상기' inside '이상기록'; the causative verb is not a reference.
_REFERENCE = r'(?<![가-힣A-Za-z0-9])(?:상기(?!시키)|전항|후항|전술|후술|전자|후자)'
_SPATIAL_REFERENCE = (r'(?<![가-힣A-Za-z0-9])(?:앞\s*(?:항목|문장|조건)|'
                      r'위\s|위\s*(?:기한|항목|조건|내용)|아래|'
                      r'(?:최|차|중)?[상하]위|맨\s*(?:위|아래|앞|뒤)|'
                      r'[가-힣]+위\s*(?:항목|문장|조건|내용|기한|행|열|줄))')
_DEPENDENT = re.compile(
    r'(?:^\s*\d+[.)]|제\s*\d+\s*[항호단계]|[①-⑳]|'
    + _SPATIAL_REFERENCE + r'|해당|이때|'
    r'(?:첫|두|세|네|다섯|여섯)\s*번째|(?:조건|항목)\s*\d+\s*번|'
    r'이후|이전|먼저|다음|순서|단계|각각|차례|' + _REFERENCE + r'|'
    r'^\s*(?:단[ ,，:]|다만|예외[ :：]))')
_LABEL = r'[가-힣][가-힣A-Za-z0-9 ()·/_-]{0,39}?'
_FIELD = re.compile(r'^(' + _LABEL + r')\s*[:：]\s*(\S.*)$')
_NOMINAL_FIELD = re.compile(r'^(' + _LABEL + r')(?:은|는)\s+(.+?)입니다\.?$')
_ACTION = r'(?:제출|지참|확인|선택|작성|신청|참여|입장|납부)'
_STYLE = re.compile(r'^(.+?)(' + _ACTION + r')(합니다|해요)\.?$')
_OBLIGATION = re.compile(r'^(.+?)(' + _ACTION + r')(?:하여야|해야) 합니다\.?$')
_REQUEST = re.compile(r'^(.+?)(' + _ACTION + r')(?:해 주세요|하십시오|하세요)\.?$')
# Only a short single-positive prefix can bind '반드시' to the final action.
# This prefix alone is not a role proof. Case-bound movement uses the separate
# full-clause grammar below, excluding quantified or restrictive arguments.
_NOUN = r'[가-힣]{2,24}?'
_ROLE_NOUN = r'[가-힣]{1,24}?'
_ROLE_END = r'(?:합니다|해요|해야 합니다|하여야 합니다|해 주세요|하십시오|하세요)\.?'
_SVO = re.compile(r'^(' + _ROLE_NOUN + r')(은|는|이|가) 반드시 (' + _ROLE_NOUN +
                  r')(을|를) (' + _ACTION + r')' + _ROLE_END + r'$')
_OSV = re.compile(r'^(' + _ROLE_NOUN + r')(은|는) (' + _ROLE_NOUN +
                  r')(이|가) 반드시 (' + _ACTION + r')' + _ROLE_END + r'$')
# Atomic case marking does not prove quantifier scope. Exclude quantified,
# reflexive, additive/restrictive or connective arguments rather than deleting
# their particles or guessing that a number-looking prefix is a quantifier.
# Ambiguous '전-' arguments (including genuine compound nouns) stay strict;
# this shallow grammar is not a complete Korean morphological analyser.
_ROLE_SCOPE = re.compile(r'모든|모두|전부|전원|일체|일부|각자|각각|각기|누구|누군|아무|'
                         r'여러|여럿|다수|소수|자신|자기|서로|몇|매번|적어도|'
                         r'^전|개개|일동|전체|총원|양측|양쪽|양편|양자|쌍방|절반|과반|대부분')
_NUMBER = r'(?:하나|한|둘|두|셋|세|넷|네|다섯|여섯|일곱|여덟|아홉|열|스무|스물|일|이|삼|사|오|육|칠|팔|구|십|백|천|만)'
_NUMBER_ARGUMENT = re.compile(r'^(?:' + _NUMBER + r')+(?:명|개|과목|가지|번|회|장|부|권|인|쪽|편|측)?$|^각인$')
_ROLE_SUFFIX = re.compile(r'(?:만|도|뿐|조차|마저|까지|부터|마다|라도|든|의|하고|하며|하면|거나|해서|하여|'
                          r'에게|에게서|한테|한테서|께|께서|에서|으로|로|로서|로써|에|처럼|같이|보다|대로)$')
_MUST_PREFIX = re.compile(r'^(?:' + _NOUN + r'(?:은|는|이|가) )?반드시 '
                          r'(?:' + _NOUN + r'(?:을|를)|\d+\s*(?:개|과목)(?:을|를)?) $')
_SELECTION = re.compile(r'^(반드시|각자|학생은|참가자는)\s*\d+\s*(?:개|과목).{0,25}선택(?:합니다\.|해야 합니다\.)?$')
_CONDITIONAL = re.compile(r'^(.{2,45}?(?:면|경우에는|경우에|경우))\s+\S.+(?:합니다|됩니다|바랍니다)\.$')
_SUBJECT = re.compile(r'^([가-힣0-9 ]{2,35}?)(?:은|는|이|가)\s+\S.+(?:합니다|됩니다|바랍니다)\.$')


def normalize_ordered_presentation(text):
    """Terminal present speech level only; the complete ordered prefix stays.

    This proof cannot move clauses, normalize quoted words, or alter modality,
    polarity, roles or facts. References/numbering are allowed here precisely
    because every prefix and every clause position must remain identical.
    """
    if not isinstance(text, str) or len(text) > 1000 or re.search(r'["\'“”‘’「」『』]', text):
        return text, []
    value = re.sub(r'\s+', ' ', text).strip()
    for name, endings in (
        ('active', ('합니다.', '해요.')),
        ('passive', ('됩니다.', '돼요.')),
        ('exists', ('있습니다.', '있어요.')),
        ('absent', ('없습니다.', '없어요.')),
    ):
        for ending in endings:
            if value.endswith(ending) and len(value) > len(ending):
                return value[:-len(ending)] + endings[0], ['ordered_present_' + name]
    # ...입니다 can also be a verb (모입니다/보입니다/움직입니다).
    # Neither a suffix nor final-consonant agreement proves a nominal copula.
    # Unknown copular paraphrases deliberately retain strict comparison.
    return value, []


def ordered_presentation_correspondence(source, dom):
    """Identity mapping only, with independently canonicalized whole strings."""
    if not isinstance(source, list) or not isinstance(dom, list) or not source or len(source) != len(dom):
        return None
    if not all(isinstance(v, str) for v in source + dom):
        return None
    left = [normalize_ordered_presentation(v) for v in source]
    right = [normalize_ordered_presentation(v) for v in dom]
    if not any(rules for _, rules in left + right) or [v[0] for v in left] != [v[0] for v in right]:
        return None
    return {'version': VERSION, 'source_to_dom': list(range(len(source))),
            'complete_ordered_prefixes_equal': True,
            'source_rule_ids': [v[1] for v in left], 'dom_rule_ids': [v[1] for v in right]}


def has_order_dependency(value):
    """References in another field can constrain an otherwise independent list."""
    if isinstance(value, str): return bool(_DEPENDENT.search(value))
    if isinstance(value, list): return any(has_order_dependency(v) for v in value)
    if isinstance(value, dict): return any(has_order_dependency(v) for v in value.values())
    return False


def _simple_value(value):
    # No multi-sentence/quoted values: do not reinterpret quoted instructions or
    # lose an embedded predicate while turning a copular sentence into a field.
    if re.fullmatch(r'\d{1,2}:\d{2}(?:\s*[~～–-]\s*\d{1,2}:\d{2})?', value):
        return True  # A complete clock/range, not a second named field.
    return bool(value.strip()) and not re.search(r'[\r\n.!?;:："“”‘’]', value)


def role_bindings(text):
    """Bounded positive must-clause: one atomic actor/object, explicit cases.

    No synonym lookup, noun sorting, quantifier movement or missing-role fill.
    Unknown/modifying phrases remain strict. Bindings come from THIS text only.
    """
    if not isinstance(text, str) or len(text) > 1000 or _DEPENDENT.search(text):
        return None
    svo, osv = _SVO.fullmatch(text), _OSV.fullmatch(text)
    if svo:
        actor, actor_case, obj, object_case, action = svo.groups()
    elif osv:
        obj, object_case, actor, actor_case, action = osv.groups()
    else:
        return None
    for noun, case in ((actor, actor_case), (obj, object_case)):
        consonant = (ord(noun[-1]) - 0xac00) % 28 != 0
        allowed = ('은', '이', '을') if consonant else ('는', '가', '를')
        if case not in allowed:
            return None
    if actor == obj or any(_ROLE_SCOPE.search(v) or _NUMBER_ARGUMENT.fullmatch(v)
                           or _ROLE_SUFFIX.search(v) for v in (actor, obj)):
        return None
    return {'actor': actor, 'object': obj, 'action': action, 'obligation': 'explicit_must'}


def normalize_clause(text):
    """Independently normalize this observed clause, returning applied rule IDs.

    This is not a synonym dictionary or a general entailment judgement. Copular
    field presentation and a few Korean speech-level/contraction forms have
    explicit grammars. Imperative/declarative forms are equivalent ONLY with an
    unchanged explicit '반드시' obligation; optionality is never inferred away.
    """
    if not isinstance(text, str) or len(text) > 1000 or _DEPENDENT.search(text):
        return text, []
    field = _FIELD.fullmatch(text)
    nominal = _NOMINAL_FIELD.fullmatch(text)
    if field or nominal:
        # A copular clock such as '시각은 14:15입니다' is not a field named
        # '시각은 14'. Parse the whole sentence before considering its colon.
        label, value = (nominal or field).groups()
        rules = ['explicit_named_field']
        if nominal:
            if not _simple_value(value):
                return text, []
            rules.append('copular_field_presentation')
        elif value.endswith(('입니다.', '입니다')):
            stripped = re.sub(r'입니다\.?$', '', value)
            if _simple_value(stripped):
                value = stripped
                rules.append('copular_field_presentation')
        return label.strip() + ': ' + value, rules
    # Only the final predicate form changes; its complete prefix remains
    # literal evidence, including subjects, targets, quantities and negations.
    if re.search(r'[\r\n.!?;:："“”‘’]', text.rstrip('.')):
        return text, []
    roles = role_bindings(text)
    if roles:
        # Canonical particles are calculated from this independently parsed
        # noun, never borrowed from the other document's words or bindings.
        actor, obj = roles['actor'], roles['object']
        topic = '은' if (ord(actor[-1]) - 0xac00) % 28 else '는'
        case = '을' if (ord(obj[-1]) - 0xac00) % 28 else '를'
        return (actor + topic + ' 반드시 ' + obj + case + ' ' + roles['action'] +
                '해야 합니다.'), ['case_bound_atomic_roles']
    obligation = _OBLIGATION.fullmatch(text)
    if obligation:
        prefix, action = obligation.groups()
        return prefix + action + '해야 합니다.', ['obligation_contraction']
    style, request = _STYLE.fullmatch(text), _REQUEST.fullmatch(text)
    match = style or request
    if match:
        prefix, action = match[1], match[2]
        # A 'must' in a subordinate/negative clause does not impose an
        # obligation on the final action. Require a complete single-positive-
        # instruction prefix, not the mere occurrence of the adverb.
        explicit_must = bool(_MUST_PREFIX.fullmatch(prefix))
        if explicit_must:
            return prefix + action + '해야 합니다.', ['explicit_must_speech_level']
        if style:
            return prefix + action + '합니다.', ['assertive_speech_level']
    return text, []


def _anchor(text):
    if not isinstance(text, str) or len(text) > 1000 or _DEPENDENT.search(text):
        return None
    match = _FIELD.fullmatch(text)
    if match:
        return ('named_field', re.sub(r'\s+', '', match[1]))
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
    """Return a complete, bound-clause bijection, or no normalization permission.

    Both sides are normalized separately. A DOM clause is never reconstructed
    from source text. No partial matching, global token bags or model labels.
    """
    if not isinstance(source, list) or not isinstance(dom, list) or len(source) != len(dom):
        return None
    source_normal = [normalize_clause(v) for v in source]
    dom_normal = [normalize_clause(v) for v in dom]
    left = [_anchor(v[0]) for v in source_normal]
    right = [_anchor(v[0]) for v in dom_normal]
    if (not source or None in left or None in right or len(set(left)) != len(left)
            or len(set(right)) != len(right) or set(left) != set(right)):
        return None
    positions = {anchor: i for i, anchor in enumerate(right)}
    mapping = [positions[a] for a in left]
    if any(content_tokens(value[0]) != content_tokens(dom_normal[j][0])
           for value, j in zip(source_normal, mapping)):
        return None
    return {'version': VERSION, 'source_to_dom': mapping,
            'anchors': [list(a) for a in left],
            'complete_clause_tokens_equal': all(content_tokens(v) == content_tokens(dom[j])
                                                for v, j in zip(source, mapping)),
            'canonical_clause_tokens_equal': True,
            'source_role_bindings': [role_bindings(v) for v in source],
            'dom_role_bindings': [role_bindings(dom[j]) for j in mapping],
            'source_rule_ids': [v[1] for v in source_normal],
            'dom_rule_ids': [v[1] for v in dom_normal]}
