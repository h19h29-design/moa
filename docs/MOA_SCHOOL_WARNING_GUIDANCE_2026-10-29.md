# 학교 담당자용 분석 경고 안내 — 27단계, 2026-10-29

자동 분석 경고를 담당자가 읽을 수 있는 설명, 확인할 내용, 파일·구역 위치로 안내한다.
경고 코드만 보여 주던 자동 분석·담당자 검수·최종 확인·변경점 비교 화면을 개선하고,
검수 수정본 미리보기에도 같은 안내를 추가했다. 새 분석이나 모델 판정은 수행하지 않는다.

## 담당자가 확인하는 내용

- 각 경고에 쉬운 제목, 발생 의미, 원본에서 확인할 내용을 표시한다.
- 파일 번호와 파일명을 함께 보여 같은 이름의 첨부도 구분한다. 구역 근거가 있으면
  해당 파일의 **자동 분석 구역 번호**와 당시 변환 버전에 고정된 위치 링크를 제공한다.
- PDF의 실제 관찰된 쪽수와 추출 텍스트 줄 번호가 있으면 표시한다. 추출 줄 번호는 인쇄 줄 번호와
  다를 수 있다. HWP/HWPX/DOCX 내부 경로를 인쇄 쪽수로 바꾸거나 원본 문단 번호를 추정하지 않는다.
- 구역을 알 수 없으면 `파일 전체 · 세부 위치 미확인`, 파일도 특정되지 않으면
  `문서 묶음 · 파일 위치 미확인`으로 안내한다. 서로 다른 파일의 구역 ID가 섞이면 위치를 연결하지 않는다.
- 위치 링크를 열면 원래 자동 분석의 해당 파일 또는 구역에 이동하고 테두리로 강조한다.
  검수 화면의 페이지나 최신 변환본으로 잘못 넘어가지 않는다.
- 원래 경고 코드·파일/구역 식별자·참조·위치 근거는 접힌 상세 영역에서 확인한다.
  내부 각주 식별자를 문서에 보이는 각주 문구로 표시하지 않는다. 외부 참조는 실행하거나 링크하지 않는다.
- 수정 또는 담당자 확인 완료 후에도 경고는 자동으로 해결 처리하지 않는다.
  알 수 없는 새 코드는 일반 확인 안내와 원래 코드를 남기며, 경고가 없어도 원본 대조 필요를 표시한다.

| 자동 분석 경고 | 담당자 안내 |
|---|---|
| `unreadable_file` | 파일 내용을 충분히 읽지 못함 — 원본 열림 여부와 빠진 본문·표·첨부 확인 |
| `native_zones_unavailable` | 머리말·본문·각주 구분 미확인 — 누락·섞임 대조 |
| `unsupported_structure` | 지원하지 않는 파일 구조 — 원본 내용과 첨부 관계 직접 확인 |
| `structure_parser_failed` | 구조 분석 실패 — 원본 상태 확인 및 담당 관리자에게 안내 |
| `notice_structure_limit` | 묶음의 분석 한도 초과 — 원본 목록·분석 범위 확인 |
| `invalid_table_header_flag` | 표 제목 행 표시를 읽지 못함 — 열 제목 위치 확인 |
| `noncontiguous_table_header_ignored` | 연속되지 않은 표 제목 행 표시 — 제목·본문 행 대조 |
| `table_geometry_unconfirmed` | 셀 배치·병합 범위 미확정 — 원본의 행·열·값 대조 |
| `missing_header_part` | 참조된 머리말·바닥글 자료 없음 — 학교명·발신 정보 누락 확인 |
| `external_or_unsafe_part_ignored` | 읽지 않은 외부/허용 밖 참조 — 외부 주소 실행 없이 원본 대조 |
| `unresolved_footnote` | 각주 표시와 설명을 하나로 연결하지 못함 — 원본 표시·설명 대조 |
| `unlinked_footnote` | 각주가 가리키는 본문 미확인 — 본문과의 관계 확인 |
| `unresolved_attachment` | 언급된 첨부 파일을 찾지 못함 — 누락·파일명 차이 확인 |
| `ambiguous_attachment` | 첨부 후보가 여러 개임 — 파일 번호·내용과 관계 대조 |

## 데이터와 다운로드 보존 계약

새 모듈은 권한 검사로 가져온 분석 결과를 읽어 화면 안내만 만든다. 추가 DB 테이블·마이그레이션·작업 큐는 없다.
자동 분석 JSON, 원본 SHA 객체, 변환·검수 스냅샷, 개인 입력, 묶음 목록, 영수증 행을 수정하지 않는다.
분석기와 분석 버전, 검수 수정본 HTML 렌더러, ZIP/CSV 생성 방식도 변경하지 않는다.

따라서 **내려받는 기존 HTML/ZIP의 경고 표현은 종전 형식**이며, 쉬운 안내는 인증된 검수 화면에서 제공한다.
고정된 파일 바이트와 다운로드 영수증 해시가 달라지지 않게 하기 위한 범위다.
미리보기의 HTML iframe과 다운로드는 같은 저장본이며 안내 영역만 화면 바깥에 더한다.

모든 링크는 같은 수신본과 변환 버전으로 고정된다. 학교 A의 링크를 학교 B에서 열면 거절하고,
소속 해제 이후에도 접근을 차단한다. 파일명·참조·근거는 HTML 이스케이프하며 구역 링크의 fragment는 해시로 만든다.

## 실제 검증

- 관련 테스트 164개 통과. 문구를 마지막으로 정리한 뒤 신규 경고 테스트 32개도 별도 통과했다.
- 최종 전체 회귀는 **2,276 passed / 17 skipped / 실패 0, 574.18초**다.
  17건은 기존 선택적 의존성·맥북 전용 검사·비공개 HWP fixture 조건의 제외다.
  이를 실제 학습·모델 추론으로 대체하지 않았다.
- 최종 코드로 Playwright·Chrome 브라우저 확인 **10개 항목 통과**, 콘솔 오류·경고 0.
  다섯 화면의 데스크톱·모바일 가로 넘침 없음, 원래 근거 펼치기, 정확한 구역·파일 이동,
  JavaScript 없는 동작, 학교 B 접근 차단, 실제 HTML 다운로드 바이트 보존을 확인했다.
- 최종 합성 DB의 모든 행, 원본·추출 캐시 SHA/mtime, 분석 JSON, HTML·묶음 ZIP·영수증 CSV가
  조회 전후 모두 동일했다. `quick_check=ok`, 외래 키 오류 0. 마지막 화면 검증 서버는
  `PRAGMA query_only=ON`으로 실행했으며 종료 후에도 같은 보존 검사를 통과했다.
- `compileall moa tests`, `sh -n scripts/install-nas.sh`, `git diff --check` 통과.
  변경 파일 9개의 비밀 패턴 검사에서도 발견 사항이 없었다.
  초기 시험 자료의 HTML 형식과 저장 묶음 ID 길이를 기존 입력 계약에 맞게 고친 뒤 재검증했다.

합성 자료는 실제 HTML, HWPX, 텍스트 PDF와 구조 분석 미지원 텍스트 첨부를 사용했다.
같은 이름의 다른 첨부, 없는 첨부, 연결되지 않은 각주를 실제 파서로 처리했다.
분석 JSON, 모든 DB 행, 원본·추출 캐시의 SHA/mtime, 저장 HTML·원본 ZIP·확인 완료 묶음 ZIP·영수증 CSV가
화면 조회 전후 동일한지 확인한다. 버전이 바뀐 뒤에도 과거 경고의 링크가 원래 변환본에 머무는지 검사한다.

브라우저는 기존 Playwright·Chrome을 사용하며 Browser 플러그인은 없어 추가 설치하지 않았다.
1280×900 데스크톱과 390×844 모바일에서 다섯 화면을 열고, 근거 펼치기·위치 링크·실제 HTML 다운로드를
시험한다. JavaScript 비활성 상태와 다른 학교 계정도 포함한다. 네트워크는 시험 서버의 loopback으로 제한한다.

## 외장 SSD·운영 경계와 파일 위치

`dev-ssd check`로 마운트 `/Volumes/KIOXIA-2TB`, UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`,
소유권·쓰기 가능·기존 작업본을 확인했다. 시작 여유 공간은 **1,322.29GiB**였다.
기존 `dev-storage` 보호와 `.venv312`를 재사용했다. 전체 readiness 및 `physical_stability_resolved`는
기존처럼 false이며, 검증된 작업본 허용 범위와 I/O 오류 시 중단 보호를 유지했다. 물리 안정성 해결로 보고하지 않는다.

소스 기준: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

| 변경 파일 | 내용 |
|---|---|
| `moa/school_warning_web.py` | 경고 14종 설명·위치·안전한 근거 표시·고정 링크 |
| `moa/web_school.py` | 자동 분석 안내, 원래 파일/구역의 이동 지점 |
| `moa/school_review_web.py` | 담당자 검수 화면 안내 |
| `moa/school_review_check_web.py` | 최종 확인 화면 안내 |
| `moa/school_compare_web.py` | 기존 경고 페이지 구분을 유지한 변경점 비교 안내 |
| `moa/school_export_web.py` | 저장본 미리보기 외부의 안내 영역 |
| `tests/test_school_warning_guidance.py` | 실제 파서·위치·권한·보존·이스케이프 회귀 검사 |
| `README.md` | 이 단계 문서 연결 |
| `docs/MOA_SCHOOL_WARNING_GUIDANCE_2026-10-29.md` | 이 보고서 |

개발자료 기준: `/Volumes/KIOXIA-2TB/Cache/MOA/school-warning-guidance-20261029`

- `STORAGE_CHECK.json`, `GIT_BEFORE.txt`: 시작 장치·Git 점검.
- `targeted*.log`, `final-target.log`, `regression*.log/xml`, `static.log`, `SECRET_SCAN.json`: 검사 근거.
- `tmp/`, `xdg/`, `pytest-*/`: 캐시·임시 파일·격리 시험 DB와 파서 결과.
- `browser/data/`: 새 합성 학교 SQLite·원본·추출물. NAS DB를 복사하지 않았다.
- `browser/downloads/`, `browser/screenshots/`, 브라우저 프로필·캐시: 실제 다운로드·화면 증거.
- `browser/BASELINE.json`, `browser/BROWSER_RESULT.json`, `browser/SERVER_RESULT.json`: 보존 기준과 확인 결과.
- `browser_*.py/cjs`, `browser-*.log`: 시험 서버와 브라우저 드라이버·로그.
- `PR_BEFORE.json`, `PR_BODY.md`, `RESULT.json`: 비밀 없는 GitHub 설명과 결과 집계.

이 캐시의 원본 실행 기록·DB·프로필·자료는 Git에 올리지 않는다. 코드·테스트·설명서만 기존
`codex/moa-autoqa-v1` 및 [PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push한다.

이번 작업에서는 NAS에 접속하지 않았다. 기존 `/volume2/moa/app`, `/volume2/moa/data`,
`/volume2/moa/data/db/moa.sqlite3`의 운영 DB·서비스·원본·수집기·인증 설정을 변경하지 않았다.
현재 NAS 상태를 새로 검증했다는 뜻은 아니다. 학습·모델 추론·유료/외부 모델·운영 배포·강제 push·main 병합은 없다.

## 남아 있는 범위

대표 실문서를 담당자가 원본과 대조하는 수용 시험, 최신 NAS 전체 백업과 독립 매체 복구 검증,
실학교 계정·기존 경로의 적용/롤백 계획은 운영 적용 전에 별도로 필요하다.
HWP/HWPX/DOCX의 인쇄 쪽수 복원, 스캔 문서 OCR, 새 파서 개선이나 실제 학교 홈페이지 수신은 이번 범위에 포함하지 않는다.
