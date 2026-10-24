"""Explicit shared Korean questions, hash vectors, and bounded worker readiness.

These question definitions were compared with the deployed MacBook policy.py.
The old internal rubric name maps only when the actual definitions match.
"""
from __future__ import annotations

import hashlib
import re
from . import worker_contract as wc

RUBRIC_REVISION = 'moa.rubric.v1'
QUESTIONS_REVISION = 'moa.ko.section.v1'
CRITERIA = {
    'omission': '원문의 중요한 내용이 결과에서 누락되었는지 비교하라.',
    'addition': '결과가 원문에 없는 사실을 추가했는지 비교하라.',
    'conditions': '대상, 신청 마감, 필수 사항, 제외 및 미신청 조건이 보존되었는지 비교하라.',
    'table_relations': '표의 학과와 교실 등 행 및 열의 연결 관계가 보존되었는지 비교하라.',
    'attachment_role': '안내문과 원본 첨부 신청서의 역할 및 제출기한이 보존되었는지 비교하라.',
}


def questions():
    return {key: {'type': 'choice', 'instructions': text +
                  ' 제공된 증거만 사용하라. 필요한 근거가 없으면 근거부족을 선택하라.',
                  'criteria': {'preserved': '원문과 결과의 내용 및 관계가 일치한다.',
                               'changed': '원문과 결과에 실제 불일치가 있다.',
                               'insufficient': '판단에 필요한 원문이나 문맥이 부족하다.'}}
            for key, text in CRITERIA.items()}


def rubric_sha256():
    return hashlib.sha256(wc.canonical(questions())).hexdigest()


def handoff(source_commit='unrecorded'):
    from . import worker_api as api
    vectors = []
    for obj in ({'한글': '표', 'n': 1, 'empty': None},
                {'state': '원문\n표', 'values': [1, True, None, '']},
                {'float': 2.0, 'fraction': 0.125, 'unicode': '가'}):
        raw = wc.canonical(obj)
        vectors.append({'input': obj, 'utf8_hex': raw.hex(), 'sha256': hashlib.sha256(raw).hexdigest()})
    return {'handoff_version': 'moa.handoff.v1', 'source_commit': source_commit,
            **api.capabilities(api.contract_hash(), api.validator_hash(), RUBRIC_REVISION),
            'questions_revision': QUESTIONS_REVISION, 'rubric_sha256': rubric_sha256(),
            'questions': questions(), 'canonical_vectors': vectors,
            'source_block_hash': 'sha256-text-utf8',
            'legacy_schema_sha256': 'connection-file name only; compare actual schema bytes',
            'rubric_mapping': {'from': QUESTIONS_REVISION, 'to': RUBRIC_REVISION,
                               'only_if_actual_questions_sha256': rubric_sha256()},
            'auto_pass_enabled': False, 'auto_publish_enabled': False,
            'supported_execution': ['verify'], 'training_transport': 'private-ssh-shards',
            'model_weights_upload': False}


def readiness_errors(ack):
    from . import worker_api as api
    errors = []
    expected = handoff()
    for key in ('contract_hash', 'rubric_sha256', 'source_block_hash', 'max_artifact_bytes'):
        if ack.get(key) != expected[key]:
            errors.append('readiness mismatch: ' + key)
    if ack.get('questions') != questions():
        errors.append('actual question definitions differ')
    if ack.get('canonical_vectors') != expected['canonical_vectors']:
        errors.append('canonical vector mismatch')
    if not wc.ASCII_ID.fullmatch(str(ack.get('worker_id', ''))):
        errors.append('invalid worker ID')
    if not re.fullmatch('[0-9a-f]{64}', str(ack.get('worker_code_sha256', ''))):
        errors.append('actual worker implementation hash required')
    bounded_ready = ack.get('execution_mode') == 'bounded_pull_client' and ack.get('executor_ready') is True
    if ack.get('daemon_ready') is not True and not bounded_ready:
        errors.append('daemon not ready')
    if ack.get('validator_hash') != api.validator_hash():
        errors.append('validator revision changed')
    return errors
