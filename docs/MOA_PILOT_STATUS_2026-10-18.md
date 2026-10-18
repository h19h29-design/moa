# MOA 시험 운영 진행상황 — 2026-10-18

확인 기준: 2026-10-18 15:20 KST. 기능 코드와 문서 전용 커밋을 구분한다.

## 한눈에 보기

**부분 완료다.** NAS 배포, 실제 문서3건의 HTML·브라우저 검사, 맥북 소량 검증 연결,
독립 합성 학습자료 전달까지 실행했다. 20건 확대·새 자료 훈련·상시 워커 통합은 완료하지 않았다.

| 항목 | 실제 결과 |
|---|---|
| 기존 수집 통신문 | 6,581건 유지. 수집 목표·요청 예산·백필 배치 변경 없음 |
| 이번 실제 문서 | 3건 HTML 생성, 브라우저 검사9회(360/393/430px) |
| 원문 ↔ 실제 DOM 규칙 | 2건 일치. 혼합 문서1건은 원본/서식 경계 확인 필요 |
| 맥북 작업 | 새 pilot verify45개 NAS 수락. 실제 추론44개, 문맥 한도 사전 보류1개 |
| 문서 전체 모델 구역 커버리지 | 1/3건 완료. 나머지2건에는 미검사 구역이 있음 |
| 20건 확대 | 실행하지 않음. 첫3건의 미검사·중요 예외가 남아 확대 차단 |
| 사람 승인 | 표 사례0건, 모바일0건. 검수 기록을 대신 만들지 않음 |
| 미검수 | 변환 후보4건 중 이번 시험3건. 원문 파일 준비 상태6,579건은 HTML 변환 완료가 아님 |
| 새 학습자료 | 독립 합성 원본 그룹50개 / semantic family10개 / 판정쌍1,000개 |
| 새 묶음 훈련 | 미실행. 전달/데이터 검사 통과와 훈련 완료를 구분 |
| 자동 품질 통과·공개·모델 승격 | 모두 꺼짐 |

## 사이트 사용법과 접속 확인

접속: <https://moa.h19h19.com>

기존 검수 암호로 로그인한다. 암호는 변경하지 않았으며 이 문서에 적지 않는다.
첫 화면의 **오늘 확인할 자료**는 중요 예외 최대2건 + 전체 점검 표본1건, 하루 최대3건으로 고정한다.
아래 전체 기록을 매일 모두 검수할 필요는 없다.

자료를 열고 원본/실제 모바일 결과를 비교한 뒤 **승인** 또는 **보류**를 누른다.
검수자 이름 입력은 없다. 미확인 보류는 오답 라벨이 아니다.
자동 검증 구역에서 현재 규칙 결과, 맥북 원판정·확률, NAS 최종 상태를 따로 확인할 수 있다.
신청서·서식은 실제 원본 다운로드를 사용한다. 온라인 신청 기능으로 바꾸지 않았다.

실측:

- Chrome에서 로그인·자료 화면·원본·HTML·첨부 ZIP 다운로드 성공.
- 원본 다운로드 SHA-256 전부 일치, ZIP 안 실제 원본도 일치. JS 오류0, 전체 페이지 가로 넘침0.
- 비로그인 상태의 원본·미리보기·ZIP·worker handoff는401. 공개 접근으로 바꾸지 않았다.
- Python 기본 User-Agent는 외부 Cloudflare 경유403, NAS 직접 요청은401이었다.
  일반 브라우저는 로그인 페이지401 → 로그인 후200. 구체적인 Cloudflare 차단 규칙 ID는 미확인이다.
- Cloudflare 분석 스크립트 삽입 때문에 외부 HTML 바이트가 달랐다.
  `Cache-Control: private, no-store, no-transform`을 적용한 뒤 3건×3폭 모두 저장 HTML 해시와 일치했다.
  인증·TLS·방화벽을 해제하지 않았다.

브라우저/보안 검증 스킬 기준으로 실제 DOM·다운로드·인증 경계를 검사했다.
브라우저 플러그인은 없어 설치된 Chrome + 일반 Playwright를 사용했다.
기하 검사 통과를 모든 가독성 문제의 해결로 해석하지 않는다.

## 실제 문서별 결과

| 문서 | HTML/규칙/브라우저 | 맥북 결과와 남은 문제 |
|---|---|---|
| 멘토링 HWP | 모두 실행. 사용자 제공 원문 기준1~17번 학과·교실 연결을 파싱 결과와 실제 DOM3폭에서 대조해 일치 | 21/21구역 추론 완료. 최종은 미검수 |
| 정기시험 안내 | 병합 관계를 유지한 표로 표시, 규칙·브라우저 실행 | 10작업 수락 중9개 추론, 1개는1,274토큰 예상으로1,024 상한 초과 사전 보류. 잘라서 통과시키지 않음 |
| 지원금 안내/신청서 혼합 | HTML·원본 첨부·브라우저 실행. 서식 구역은 원본에 남기고 경계 확인 경고 유지 | 14작업 추론. 게시글 본문의37,739자 구역은 안전한 분할이 없어 모델 검사에서 제외. 전체 문서 성공 아님 |

원문 inventory와 실제 DOM은 별도로 읽는다. 다만 지원 파서가 원본 전체를 읽었다는 것은 사람의 원본 대조 완료와 다르다.
HWP 자체의 브라우저 내 원본 미리보기는 제공하지 못하며 원본 다운로드를 사용한다.
사용자가 제공한 원본 이미지와 생성 HTML 캡처를 혼동하지 않았다.
이미지·스캔 PDF는 이번 로컬 경로에서 자동 변환 성공으로 취급하지 않는다.

모델 `preserved`가 날짜·금액·선택 개수·조건·학과-교실 불일치 같은 중요 규칙을 지우지 못한다.
모델 원판정을 규칙 정답으로 고쳐 저장하거나 학습 정답으로 재사용하지 않았다.
전체 실자료 NAS 최종 상태는 `needs_review`다. job done은 사람 승인이나 전체 문서 품질 통과가 아니다.

## 맥북과 주고받은 증거

기존 LAN SSH 터널과 보호된 토큰 파일을 재사용했다. 공개 포트·Tailscale·새 모델 설치 없음.
`macbook-laya-01`의 기존 LAYA policy/ml/runtime을 사용하는 별도 bounded pull client로 실행했다.
기존 상시 데몬은 덮어쓰지 않았으며, 새 소량 연결기를 상시 데몬 완료로 보고하지 않는다.
새 작업은 준비 확인한 executor 코드 해시까지 일치해야 claim 가능하므로 옛 데몬이 가져가지 않는다.

- 공통 계약: `moa.worker.v1`, 스키마 바이트 SHA-256 `a3b085c9b48e72418c6737f3da4f4d61a5d0bbba59a006be2e582271fb6da1fb` 유지.
- 실제 Book 질문 정의와 canonical 계산 벡터를 비교하고 준비 ACK를 NAS가 수락했다.
- 서버 rubric `moa.rubric.v1`, 실제 질문 집합 `moa.ko.section.v1`의 명시적 매핑.
  질문 정의 SHA-256 `021c2610b934ce90a9223723fcc7c1fd6f030cd5efc90add71458f90e92c024f`.
- NAS validator `904d0346eec340fb63063958ea88ad8606d1350cb23d254718120f57625aaed2`는 NAS 구현 지문이며 모델 해시가 아니다.
- 실제 모델 revision `1720e3e3357cfe1e281542e223f8273b0890ca34`, checkpoint SHA-256 `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204`.
  MOA 보정 없음. 모델/규칙/사람 승인 구분 유지.
- 새 작업 ID 범위 예: `job-pilot-6fe201baa87f5c52841eb2ff42bb7d8a` … `job-pilot-d88ce3d16f079ceb6f5f0e2a6de1c2db`.
  전체45개는 DB와 `pilot_documents.manifest.model_job_ids`에 보관.
- claim46회 / heartbeat45회 / complete45회. 최초 claim 응답 wrapper 처리 오류1회는 정상 fail/retry로 해결했다.
  기존 완료 합성 작업 `job-synthetic-verify-20261018-01`은 done/fence2 그대로이며 다시 발행하지 않았다.
- 비밀 없는 `HANDOFF_READY.json`은 NAS `/volume2/moa/data/coordination/`과 Book `~/Library/Application Support/MOA-LAYA/incoming/`에 전달하고 Book에서 파일 해시 수신 확인을 남겼다.

## 학습자료 전달 — 훈련 완료 아님

묶음: `moa-ko-synthetic-20261018-v1`

- train600쌍(원본30/family6), calibration200(10/2), held-out validation200(10/2).
- 원본·family·source hash의 모든 파생본을 동일 분할에 둔다. 같은 원본을 무작위 행 단위로 섞지 않는다.
- 사람이 확인한 정답0개, 독립 구조/변형 oracle 합성 정답1,000개로 구분한다.
- 기존 평가 전용941건과 그 파생본, 운영 보류, JEV 출력, LAYA 자기 판정을 넣지 않았다.
- 실자료는 권한·정확성 검수가 완료되지 않아 이 학습 묶음에 넣지 않았다.
- shard4개, 각512KiB 이하. manifest SHA-256 `ef81fadafc79bb2cfdee81dffcd02c14b237cef11ffd48d885e312d8bb932a7e`.
- 실제 Book 수신 파일 해시/크기를 모두 검증했고, Book의 실제 `validate_dataset(..., minimum=True)`도 통과했다.
  NAS `training_receipts`에도 ACK1건을 저장했다.
- 비공개 보관: NAS `/volume2/moa/data/training-bundles/moa-ko-synthetic-20261018-v1/`, Book `incoming/moa-ko-synthetic-20261018-v1/`.
  JSONL·원문·DB·토큰·모델은 Git에 넣지 않았다.

새 묶음 학습은 실행하지 않았다. 사전 점검 때 Book의 AC/유휴15분/메모리 기준 중 유휴 기준이 미충족이었다.
또 기존 trainer 내부90분 상한은 이번45분 상한과 맞지 않아 학습 실행기 정합성 보완이 필요하다.
기존 Book의 합성 스모크 학습 증거는 이번1,000쌍 훈련 실적으로 세지 않는다.
새 후보 학습·재로딩·독립 평가·결과 통합·상시 소비 연결이 남았다. 현재 모델은 승격하지 않았다.

Muse/JEV는 실제 계정의 무료 자격·지원·전송 조건이 확인되지 않아 `provider_not_configured/not_tested` 상태로 남겼다.
이번 작업의 유료 외부 AI 호출·자동 충전·유료 대체는0회다. Codex 계정 자체 사용량·전력 비용의 총액을 뜻하지 않는다.

## 테스트·배포·복구점

- 기준 코드 `84a630e136638fea4536ba926b996d3676010dec` → 기능 코드 `a57edf282c69a80da910919111fbb61514715197`.
- 브랜치 `codex/moa-autoqa-v1` push 완료. [PR #1](https://github.com/h19h29-design/moa/pull/1)에 이번 변경·검증 결과를 반영했다. main 병합·강제 push 없음.
- 새 전체 회귀225개 통과. compileall / JS syntax / git diff --check 통과. GitHub CI 결과는 별도 미확인.
- 백업: `/volume2/moa/data/deploy-backups/pilot-20261018T150000/`의 `before.sqlite3`, `app-before.tar.gz`.
  DB 온라인 백업 및 배포 후 quick_check 모두ok. 백업·설정은 보호 권한 유지.
- rollback image: `moa-notice-collector:pre-pilot-20261018-150000` 보존. 자동 복원은 실행하지 않음.
- review만 재생성. 실제 image `4db66a831c3576294aae7782eacd648af3bfcc15b76bbb24eb0a1323666c13e6`.
- collector image `9b3978df5a0256d0c9377260813333a63cdbca1baf25ba66fa43b88149d6617f`, 시작 시각2026-10-17 그대로/healthy.
- 기존 collector 코드로 새 DB를 열어6,581건 읽기 성공. core user_version5/기존 컬럼 계약 유지, core.py 바이트 변경 없음.
- 기존 `.env` 해시 유지. 원본·출처·승인 기록 보존. 추가 worker/evidence/추천 테이블만 사용.
- NAS 실행 모듈10개와 외장 작업본 해시를 대조해 모두 일치를 확인했다. 다른 NAS 서비스·Docker 시스템 설정은 변경하지 않았다.

## 확인·중지·다음 실행

NAS 상태 확인(기존 SSH 별칭):

```sh
ssh ds925-home 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec app-review-1 python -m moa.pilot --data-dir /data status'
```

소량 맥북 연결기는 이번 실행을 마치고 종료됐다. 자동으로 추가20건을 처리하지 않는다.
상시 기존 MOA 워커의 신규 claim을 잠시 멈추려면 Book에서 기존 control의 `pause`를 사용한다.
`resume`은 기존 워커만 재개하며 새 상시 어댑터 통합 완료를 뜻하지 않는다.

```sh
MOA_RUNTIME="$HOME/Library/Application Support/MOA-LAYA"
cd "$MOA_RUNTIME/app"
"$MOA_RUNTIME/venv-smoke/bin/python" -m workers.laya_local.control pause
"$MOA_RUNTIME/venv-smoke/bin/python" -m workers.laya_local.control resume
```

후속 Book 작업은 이미 전달한 `incoming/HANDOFF_READY.json`, `incoming/MINI_HANDOFF_20261018.md`,
`incoming/moa-ko-synthetic-20261018-v1/TRAINING_BUNDLE_READY.json`을 읽으면 된다. 토큰 복사·붙여넣기는 다시 필요 없다.
우선 남은 두 문서의 안전한 문맥 분할/서식 경계를 해결하고,45분 학습 제한과 상시 워커의 계약 어댑터를 통합해야 한다.
절전·재로그인 복구, 새 모델 학습/독립 평가, 전체 형식 지원,20건 완료는 아직 검증하지 않았다.

현재 `blocked_by`:

- `pilot_incomplete_context`: 시험 안내의 토큰 초과 구역과 혼합 안내의 긴 본문/서식 경계.
- `book_trainer_budget_alignment`: 기존90분 한도를 이번45분 상한에 맞추고 실제 새 묶음 소비를 연결해야 함.
- `book_daemon_adapter_pending`: 기존 상시 워커의 수정·테스트·patch 통합은 Book 담당 영역이며 소량 연결기로 대체 완료 처리하지 않음.

추가 암호·토큰 입력이나 반복적인 프롬프트 복사·붙여넣기는 필요 없다.
