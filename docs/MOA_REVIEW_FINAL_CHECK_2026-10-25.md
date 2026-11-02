# 정식 수정본 최종 검토와 담당자 확인 — 16단계, 2026-10-25

## 담당자 흐름

정식 수정본의 변경 요약·파일·구역·첨부 관계·남은 경고와 HTML을 대조한 뒤,
확인 사유를 기존 검수 이력에 추가한다.

1. 정식 검수 화면, 수정본 HTML 미리보기 또는 개인 입력의 정식 저장 결과에서
   **확인 전 변경 요약·HTML 검토**를 연다.
2. 자동 분석본 대비 구역 수정 수, 관계 추가·제거·근거 변경 수와 상세 비교 링크를 확인한다.
3. 파일 목록·분석 완전성·남은 경고와 같은 정식 검수 시점의 HTML을 대조한다.
   HTML 크게 보기·HTML 다운로드·HTML/원본 ZIP도 같은 버전에 고정된다.
4. 확인 범위를 읽고 확인란을 직접 선택한 뒤 필수 **확인 사유**를 입력한다.
   **이 정식 검수본 확인 완료**를 누르면 새 확인 이력으로 이동한다.
5. 확인 기록은 작성자·시각·사유를 남기며 이후 다시 열 수 있다.

개인 임시 입력과 다른 탭의 미저장 입력을 자동으로 반영하지 않는다.
기존 직접 검수·확인 기능은 유지한다. 새 화면은 이미 저장된 정식 검수본에 사용한다.
JavaScript 없이도 조회·확인할 수 있다. JavaScript 사용 시 확인 사유를 입력한 뒤
다른 링크로 이동하면 입력 유실 경고가 표시된다.
확인 사유는 임시 자동저장하지 않는다.

확인해도 자동 분석의 경고·미확정 범위는 그대로 남는다.
문서 전체의 완전성 보증, 공개·모델 학습·운영 승인을 뜻하지 않는다.

## 저장·권한·충돌 계약

| 경로 | 계약 |
|---|---|
| `GET /structure-confirm?id=…&revision=…&event=…` | 수신·변환·정식 검수 버전 명시. 한 SQLite 읽기 시점에서 요약·범위·확인 가능 여부 조회 |
| `POST /structure-confirm` | 인증·CSRF·Origin·명시적 확인·필수 사유 검사 후 기존 검수 writer로 확인 1개 추가 |

- 새 테이블·schema 변경·마이그레이션은 없다. 기존 학교 schema v6를 유지한다.
- 기존 `review.save`의 `BEGIN IMMEDIATE`에서 현재 학교 소속·쓰기 권한,
  최신 수신/변환·자동 분석 hash·직전 검수 이벤트·구역/관계 유효성을 다시 검사한다.
  성공 시 기존 `school_structure_reviews`에 `draft → confirmed` 상태 기록 1개만 추가한다.
  자동 분석·수정 내용·이전 검수 행·개인 입력·원본·추출·이전 HTML/ZIP을 고치지 않는다.
- 두 탭의 동시 확인·중복 제출·미리보기 뒤 정식 검수/재변환/정정본 변경은 409이다.
  오래된 검수본은 읽기 전용이며 최신본을 다시 열도록 안내한다.
  이미 확인한 검수본, 빈 구역, 구역 종류 변경으로 맞지 않게 된 관계는 새 확인을 차단한다.
- reader는 조회만 가능하다. 다른 학교는 404, 해제된 소속은 인증 단계에서 차단한다.
  학교·작성자는 세션에서 결정한다. 개인 초안 ID·개인 출처는 새 공유 화면·다운로드에 추가하지 않는다.
- 정확한 단일 필드만 허용한다. GET query 4,096자, POST 32,768바이트,
  확인 사유 2,000자 제한이다. 중복/추가 필드·확인란 누락·빈 사유·잘못된 UTF-8을 거부한다.
  최대 길이 이모지 사유도 POST 한도에 들어가며 그대로 저장된다.
- HTML·파일명·사유·경고를 escape하고 기존 CSP·인증·no-store 정책을 유지한다.
  HTML iframe은 sandbox를 유지한다. 새 조회에서 파일 생성·변환·큐 등록·모델 호출을 하지 않는다.

## 변경 파일·실제 저장 위치

소스 기준 경로:
`/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

- `moa/school_review_check.py`: 읽기 시점 고정·기존 확인 검증/writer 재사용.
- `moa/school_review_check_web.py`: 변경 요약·파일/경고·HTML·명시적 확인·확인 기록 화면.
- `moa/web_school.py`: 인증 라우트·요청 크기 제한.
- `moa/school_review_web.py`, `moa/school_export_web.py`, `moa/school_draft_handoff_web.py`: 기존 흐름에서 최종 검토로 연결.
- `tests/test_school_review_check.py`: 보존·동시성·권한·HTTP·입력 한도·충돌 검증.
- `tests/test_school_formats.py`: 실제 형식별 변환부터 최종 확인까지 보존 검사 확장.
- `README.md`, 이 문서: 사용법·계약·검증 결과.

시험 DB·문서 변환 결과·임시파일·캐시·로그·브라우저 프로필·스크린샷:
`/Volumes/KIOXIA-2TB/Cache/MOA/school-review-check-20261025`

기존 `.venv312`, Node, Playwright, Chrome을 재사용했다. 패키지·모델 재설치는 없다.
시험 명령은 `dev-storage run Relocated-20261017/work/moa-push -- env …`로 실행하고,
TMPDIR·XDG_CACHE_HOME·Python pycache·브라우저 profile/download/cache를 위 시험 경로로 지정했다.

## 실제 검증

- 신규 확인/충돌/권한 검사 37개와 실제 HWP·HWPX·텍스트 PDF·DOCX 시험 4개를 통과했다.
  기취득 비공개 HWP와 나머지 합성 파일을 기존 실제 parser·HTTP로 처리했다.
  업로드·역할 선택·변환·임시 입력 복구·정식 저장·최종 확인·HTML/ZIP 다운로드를 시험했다.
  원본/추출 hash·mtime, 기존 이력 행, 이전 HTML/ZIP 바이트가 같았다.
  비공개 원문의 내용·파일명·식별 hash는 보고서/Git에 포함하지 않는다.
- 초기 형식 시험에서 테스트 로컬 변수와 모듈 이름의 충돌 4건을 발견해 별칭을 고쳤다.
  실제 형식 4개를 재실행해 통과했다. 제품 writer나 저장 자료의 오류는 아니었다.
- 전체 pytest **1,769개 통과 / 16개 skip / 실패 0, 298.65초** (`full.xml`).
  기존 선택적 Torch/맥북 검사는 환경 조건대로 생략했다. 모델·패키지를 설치하지 않았다.
- Node 자동저장 **7개 통과**, 학교 UI JS 두 파일의 구문 검사·Python compileall·
  `git diff --check`를 통과했다. NAS 설치 스크립트는 `sh -n` 구문 검사만 했으며 실행하지 않았다.
- 새 엔드포인트의 세션 권한·CSRF/Origin·필드/크기 한도·escape·저장 시 재검증을 코드와 테스트로 대조했다.
  변경할 코드/문서 10개 파일에서 비밀 키/토큰/개인키 패턴 검색 결과 0건이었다.

Browser 플러그인이 없어 기존 Playwright 1.62.1·설치된 Chrome으로 검증했다.
합성 학교 A/B, 외장 SSD DB, loopback origin만 사용했다.
desktop 1280×900·mobile 390×844·JavaScript-off 환경이다.

| UI 검사 | 결과 |
|---|---|
| 페이지 식별·빈 화면 없음·오류 overlay 없음 | 통과 |
| 콘솔·런타임 | 의도한 404/409 외 관련 오류 없음 |
| 정식 저장 결과 → 최종 검토 → 확인란·사유 → 확인 기록 | 통과 |
| 확인란 필수·사유 유실 경고·중복 제출 차단 | 통과 |
| 다른 탭 정식 수정 뒤 충돌·이전 버전 읽기 전용 | 통과 |
| 잘못된 관계·reader·다른 학교 차단·재로그인 | 통과 |
| JavaScript 없이 확인·고정 HTML/ZIP 보존 | 통과 |
| desktop·mobile 화면·가로 넘침 없음·iframe 텍스트/escape | 통과 |

`browser/BROWSER_RESULT.json`의 21개 검사를 통과했다.
모바일 전체 페이지 캡처에서 화면 밖 iframe이 빈 영역으로 찍혀,
같은 저장본을 읽기 전용으로 다시 열어 iframe을 실제 화면 안으로 스크롤했다.
`screenshots/mobile-html-viewport.png`에서 HTML 표시와 390px 내 레이아웃을 직접 확인했다.
desktop-preview와 desktop-confirmed 화면도 직접 확인했다.

시험 서버 종료 후 `browser/PRESERVATION.json`은 다음을 확인했다.
정식 확인 2개와 경합 시험용 정식 수정 1개만 추가했고, 기존 검수 행·다른 DB 행·원본/추출 파일을 보존했다.
SQLite quick_check=ok·FK 오류 0이다. 모바일 재확인도 DB 행을 바꾸지 않았다.
이는 실제 학교 담당자의 승인 시험·다른 브라우저·운영 NAS 시험을 의미하지 않는다.

## 저장 장치·운영 보호

- `dev-ssd check`에서 실제 `/Volumes/KIOXIA-2TB` 마운트,
  UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, ownership·검증 작업본을 확인했다.
  여유 약 1.3TiB이며 기존 `dev-storage`의 검증 작업본 보호를 통과했다.
  내부/다른 볼륨으로 우회하지 않았다. `development_ready=false`,
  `physical_stability_resolved=false`는 그대로이고 물리 연결 위험 해소를 주장하지 않는다.
- NAS에 접속하지 않았다. 기존 `/volume2/moa/app`, `/volume2/moa/data`의
  운영 DB·서비스·원본·수집 설정·인증은 변경하지 않았다.
- 학습·모델 추론·유료/외부 모델·운영 모델 교체·배포·자동작업 변경은 수행하지 않았다.
- 코드·테스트·비밀 없는 설명서만 기존 `codex/moa-autoqa-v1`과
  [PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push한다.
  원본·DB·모델·캐시·비공개 실행 기록·무관한 미추적 파일은 포함하지 않는다.
  force push·main 병합은 하지 않는다.
