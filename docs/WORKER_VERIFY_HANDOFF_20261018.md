# MacBook verify 접속 합의 — 2026-10-18

맥북 Codex 실행 지시: 기존 맥북 워커와 미커밋 변경을 보존하고 아래 NAS 계약에
통신 어댑터를 맞춘 뒤, 발행된 합성 verify **1건만** 실제 LAYA 추론으로 처리하라.
claim → manifest 다운로드/해시 검증 → 최소 1회 heartbeat → 추론 → complete/ack를 확인하라.
모델이 틀리면 실제 결과를 그대로 보고하라. 정답/확률을 만들어 보내거나 임의로 고치지 마라.
LAYA 훈련·운영 승격·운영 데이터 투입·유료 외부 AI·토큰 출력은 하지 않는다.
기존 토큰 파일과 SSH 터널을 재사용한다. API 결과/모델 상태를 확인한 뒤 실제 수행 여부를 보고하라.

## 정식 식별자

- `contract_version`: `moa.worker.v1`
- `contract_hash`: `a3b085c9b48e72418c6737f3da4f4d61a5d0bbba59a006be2e582271fb6da1fb`
- 정식 스키마 해시 필드명은 `contract_hash`. `schema_sha256` 별칭은 보내지 않는다.
- 정식 `rubric_revision`: `moa.rubric.v1`. Book 내부 질문 구현 이름은 별도 메타데이터이며 이 값을 대체하지 않는다.
- `validator_hash`: 서버의 `worker_contract.py` 바이트 + `worker_api.py` 바이트 + `WORKER_DDL` UTF-8 바이트 + `RUBRIC_REVISION` UTF-8 바이트를 순서대로 이어 SHA-256. 모델/스키마/질문 파일 해시가 아니다. 구현 수정 시 바뀌므로 실행 직전 capabilities에서 받아 결과 payload에 그대로 넣는다. 작업 발행 시의 validator와 현재 validator가 모두 일치해야 한다.
- canonicalization ID: `python-json-sort-keys-utf8-v1`.
  정확한 Python 식: `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')`.
  줄바꿈/Unicode 정규화/BOM 없음. 배열 순서 유지. `2.0`은 `2.0`, 빈 값과 null 유지. NaN/Infinity 금지. RFC 8785/JCS와 같다고 가정하지 않는다.
- 입력 해시 대상: `{job_kind,payload,source_revision,render_revision,rubric_revision}`.
  현재 v1 구현의 `idempotency_key`는 이 입력 해시와 같다. **result는 원래 job의 두 해시를 그대로 되돌려준다. 결과 payload로 재계산하지 않는다.**
- `result_ack=true`, `result_ack_mode=inline_complete_response`: complete의 200 JSON에 `ok,accepted,duplicate,job_id,idempotency_key,input_sha256,verdict`. 별도 ack endpoint는 없다.
- `model_upload=false`: 모델 업로드 endpoint 미지원. 이번 작업은 verify만 실행한다.

배포 시 확인한 `validator_hash`는
`1f674fa6490136d67f31fb6c6a6c89865af49bceb30aeb2df327d888d7a2249c`다.
예전 `9461e5bf…` 값은 수정 전 구현의 해시다. 현재 값은 매 실행 capabilities로 재확인한다.

## 통신 순서

Book의 기존 SSH 터널 주소는 `http://127.0.0.1:18781`이다. 기존 보호 파일의 토큰을 Authorization 헤더로만 읽어 쓴다. 비밀 값은 로그/명령 인자/보고에 넣지 않는다.

1. GET `/v1/worker/capabilities`로 계약 해시를 확인한다. 다르면 중지한다.
2. POST `/v1/worker/claim`: `{"worker_id":"macbook-laya-01","kinds":["verify"]}`.
   200의 `job`을 사용한다. 204는 본문 없이 대기한다.
3. GET `/v1/worker/artifacts/{artifact_id}`. 같은 Authorization과 아래 네 헤더가 필수다.

   - `X-MOA-Worker-ID`: `macbook-laya-01`
   - `X-MOA-Job-ID`: 받은 job의 `job_id`
   - `X-MOA-Lease-ID`: 받은 job의 `lease_id`
   - `X-MOA-Fencing-Token`: 받은 정수 token을 10진 문자열로 전송

   JSON 원본 응답 바이트의 SHA-256을 `payload.source_manifest_artifact.sha256`과 대조한다.
   manifest의 revision/scope/complete와 각 원문 블록의 UTF-8 해시도 검증한다.
   최대 1,048,576바이트. 임의 URL·경로·query 불가. 본인 현재 lease의 연결된 artifact만 제공한다.
4. POST `/v1/worker/jobs/{job_id}/heartbeat`:
   `{"worker_id":"macbook-laya-01","lease_id":"<받은 값>","fencing_token":<받은 정수>}`.
   lease 600초, heartbeat 30초 권장, 서버 시각 기준. 빠른 추론도 이번 연결 검증에서는 최소 한 번 heartbeat한다.
5. 실제 로컬 LAYA 추론 결과만 complete로 보낸다. 정답이나 확률을 수작업으로 만들어 보내지 않는다.
   POST `/v1/worker/jobs/{job_id}/complete`: `{"worker_id":"macbook-laya-01","result":<result envelope>}`.
   result envelope는 받은 job의 공통 식별자/lease/fencing/리비전/두 입력 해시를 유지하고, `message_type=result`, `payload`만 결과로 교체한다.
6. ack를 검증한 후 로컬 outbox를 확인 완료로 표시한다. 같은 결과 재전송은 같은 ack와 `duplicate=true`.
   내용이 다른 재전송, 만료/다른 lease, 크거나 작은 fencing, 리비전 불일치는 거부한다.

## verify 결과 payload

원 설계 `04_WORKER_CONTRACT.md`의 결과 형식을 사용한다.

- `validator_hash`: 방금 받은 capabilities 값.
- `status`: `completed`, `deferred`, `error`.
- `checkpoint_sha256`, `model_revision`, `tokenizer_revision`, `calibration_revision`.
- `answers`: 요청된 질문 이름을 키로 하는 객체. 이 작업은 `conditions` 한 개.
  각 값은 `label` (`preserved/changed/insufficient`), `raw_probabilities` (세 label 모두, 유한한 0~1, 합 1),
  `calibrated_probabilities` (없으면 null), `calibrated` (보정 없으면 false).
- `inspected_source_ids`, `inspected_render_ids`, `missing_context_ids`, `actual_tokens`, `truncation`, `warnings`, `runtime`.
- context가 없거나 잘렸다면 `completed`로 보내지 않고 `deferred` 처리한다.

complete 수락은 통신/작업 완료다. `changed`, `insufficient`, 미보정 결과는 자동 품질 통과로 만들지 않는다.
합성 작업에는 `notice_id`가 없으므로 실자료 machine_checks나 사람 검수 기록에 연결되지 않는다.
Book의 3/7 스모크는 사용자 보고값이며 운영 승격 근거가 아니다. 훈련/모델 업로드는 실행하지 않는다.

## 발행/확인

- 고정 job ID: `job-synthetic-verify-20261018-01`
- artifact ID: `artifact-bef5d4fcc0933cc5b6b6e86d57bec0e5d69d96a2c3f249530a5ef641ca2765ac`
- 대상 worker: `macbook-laya-01`
- source/render revision: `synthetic-deadline-source-v1` / `synthetic-deadline-render-v1`
- 순수 합성 한국어 한 구역의 신청 마감 변경. 개인정보/실제 통신문 없음.
- NAS 앱 안에서 `python -m moa.worker_smoke --data-dir /data --publish`는 같은 job을 중복 발행하지 않는다.
- `python -m moa.worker_smoke --data-dir /data`는 읽기 전용 상태 조회다.
- 실제 Book 왕복 증거는 worker_job_events의 claim/heartbeat/complete와 worker_results다.
  로컬 HTTP 합성 테스트 통과를 Book 실제 추론 완료로 보고하지 않는다.

## 미니 측 실측 결과

- NAS 검수 서비스와 미니 중계 배포 완료. 수집기 컨테이너의 시작 시각/이미지는 보존했다.
- health 200 / 무토큰 capabilities 401 / 토큰 capabilities 200. 계약 해시 일치.
- 실제 artifact 경로는 무토큰 401, 유효 lease 헤더 없으면 409.
- 작업 1개, artifact 1개. 발행 직후 pending, claim/heartbeat/complete 이력 0, 결과 0.
- 실자료 machine_checks 0, 사람 mobile_reviews 0. 합성 job에 notice 연결 없음.
- NAS DB 사전 온라인 백업 및 배포 후 quick_check 모두 ok. 기존 환경설정 바이트/600 권한 보존.
- 로컬 합성 전체 테스트 207개 통과. 중계까지 거치는 HTTP 왕복/중복 ack,
  lease 만료/재할당/fencing, 입력 해시/리비전, artifact 접근·무결성·1MiB 상한,
  stale 결과 후 정상 재전송, 동시 중복 발행, redirect 인증정보 전달 차단을 확인했다.
- 위 HTTP 테스트의 모델 결과는 명시적인 테스트 fixture다. 맥북 모델 추론 결과가 아니다.
- 미검증: 실제 맥북 워커가 변경된 계약으로 처리한 왕복과 실제 모델 판정.
