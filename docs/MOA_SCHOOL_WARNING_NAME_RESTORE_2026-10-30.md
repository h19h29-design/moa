# 저장한 분석 경고 조건의 이전 이름 선택·복원 — 32단계, 2026-10-30

31단계 이름 변경 이력에서 **이전 이름 또는 처음 이름을 선택해 복원**하는 기능을 추가했다.
담당자가 현재 이름·선택한 이름을 확인한 뒤 제출하면 선택한 이름을 새 이름 기록으로 추가한다.
기존 이름 이력·처음 저장한 검색 조건·보관 상태는 그대로 보존한다.

## 사용 흐름

1. **저장한 경고 검색 조건 → 이름 변경과 이력**을 연다.
2. 이름 이력에서 **이 이름으로 복원**, 또는 **처음 저장한 이름으로 복원**을 선택한다.
   현재 이름과 같은 기록에는 복원 링크를 표시하지 않는다.
3. **이전 이름 복원 확인** 화면에서 현재 이름·복원할 이름·기록 시각·문서·보관 상태·검색 필터를 확인한다.
   이 화면을 여는 것만으로는 변경하지 않는다.
4. **이름 이력으로 돌아가기**를 누르면 원래 이름 이력 페이지와 목록 검색어·상태·페이지로 돌아간다.
5. 확인란을 선택하고 **선택한 이름으로 복원**을 누른다.
   최신 이름 이력 첫 페이지에서 새 이름 기록을 확인한다. 목록으로 돌아갈 검색 조건은 유지한다.
6. 보관함의 검색 조건도 이름만 복원할 수 있다. 보관 상태 자체는 바뀌지 않는다.
   처음 이름으로 되돌린 뒤에도 기록 번호가 증가하므로 이전 확인 화면은 다시 사용할 수 없다.
7. 재로그인 후에도 복원한 이름과 이전 이름들이 남는다. JavaScript 없이 기본 HTML 폼으로 작동한다.

## DB·권한·충돌·보존 계약

- 기존 학교 스키마 **v13**과 `school_warning_search_names`를 재사용한다.
  새 테이블·스키마 마이그레이션·운영 DB 적용은 없다. v13 미설치 시 복원은 409로 거절하며 자동 설치하지 않는다.
- 선택 기록 번호 `source_version=0`은 처음 저장한 이름, 양수는 해당 조건의 이름 이력 번호다.
  이름 문자열을 POST로 받지 않는다. 서버가 조건 ID·학교·계정·기록 번호로 저장된 이름을 읽는다.
  존재하지 않는 기록은 404, 잘못된 기록 번호는 400이다.
- `selection`은 기존 읽기 스냅샷에서 현재 메타데이터와 선택 기록을 조회한다.
  원본·변환본·분석 JSON을 읽거나 모델을 호출하지 않는다.
- 복원과 직접 이름 변경은 같은 `BEGIN IMMEDIATE` 트랜잭션 경로를 사용한다.
  쓰기 권한·조건 소유자·예상 이름 버전·예상 보관 버전을 재확인하고 이름 기록 한 행만 추가한다.
  동시에 복원/이름 변경을 제출하면 한 건만 성공한다. 원래 v13 불변/소유자/연속 버전 트리거를 유지한다.
- 다른 탭의 이름 변경·보관/복원, 중복 제출, 현재와 같은 이름 제출은 409다.
  충돌 화면은 최신 현재 이름과 선택한 이름을 다시 보여준다. 같아진 이름은 재제출 폼을 표시하지 않는다.
  보관 상태가 바뀌었다가 돌아온 경우도 상태 기록 번호로 오래된 폼을 차단한다.
- GET/POST `/structure-warning-searches/name-restore`는 기존 세션·학교 소속을 확인한다.
  POST는 기존 CSRF·Origin 검사 후 정확히 한 번씩 제공된 허용 필드만 받으며 최대 4KiB다.
  허용 필드: `csrf/id/source_version/expected/expected_state/history_page/acknowledge/q/state/list_page`.
  폼 밖 이름·학교·계정 주입, 중복/미지 필드, URL 쿼리, 잘못된 숫자·검색 문맥·미확인 제출을 차단한다.
- 다른 학교와 같은 학교의 다른 계정은 이름 이력·복원 확인에 접근할 수 없다(404).
  본인의 읽기 역할은 조회만 가능하고 제출은 403, 소속 해제 시 조회도 차단한다.
- 이름과 문서 제목은 HTML 이스케이프한다. 기존 `private, no-store` 응답을 유지한다.
- 처음 검색 조건·수신본·변환 버전·검색 필터·보관 이력·원본·추출 캐시·분석 JSON·검수·초안·묶음·영수증은 수정하지 않는다.
  기존 HTML·ZIP·CSV와 버전 변경 시 다운로드를 차단하는 계약도 그대로 유지한다.
- 복원 결과는 다른 이름 변경과 동일한 이름 이력 행이다. 복원 출처를 별도 종류나 새 테이블로 기록하지 않는다.

## 실제 검증

- 대상 검사: **128 passed / 실패 0, 68.47초**.
  신규 복원·기존 이름 관리·검색/보관·경고 조건 저장·학교 CLI를 함께 실행했다.
- 최종 신규 검사: **20 passed / 실패 0, 15.62초**.
  이전/처음 이름 복원, DB 재개방, 기존 이름 행 보존, 읽기 전용 선택,
  잘못된/없는 원본 기록, 동일/중복/ABA/동시 요청, 역할·학교/계정·CSRF·Origin,
  명시 확인·취소/검색 문맥·재로그인·미설치 거절·다운로드 불변을 검증했다.
- 전체 회귀: **2,432 passed / 17 skipped / 실패 0, 655.47초**. 기존 Torch 선택 의존성·맥북 전용·비공개 HWP 자료 조건의 17개 skip을 유지했다. 학습·추론으로 대체하지 않았다.
- `compileall moa tests`, `sh -n scripts/install-nas.sh`, `git diff --check` 통과. 최종 변경 파일 7개의 비밀 패턴 발견 사항 0건이다.

### 브라우저 QA

검증 흐름: **내 이름 이력 → 이전/처음 이름 선택 → 확인 또는 취소 → 새 이름 기록·재로그인·보존 다운로드 확인**.

환경: 소유한 `http://127.0.0.1` 임의 포트의 합성 서버, Chrome/설치된 Playwright,
데스크톱 1280×900·모바일 390×844·JavaScript 비활성 환경.
Browser 플러그인/skill이 제공되지 않아 기존 Playwright를 재사용했다(`Browser plugin not available`).
패키지·브라우저·모델 설치/다운로드 없이 모든 요청을 소유 loopback origin으로 제한했다.

| 확인 | 결과 |
|---|---|
| 페이지 URL·제목·본문 | 의도한 경로·MOA 검수·유의미한 본문 확인 |
| 빈 화면·프레임워크 오류 표시 | 없음 |
| 콘솔·페이지 오류 | 예상하지 않은 오류/경고 0, 오래된 폼의 예상 HTTP 409 기록 1건 |
| 데스크톱·모바일 화면 | 현재/선택 이름·확인란·복원 버튼 읽기 가능, 가로 넘침 없음 |
| 명시 확인·취소 | 미확인 HTML 폼 제출 차단, 취소 후 이력/검색 문맥 유지 |
| 이전·처음 이름 복원 | 새 기록 추가, 기존 행 유지, 같은 이름의 불필요한 복원 링크 숨김 |
| 오래된 탭 | 409와 최신 현재 이름 표시, 중복 이력 미발생 |
| 긴 이력·보관 상태 | 2번째 이력 페이지에서 복원·취소, 보관 상태 기록 #21 유지 |
| 재로그인·JavaScript 없음 | 복원 이름/이력 유지, 기본 폼 동작 |
| 학교·계정 분리 | 다른 학교와 같은 학교의 다른 계정은 확인 화면 404 |
| 실제 다운로드 | 기존 HTML 바이트 동일 |

실제 브라우저 **11개 항목 통과**. 데스크톱·모바일 스크린샷을 직접 확인했다.
소유한 서버·브라우저를 정상 종료했다.
이름 기록 **4건만 추가**됐고, 그 테이블의 INSERT를 제외한 전체 DB 덤프가 같았다.
기존 이름 행·원본/추출 SHA와 mtime·두 변환본 분석 JSON·HTML·ZIP·영수증 CSV 모두 동일했다.
종료 검사 `quick_check=ok`, FK 오류 0이다.

주요 실행: 기존 `dev-storage run … -- .venv312/bin/python -m pytest`,
`python -m compileall -q moa tests`, `sh -n scripts/install-nas.sh`, `git diff --check`,
설치된 Playwright의 영속 브라우저 프로필·loopback 경로·폼 클릭·상태/다운로드 비교.

## 외장 SSD·파일 위치

UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, 실제 마운트 `/Volumes/KIOXIA-2TB`,
소유권·쓰기·같은 장치의 소스/캐시 경로와 기존 `.venv312`를 검증했다. 시작 여유 **1,283.43GiB**.
기존 `dev-storage`의 개별 작업본·I/O·장치 상태 보호를 그대로 사용한다.
전체 `development_ready=false`, `physical_stability_resolved=false`는 유지하며 물리 안정성 문제의 해결로 보고하지 않는다.
장치 검사가 실패하면 내부 SSD나 다른 볼륨으로 우회하지 않는다.

소스 기준: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

| 파일 | 내용 |
|---|---|
| `moa/school_warning_search_names.py` | 소유한 이전/처음 이름 선택과 기존 이름 변경 경로를 통한 원자적 복원 |
| `moa/school_warning_name_restore_web.py` | 확인 화면·취소·명시 복원·충돌 표시 |
| `moa/school_warning_search_names_web.py` | 이전/처음 이름 복원 링크·이력 페이지 문맥 |
| `moa/web_school.py` | 인증된 GET/POST·4KiB 제한·엄격한 폼 |
| `tests/test_school_warning_name_restore.py` | 신규 20개 DB/HTTP·동시성·권한·보존 검사 |
| `README.md`, 이 문서 | 32단계 안내·계약·실제 결과 |

개발자료 기준: `/Volumes/KIOXIA-2TB/Cache/MOA/school-warning-name-restore-20261030`

- `STORAGE_CHECK.json`, `GIT_BEFORE.json`, `PR_BEFORE.json`: 장치·원래 Git/PR 상태.
- `target.log/xml`, `restore-final.log/xml`, `full.log/xml`, `pytest-*/`: 검사·SSD 격리 테스트 DB·파서 산출물.
- `tmp/`, `xdg/`, 기존 래퍼의 외장 공통 캐시: 임시·개발 캐시.
- `browser/data/`, 프로필·캐시, `browser/downloads/`, `browser/screenshots/`: 합성 자료·다운로드·화면 근거.
- `browser/BASELINE.json`, `browser/BROWSER_RESULT.json`, `browser/SERVER_RESULT.json`: 보존 기준·UI 동작·종료 검증.
- `browser_server.py`, `browser_helpers.cjs`, `browser_scenarios.cjs`, `browser_check.cjs`, 로그: 합성 QA 실행기.
- `FINAL_TEST_SOURCES.json`, `BROWSER_VISUAL_REVIEW.json`, `STATIC_OK.json`, `SECRET_SCAN.json`, `RESULT.json`: 최종 소스·검사·집계.
- `PR_BODY.md`, `LOCAL_COMMIT.json`, `PR_AFTER.json`: 비밀 없는 PR 설명·커밋·반영 확인.

코드·테스트·비밀 없는 문서 **7개만** 기존 `codex/moa-autoqa-v1`과
[PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push한다.
기존 미추적 가상환경·문서 4개는 보존하고 반영에서 제외한다.
원본·DB·모델·캐시·브라우저 자료·비공개 실행 기록을 Git에 넣지 않는다.

NAS에 접속하지 않았고 `/volume2/moa/app`, `/volume2/moa/data` 운영 DB·서비스·원본을 변경하지 않았다.
학습·모델 추론·유료/외부 모델·운영 배포·force push·main 병합은 하지 않았다.
실학교 실자료 수용 시험·다른 브라우저·NAS 독립 백업/복구·운영 적용은 이번 검증 범위가 아니다.
