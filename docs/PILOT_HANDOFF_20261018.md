# MOA 시험 운영 공통 계약 · 2026-10-18

이 문서는 이전 합성 verify 완료 기록을 대체 발행하지 않는다.
`job-synthetic-verify-20261018-01`은 완료 상태를 보존한다.

## 서로 다른 식별자의 의미

| 항목 | 의미와 계산 |
|---|---|
| `contract_hash` | `contracts/worker-envelope.schema.json` 원본 바이트의 SHA-256. `a3b085c9b48e72418c6737f3da4f4d61a5d0bbba59a006be2e582271fb6da1fb` 유지 |
| 옛 `schema_sha256` | 연결 파일의 옛 이름일 뿐, 다른 해시/스키마를 허용하는 별칭이 아니다. 실제 배포 스키마 바이트로 대조 |
| `rubric_revision` | NAS 통신 식별자 `moa.rubric.v1` |
| `questions_revision` | 한국어 질문 5종 `moa.ko.section.v1`. 날짜·대상·마감·필수/제외/미신청 조건, 행/열 연결, 첨부 역할을 실제 질문으로 검사 |
| `rubric_sha256` | 위 질문 정의 전체를 canonical JSON으로 직렬화한 SHA-256. 실제 맥북 policy 질문과 대조: `021c2610b934ce90a9223723fcc7c1fd6f030cd5efc90add71458f90e92c024f` |
| `validator_hash` | NAS 판정 구현의 지문. `worker_contract.py` 바이트 → `worker_api.py` 바이트 → `WORKER_DDL` UTF-8 → `moa.rubric.v1` UTF-8 → `worker_handoff.py` 바이트 → `verification.py` 바이트 순서로 SHA-256에 누적. 모델 가중치/스키마 해시가 아님 |
| `worker_code_sha256` | 별도 맥북 executor의 구현 지문. 소량 연결기는 자기 파일 → Book policy.py → ml.py → runtime.py 바이트를 순서대로 누적. NAS validator와 같아야 하는 값이 아님 |

Canonicalization은 `python-json-sort-keys-utf8-v1`:
`json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')`.
개행 추가·Unicode 정규화 없음. 정수만 허용하는 옛 구현과 달리 유한 실수도 처리한다.
원문 블록은 **따옴표 없는 text의 UTF-8 바이트** SHA-256이며 canonical JSON 문자열 해시가 아니다.

## 준비 확인과 전송

- `/v1/worker/handoff`는 토큰 인증 후 실제 질문·canonical 벡터·크기 상한·현재 구현 지문을 제공한다.
- `/v1/worker/ready`는 실제 워커의 스키마 해시, 질문 정의, canonical 벡터 재계산과 구현 지문을 검사한다.
  서버 값을 그대로 복사한 별칭 치환만으로 정식 지원이라고 주장하지 않는다.
- 새 pilot job은 준비 확인된 **동일 executor 코드 해시를 claim body로 제시**해야 한다.
  옛 상시 데몬이 같은 worker_id로 새 pilot job을 가져가지 못한다.
- `scripts/moa-book-pilot.py`는 기존 Book LAYA policy/ml/runtime을 사용하는 별도 bounded pull client다.
  기존 상시 데몬을 덮어쓰거나 재설치하지 않는다. `daemon_ready=false`를 명시한다.
- 아티팩트는 1MiB 이하, live lease의 worker/job/lease/fencing 헤더 네 개를 모두 검사한다.
- result_ack는 complete 응답 내 inline ACK. 별도 ACK API 없음. 모델 업로드는 지원하지 않는다.
- 토큰은 기존 보호 파일에서 읽어 Authorization 헤더에만 넣는다. 기존 LAN SSH 터널을 재사용한다.

## 실자료 판정

`moa.pilot`은 기존 DB만 사용한다. 3건부터, 첫 3건의 전체 경로 확인과 중요 오류 해결 전 추가 확장을 막고 최대20건으로 제한한다.
원문 객체 해시, 지원 파서의 별도 원문 inventory, HTML revision/hash, 실제 DOM 세 화면 크기(360/393/430),
원본·HTML·ZIP 다운로드를 검사한다. 파서 커버리지는 사람의 원본 대조 완료와 다르다.
긴 문맥을 임의로 잘라 완료로 만들지 않는다. 미지원 구역·누락 문맥은 예외로 남긴다.

원문/실제 DOM 규칙 판정, 모델 원판정·확률, NAS 최종 판정을 서로 다른 필드에 저장한다.
중요 내용 불일치는 LAYA `preserved`로 지워지지 않는다. 이 단계 실자료 최종은 항상 `needs_review`다.
job done은 통신 작업 완료이지 문서 추출 성공·전체 품질 통과·사람 승인이 아니다.
승인·자동 공개·모델 자동 승격을 실행하지 않는다.

## 독립 합성 학습 묶음

`moa.training_bundle`은 원본 사실 그룹50개/semantic family10개에서 1,000쌍을 만든다.
**독립 양식 family50개를 확보했다는 뜻은 아니다. 원본 그룹50개와 family10개를 구분한다.**
train600(원본30/family6), calibration200(10/2), validation200(10/2), family/source hash 그룹 경계를 공유한다.
기존 평가 전용 자료·파생본, 미확인 보류, JEV 출력·LAYA 자기 판정은 사용하지 않는다.
인간 정답0개, 합성 oracle 정답1,000개를 분리한다. 정답/변형종류/분할 메타데이터는 추론 state에 넣지 않는다.
실자료는 이용 권한·사람 검수가 확인되지 않아 이번 학습 묶음에 넣지 않았다.

실제 JSONL은 private NAS/Book 폴더에만 두고 Git에는 생성 코드·계약·테스트·메타데이터만 둔다.
각 shard는512KiB 이하, manifest와 각 파일의 바이트 해시/행 수로 Book 수신 확인을 받는다.
`training-receipt`는 전송 확인이지 훈련 완료나 품질 통과 증거가 아니다.

## 호환성과 운영 경계

core schema version5와 기존 collector 테이블/컬럼은 변경하지 않는다. 새 worker/evidence 테이블만 추가한다.
DB 온라인 백업 quick_check와 앱/환경설정 보호 백업 후 review만 교체한다. collector는 재시작하지 않는다.
기존 승인 기록·원본 파일·수집 실적을 초기화하지 않는다. 전체 자료 일괄 변환도 하지 않는다.
Muse/JEV의 실제 무료 자격·전송 조건은 확인되지 않아 호출하지 않는다. 유료 대체/충전 없음.

## Book 후속 작업과 현재 미완료 경계

실행 기준 코드는 `a57edf282c69a80da910919111fbb61514715197`다. 문서 전용 후속 커밋은 실행 모듈 지문을 바꾸지 않는다.
별도 bounded client로 새 pilot 작업45개가 NAS에 수락됐다(실추론44, 토큰 초과 사전 보류1).
이 결과는 기존 상시 데몬의 통합 완료가 아니다. 새 묶음 `moa-ko-synthetic-20261018-v1`은 Book 실제 파일/해시/loader 확인과 NAS receipt까지 완료했지만 아직 훈련하지 않았다.

Book 담당자는 `incoming/MOA_PILOT_STATUS_2026-10-18.md`, `incoming/HANDOFF_READY.json`,
`incoming/moa-ko-synthetic-20261018-v1/TRAINING_BUNDLE_READY.json`을 함께 읽는다.
기존 worker와 로컬 변경을 보존한 별도 patch/커밋으로 계약 어댑터와 학습 소비를 통합한다. 같은 원격 브랜치 동시 push 금지.

- `blocked_by=book_daemon_adapter_pending`: 실제 스키마 바이트·질문·canonical 벡터·4개 lease 헤더·inline ACK를 검증한다. 서버 해시 복사로 우회하지 않는다.
- `blocked_by=book_trainer_budget_alignment`: 현재 trainer의 내부90분 상한을 이 시험의45분 상한에 맞춘다. 최대1epoch/200updates/45분, encoder freeze/micro batch1을 지키며 AC·유휴15분·메모리·디스크·기기 lock을 우회하지 않는다.
- 새 묶음 실제 학습 → candidate 재로딩 → 분리된 calibration/held-out 평가 증거를 남기고 Mini에 patch와 결과를 돌려준다. JEV/자기 판정/미확인 보류를 정답으로 쓰지 않는다. 운영 champion 승격 없음.
- `blocked_by=pilot_incomplete_context`: Mini가 긴 표/본문의 문맥 보존 분할과 혼합 문서 경계를 해결하기 전 누계20건으로 확대하지 않는다.

훈련 실행이나 데몬 재시작을 했다고 주장하지 않는다. 기존 완료 합성 job을 재발행하지 않는다.
