# 저장한 분석 경고 검색 조건 표시 이름·이력 — 31단계, 2026-10-30

30단계의 개인 저장 조건 관리에 **표시 이름 변경과 이름 이력**을 추가했다.
처음 저장한 조건 행은 그대로 두고 이름 변경 기록만 추가한다.

## 담당자 사용 흐름

1. 상단 **저장한 경고 검색 조건**에서 이름·조건을 검색하고 **이름 변경과 이력**을 연다.
2. 현재 표시 이름, 처음 저장한 이름, 문서 제목·보관 상태·이름 기록을 확인한다.
3. **새 표시 이름**을 입력하고 표시 이름만 변경한다는 확인란을 선택한 뒤 **표시 이름 변경**을 누른다.
   최대 120자이며 앞뒤 공백을 제거하고 NFC로 정규화한다. 빈 이름·제어 문자·잘못된 UTF-8은 거절한다.
4. 목록과 보관·복원 관리 화면에 현재 이름을 표시한다. 이름이 달라졌으면 처음 이름도 함께 표시한다.
   목록 검색은 **현재 이름과 처음 이름**에 모두 적용하며 기존 문서 제목·저장 검색어·파일/경고 유형 검색을 유지한다.
5. 이름 이력은 최신부터 20개씩 조회한다. 이전 이름으로 돌아갈 때도 새 변경으로 추가한다.
   현재와 같은 이름은 새 기록을 만들지 않는다. 모든 과거 이름을 목록 검색 대상으로 확장하지 않는다.
6. **검색한 조건 목록으로**는 원래 검색어·상태·목록 페이지를 유지한다.
   현재 이름으로만 검색했던 결과가 변경 후 사라지면 검색 조건에 맞는 빈 결과를 보여준다.
7. 보관함에서도 이름을 바꿀 수 있으며 보관 상태 자체는 유지한다.
   재로그인·DB 재개방 후에도 이름과 이력이 남는다. 검색 조건 불러오기는 기존 문서·변환본·필터를 그대로 사용한다.

## DB·권한·충돌·보존 계약

- 학교 스키마 **v13**: `school_warning_search_names`와 소유자·버전·불변 트리거를 추가한다.
  열은 `search_id`, `school`, `subject`, `version`, `name`, `created_at`이다.
  `(search_id, version)`이 기본키이고 기존 저장 조건·학교 소속을 FK로 연결한다.
  소유자 일치·버전 1부터 연속 증가·직전 이름과 다름을 삽입 트리거로 검사한다.
  UPDATE/DELETE는 차단한다. 기존 검색 조건과 보관·복원 이벤트도 수정하지 않는다.
- 현재 표시 이름은 가장 최신 이름 기록이며 기록이 없으면 처음 저장한 이름이다.
  기존 저장 조건의 `name`, 검색어·파일/유형 필터·수신본·변환본은 동결 상태로 유지한다.
  기존 저장 요청을 다시 보내더라도 이름 이력이나 현재 표시 이름을 덮어쓰지 않는다.
- 명시적 `school_cli migrate`가 전체 DB 일관 백업 후 트랜잭션으로 추가한다.
  실패 시 DDL/등록을 롤백하며 v13 재실행은 변경·중복 백업 없이 종료한다.
  core `PRAGMA user_version`과 기존 데이터 행은 변경하지 않는다. 이번 적용은 SSD 합성 DB에 한정한다.
  HTTP나 수집기는 자동 마이그레이션하지 않는다. v12에서는 기존 검색·보관/복원·불러오기를 유지하고
  이름 화면에서 미설치를 안내하며 이름 변경 POST는 409로 거절한다.
- `GET /structure-warning-searches/name?id=…`는 소유한 조건의 이름·이력을 조회한다.
  `page`는 이름 이력 페이지, `q/state/list_page`는 검색 목록으로 돌아갈 조건이다.
- `POST /structure-warning-searches/rename`는 현재 소속·학교·계정·쓰기 역할·CSRF·Origin을 재검사한다.
  허용 필드는 `csrf/id/expected/expected_state/name/acknowledge/q/state/list_page` 하나씩이며 폼은 최대 4KiB다.
  알 수 없는 필드·중복 필드·URL 쿼리·계정/학교 위조·잘못된 숫자·미확인 요청을 거절한다.
- `BEGIN IMMEDIATE` 안에서 예상 이름 버전과 예상 보관 버전을 모두 확인한 뒤 이름 기록 한 행만 추가한다.
  다른 탭의 이름 변경·보관/복원, 중복 재전송, 같은 이름 제출은 409로 안내한다.
  처음 이름으로 돌아온 뒤에도 버전이 증가하므로 오래된 폼은 통과하지 않는다. 동시 요청은 한 건만 성공한다.
  보관·복원은 별도 버전을 사용하고 이름을 변경하지 않는다.
- 다른 학교·같은 학교의 다른 담당자는 검색 결과와 이력에 접근할 수 없고 직접 ID 접근은 404다.
  읽기 역할로 바뀐 본인은 자기 이름 이력을 조회할 수 있지만 변경할 수 없다. 소속 해제 시 조회도 차단한다.
  메타데이터 검색·이력은 원본·분석 JSON을 재판정하거나 모델을 호출하지 않는다.
- 이름·제목은 HTML 이스케이프하고 기존 인증·`private, no-store` 응답을 유지한다.
  기본 HTML 폼으로 구현해 JavaScript 없이도 변경·이력 조회가 가능하다.
- 원본·추출 캐시·분석 JSON·정식 검수·개인 초안·확인 묶음·다운로드 영수증을 수정하지 않는다.
  HTML/ZIP/CSV와 버전 변경 시 기존 묶음 다운로드를 차단하는 계약은 유지한다.

## 실제 검증

- 대상 검사: **142 passed / 실패 0, 66.94초**.
  신규 이름 관리와 기존 경고 저장·검색/보관·영수증 조건 저장·CLI를 함께 실행했다.
  v12 백업·추가형 업그레이드·롤백·재실행, DB 재개방·과거 변환본,
  Unicode·리터럴 검색, 잘못된 이름/버전, 동일/동시/오래된 요청, 역할/소유자/CSRF,
  변경 이력·원본/검수/영수증/보관 상태 보존을 확인했다.
- 전체 회귀: **2,412 passed / 17 skipped / 실패 0, 650.22초**. 기존 선택적 의존성·맥북 전용·비공개 HWP fixture 제외 17건을 유지했다. 학습·추론으로 대체하지 않았다.
- 설치된 Playwright·Chrome 브라우저 **11개 항목 통과**.
  데스크톱 1280×900·모바일 390×844에서 명시 확인·이름 변경·HTML 이스케이프,
  현재/처음 이름 검색, 목록 페이지/검색 문맥 유지, 새 브라우저 재로그인,
  처음 이름으로 되돌리기·보관한 항목 변경·22건 이력의 20/2 페이지,
  JavaScript 없는 폼·다른 학교/계정 차단·기존 HTML 실제 다운로드를 검증했다.
  화면 넘침·페이지 예외·예상하지 않은 콘솔 오류/경고 0이다.
  오래된 탭 제출의 예상 HTTP 409 콘솔 기록 1건을 충돌 안내·이력 중복 방지와 함께 확인했다.
  데스크톱·모바일 스크린샷을 직접 확인했고 소유한 시험 서버와 브라우저를 종료했다.
- 이름 기록 **4건만 추가**됐다. 그 테이블의 INSERT를 제외한 전체 DB 덤프,
  기존 이름 이벤트, 원본·추출 캐시 SHA/mtime, 두 변환본 분석 JSON,
  HTML·묶음 ZIP·영수증 CSV가 동일했다. 보관·복원 기록도 전체 DB 덤프 비교에 포함했다.
  종료 검사 `quick_check=ok`, FK 오류 0이다.
- `compileall moa tests`, `sh -n scripts/install-nas.sh`, `git diff --check` 통과. 최종 변경 파일 23개의 비밀 패턴 검사 발견 사항 0건이다.

Browser 플러그인 대신 설치된 Playwright·Chrome을 재사용했고 브라우저 요청은 소유한 loopback 합성 서버로 제한했다.
패키지·브라우저·모델 설치/다운로드는 실행하지 않았다. 이전 단계의 과거 스키마 fixture는
v13 테이블을 먼저 제외하도록 갱신해 기존 미설치/마이그레이션 검사를 유지했다.

## 외장 SSD·파일 위치

실제 마운트 `/Volumes/KIOXIA-2TB`, UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`,
소유권·쓰기·개별 검증 작업본을 `dev-ssd check`와 기존 `dev-storage` 래퍼로 확인했다.
시작 여유 공간: **1,293.22GiB**. 기존 `.venv312`를 재사용했다.
전체 `development_ready=false`, `physical_stability_resolved=false`는 유지한다.
개별 검증 작업본 허용과 I/O·장치 보호를 유지했으며 물리 안정성 문제가 해결됐다는 의미는 아니다.

소스 기준: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

| 파일 | 변경 내용 |
|---|---|
| `moa/school_warning_search_names.py` | v13·현재/처음 이름·이력·원자적 변경 |
| `moa/school_warning_search_names_web.py` | 명시 확인 폼·충돌 안내·개인 이름 이력 |
| `moa/school_warning_search_management.py` | 현재/처음 이름을 목록 검색·표시에 반영 |
| `moa/school_warning_search_management_web.py`, `moa/school_warning_searches_web.py` | 표시 이름·처음 이름·이름 관리 연결 |
| `moa/school_service.py`, `moa/web_school.py` | 명시적 v13 마이그레이션·인증된 GET/POST |
| `tests/test_school_warning_search_names.py` | 신규 DB/HTTP·권한·동시성·검색·보존 검증 |
| 기존 학교 테스트 13개 | 최신 스키마·과거 스키마 격리 fixture 유지 |
| `README.md`, 이 문서 | 31단계 사용 흐름·계약·결과 |

개발자료 기준: `/Volumes/KIOXIA-2TB/Cache/MOA/school-warning-search-names-20261030`

- `STORAGE_CHECK.json`, `GIT_BEFORE.json`, `PR_BEFORE.json`: 시작 장치·Git/PR 상태.
- `target.log/xml`, `full.log/xml`, `pytest-*/`: 실제 검사·격리 DB·형식별 파서 산출물.
- `tmp/`, `xdg/`: 임시·개발 캐시. 기존 래퍼의 외장 SSD 공통 캐시도 유지한다.
- `browser/data/`, `browser/downloads/`, `browser/screenshots/`, 프로필·디스크 캐시: 합성 자료·보존 다운로드·화면 근거.
- `browser/BASELINE.json`, `browser/BROWSER_RESULT.json`, `browser/SERVER_RESULT.json`: 보존 기준·화면 동작·종료 검사.
- `browser_server.py`, `browser_check.cjs`, `browser_scenarios.cjs`, 실행 로그: 소유한 합성 브라우저 시험.
- `FINAL_TEST_SOURCES.json`, `BROWSER_VISUAL_REVIEW.json`, `STATIC_OK.json`, `SECRET_SCAN.json`, `RESULT.json`: 최종 소스·검사·집계.
- `PR_BODY.md`, `LOCAL_COMMIT.json`, `PR_AFTER.json`: 비밀 없는 PR 설명·커밋·게시 확인.

코드·테스트·비밀 없는 설명서만 기존 `codex/moa-autoqa-v1`과
[PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push한다.
기존 미추적 문서·가상환경 4개를 보존하고 Git 반영에서 제외한다.
원본·DB·모델·캐시·브라우저 자료·비공개 실행 기록은 Git에 넣지 않는다.

NAS에 접속하지 않았으며 기존 `/volume2/moa/app`, `/volume2/moa/data`의 운영 DB·서비스·원본·수집기·인증을 변경하지 않았다.
학습·추론·유료/외부 모델·운영 배포·강제 push·main 병합은 실행하지 않았다.
실학교 수용 시험·NAS 백업/독립 복구·운영 적용은 별도 범위다.
