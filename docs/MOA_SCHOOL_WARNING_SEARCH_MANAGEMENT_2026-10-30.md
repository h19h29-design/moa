# 저장한 분석 경고 검색 조건 검색·보관·복원 — 30단계, 2026-10-30

29단계의 개인 경고 검색 조건 목록에 **메타데이터 검색과 보관함·복원·상태 이력**을 추가했다.
기존 이름·조건·문서·변환 버전은 변경하지 않고 보관·복원 기록만 추가한다.

## 담당자 사용 흐름

1. 상단 **저장한 경고 검색 조건**에서 **저장 조건 검색**과 **표시 상태**를 적용한다.
   기본은 **사용 중**, 그 외 **보관함 / 전체**를 선택할 수 있다.
2. 이름·문서 제목·저장한 검색어·파일/경고 유형의 표시 이름과 필터 값을 검색한다.
   대소문자를 구분하지 않는 NFC 정규화·부분 문자열 검색이다. `%`, `_`, SQL 모양의 입력도 글자 그대로 찾는다.
   검색어는 최대 120자이며 제어 문자와 잘못된 UTF-8은 거절한다.
3. 결과는 저장 시각 내림차순, 같은 시각은 ID 내림차순으로 20개씩 표시한다.
   상태 탭은 검색어를 유지하며 페이지 이동은 검색어·상태를 유지한다. **검색 초기화**는 기본 목록으로 돌아간다.
4. 항목의 **보관·복원과 이력**에서 현재 상태와 기록을 확인한다.
   표시 상태만 바꾼다는 확인란을 직접 선택한 뒤 **보관함으로 옮기기** 또는 **사용 중인 목록으로 복원**을 누른다.
5. 보관은 기본 목록에서 숨기는 동작이다. 삭제·이름 수정·조건 변경·경고 해결 처리는 하지 않는다.
   보관함에서도 **경고 검색 조건 불러오기**로 저장 당시 변환본의 첫 경고 검색 페이지를 열 수 있다.
6. **검색한 조건 목록으로**는 이전 검색어·상태·목록 페이지로 돌아간다.
   보관·복원 뒤 결과가 줄면 페이지를 유효 범위로 조정한다. 상태 이력도 20개씩 보며 검색한 목록으로의 연결을 유지한다.
7. 새 브라우저에서 재로그인하거나 DB를 재개방해도 상태·이력이 유지된다.
   조회 중 새 변환이 생겨도 저장한 조건의 문서·변환 버전은 자동으로 바꾸지 않는다.

## DB·권한·보존 계약

- 학교 스키마 **v12**는 `school_warning_search_events` 한 테이블과 범위·전이·불변 트리거를 추가한다.
  기존 `school_warning_searches` 행과 기존 학교/수신/변환/검수/영수증 테이블은 수정하지 않는다.
  core `PRAGMA user_version`도 그대로 유지한다.
- 이벤트 열: `search_id`, `school`, `subject`, `version`, `action(archive/restore)`, `created_at`.
  `(search_id, version)`이 기본키이고 기존 조건·학교 소속을 FK로 연결한다.
  소유자 일치, 버전 1부터 연속 증가, archive→restore 교대만 DB 트리거로 허용한다.
  이벤트 UPDATE/DELETE와 기존 검색 조건 UPDATE/DELETE는 차단한다.
- 현재 상태는 가장 높은 버전으로 계산하며 이벤트가 없으면 사용 중·버전 0이다.
  메타데이터 검색은 저장 조건·상태 기록만 읽고 원본·분석 JSON을 다시 읽어 판단하거나 모델을 호출하지 않는다.
  검색·개수·페이지는 같은 읽기 스냅샷에서 조회하고 현재 로그인 학교·계정으로 제한한다.
- 기존 명시적 `school_cli migrate` 경로가 **전체 DB의 일관 백업 후** 트랜잭션으로 추가한다.
  실패 시 DDL·등록을 롤백하며 이미 v12이면 새 백업이나 변경 없이 종료한다.
  이번 마이그레이션은 SSD 합성 DB에서만 실행했다. HTTP 요청·수집기는 자동 설치하지 않는다.
  v11 DB는 기존 조건 검색·불러오기를 유지하며, 보관 기능 미설치를 안내하고 변경 요청은 409로 거절한다.
- `GET /structure-warning-searches?q=…&state=…&page=…`는 내 목록을 검색한다.
  `GET /structure-warning-searches/manage?id=…`는 내 상태·이력을 조회한다.
  `q/state/list_page`는 목록으로 돌아갈 조건이며 관리 화면의 `page`는 상태 이력 페이지다.
- `POST /structure-warning-searches/archive`와 `/restore`는 현재 로그인·소속·쓰기 역할·CSRF·Origin을 재검사한다.
  필드는 `csrf/id/expected/acknowledge/q/state/list_page` 하나씩만 받고 폼은 최대 4KiB다.
  예상 상태 버전과 전이 방향을 `BEGIN IMMEDIATE` 안에서 검사한 후 이벤트 한 행만 추가한다.
  동일 요청 재전송·오래된 탭·archive→restore 이후 예전 폼은 409로 차단한다. 동시 요청도 한 건만 성공한다.
  409 화면은 최신 상태·이력을 보여주고 다시 명시적으로 선택하도록 한다.
- 다른 학교와 같은 학교의 다른 계정은 목록 검색에서 제외하고 직접 ID 접근도 404다.
  읽기 역할로 변경된 본인은 기존 자기 항목을 조회할 수 있으나 보관·복원은 할 수 없다.
  소속 해제 시 조회도 차단한다. 필드·URL을 통한 학교·계정·리디렉션 위조는 거절한다.
- 화면 문자열은 HTML 이스케이프하고 기존 인증·`private, no-store` 응답을 유지한다.
  기본 HTML 폼으로 동작하여 JavaScript가 없어도 검색·보관·복원할 수 있다.
- 원본·추출 캐시·분석 JSON·검수 이력·개인 초안·확인 목록·다운로드 영수증을 유지한다.
  기존 HTML/ZIP/CSV 렌더러와 버전 변경 시 묶음 다운로드를 차단하는 계약은 유지한다.

## 실제 검증

- 대상 검사: **115 passed / 실패 0, 74.76초**.
  신규 검색·보관/복원, 기존 경고 조건 저장·영수증 조건 저장·CLI를 함께 실행했다.
  v11 일관 백업·추가형 업그레이드·롤백·재실행, DB 재개방, 과거 변환본 유지,
  Unicode 정규화·casefold·리터럴 검색, 상태/검색 교집합·안정적 페이지,
  소유자/역할·CSRF·엄격한 요청·중복/동시/오래된 버전 충돌과 보존을 확인했다.
- 전체 회귀: **2,385 passed / 17 skipped / 실패 0, 609.12초**. 기존 선택적 의존성·맥북 전용·비공개 HWP fixture의 제외 17건을 유지했다. 학습·추론으로 대체하지 않았다.
- 기존 Playwright·Chrome 브라우저 **13개 항목 통과**.
  데스크톱 1280×900·모바일 390×844에서 필터·45개 조건의 20/20/5 페이지,
  확인란 필수·보관·복원·이력 페이지, 목록으로 돌아갈 조건 유지, 새 브라우저 재로그인,
  보관한 과거 변환본 불러오기, 문자열 이스케이프·빈 결과·초기화,
  JavaScript 없는 폼, 다른 학교/계정 접근 차단과 기존 HTML 실제 다운로드를 시험했다.
  화면 넘침·페이지 예외·예상하지 않은 콘솔 오류/경고 0이다.
  오래된 탭 제출의 예상 HTTP 409 콘솔 기록 1건은 충돌 안내·이력 중복 방지와 함께 확인했다.
  데스크톱·모바일 스크린샷을 직접 확인했고 시험 서버와 브라우저를 종료했다.
- 보관·복원 이벤트 **4건만 추가**됐다. 해당 테이블의 INSERT를 제외한 전체 DB 덤프,
  기존 이벤트 행, 원본·추출 캐시 SHA/mtime, 두 변환본 분석 JSON,
  HTML·묶음 ZIP·영수증 CSV가 동일했다. 종료 검사 `quick_check=ok`, FK 오류 0이다.
  이전 스키마 fixture는 새 테이블을 먼저 제외하도록 갱신해 기존 미설치 동작 검증을 유지했다.
- `compileall moa tests`, `sh -n scripts/install-nas.sh`, `git diff --check` 통과. 최종 변경 파일 21개의 비밀 패턴 검사 발견 사항 0건이다.

Browser 플러그인을 사용할 수 없어 설치된 Playwright와 Chrome을 재사용했다.
새 패키지·브라우저·모델을 설치/다운로드하지 않았고 브라우저 요청은 소유한 loopback 합성 서버로 제한했다.

## 외장 SSD·파일 위치

`/Users/mac-mini/.local/bin/dev-ssd check`로 실제 마운트 `/Volumes/KIOXIA-2TB`,
UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, 소유권·쓰기·검증 작업본을 확인했다.
시작 여유 공간은 **1,299.01GiB**였다. 기존 `dev-storage` 실행 래퍼와 `.venv312`를 재사용했다.
전체 `development_ready=false`, `physical_stability_resolved=false`는 유지한다.
개별 검증 작업본 허용과 I/O 오류·장치 보호를 유지했으며 물리 안정성 문제를 해결했다는 의미는 아니다.

소스 기준: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

| 파일 | 변경 내용 |
|---|---|
| `moa/school_warning_search_management.py` | v12 DDL·메타데이터 검색·상태/이력·원자적 전이 |
| `moa/school_warning_search_management_web.py` | 명시 확인 폼·충돌 안내·이력과 목록 복귀 |
| `moa/school_warning_searches.py`, `moa/school_warning_searches_web.py` | 기존 개인 목록에 검색·상태 필터·관리 연결 |
| `moa/web_school.py` | 인증된 관리 GET·변경 POST 경로 연결 |
| `moa/school_service.py` | 기존 명시적 마이그레이션에 v12 추가 |
| `tests/test_school_warning_search_management.py` | 신규 DB/HTTP·권한·검색·이력·충돌·보존 검사 |
| `tests/test_school_warning_searches.py`, `tests/test_school_receipt_searches.py`, `tests/test_school_cli.py` | 최신 버전·기존 조건 계약·과거 스키마 fixture |
| 기존 학교 테스트 9개 | 과거 스키마 재현 시 v12 테이블을 먼저 제외 |
| `README.md`, 이 문서 | 30단계 사용 흐름·계약·실행 결과 |

개발자료 기준: `/Volumes/KIOXIA-2TB/Cache/MOA/school-warning-search-management-20261030`

- `STORAGE_CHECK.json`, `GIT_BEFORE.json`, `PR_BEFORE.json`: 장치·Git/PR 시작 상태.
- `target.log/xml`, `full.log/xml`, `pytest-*/`: 실제 검사·격리 DB·형식별 파서 산출물.
- `tmp/`, `xdg/`: 임시·개발 캐시. 기존 외장 SSD의 공통 래퍼 캐시도 유지한다.
- `browser/data/`: 합성 DB·원본·분석·검수·영수증.
- `browser/downloads/`, `browser/screenshots/`, 브라우저 프로필/디스크 캐시: 실제 다운로드·화면 근거.
- `browser/BASELINE.json`, `BROWSER_RESULT.json`, `SERVER_RESULT.json`: 시험 전 기준·화면 동작·종료 보존 검사.
- `browser_server.py`, `browser_check.cjs`, `browser/server.log`, `browser/check.log`: 합성 서버·브라우저 실행 근거.
- `FINAL_TEST_SOURCES.json`, `BROWSER_VISUAL_REVIEW.json`, `STATIC_OK.json`, `SECRET_SCAN.json`, `RESULT.json`: 최종 소스·정적/보존 검사·집계.
- `PR_BODY.md`, `LOCAL_COMMIT.json`, `PR_AFTER.json`: 비밀 없는 PR 설명·커밋·게시 확인.

코드·테스트·비밀 없는 설명서만 기존 `codex/moa-autoqa-v1`과
[PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push한다.
기존 미추적 가상환경·문서 4개는 그대로 보존하고 Git 반영에서 제외한다.
원본·DB·모델·캐시·브라우저 자료·비공개 실행 기록은 Git에 올리지 않는다.

이번 작업은 NAS에 접속하지 않았다. 기존 `/volume2/moa/app`, `/volume2/moa/data`의
운영 DB·서비스·원본·수집기·인증을 변경하지 않았다. 최신 NAS 상태를 새로 점검했다는 의미도 아니다.
학습·모델 추론·유료/외부 모델·운영 배포·강제 push·main 병합은 실행하지 않았다.
실학교 수용 시험·NAS 백업/독립 복구·운영 적용은 별도 범위다.
