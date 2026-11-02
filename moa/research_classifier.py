"""Option-free binary research head. No operational loading or approval path.

Keep the original state serialization, question instructions, frozen encoder,
two Transformer head layers and scorer MLP prefix. Replace option-position
scoring with CLS pooling and two fixed output coordinates. No class wording,
option order, case metadata or target enters the token sequence.
"""
from __future__ import annotations

import json
import math

VERSION = 'moa.fixed-binary-cls.v1'
LABELS = ('preserved', 'changed')
FIELDS = ('title', 'file_role', 'source_complete', 'source_blocks',
          'rendered_blocks', 'attachment_manifest')
CRITERIA = {
    'omission': '원문의 중요한 내용이 결과에서 누락되었는지 비교하라.',
    'addition': '결과가 원문에 없는 사실을 추가했는지 비교하라.',
    'conditions': '대상, 신청 마감, 필수 사항, 제외 및 미신청 조건이 보존되었는지 비교하라.',
    'table_relations': '표의 학과와 교실 등 행 및 열의 연결 관계가 보존되었는지 비교하라.',
    'attachment_role': '안내문과 원본 첨부 신청서의 역할 및 제출기한이 보존되었는지 비교하라.',
}
SUFFIX = ' 제공된 증거만 사용하라. 근거 확보 검사를 통과한 구간의 내용과 관계만 비교하라.'
DESCRIPTIONS = {'preserved': '원문과 결과의 내용 및 관계가 일치한다.',
                'changed': '원문과 결과에 실제 불일치가 있다.'}


def validate(row):
    state, qs = row['state'], row['questions']
    if (set(state) != set(FIELDS) or state['source_complete'] is not True or
            state['file_role'] != 'notice' or not isinstance(state['title'], str) or
            not isinstance(state['attachment_manifest'], list) or len(qs) != 1):
        raise ValueError('complete gold-free notice state and one question required')
    qid = next(iter(qs))
    if (qid not in CRITERIA or qs[qid] != dict(type='choice',
            instructions=CRITERIA[qid] + SUFFIX, criteria=DESCRIPTIONS)):
        raise ValueError('question differs from the frozen comparison rubric')
    known, rendered = set(), set()
    for source in state['source_blocks']:
        if (set(source) != {'id', 'text'} or not isinstance(source['id'], str) or
                not source['id'] or source['id'] in known or not isinstance(source['text'], str)):
            raise ValueError('unique literal source blocks required')
        known.add(source['id'])
    if not known:
        raise ValueError('source evidence absent')
    for dom in state['rendered_blocks']:
        if (set(dom) != {'id', 'source_refs', 'visible_text'} or not isinstance(dom['id'], str) or
                not dom['id'] or dom['id'] in rendered or not isinstance(dom['visible_text'], str) or
                not isinstance(dom['source_refs'], list) or
                any(not isinstance(ref, str) or ref not in known for ref in dom['source_refs'])):
            raise ValueError('literal actual DOM and acquired source references required')
        rendered.add(dom['id'])
    # The provenance/evidence inventory is checked by the coordinator before
    # staging. source_complete alone is NOT accepted as evidence authority.
    return qid


def target(row):
    qid = validate(row)
    gold = row.get('expected')
    if not isinstance(gold, dict) or set(gold) != {qid} or gold[qid] not in LABELS:
        raise ValueError('one independent normal/error target required; insufficiency stays a rule')
    return LABELS.index(gold[qid])


def encode(tok, row, *, maximum=1024):
    qid = validate(row)
    if type(maximum) is not int or not 1 <= maximum <= 4096:
        raise ValueError('invalid input cap')
    # Same JSON body, field order and question wording as the previous option
    # model. Only its type/option header and MASK markers are removed.
    state = {k: row['state'][k] for k in FIELDS}
    text = json.dumps(state, ensure_ascii=False, allow_nan=False)
    instruction = row['questions'][qid]['instructions']
    if tok.mask_token and (tok.mask_token in instruction or tok.mask_token in text):
        raise ValueError('reserved marker text cannot be silently stripped')
    ids = [tok.cls_token_id] + tok.encode(instruction, add_special_tokens=False) + [tok.sep_token_id]
    ids += tok.encode(text, add_special_tokens=False) + [tok.sep_token_id]
    if any(type(i) is not int or i < 0 for i in ids):
        raise ValueError('missing or invalid special token')
    if len(ids) > maximum:
        raise ValueError('input overflow: no header, condition or footnote truncation allowed')
    return dict(input_ids=ids, attention_mask=[1] * len(ids))


def build_classifier(base):
    """Transfer in-memory modules, not files. The R1 checkpoint is read-only."""
    import torch
    from torch import nn

    class FixedClassifier(nn.Module):
        def __init__(self):
            super().__init__()
            self.encoder, self.head, self.type_emb = base.encoder, base.head, base.type_emb
            self.encoder.requires_grad_(False)
            self.classifier = nn.Sequential(*list(base.scorer.children())[:-1],
                                            nn.Linear(base.encoder.config.hidden_size, 2))

        def forward(self, input_ids, attention_mask):
            with torch.no_grad():
                h = self.encoder(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state
            # Prior LAYA question type=choice has index0. It no longer carries
            # options; retaining this type embedding avoids another variable.
            h = h + self.type_emb.weight[0][None, None, :]
            if self.head is not None:
                for layer in self.head.layers:
                    h = layer(h, src_key_padding_mask=~attention_mask.bool())
            return self.classifier(h[:, 0]).float()

    return FixedClassifier()


def head_state(model):
    return {k: v.detach().cpu().contiguous().clone() for k, v in model.state_dict().items()
            if not k.startswith('encoder.')}


def load_head(model, state):
    wanted = {k: v for k, v in model.state_dict().items() if not k.startswith('encoder.')}
    if (set(state) != set(wanted) or any(state[k].shape != v.shape or
            state[k].dtype != v.dtype or not state[k].isfinite().all() for k, v in wanted.items())):
        raise ValueError('exact finite fixed-classifier weights required')
    missing, unexpected = model.load_state_dict(state, strict=False)
    if unexpected or any(not k.startswith('encoder.') for k in missing):
        raise ValueError('head reload failed')


def score(records, gold):
    # Avoid depending on the option scorer or synthesizing a second order.
    indexed = {r['case_id']: r for r in records}
    if len(indexed) != len(records) or set(indexed) != set(gold):
        raise ValueError('one actual classifier observation per fixed case required')
    matrix = {g: {p: 0 for p in (*LABELS, 'incomplete')} for g in LABELS}
    for identity, g in gold.items():
        r = indexed[identity]
        p, probs = r.get('prediction'), r.get('probabilities', {})
        if g not in LABELS or r.get('system_forced') is not False or p not in (*LABELS, None):
            raise ValueError('raw model answers only, not evidence rules')
        if p is not None:
            if (set(probs) != set(LABELS) or any(type(v) not in (int, float) or
                    not math.isfinite(v) or not 0 <= v <= 1 for v in probs.values()) or
                    abs(sum(probs.values()) - 1) > .002 or
                    probs[p] < max(probs.values())):
                raise ValueError('fixed-coordinate argmax/probability mismatch')
        matrix[g][p or 'incomplete'] += 1
    n = len(gold)
    counts = {g: sum(matrix[g].values()) for g in LABELS}
    recalls = {g: matrix[g][g] / counts[g] if counts[g] else None for g in LABELS}
    correct = sum(matrix[g][g] for g in LABELS)
    return dict(items=n, correct=correct, accuracy=correct/n if n else None, confusion=matrix,
        normal_false_positive_rate=matrix['preserved']['changed']/counts['preserved'] if counts['preserved'] else None,
        recall=recalls, incomplete=sum(matrix[g]['incomplete'] for g in LABELS),
        balanced_accuracy=sum(recalls.values())/2 if all(counts.values()) else None,
        error_miss_rate=1-recalls['changed'] if counts['changed'] else None,
        new_blind=False, rule_answers_counted_as_model_success=False)
