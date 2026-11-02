# 검수 중인 입력 임시 저장·복구 — 10단계, 2026-10-24

## 담당자 사용법과 범위

구조 검수에서 구역의 종류·텍스트·사유를 작성하다가 **이 항목 입력 임시 저장**을 누른다.
관계 추가의 미완성 ID·종류·사유와 기존 관계 제거 사유도 각각 저장할 수 있다.
로그아웃하거나 서버가 재시작된 뒤 같은 계정으로 로그인해 상단 **내 임시 저장 입력** →
**임시 입력 복구·확인**으로 열고, 내용을 확인한 뒤 기존 **구역 수정 저장/관계 추가/제거**로 정식 저장한다.

- **수동·항목별 저장**이다. 버튼을 누르지 않은 입력이나 다른 항목은 자동 저장하지 않는다.
  빈 텍스트·빈 사유·미완성 관계 ID도 임시 저장할 수 있지만 정식 검수 저장 시 기존 검증을 통과해야 한다.
- 임시 저장·복구 GET은 정식 검수 이벤트·확인 상태·HTML·원본을 변경하지 않는다.
  확인 체크, 확인 취소, 과거 검수본 복원 동작은 임시 저장하지 않으며 자동 승인하지 않는다.
- 저장할 때마다 별도 불변 스냅샷을 추가한다. 같은 항목의 이전 입력도 DB에서 보존한다.
  목록에는 학교·담당자·수신/변환·항목별 최근 입력의 제목/종류/시각만 20개씩 표시하고,
  저장 내용은 소유자가 개별 복구 화면에서만 조회한다.
- 다른 담당자나 학교는 쓰기 권한이 있어도 이 개인 입력을 볼 수 없다.
  자기 계정의 reader는 읽기 전용이며 소속 회수/학교 중지는 접근을 차단한다.
- 새 정식 검수·정정 수신본·재변환·변환 실패·기준 hash 변경·새 임시 입력이 있으면
  이전 입력은 **읽기 전용**으로 보존한다. 최신본으로 자동 적용·병합하거나 다시 가리키지 않는다.
  원래 구조가 손상되어 열리지 않아도 저장 입력 자체는 읽기 전용으로 확인할 수 있다.
- 저장된 연속 검수 목록에서 임시 저장하면 해당 목록/항목 문맥을 검증해 유지한다.
  위치는 임시 저장·복구가 아닌 기존 위치 저장 버튼으로만 이동한다.
  로그인 메모리에만 있는 미저장 연속 목록은 영구 저장하지 않으므로 개인 초안 복구에서 분리된다.
- 다른 항목에 미저장 입력이 있으면 저장·이동 시 기존 JavaScript 유실 경고가 표시된다.
  단독 검수 화면에도 적용했다. JavaScript가 꺼져도 명시적 서버 저장/복구는 동작하지만 이동 경고는 없다.
  임시 입력 삭제·자동 만료·전체 페이지 자동 저장·초안 공유/배정은 이번 범위가 아니다.

## DB 계약·보호 경계

`school_service.migrate`의 명시적 추가형 **v4**가 `school_review_drafts`를 추가한다.
NAS에 적용하지 않았으며 실제 마이그레이션/백업 시험은 외장 SSD 격리 DB에서만 수행했다.

| 필드 | 계약 |
|---|---|
| `id`, `school`, `subject` | 무작위 32 hex ID, 서버 인증의 학교·담당자 소유권 |
| `notice_version`, `conversion_revision`, `target_key` | 같은 학교 수신/변환과 구역 또는 관계 항목 고정 |
| `sequence`, `previous_id` | 항목별 증가 순번·동일 소유 범위 이전 입력 연결 |
| `base_hash`, `base_review` | 작성 당시 자동 구조 SHA와 정식 검수 이벤트(미검수이면 빈 문자열) |
| `title`, `action_kind`, `payload`, `created_at` | 수신 제목, 허용한 3종 입력, 입력 JSON, 저장 시각 |

- DDL 전 기존 `Store.backup`으로 WAL-consistent 백업을 `db/backups`에 만든다.
  v1/v2/v3에서 누락된 스키마만 추가하고 재호출은 무변경이다.
  알 수 없는 버전은 중단하며 core `PRAGMA user_version`을 변경하지 않는다.
  서버 시작/화면 조회/임시 저장 요청으로 마이그레이션을 자동 실행하지 않는다.
  v3 화면에서는 기존 정식 검수는 유지하고 임시 저장의 명시적 마이그레이션 필요만 안내한다.
- FK·INSERT 범위 trigger가 학교/수신/변환/기준 검수를 검증한다.
  임시 스냅샷은 UPDATE/DELETE trigger로 고정하며 정식 검수·기존 저장 목록 테이블과 분리한다.
- `BEGIN IMMEDIATE` 안에서 현재 쓰기 권한, 최신 수정 가능 여부, 구조 hash·정식 검수 ID,
  기대 임시 입력 ID를 재확인한 뒤 한 행만 추가한다. 오래된 탭·재전송·동시 저장은 409이며 덮어쓰지 않는다.
- 복구는 실제 폼을 만드는 **동일한 구조 조회 결과**와 최신 임시 입력 ID를 대조한다.
  호환성 검사 뒤 다른 정식 검수가 저장되는 경우를 테스트로 재현하고,
  오래된 입력을 새 기준 폼으로 다시 적재하지 않도록 수정했다.
- 구역 텍스트 최대 20,000자, 사유 2,000자, 관계 필드 160자, 스냅샷 JSON 100,000 bytes,
  HTTP POST 300,000 bytes, 쿼리 4,096자 제한이다. Unicode·NUL·중복/위조 필드·범위를 검증한다.
  불완전한 관계 추가의 종단 ID는 임시 입력으로만 보관하고 정식 저장은 기존 관계 검증을 거친다.
- 기존 인증·CSRF·Origin·현재 소속 검사·no-store·CSP·SQL 바인딩·HTML escape를 유지한다.
  URL에는 무작위 ID/목록 위치만 포함하고 본문·사유·인증정보를 로그에 추가하지 않는다.
  localStorage·외부 전송·새 의존성·모델 호출·백그라운드 작업을 추가하지 않았다.

| 경로 | 동작 |
|---|---|
| `POST /structure-draft/save` | 허용한 단일 항목의 입력을 개인 스냅샷으로 명시적 저장 |
| `GET /structure-drafts?page=…` | 내 최신 임시 입력 메타데이터만 페이지 조회, DB 쓰기 없음 |
| `GET /structure-draft?id=…` | 호환되면 입력 폼 복구, 아니면 저장 내용을 읽기 전용으로 표시 |
| `POST /structure-review` | 기존 정식 검수 검증·추가형 이력 저장. 임시 복구만으로 호출하지 않음 |

## 실행·검증 결과

- TDD: 임시 저장 버튼 없는 실제 HTTP 복구 테스트 실패(`red.xml`)를 확인한 뒤 구현했다.
  동시 정식 검수에 의한 재기준화 문제도 실패(`race-red.xml`)를 먼저 확인했다.
- 신규 입력 초안 테스트 **40개**, 형식/저장 목록/연속 검수/구조 검수 포함 **112개 통과**(`green3.xml`).
- 전체 회귀 **1,558 통과 / 16 skip / 실패 0**, **182.54초**(`full.xml`).
  선택적 Torch/맥북 검사를 강제로 실행하려고 설치·모델 다운로드·학습을 하지 않았다.
- `compileall -q moa`, `node --check moa/school_queue_ui.js`,
  `sh -n scripts/install-nas.sh`(구문만), `git diff --check` 통과.
- 실제 HTTP/격리 SQLite에서 새 로그인·새 서버 핸들러/DB 재연결, 학교 A/B와 동일 학교 다른 담당자,
  reader/권한 회수, 동시 연결 CAS·재전송·스냅샷 불변성, 미완성/빈 입력,
  관계 추가/제거·확인 동작 차단·문서/목록 문맥 위조, CSRF/Origin/중복/UTF-8/NUL/길이/XSS,
  메타데이터 페이지/20개 경계, 21번째 이후 구역 복구, 오래된 정정/재변환/실패/손상 기준을 시험했다.
- 기존 실제 **HWP(기취득 비공개 fixture)**와 합성 **HWPX·DOCX·텍스트 PDF** 모두 실제 parser로
  업로드 → 역할/변환 → 원본 HTML/ZIP → 임시 저장 → 로그아웃/새 로그인 → 입력 복구 →
  명시적 정식 검수 → HTML/ZIP/비교/복원/저장 목록까지 통과했다.
  복구 GET의 DB 무변경, 원본 SHA·mtime·변환·자동 분석·이전 검수/다운로드 보존을 검증했다.

### 브라우저 QA

검증 흐름: 학교 현황 → 저장 연속 목록 → 미완성 텍스트/빈 사유 임시 저장 →
로그아웃/재로그인 → 내 입력 목록 → 복구 → 정식 저장 → 오래된 초안 읽기 전용 →
두 번째 문서의 모바일 입력 저장/재로그인 복구.

Browser plugin/skill이 없어 기존 Playwright 1.62.1·설치된 Chrome을 사용했다.
합성 loopback `http://127.0.0.1:54521`, 데스크톱 1280×900·모바일 390×844에서 검사했다.
검사 후 이 작업의 서버와 전용 브라우저만 종료했다.

| 확인 | 결과 |
|---|---|
| URL·페이지 제목 | 학교/개인 초안 경로 및 `MOA 검수` 일치 |
| 빈 화면·framework 오류 overlay | 없음, 실제 입력/목록 렌더링 |
| Console/runtime | 관련 오류 없음. 기존 미구현 favicon의 401/404 두 건만 기록 |
| 상호작용 | 빈 사유 저장, 다른 미저장 항목 경고/취소, 재로그인·새로고침 복구, 명시적 정식 저장, 오래된 입력 읽기 전용 통과 |
| 화면 | 스크린샷 육안 확인, 모바일 가로 넘침 없음, 입력/사유/두 저장 버튼 표시 |
| 보존 | 원본·추출 SHA/mtime·변환·기존 감사·나머지 DB 동일. 임시 입력 2개, 정식 검수 1개, 목록 1개/항목 3개만 추가 |
| DB 무결성 | quick_check `ok`, FK 오류 0 |

첫 브라우저 시험의 로그인 전 보호 경로 401을 console 검사와 구별한 뒤,
새 합성 DB에서 로그인 경로부터 전체 흐름을 다시 통과했다. 앱의 인증 보호를 해제하지 않았다.
다른 브라우저·실사용 학교/담당자·NAS 운영 적용은 미검증이다.
테스트 우선 개발·보안 입력 검토·화면/완료 전 검증 스킬을 사용했으며 추가 모델/에이전트는 호출하지 않았다.

## 저장 위치·Git·운영 경계

- 기존 작업본: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`
  - 신규 `moa/school_review_drafts.py`, `moa/school_draft_web.py`, `tests/test_school_review_drafts.py`, 이 문서.
  - 수정 `moa/school_review_web.py`, `moa/school_service.py`, `moa/web_school.py`,
    `tests/test_school_formats.py`, `README.md`.
- SQLite/백업·임시/Python/browser 캐시·JUnit·형식 HTML/ZIP·스크린샷은 모두
  `/Volumes/KIOXIA-2TB/Cache/MOA/school-review-drafts-20261024` 아래다.
  - `red.xml`, `race-red.xml`, `green1.xml`, `green2.xml`, `green3.xml`, `full.xml`, 각 격리 시험 폴더
  - `formats-full/{hwp,hwpx,pdf,docx}/` (실제 원본·입력 복구 HTML 등은 비공개, Git 제외)
  - `browser2/browser-data/`, `browser2/BROWSER_RESULT.json`, `browser2/BROWSER_PRESERVATION.json`
  - `browser2/screenshots/{desktop-restored,desktop-input,mobile-restored,mobile-input,mobile-directory}.png`
- `dev-ssd check`·기존 래퍼의 검증 작업본 검사 통과. mount `/Volumes/KIOXIA-2TB`,
  UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, ownership 정상, 약 1.4TiB 여유.
  전역 `development_ready=false`와 `physical_stability_resolved=false`는 그대로이며
  과거 외장 분리 위험이 해결됐다는 뜻은 아니다. 새 I/O/마운트 오류 시 중단하고 내부로 우회하지 않는다.
- 기존 `.venv312` 재사용. 의존성 재설치·원문/모델 다운로드 없음.
- NAS `/volume2/moa/app`, `/volume2/moa/data` 및 운영 DB·서비스·원본에는 접속/변경/배포하지 않았다.
  학습·모델 추론·유료/외부 모델·운영 모델 교체·수집/백필·인증/공개 설정 변경도 없다.
- 코드·테스트·비밀 없는 설명서만 기존 `codex/moa-autoqa-v1`, PR #1에 정상 commit/push한다.
  원본·DB·모델·캐시·인증정보·비공개 실행 기록과 기존 무관한 미추적 파일은 포함하지 않는다.
  force push·main 병합은 하지 않는다. NAS 적용은 별도 승인/백업/검증이 필요한 후속 범위다.
