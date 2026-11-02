# 학교 문서 구조 담당자 검수 — 2026-10-22

## 범위와 보호 경계

학교 담당자가 단계 2의 헤더·본문·각주·표·여러 첨부 관계를 보고 수정하고,
검수 전후 내용·담당자·시각·사유를 남기는 **비공개 개발 기능**이다.
기존 `codex/moa-autoqa-v1` / PR #1에 코드·합성 테스트·이 설명서만 반영한다.

- 검증 작업본: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`.
- 실행 자료: `/Volumes/KIOXIA-2TB/Cache/MOA/school-structure-review-20261022`.
- UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`과 실제 마운트·쓰기·프로젝트별 실행 보호를 확인했다.
  시작 시 가용 약 1.4TiB. `dev-storage`는 SMART/카운터와 검증 작업본 보호를 매번 재확인한다.
  과거 연결 끊김 위험과 전체 readiness 미해결 표시는 해제하지 않았다.
- 기존 `.venv312` Python 3.12.13을 재사용한다. 패키지 설치·모델 다운로드 없음.
- NAS `/volume2/moa/app`, `/volume2/moa/data`, 운영 DB·서비스·원본·검수 이력에 접속하거나 쓰지 않는다.
  NAS 상태를 재검증하거나 배포했다고 보고하지 않는다.
- MacBook 실행·학습·추론·유료/외부 모델·자동 수집·공개·운영 모델 교체 없음.
  기존 연구 큐·400건 설정·backfill·모바일 승인/학습 내보내기도 그대로다.

## 담당자 사용 흐름

학교 전용 로그인 → 수신본 → **담당자 구조 검수·수정·이력**으로 들어간다.

1. 파일 묶음, 자동 분석 원본, 원본 위치, 미확정·누락 경고를 확인한다.
2. 구역 종류(헤더/제목/본문/푸터/각주/표)와 추출 텍스트를 사유와 함께 수정한다.
3. 헤더→파일, 참조→각주, 참조→다른 첨부, 구역 포함, 표 머리 셀→표 관계를 추가/제거한다.
   같은 수신·변환 버전에 실제 존재하는 ID만 쓸 수 있다. 포함 순환·잘못된 형식·소속은 거절한다.
4. 파일·구역·관계와 미확정 범위를 확인했다는 체크 및 사유로 **담당자 확인 완료**를 기록한다.
5. 이후 수정하면 자동으로 `draft`(다시 확인 필요)가 된다. 확인 취소도 새 이력으로 남는다.
6. **검수 이력**에서 수정 전/후, 검수자, 시각, 사유, 당시 수정본을 확인한다.
   변환 버전별 링크와 이전 이력 페이지를 통해 재변환 전 기록도 찾는다.

| 데이터 | 보존/표시 방식 |
|---|---|
| 원본 파일·SHA·위치·파일 역할 | 변경하지 않음 |
| 자동 구조 분석 | 기존 conversion revision의 원판정 보존; 원본 화면/JSON 유지 |
| 담당자 수정본 | 별도 overlay; 자동 근거와 담당자 수정 출처를 구분 |
| 검수 완료 | 해당 수신본 + 변환 버전에만 적용; `confirmed` |
| 재변환·새 정정본 | `unreviewed`부터 시작; 이전 확인을 승계하지 않음 |
| 이전 수신·변환·이력 snapshot | 조회만 가능 |
| HTML·원본 ZIP | 구조 검수로 재생성/승인되지 않음; 내용 그대로 |
| 자동 분석 `complete`, 경고, `human_approved` | 담당자 확인으로 변경/삭제하지 않음 |

`confirmed`는 **제공된 구조와 미확정 범위를 담당자가 확인했다는 기록**이다.
문서에 누락이 없다는 보증, 개인정보·공개 허가, 운영 승인, 모델 정답/학습자료가 아니다.
구역 종류를 바꿔 기존 의미 관계가 맞지 않으면 그 관계를 수정/제거하기 전 확인 완료를 거절한다.

## 권한·동시성·입력 계약

- 기존 활성 학교 membership 사용: `uploader`와 `manager`는 수정·확인 가능, `reader`는 조회만 가능.
  actor와 school은 서버 세션에서만 취한다. 요청의 actor/subject/school 주입은 거절한다.
- 학교 B는 학교 A의 수신본·변환·수정본·이력·당시 snapshot을 읽거나 수정할 수 없다.
- 모든 쓰기는 기존 CSRF/Origin 검증 및 회원 재확인 후 처리한다. 표시하는 문서/메모는 HTML escape,
  `no-store` 및 기존 CSP 보호를 사용한다. 문서 내용을 실행하지 않는다.
- `BEGIN IMMEDIATE` 안에서 최신 수신본·변환 revision·분석 SHA-256·직전 review ID·권한을 다시 확인한다.
  다른 담당자 저장, 재변환 중/완료, 새 정정본, 오래된 폼 재전송은 HTTP 409로 차단한다.
  자동 덮어쓰기나 자동 재학습/재변환은 하지 않는다.
- 쓰기 요청 300,000bytes, 텍스트 20,000자, 사유 2,000자, overlay 2MiB, 수정 구조 8MiB 제한.
  관계 4,000개 제한을 유지하고 필드·종류·ID를 allowlist 검증한다.
- 구역/관계/이력/변환 목록은 20개 단위다. 이력은 cursor 방식으로 오래된 기록도 조회한다.
  읽기 요청은 작업·마이그레이션·이력을 생성하지 않는다.

## 추가형 DB 계약 — 명시적 학교 schema v2

`school_service.migrate()` / `python -m moa.school_cli --data <격리 경로> migrate`만 적용한다.
Store 열기, 서버 시작, 수집기 실행으로 자동 적용하지 않는다. **이번 검증은 외장 SSD 격리 DB만 사용**했다.

- 기존 v1 DB는 WAL 일관 백업을 `db/backups/`에 만든 다음 단일 DDL 트랜잭션으로 v2를 추가한다.
- 새 DB는 v1+v2를 설치한다. 재호출은 no-op. 알 수 없는 migration 버전은 먼저 중단한다.
- 기존 테이블/레코드/원문과 core `PRAGMA user_version=5`는 유지한다.
- 새 `school_structure_reviews`: school, post, receiving version, conversion revision, sequence,
  previous ID, base analysis hash, actor, timestamp, action, status, comment,
  warning acknowledgement, before/after changes, overlay snapshot.
- receiving version 및 previous event는 학교/버전 composite FK로 연결한다.
  별도 insert trigger가 conversion revision의 실제 학교 수신본 소속까지 검증한다.
- update/delete 금지 trigger로 기존 이력의 덮어쓰기/삭제를 차단한다. 앱은 INSERT만 사용한다.
  이는 DB 관리자까지 방어하는 외부 서명/법적 감사 시스템은 아니다.
- 재변환해도 이력은 남는다. 모바일 연구용 `mobile_reviews`, 승인 revision, 학습 export와 분리한다.

## 검증

외장 SSD의 격리 SQLite·학교 A/B 합성 계정·loopback HTTP 서버를 사용한다.
먼저 누락된 기능에서 새 테스트 14개가 실패하는 것을 확인하고 구현했다.
추가로 재변환 후 구 이력 발견 테스트를 실패→수정→통과로 검증했다.

핵심 검증: 수정 전후·actor·사유, 확인/취소, append-only, 학교 A/B·reader·회원 회수,
위조 필드·CSRF·Origin·XSS, 실제 두 DB 연결 동시 저장, stale 분석/변환/정정본,
관계 endpoint·순환·의미 일관성, 불완전성 보존, 용량 제한, 이력 pagination/snapshot,
v1→v2 사전 백업·idempotence·원문/기존 payload 보존·외래키/quick_check.

형식별 시험은 기존 HWP 비공개 fixture(새 취득 없음), 합성 HWPX·DOCX·텍스트 PDF를 사용한다.
실제 업로드→역할→변환/구조→담당자 수정→확인→이력→HTML/ZIP 다운로드 흐름을 시험한다.
ZIP의 생성 시각은 바뀔 수 있으므로 컨테이너 전체 바이트 대신 **각 항목 이름·바이트와 원본 SHA**를 비교한다.
형식별 수정본과 기록은 Git 밖 `formats/`에만 남긴다. 원문은 설명서/채팅/Git에 싣지 않는다.

검증 결과(2026-10-22, 기존 외장 `.venv312`):

- 신규 핵심 DB/HTTP 검수 테스트 **24 통과**.
- 최종 전체 회귀 **1,337 통과 / 16 skip / 실패·오류 0**, 53.34초 (`final.xml`).
  skip은 기존 선택적 Torch/MacBook tensor 검사와 관련 수집 단계 검사다.
  이 기능을 시험하기 위해 모델 환경을 설치하거나 MacBook 학습을 실행하지 않았다.
- 독립 코드 검토의 헤더 내부 표 텍스트 수정/종류 원복, 64-bit 초과 이력 cursor 문제를
  합성 재현 테스트 실패→수정→통과로 해결했다. 실제로 달라진 필드만 override로 표시하고
  과대 cursor는 요청 처리 예외 대신 HTTP 400을 반환한다. 수정 후 독립 재검토도 통과했다.
- HWP/HWPX/DOCX/PDF 모두 검수·이력 HTTP **200**, 수정+확인 이력 **2건**,
  자동 분석·원본 SHA·기존 HTML·ZIP 내부 파일 보존 확인.
- `compileall -q moa`, `sh -n scripts/install-nas.sh`(구문 검사만), `git diff --check` 통과.
- GitHub CI 결과는 PR #1의 해당 커밋 checks에서 확인한다. NAS 실행·배포는 없음.

## 파일별 저장 위치

모두 검증된 외장 작업본 아래:

- `moa/school_review.py`: overlay·append-only event·권한·동시성·관계 검증·조회.
- `moa/school_review_web.py`: 담당자 수정/확인 폼·이력·당시 snapshot 화면.
- `moa/school_service.py`: 명시적 추가형 v2 마이그레이션.
- `moa/web_school.py`: 학교 전용 인증/CSRF 안의 검수 경로 및 링크.
- `moa/school_cli.py`: v2 마이그레이션 결과 표시.
- `tests/test_school_structure_review.py`: 합성 DB/HTTP/경합/권한 회귀 시험.
- `tests/test_school_formats.py`: 네 형식의 실제 담당자 검수 HTTP 흐름 검증.
- `docs/MOA_SCHOOL_STRUCTURE_REVIEW_2026-10-22.md`, `README.md`: 비밀 없는 계약·결과.

실행 자료는 `/Volumes/KIOXIA-2TB/Cache/MOA/school-structure-review-20261022`의
`pytest/`(DB·사전 백업·합성 원본), `formats/`(형식별 원본·HTML·ZIP·분석·검수 이력),
`tmp/`, `cache/`, JUnit XML에만 저장한다. 기존 사용자 미커밋/미추적 자료는 보존한다.

## 현재 한계

- 블록 분할·병합·추가, 표 셀별 편집/표 재구성, OCR은 포함하지 않는다.
  추출 텍스트 수정은 원본 파일이나 기존 변환 HTML 내용을 고치지 않는다.
- 서명·다중 승인·공개 워크플로·운영 적용은 별도 범위다.
- 원본의 의미적 진실성이나 문서 완전성을 자동 보증하지 않는다. 원래 미확정/누락을 숨기지 않는다.
- 브라우저 엔진 시각 검증과 NAS 운영 적용을 완료한 것으로 보고하지 않는다.
  이번 기능은 서버 렌더링 HTML 폼의 실제 HTTP 왕복·DB 저장 결과로 검증한다.
