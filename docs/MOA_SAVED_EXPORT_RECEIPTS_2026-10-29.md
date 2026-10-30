# 저장 묶음 다운로드 영수증 — 21단계, 2026-10-29

## 담당자 흐름

1. 저장한 확인 완료본 묶음에서 확인란을 직접 선택해 ZIP을 내려받는다.
   검증된 ZIP을 내보내기 직전에 **다운로드 영수증**을 한 건 기록한다. 수신 완료를 보증하지는 않는다.
2. 저장 목록의 **보관·복원 이력** 화면에서 `다운로드 N회 · 마지막 시각`을 확인한다.
3. **다운로드 영수증 전체 보기**에서 회차·시각·문서 수·ZIP 바이트·SHA-256을 페이지로 확인한다.
4. 재로그인·서버 재시작·보관 이후에도 같은 기록을 이어서 본다.

영수증은 지난 다운로드를 확인하고 중복 전송을 판단하는 읽기 기록이다.
다운로드 허용·차단 조건을 바꾸지 않으며, 고정 목록·문서 순서·검수 이력·원본을
수정하거나 삭제하지 않는다. 보관·복원도 영수증을 바꾸지 않는다.

로그인 중에만 유지되는 **저장하지 않은 준비 묶음**의 ZIP은 영수증 대상이 아니다.
영수증은 저장한 목록에만 남는다.

## HTTP·권한 계약

| 경로 | 동작 |
|---|---|
| `GET /confirmed-export/saved/receipts?id=…&page=…` | 소유자의 다운로드 영수증 목록 |
| `GET /confirmed-export/saved/manage?id=…` | 보관·복원 이력과 영수증 요약·링크 |
| `GET /confirmed-export/saved` | 목록별 마지막 다운로드 표시 |

- 영수증은 새 POST 경로를 만들지 않는다. 기록은 기존 `POST /confirmed-export/saved/download`
  성공 시에만 남는다. 영수증 화면은 GET 전용이며 CSRF 폼이 없다.
- 목록과 영수증은 현재 인증된 학교·소유자만 읽는다. 같은 학교의 다른 담당자도 읽을 수 없다.
  다른 학교·다른 담당자 목록 ID는 404, 비로그인은 401이다.
- 소유자가 reader로 바뀌어도 자기 목록의 영수증 열람과 기존 다운로드는 유지된다.
  소속 해제·학교 비활성은 기존 매 요청 인증에서 차단한다.
- 영수증은 페이지당 최대 20개, `page`는 1~1,000,000, 조회 문자열은 4,096자로 제한한다.
  `id` 누락·중복, 추가 필드, 잘못된 페이지 값은 400이다.
- 목록·영수증 조회는 메타데이터만 읽는다. 문서 분석·변환·ZIP 생성·원본 hash 검사를
  목록마다 실행하지 않는다.
- 기존 CSRF·Origin·no-store·CSP·본문 16,384바이트 제한은 그대로 적용한다.
  영수증 화면은 JavaScript 없이도 동작한다.

## 기록 시점과 실패 처리

- 저장 목록의 고정 버전·원본 무결성 검사(`batch.bundle`)를 통과한 **전송 직전**에,
  실제로 내보내는 ZIP 바이트의 SHA-256·길이·문서 수를 기록한다.
- 기록은 `BEGIN IMMEDIATE` 안에서 소유자·목록 존재·현재 권한을 다시 확인한 뒤 추가한다.
  같은 목록의 회차는 1부터 증가하며, 두 연결이 동시에 내려받아도 회차가 겹치지 않는다.
- 기록 실패는 다운로드를 막지 않는다. 이미 검증된 ZIP은 그대로 전송하고, 영수증만 남기지 않는다.
  반대로 전송 직후 연결이 끊기면 영수증이 한 건 남을 수 있다. 영수증은 전송 완료 보증이 아니라
  **검증된 ZIP을 내보낸 시점의 기록**이다.
- 같은 화면에서 다시 내려받으면 새 회차가 남는다. 이전 회차는 수정·삭제할 수 없다.

## 추가형 DB v9

`school_service.migrate()`에 명시적 v9을 추가했다. v7·v8의 불변 목록·항목·보관 이력과
core `user_version`은 유지한다. HTTP·Store 열기·조회·기존 수집기는 마이그레이션을 자동 적용하지 않는다.

새 `school_confirmed_export_downloads`의 열:
`list_id`, `school`, `subject`, `version`, `item_count`, `zip_sha256`, `zip_bytes`, `created_at`.
`(list_id, version)`이 기본 키이며 외래키와 트리거로 소유자·학교·목록 일치,
연속 회차, UPDATE/DELETE 금지를 적용했다. SHA는 64자 소문자 hex, 바이트는 1~96MiB만 허용한다.

v9이 없는 환경은 기존 목록을 읽고 내려받을 수 있으며, 영수증만 남기지 않고
화면에 설치 안내를 표시한다. 마이그레이션 전 기존 WAL을 포함하는 SQLite backup을 만들고
실패 시 DDL·이력 추가를 rollback한다. v8 이전 버전도 순서대로 추가 설치한다.
이번에는 외장 SSD의 합성 격리 DB에서만 설치·복구 경계를 시험한다. NAS 운영 DB에는 적용하지 않는다.

## 파일·저장 위치와 보호 경계

기준 소스: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

- `moa/school_confirmed_export_downloads.py`: v9 schema, 영수증 기록·조회·목록별 집계.
- `moa/school_confirmed_export_downloads_web.py`: 소유자 전용 영수증 화면·페이지 이동.
- `moa/school_confirmed_export_saved_web.py`: 다운로드 성공 시 기록, 목록 카드 표시.
- `moa/school_confirmed_export_management.py`: 목록 조회와 같은 읽기 시점의 다운로드 집계.
- `moa/school_confirmed_export_management_web.py`, `moa/school_confirmed_export_web.py`: 요약·링크.
- `moa/school_service.py`: 명시적 v9 설치와 설치 전 백업.
- `moa/web_school.py`: 기존 인증·한도 경로에 영수증 GET 연결.
- `tests/test_school_confirmed_export_downloads.py`: 신규 기능·권한·동시성·보존·HTTP·마이그레이션 검증.

합성 SQLite·HTML·ZIP·테스트 결과·브라우저 프로필/캐시·스크린샷·임시 파일·실행 기록:
`/Volumes/KIOXIA-2TB/Cache/MOA/school-saved-export-receipts-20261029`

`dev-ssd check`와 기존 `dev-storage`로 실제 UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`,
마운트·건강 보호 조건·검증 작업본·source/cache 실경로·쓰기·여유 공간을 확인했다.
과거 물리 안정성 미해결/전역 readiness 표시는 해제하지 않고 기존 검증 작업본만 보호 실행했다.
기존 Python 3.12/Node/Playwright/Chrome을 재사용하며 의존성·모델을 설치/다운로드하지 않는다.

NAS `/volume2/moa/app`, `/volume2/moa/data`, 운영 DB·서비스·원본에는 연결/변경하지 않는다.
학습·유료/외부 모델·운영 모델 교체·배포를 수행하지 않는다.
Git에는 코드·테스트·비밀 없는 설명서만 넣으며 기존 미추적 파일과 비공개 시험 자료는 보존한다.

## 실제 검증

- 신규 영수증 테스트 57개: **57개 통과 / 실패 0**.
  실제 ZIP 바이트 SHA·길이·문서 수, 소유자/학교/reader 경계, 위조 행·회차 건너뛰기·수정/삭제 차단,
  21건 페이지 이동, 두 연결 동시 기록, v9 없음·v8→v9·실패 마이그레이션, 다운로드 2회 바이트 동일,
  차단된 ZIP·잘못된 POST의 무기록, 재로그인·보관·reader 다운로드 유지, 디렉터리 표시를 실행했다.
- 최종 전체 pytest **2,090개 통과 / 17개 skip / 실패 0, 490.55초** (`full.xml`).
  기존 저장 묶음·보관·상태·재로그인·형식별(HWPX·PDF·DOCX) 실제 업로드 회귀를 포함해 전체를 실행했다.
  skip 17개는 기존과 같은 선택적 의존성·장치 조건이다.
- Browser plugin이 제공되지 않아 기존 Playwright 1.62.1/Chrome의 loopback 합성 서버로 실제 UI를 시험했다.
  데스크톱 1280×900, 모바일 390×844, JavaScript 비활성 환경에서 **11개 확인 항목을 통과**했다.
  영수증 22건 → 1페이지 20건·2페이지 2건 → 실제 ZIP 다운로드 후 23건과 SHA 일치,
  보관 이력 요약·디렉터리 표시, 학교/담당자/reader 404·비로그인 401, 모바일·JavaScript 비활성 넘침과
  다음 페이지 이동, 콘솔 오류·스크린샷을 확인했다.
- 실제 다운로드 ZIP은 `PK` 시그니처와 바이트 SHA가 영수증 값과 일치했다.
- 브라우저 격리 DB는 목록 3개와 기존 검수 자료를 그대로 두고 영수증만 22→23건으로 늘렸다.
  다른 행·목록·항목·원본 hash/mtime을 보존했고 quick_check=ok, FK 오류 0이며 시험 서버·브라우저를 정상 종료했다.
- `compileall moa tests`, `git diff --check`를 통과했다. 변경 파일의 코드/문서 검사에서
  provider key·private key 패턴 검출은 0건이었다.
- 기존 `codex/moa-autoqa-v1`과 [PR #1](https://github.com/h19h29-design/moa/pull/1)에
  코드·테스트·비밀 없는 설명서만 정상 commit/push한다. force push·main 병합은 하지 않는다.

격리 개발 검증 결과다. NAS 운영 적용·실사용 학교 계정/자료 검증은 포함하지 않는다.
