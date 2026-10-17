# MOA 자동 변환·검증·학습 (2026-10-12 설계) — 구현 상태

기준: moa.worker.v1 계약 + contracts/worker-envelope.schema.json.
NAS 앱 DB가 작업 상태의 단일 기준이다. Mac mini는 중계·실행, MacBook은
pull worker다. SMB로 운영 SQLite를 직접 열지 않는다.

## 상태 정의 (합치지 않는다)

- machine_passed: 규칙·지정 검사를 통과한 버전. 사람 승인이 아니다.
  machine_checks 테이블에 버전별로 기록된다.
- human_approved: 실제 사람이 해당 버전을 원문과 비교해 승인.
  mobile_reviews / cases에만 기록된다. worker complete는 이 표를
  절대 쓰지 않는다 (테스트로 강제).
- needs_review / deferred / source_unverified / repair_exhausted:
  의미·역할·원문 판단 필요, 자원·한도 대기, 원문 미확보, 수정 횟수 소진.

## 사람 예산

- 초기 대표 20~30건 확인 후 하루 표시 상한 3건 (무작위 1 + 예외 최대 2).
- 상한 초과 미확실 자료는 원본 유지·대기. 자동 승인하지 않는다.
- GET /mobile/state가 machine_check와 human_approved_revision을
  분리해서 보여준다.

## 외부 모델 게이트 (현금 0 고정)

- Muse / JEV 키가 없으면 NEEDS_PROVIDER_CONFIGURATION으로 남고 호출하지
  않는다. 유료 fallback·자동 충전·BYOK는 설정으로도 켤 수 없게 강제된다.
- JEV는 운영 검증 전용. JEV 판정·확률·선택 자료는 학습 진입에서
  거부된다 (validate_train_payload fail-closed).
- Muse 출력의 학습 재이용은 명시적 허용 근거 전까지 차단된다.
- LAYA는 MacBook 측. 이 저장소에는 계약·스플릿·계보 검사만 있다.

## 학습 데이터 계보

- 허용: 독립 사람 라벨 + 결정 가능한 합성 oracle.
- 금지: jev_output / jev_label / jev_selected / laya_self_label,
  eval·sealed audit 행, Muse 유도(허가 전).
- 원문 그룹 단위 분할. 같은 원문의 변형은 같은 그룹.

## 워커 API (별도 토큰)

- MOA_WORKER_TOKEN (MOA_REVIEW_TOKEN과 분리, 미설정 시 비활성).
- GET /v1/worker/capabilities, POST /v1/worker/claim,
  POST /v1/worker/jobs/id별 heartbeat·complete·fail.
- 토큰은 Authorization 헤더 전용. 서버 시각 lease, fencing token,
  idempotency 중복 무시, 오래된 결과는 stale 기록·미반영.
- 연결 파일 MOA_MACBOOK_CONNECTION.json에는 비밀을 넣지 않는다.

## 아직 실측 전인 것

- Muse 무료 연결, JEV 무료 자격·잔액·지원 여부 (키 없음 — 미호출).
- MacBook LAYA 설치·MPS 추론·훈련 (터널·키 전달 전).
- 실물 5건부터 20건 파일럿, 브라우저 검사, 봉인 audit.
