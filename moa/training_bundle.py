"""Author-owned Korean synthetic gold, split by source AND template family.

No corpus, model output, provider output or unreviewed hold is a label source.
The oracle compares structured facts before rendering, independently of the gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from .core import atomic_write
from .worker_contract import canonical
from .worker_handoff import RUBRIC_REVISION, questions, rubric_sha256

BUNDLE_ID = 'moa-ko-synthetic-20261018-v1'
MAX_SHARD_BYTES = 512 * 1024
# Fifty independently authored fact records, ten reusable format families.
# This is NOT a claim of fifty independent form/layout families.
TOPICS = (
 ('돌봄', [('아침 돌봄 교실','등교 전 도서관에서 대기하고 보호자가 출석을 확인합니다.'),
          ('방과후 놀이 돌봄','귀가 동행자를 신청서에 적고 변경 시 담당자에게 알립니다.'),
          ('방학 중 돌봄','도시락은 개별 지참하며 급식 신청과 별도로 접수합니다.'),
          ('긴급 돌봄','보호자 사유를 제출해야 하며 정규 돌봄 배정을 보장하지 않습니다.'),
          ('토요 돌봄','오전 활동 후 보호자 인계가 필요하고 차량은 운행하지 않습니다.')]),
 ('진로', [('학과 멘토링','미신청자는 남은 학과에 임의 배정하며 선착순으로 마감합니다.'),
          ('직업인 대화','질문지를 사전 제출하고 촬영은 진행자의 허락을 받습니다.'),
          ('대학 실험실 탐방','실험복을 착용하고 보호자 동의서를 출발 전에 제출합니다.'),
          ('진로 독서 모임','지정 도서를 읽고 참석하며 도서 구입은 필수가 아닙니다.'),
          ('직업 체험 발표','팀별 발표 후 귀가하며 팀원 전원이 같은 시간에 참석합니다.')]),
 ('보건', [('예방접종 확인','접종 여부는 보호자가 확인하며 안내문 제출이 접종 동의는 아닙니다.'),
          ('구강 건강 교육','칫솔을 개별 준비하며 검진이나 치료를 대신하지 않습니다.'),
          ('시력 보호 수업','안경 착용 학생도 참여하고 개인 검사 결과는 공개하지 않습니다.'),
          ('응급처치 실습','실습 도구는 제공되며 개인 의약품을 서로 나누지 않습니다.'),
          ('알레르기 안내','관련 증상이 있으면 보건실에 먼저 알리고 식단표를 확인합니다.')]),
 ('독서', [('작가와의 만남','서명은 준비한 책에만 가능하며 현장 판매는 하지 않습니다.'),
          ('도서관 야간 개방','귀가 보호자를 지정하고 신청하지 않은 형제는 동반하지 않습니다.'),
          ('책 교환 행사','훼손되지 않은 책만 접수하고 교환 후 반환은 불가합니다.'),
          ('독서 토론','발언 순서를 지키며 개인 연락처가 적힌 자료를 배포하지 않습니다.'),
          ('문학 기행','편한 신발을 준비하고 우천 시 실내 독서 활동으로 변경합니다.')]),
 ('안전', [('화재 대피 훈련','안내 방송 후 이동하며 개인 물건을 가지러 되돌아가지 않습니다.'),
          ('자전거 안전 교육','보호 장구를 착용하고 자전거가 없어도 이론 수업에 참여합니다.'),
          ('물놀이 안전','수영 실기 수업이 아니며 물에 들어가는 활동은 없습니다.'),
          ('실험실 안전','보안경을 착용하고 도구는 담당 교사의 안내에 따라 사용합니다.'),
          ('재난 가족 연락','가족 연락 방법을 확인하고 실제 연락처는 공개 게시하지 않습니다.')]),
 ('예술', [('합창 공개 수업','공연 중 이동을 자제하고 관람객 촬영은 사전 동의를 받습니다.'),
          ('미술 전시 준비','작품 뒷면에 학년만 적고 작품 반환은 전시 종료 후 진행합니다.'),
          ('악기 체험','악기는 순환 사용하며 개인 악기 지참은 선택 사항입니다.'),
          ('연극 관람','좌석은 학급별로 배정하고 음식물은 공연장에 반입하지 않습니다.'),
          ('사진 워크숍','본인 기기로 참여하되 다른 학생의 얼굴은 허락 없이 촬영하지 않습니다.')]),
 ('과학', [('천체 관측','구름이 많으면 실내 설명으로 진행하고 늦은 귀가는 보호자가 동행합니다.'),
          ('로봇 제작','부품은 공동 사용하며 완성품은 전시 후 학교에 반환합니다.'),
          ('환경 조사','수집한 생물은 원래 장소에 돌려놓고 결과는 모둠별로 제출합니다.'),
          ('코딩 교실','학교 기기를 사용하고 개인 유료 계정은 만들지 않습니다.'),
          ('기상 기록','정해진 장소에서 관찰하고 위험 기상 시 외부 활동을 중단합니다.')]),
 ('체육', [('걷기 챌린지','기록은 자율 제출하며 건강 사유로 쉬어도 불이익이 없습니다.'),
          ('학급 체육 대회','팀 배정은 학급에서 정하고 우천 시 실내 경기로 진행합니다.'),
          ('뉴스포츠 체험','운동화를 착용하고 장비는 현장에서 대여합니다.'),
          ('가족 배드민턴','보호자와 함께 참여하고 동반 인원도 사전에 신청합니다.'),
          ('아침 스트레칭','정규 등교 시간을 바꾸지 않으며 참가 여부는 자율입니다.')]),
 ('급식', [('급식 모니터링','위생 복장을 착용하고 조리실 출입 구역을 지킵니다.'),
          ('전통 음식 체험','알레르기 재료를 미리 확인하고 시식 참여는 선택 사항입니다.'),
          ('잔반 줄이기','적정량 배식을 연습하며 남긴 양으로 학생을 평가하지 않습니다.'),
          ('식단 의견 모임','특정 학생의 식습관을 공개하지 않고 제안서만 제출합니다.'),
          ('영양 표시 읽기','포장지를 준비하되 내용물이 남은 식품은 가져오지 않습니다.')]),
 ('생활', [('교복 나눔','세탁한 물품만 접수하고 수령 후 교환은 재고가 있을 때만 가능합니다.'),
          ('학부모 공개 수업','방문증을 착용하고 학생 사진은 외부에 게시하지 않습니다.'),
          ('학교 숲 가꾸기','장갑을 준비하고 도구 사용 후 지정 장소에 반납합니다.'),
          ('분실물 찾기','본인 물품인지 확인하고 보관 기간이 지나면 별도 안내합니다.'),
          ('통학로 의견 수렴','개별 가정의 주소는 적지 않고 위험 지점의 위치만 표시합니다.')]),
)


def oracle(before, after, complete=True):
    return 'insufficient' if not complete else ('preserved' if before == after else 'changed')


def examples():
    families = list(range(len(TOPICS)))
    random.Random(20261018).shuffle(families)
    family_split = {f: ('train' if i < 6 else 'calibration' if i < 8 else 'validation')
                    for i, f in enumerate(families)}
    shared = questions()
    for family, (_, records) in enumerate(TOPICS):
        for number, (title, condition) in enumerate(records):
            index = family * 5 + number
            day, amount, choice = 3 + index % 20, 12000 + index * 1000, 2 + index % 2
            date = f'2027년 4월 {day}일'
            deadline = f'2027년 3월 {day}일'
            facts = {'title': title, 'event': date, 'deadline': deadline,
                     'amount': amount, 'choice': choice, 'condition': condition,
                     'pairs': [['과정', '교실'], ['탐구', f'1-{index+1}'], ['창작', f'2-{index+1}']]}
            sha = hashlib.sha256(canonical(facts)).hexdigest()
            group, fam = 'src-' + sha[:20], f'family-{family:02d}'
            deadline_text = f'{title} 신청 마감은 {deadline} 오후 5시입니다.'
            fee = f'참가비 {amount:,}원. 형제 동반자는 무료입니다.'
            choices = f'반드시 {choice}개 활동을 선택해야 합니다.'
            condition_text = condition + ' ※ 제출 후 변경은 담당자에게 문의합니다.'
            table_text = '\n'.join(' | '.join(row) for row in facts['pairs'])
            attached = '신청서는 원본 첨부에서 작성하여 마감일까지 행정실에 제출합니다.'
            # Expected labels derive from structured mutation truth, not gate/model output.
            variants = [
                ('conditions', deadline_text, deadline_text, deadline, deadline, True, 'identity'),
                ('conditions', deadline_text, deadline_text.replace(' ', '\n'), deadline, deadline, True, 'linebreak'),
                ('conditions', deadline_text, deadline_text.replace(deadline, f'2027.4.{day}'), deadline, date, True, 'month_change'),
                ('conditions', deadline_text, deadline_text.replace(deadline, f'2027.3.{day}').replace('오후 5시', '17:00'), deadline, deadline, True, 'notation'),
                ('conditions', deadline_text, deadline_text.replace(f'{day}일', f'{day+1}일'), day, day+1, True, 'deadline_change'),
                ('conditions', deadline_text, deadline_text.replace('오후 5시', '오후 6시'), 17, 18, True, 'time_change'),
                ('conditions', fee, fee, amount, amount, True, 'fee_identity'),
                ('conditions', fee, fee.replace(f'{amount:,}', f'{amount+1000:,}'), amount, amount+1000, True, 'amount_change'),
                ('conditions', choices, choices.replace(f'{choice}개', '1개'), choice, 1, True, 'selection_change'),
                ('conditions', condition_text, condition, [condition, 'footnote'], [condition], True, 'footnote_omitted'),
                ('conditions', condition_text, '※ 제출 후 변경은 담당자에게 문의합니다.', [condition, 'footnote'], ['footnote'], True, 'condition_omitted'),
                ('conditions', '', deadline_text, deadline, deadline, False, 'missing_page'),
                ('table_relations', table_text, table_text, facts['pairs'], facts['pairs'], True, 'table_identity'),
                ('table_relations', table_text, '\n'.join(' | '.join(row) for row in [facts['pairs'][0], ['탐구', facts['pairs'][2][1]], ['창작', facts['pairs'][1][1]]]), facts['pairs'], [facts['pairs'][0], ['탐구', facts['pairs'][2][1]], ['창작', facts['pairs'][1][1]]], True, 'room_exchange'),
                ('table_relations', '\n'.join(table_text.splitlines()[1:]), table_text, facts['pairs'], facts['pairs'], False, 'missing_header'),
                ('omission', condition, condition, condition, condition, True, 'omission_identity'),
                ('omission', condition, '', condition, '', True, 'sentence_omitted'),
                ('addition', condition, condition + ' 현장 접수도 가능합니다.', condition, condition + 'unsupported', True, 'unsupported_addition'),
                ('attachment_role', attached, attached, 'original_attachment', 'original_attachment', True, 'form_attached'),
                ('attachment_role', attached, '신청서 작성 내용을 온라인으로 제출합니다.', 'original_attachment', 'online_form', True, 'role_change'),
            ]
            for criterion, source, candidate, before, after, complete, mutation in variants:
                sid = 'b1'
                state = {'title': title, 'file_role': 'notice', 'source_complete': complete,
                         'source_blocks': [{'id': sid, 'text': source}],
                         'rendered_blocks': [{'id': 'r1', 'source_refs': [sid], 'visible_text': candidate}],
                         'attachment_manifest': [{'id': 'a1', 'role': 'attachment', 'available': True}]}
                yield {'state': state, 'questions': {criterion: shared[criterion]},
                       'expected': {criterion: oracle(before, after, complete)},
                       'meta': {'schema_version': 'moa.training.row.v1', 'original_group_id': group,
                                'family_group_id': fam, 'source_sha256': sha,
                                'split': family_split[family], 'rights_status': 'allowed',
                                'rights_evidence': 'independently_authored_synthetic_by_moa',
                                'label_origin': 'deterministic_oracle', 'synthetic_gold': True,
                                'human_gold': False, 'muse_derived': False, 'forbidden_lineage': [],
                                'lineage': ['original_source', 'deterministic_transform'],
                                'mutation': {'operation': mutation, 'before': before, 'after': after,
                                             'evidence_complete': complete}}}


def validate_rows(rows):
    groups, families, sources = {}, {}, {}
    for row in rows:
        meta = row['meta']; split = meta['split']
        for mapping, key in ((groups, meta['original_group_id']), (families, meta['family_group_id']),
                             (sources, meta['source_sha256'])):
            if key in mapping and mapping[key] != split:
                raise ValueError('group leakage')
            mapping[key] = split
        proof = meta['mutation']
        if set(row['expected'].values()) != {oracle(proof['before'], proof['after'], proof['evidence_complete'])}:
            raise ValueError('independent oracle mismatch')
        if meta.get('label_origin') != 'deterministic_oracle' or meta.get('forbidden_lineage') != [] or meta.get('muse_derived'):
            raise ValueError('forbidden label provenance')
        if any(k in row['state'] for k in ('expected', 'mutation', 'label', 'split', 'meta')):
            raise ValueError('label hint in inference state')
    return groups, families


def build(root: Path):
    rows = list(examples())
    groups, families = validate_rows(rows)
    destination = Path(root) / 'training-bundles' / BUNDLE_ID
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (destination / 'TRAINING_BUNDLE_READY.json').exists():
        manifest = json.loads((destination / 'TRAINING_BUNDLE_READY.json').read_text())
        for shard in manifest['shards']:
            content = (destination / shard['file']).read_bytes()
            if len(content) != shard['bytes'] or hashlib.sha256(content).hexdigest() != shard['sha256']:
                raise ValueError('existing bundle shard integrity failure')
        return manifest
    shards = []
    for split in ('train', 'calibration', 'validation'):
        chunks = []; raw = b''
        for row in (r for r in rows if r['meta']['split'] == split):
            line = canonical(row) + b'\n'
            if len(raw) + len(line) > MAX_SHARD_BYTES:
                chunks.append(raw); raw = b''
            raw += line
        if raw: chunks.append(raw)
        for i, content in enumerate(chunks):
            name = f'{split}-{i:03d}.jsonl'
            atomic_write(destination / name, content); (destination / name).chmod(0o600)
            shards.append({'file': name, 'split': split, 'bytes': len(content),
                           'sha256': hashlib.sha256(content).hexdigest(), 'rows': content.count(b'\n')})
    manifest = {'bundle_id': BUNDLE_ID, 'schema_version': 'moa.training.bundle.v1',
                'contract_version': 'moa.worker.v1', 'rubric_revision': RUBRIC_REVISION,
                'rubric_sha256': rubric_sha256(), 'rights_status': 'allowed',
                'rights_evidence': 'independently_authored_synthetic_by_moa',
                'forbidden_lineage': [], 'sealed_audit_present': False,
                'human_gold': 0, 'synthetic_gold': len(rows), 'rows': len(rows),
                'source_groups': len(groups), 'template_families': len(families),
                'group_splits': groups, 'family_splits': families,
                'split_roles': {'train': 'training', 'calibration': 'calibration', 'validation': 'held_out_test'},
                'split_counts': {s: {'groups': sum(v == s for v in groups.values()),
                                     'families': sum(v == s for v in families.values()),
                                     'rows': sum(r['meta']['split'] == s for r in rows)}
                                 for s in ('train', 'calibration', 'validation')},
                'shards': shards, 'existing_corpus_used': False,
                'excluded_corpus_evaluation_and_derivatives': True,
                'inference_input': ['state', 'questions'], 'maximum_epochs': 1,
                'maximum_optimizer_updates': 200, 'maximum_active_seconds': 2700,
                'auto_pass_enabled': False, 'auto_publish_enabled': False}
    atomic_write(destination / 'TRAINING_BUNDLE_READY.json', canonical(manifest))
    (destination / 'TRAINING_BUNDLE_READY.json').chmod(0o600)
    return manifest


def receipt_errors(root, receipt):
    if receipt.get('bundle_id') != BUNDLE_ID:
        return ['unknown bundle']
    path = Path(root) / 'training-bundles' / BUNDLE_ID / 'TRAINING_BUNDLE_READY.json'
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != receipt.get('manifest_sha256'):
        return ['manifest hash mismatch']
    manifest = json.loads(path.read_bytes())
    if receipt.get('shard_sha256') != {s['file']: s['sha256'] for s in manifest['shards']}:
        return ['incomplete shard receipt']
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.data_dir)
    print(json.dumps({k: result[k] for k in ('bundle_id', 'source_groups', 'template_families', 'rows', 'split_counts')}))


if __name__ == '__main__':
    main()
