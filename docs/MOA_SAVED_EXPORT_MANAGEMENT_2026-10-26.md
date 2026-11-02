# 저장 묶음 검색·보관·복원 — 20단계, 2026-10-26

## 담당자 흐름

1. **저장한 확인 완료본 묶음**에서 목록 이름·저장일 시작/끝·사용 중/보관함/전체로 검색한다.
   이름은 부분 문자열로 찾으며 `%`, `_`, `\`도 문자 그대로 취급한다.
   영문 대소문자는 SQLite 기본 `lower()` 범위에서 구분하지 않는다.
   날짜는 한국 시간의 **목록을 저장한 날짜** 기준이며 양 끝 날짜를 포함한다.
2. **보관·복원 이력**을 열고 직접 확인란을 선택해 보관함으로 옮긴다.
3. 보관함에서 같은 목록을 열어 기본 목록으로 복원할 수 있다.
   재로그인 후에도 상태와 순번·시각을 포함한 이력이 유지된다.

보관은 기본 목록에서 숨기는 기능이다. 목록 이름·문서 순서·고정 버전·원본·검수 이력을
수정하거나 삭제하지 않는다. 보관함에서도 고정 묶음 확인·다운로드가 가능하며,
기존의 수신/변환/검수 버전·원본 무결성·명시적 다운로드 확인 검사를 그대로 적용한다.
복원만으로 오래된 묶음의 다운로드 차단이 풀리거나 최신본으로 바뀌지 않는다.

## HTTP·권한·동시 변경 계약

| 경로 | 동작 |
|---|---|
| `GET /confirmed-export/saved` | 이름 `q`, 저장일 `from`/`to`, `state=active/archived/all`, `page` 검색 |
| `GET /confirmed-export/saved/manage?id=…&page=…` | 소유자의 목록 상태·보관/복원 이력 |
| `POST /confirmed-export/saved/archive` | 표시 당시 상태 순번과 명시적 확인을 검사해 보관 기록 추가 |
| `POST /confirmed-export/saved/restore` | 같은 검사 후 복원 기록 추가 |

- 목록과 이력은 현재 인증된 학교·소유자만 읽는다. 같은 학교의 다른 담당자도 읽을 수 없다.
- 변경은 현재 manager/uploader만 한다. reader로 바뀐 소유자는 읽기와 기존 다운로드를 유지하지만
  보관·복원 폼과 POST는 차단한다. 소속 해제·학교 비활성 상태는 매 요청 인증에서 차단한다.
- POST는 `csrf`, `id`, `expected`, `acknowledge=yes`의 단일 필드만 허용한다.
  기존 CSRF·Origin·no-store·CSP를 유지하며 본문 16,384바이트를 초과하면 읽기 전에 거부한다.
- `BEGIN IMMEDIATE` 안에서 소유자·현재 권한·표시 당시 순번·가능한 전이를 재검사한다.
  다른 탭이 보관하거나 복원한 뒤 들어온 오래된 요청은 409와 최신 상태 화면을 반환한다.
  보관→복원으로 원래 상태로 돌아와도 순번이 증가하므로 이전 요청을 재사용할 수 없다.
- 모든 GET은 메타데이터만 읽는다. 문서 분석·변환·ZIP·원본 hash 검사를 목록마다 실행하지 않는다.
  count와 rows, 현재 상태와 이력은 각각 동일 읽기 시점을 사용한다.
- 목록과 이력은 페이지당 최대 20개이며, 페이지 이동에도 검색 조건을 유지한다.
  조회 문자열은 4,096자, 이름 검색은 120자로 제한하고 날짜 정규형·달력값·역순 범위를 검사한다.
- 새 입력과 이름을 escape한다. JavaScript 없이 검색·보관·복원·다운로드가 동작한다.

## 추가형 DB v8

`school_service.migrate()`에 명시적 v8을 추가했다. v7의 불변 목록·항목과 core `user_version`은 유지한다.
HTTP·Store 열기·조회·기존 수집기는 마이그레이션을 자동 적용하지 않는다.
v8 미설치 환경은 기존 목록을 읽고 내려받을 수 있으며, 보관·복원만 설치 안내와 함께 차단한다.

새 `school_confirmed_export_list_events`의 열:
`list_id`, `school`, `subject`, `version`, `action` (`archive`/`restore`), `created_at`.
초기 상태는 기록 0개의 사용 중이며, 첫 보관부터 순번 1로 시작한다.
목록별 순번은 유일하고 연속 증가하며 두 상태가 번갈아 기록된다.
외래키와 트리거로 소유자·학교·목록 일치, 가능한 전이, UPDATE/DELETE 금지를 적용했다.
기존 목록·항목의 불변 트리거도 유지한다.

마이그레이션 전 기존 WAL을 포함하는 SQLite backup을 만들며 실패 시 DDL/이력 추가를 rollback한다.
v7 이전 버전도 순서대로 추가 설치하며 알 수 없는 이력은 임의 복구하지 않는다.
이번에는 외장 SSD의 합성 격리 DB에서만 설치·복구 경계를 시험한다. NAS 운영 DB에는 적용하지 않는다.

## 파일·저장 위치와 보호 경계

기준 소스: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

- `moa/school_confirmed_export_management.py`: 추가형 이력·메타데이터 조회·검색·동시 변경 보호.
- `moa/school_confirmed_export_management_web.py`: 명시적 보관/복원·충돌 화면·이력.
- `moa/school_confirmed_export_saved_web.py`, `school_confirmed_export_web.py`, `web_school.py`: 기존 화면·인증 경로 연결.
- `moa/school_service.py`: 명시적 v8 설치·설치 전 백업.
- `tests/test_school_confirmed_export_management.py`: 새 기능·권한·동시성·보존·HTTP·마이그레이션 검증.
  기존 구버전 DB 시험의 fixture는 v8 종속 테이블을 먼저 제거하도록 갱신했다.

합성 SQLite·백업·HTML·ZIP·테스트 결과·스크린샷·브라우저 프로필/캐시·임시 파일·로그·실행 기록:
`/Volumes/KIOXIA-2TB/Cache/MOA/school-saved-export-management-20261026`

`dev-ssd check`와 기존 `dev-storage`로 실제 UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`,
마운트·건강 보호 조건·검증 작업본·source/cache 실경로·쓰기·여유 공간을 확인했다.
과거 물리 안정성 미해결/전역 readiness 표시는 해제하지 않고 기존 검증 작업본만 보호 실행했다.
기존 Python 3.12/Node/Playwright/Chrome을 재사용하며 의존성·모델을 설치/다운로드하지 않는다.

NAS `/volume2/moa/app`, `/volume2/moa/data`, 운영 DB·서비스·원본에는 연결/변경하지 않는다.
학습·유료/외부 모델·운영 모델 교체·배포를 수행하지 않는다.
Git에는 코드·테스트·비밀 없는 설명서만 넣으며 기존 미추적 파일과 비공개 시험 자료는 보존한다.

## 실제 검증

- Browser plugin이 제공되지 않아 기존 Playwright 1.62.1/Chrome의 loopback 합성 서버로 시험했다.
  데스크톱 1280×900, 모바일 390×844, JavaScript 비활성 환경의 **18개 항목이 통과**했다.
  실제 선택·목록 저장 → 이름/저장일 검색 → 조건 유지 페이지 이동 → 보관 → 보관함 → 복원 →
  재로그인 상태·이력 복구 → 다운로드를 실행했다. 확인란 누락·다른 탭·보관/복원 후 이전 요청 차단,
  학교/소유자/reader 경계·콘솔·스크린샷·모바일 넘침도 확인했다.
- 보관 전·보관 중·복원 후·모바일·JavaScript 비활성에서 받은 **ZIP 5개가 바이트 단위로 일치**했다.
  원본/HTML SHA·고정 버전·manifest·상대 링크를 별도로 검사했다.
- 격리 브라우저 DB에는 목록 1개/항목 2개와 보관·복원 기록 8개만 추가됐다.
  기존 목록·원본 hash/mtime·모든 검수 이력·다른 DB 행이 유지됐고 quick_check=ok, FK 오류 0이었다.
  시험 서버·브라우저를 정상 종료했다.
- 자동 테스트·전체 회귀·Git 최종 집계는 외장 `RESULT.json`에 기록한다.
- 기존 `codex/moa-autoqa-v1`과 [PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push하며
  force push·main 병합은 하지 않는다.

격리 개발 검증 결과다. NAS 운영 적용·실사용 학교 계정/자료 검증은 포함하지 않는다.
