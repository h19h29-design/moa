# 이전 검수본을 새 수정본으로 복원 — 6단계, 2026-10-23

## 담당자 흐름

학교 전용 로그인 → 수신본 → 담당자 구조 검수 → **검수 이력** → 과거 항목의
**이 검수본을 새 수정본으로 복원** → 현재/대상 변경점 확인 → 명시적 체크·사유 입력 → 저장.

- 같은 수신본·변환 버전 안의 과거 검수 overlay를 복사하여 **새 draft 이력**으로 추가한다.
  구역의 헤더·본문·각주 종류/텍스트와 여러 첨부 사이의 관계·복수 근거를 함께 되돌린다.
- 과거의 확인 완료 상태는 복사하지 않는다. 새 수정본은 항상 **수정 중 · 확인 필요**이며 경고 확인도 초기화된다.
  이후 기존 검수 화면에서 내용을 점검한 뒤 별도로 확인해야 한다.
- 원본 객체·자동 분석·기존 검수 행·과거 HTML/ZIP은 삭제하거나 덮어쓰지 않는다.
  새 수정본 HTML/ZIP은 새 검수 ID로 생성하고, 과거 검수 ID의 출력은 그대로 유지한다.
- 복원 이벤트에는 **복원 출처 ID/순번, 직전 부모 ID, 변경 전/후, 담당자, 시각, 사유**를 기록한다.
  이력에서 출처와 직전 버전 비교로 이동할 수 있다. 사유에 포함된 HTML은 문자로 표시한다.
- 현재 버전 자체, 다른 학교·수신본·변환, 자동 분석 원본으로 직접 초기화하는 복원은 허용하지 않는다.
  이미 같은 내용인 draft의 반복 복원은 거부한다. 동일 내용이어도 현재 confirmed면 새 draft로 다시 검수할 수 있다.
- 표 셀·파일 역할·원본 위치·추출 근거를 임의 재작성하거나 새 학습/공개/운영 승인을 만들지 않는다.

## 조회·쓰기·DB 계약

새 테이블·스키마 마이그레이션은 없다. 기존 학교 schema v2의 append-only `school_structure_reviews`와
자동 분석·SHA 객체 저장소를 재사용한다. 업데이트/삭제 방지 트리거도 그대로다.

| 경로/항목 | 계약 |
|---|---|
| `GET /structure-restore` | `id`, `revision`, `source`를 각각 하나씩 필수 지정; 읽기 전용 확인 화면 |
| 확인 화면 | 현재/대상 검수 순번·상태·담당자·사유, 구역/관계 변경 수, 고정 비교·당시 HTML 링크 |
| `POST /structure-review` | 기존 인증·CSRF 경로, `action=restore`, `source_event`, `acknowledge_restore=yes`, 사유 |
| 동시성 pin | 현재 `expected_review`와 원분석 `base_hash` 필수; 저장 직전 재검증 |
| 원자성 | `BEGIN IMMEDIATE` 안에서 회원 쓰기 권한·최신 수신/변환·출처·hash·부모를 확인하고 새 이벤트 한 행 추가 |
| 복원 데이터 | 출처의 overlay만 복사; 원분석/원본 파일은 그대로, 결과 상태 `draft`, `warnings_acknowledged=0` |
| 감사 변화 | `restore_source`와 변경 구역/관계 전후, 필요 시 confirmed→draft 상태 변화 |
| 이력 조회 | 최대 20개 및 감사 JSON 누적 8MiB; 큰 이력은 더 작은 페이지, 순번 커서로 빠짐없이 이어짐 |

이력 조회는 먼저 메타데이터와 UTF-8 바이트 길이만 읽고 한도 안의 행만 가져와 역직렬화한다.
내용을 잘라내지 않고 다음 페이지로 넘기며, 단일 이력이 한도를 넘으면 디코딩 전에 실패한다.
HTML 복원 이력은 출처·변경 수를 표시하고, 전체 전후 내용은 고정 비교의 페이지에서 확인한다.
기존 이력 JSON은 선택된 이벤트의 전체 감사 내용을 유지한다.

무권한/권한 회수, 타 학교, 다른 수신본/변환, 이미 진행 중인 재변환, 새 정정본,
hash 불일치·오래된 부모·중복 제출은 안전하게 거부한다. 빈 pin·중복 pin(빈 값 포함)·추가 권한/overlay 필드도 거부한다.
reader는 확인 화면을 읽을 수 있지만 복원 폼과 쓰기 권한은 없다. 미리보기 GET은 DB/큐/확인 이력을 만들지 않는다.

기존 overlay 2MiB, 유효 구조 8MiB 제한과 새 복원 감사 8MiB 제한을 적용한다.
학교 범위는 요청 필드가 아닌 인증된 principal에서 얻으며, 이름·사유·문서 텍스트·속성은 escape한다.
기존 `private, no-store`, CSP, 세션/Origin/CSRF 보호를 유지한다.

## 외장 저장소와 운영 보호

- 소스: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`.
- 실행 자료: `/Volumes/KIOXIA-2TB/Cache/MOA/school-review-restore-20261023` (0700).
- `dev-ssd check`로 실제 mount, UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, ownership과 약 1.4TiB 여유를 확인했다.
  `dev-storage` 검증 작업본·상태 보호를 통과했으며 **과거 물리 연결 불안정/전체 readiness 미해결 표시는 해제하지 않았다**.
  새로운 I/O 오류나 SSD 불일치 시 중단하며 내부 SSD·다른 볼륨으로 우회하지 않는다.
- 기존 `.venv312` Python 3.12.13, 설치된 Chrome/Playwright를 재사용했다. 패키지 재설치·모델 다운로드 없음.
- NAS `/volume2/moa/app`, `/volume2/moa/data` 및 DB·수집기·원본·서비스에 접속/쓰기/배포 명령을 실행하지 않았다.
  이번 작업이 운영 상태를 새로 검증했다는 의미는 아니다. MacBook 학습·추론, 유료/외부 모델 호출 없음.
- 미추적 사용자 문서와 `.venv-py39-unused/`는 보존하며 이번 커밋에 포함하지 않는다.

## 실제 검증

테스트 우선 개발로 복원 동작·경계 거부를 먼저 실패시킨 뒤 구현했다.
보안 검토에서 회원 범위·핀·원자성·출력 escaping을, 독립 읽기 전용 코드 검토에서 보존·자원 경계를 확인했다.

- 최초 신규 시험: `red1.xml` **11 실패 / 14 통과** → 기존 관련 시험 포함 `green1.xml` **108 통과**.
- 보강 시험에서 빈 중복 pin과 과도한 이력 HTML을 재현(`red2.xml` **2 실패 / 29 통과**)한 뒤 수정.
  신규 복원 및 문서 형식 시험 `green2.xml` **35 통과**.
- 독립 검토는 중대 문제 없이 큰 복원 감사 20개를 동시에 읽는 자원 위험 1건을 지적했다.
  바이트 기준 이력 페이지 제한과 모바일 확인 문구 간격을 실패 시험으로 확인(`red3.xml` **3 실패**)한 뒤 보강했다.
  신규 **33개**와 기존 검수/비교를 포함한 `green3.xml` **97 통과**, 32.15초.
  재검토에서 이력 메모리 지적의 해결과 페이지 커서 무누락을 확인했으며 Critical/Important 문제는 없었다.
  비교 선택 목록의 설명도 새 계약에 맞게 **최대 20개**로 표시한다.
- 기능/보호: 새 draft·출처·부모·사유·확인 초기화, 헤더/각주/복수 첨부와 관계 근거,
  이전 복원본 다시 선택, 동시 저장 1건만 성공, stale form, hash/범위/크기 실패 시 롤백,
  A/B·reader·권한 회수, 위조/중복 인자, CSRF/Origin, 원본·자동 분석·과거 감사/HTML/ZIP 보존을 확인했다.
- 전체 회귀 1차 `full1.xml`: **1,427 통과 / 16 skip / 실패 0**, 97.55초.
  자원 제한 보강 후 `full2.xml`: **1,429 통과 / 16 skip / 실패 0**, 98.82초.
  skip 16개는 기존 선택적 Torch 의존성/맥북 tensor·unit/collection 검사다. 모델 의존성을 설치하거나
  맥북 학습·추론을 실행하여 skip을 강제로 해제하지 않았다.
  마지막 비교 화면 안내 문구 수정 후 해당 회귀도 `comparison-final.xml` **40 통과**, 14.41초다.
  최종 커밋 대상 코드로 전체 회귀를 다시 실행한 `full3.xml`도 **1,429 통과 / 16 skip / 실패·오류 0**, 95.84초다.
- HWP(기취득 비공개 fixture)·합성 HWPX·DOCX·텍스트 PDF 네 형식의 업로드 → 변환 → 수정/확인 →
  비교 → 복원 → 새 HTML/ZIP을 실제 시험했다. 기존 원본 bytes, 추출 캐시 내용/시각, 이전 검수 행·HTML/ZIP을 보존한다.
  비공개 HWP 원문·내용·원본 경로 식별자는 이 문서나 Git에 포함하지 않는다.
- 네 형식 모두 parser `extracted`, 복원 확인/저장/새 HTML/ZIP **HTTP 200**, 원본·과거 검수 행/출력·자동 분석 보존을 확인했다.
- `python -m compileall -q moa`, `sh -n scripts/install-nas.sh`(구문 검사만), `git diff --check` 통과.

### 브라우저 QA

전용 Browser plugin/skill이 없어 기존 Playwright와 설치된 Chrome을 사용했다.
합성 학교/담당자/문서의 loopback 서버에서 로그인 → 이력 → 복원 확인 → 현재/대상 비교 →
명시적 체크·사유 입력 → 새 draft 저장 → 출처 이력 → 새 HTML 흐름을 실행했다.

| 검사 | 결과 |
|---|---|
| 구역 4개 변경, 첨부 관계 1개 복원 | 확인 화면과 고정 비교에서 확인 |
| 확인 화면 GET | 검수 ID 변화 없음 |
| 명시적 체크 | 체크 전 폼 유효성 거부 |
| 저장 후 상태·출처·부모 | 새 draft, 경고 미확인, 출처/부모 일치 |
| 과거 HTML·ZIP | bytes 동일 |
| 새 HTML | 복원한 비용으로 표시 |
| HTML 태그를 포함한 합성 사유 | 문자로 표시; 활성 img/script 0 |
| 1280×900 / 390×844 | 직접 화면 확인, 모바일 가로 overflow 없음 |
| 확인 체크·사유 입력 구분 | 별도 문단과 실제 배치 확인 |

애플리케이션 오류는 없고, 콘솔의 `/favicon.ico` 401/404는 기존 endpoint 부재로 별도 기록했다.
실제 NAS·다른 브라우저·모든 모바일 기기 시험을 했다는 의미는 아니다.
최종 근거는 `browser-final/BROWSER_RESULT.json`, `browser-final/screenshots/{desktop-confirmation,mobile-confirmation,mobile-restore-form,desktop-restored-history}.png`다.
시험 종료 후 이 작업이 실행한 loopback 서버와 브라우저만 종료했다. 다른 앱/서비스는 종료하지 않았다.

## 파일별 위치·Git

아래 파일은 모두 위 외장 작업본 아래에 있다.

| 파일 | 역할 |
|---|---|
| `moa/school_review.py` | 원자적 복원·출처/전후 감사·byte 제한 이력 페이지 |
| `moa/school_restore_web.py` | 읽기 전용 복원 확인·명시적 체크·사유 |
| `moa/school_review_web.py` | 복원 제출 검증·이력/출처/비교 링크 |
| `moa/school_compare_web.py` | 크기에 따라 더 작아질 수 있는 이력 목록 안내 |
| `moa/web_school.py` | 인증된 확인 경로·중복/빈 pin 파싱 보호 |
| `tests/test_school_review_restore.py` | 신규 복원·보존·권한·경합·크기 시험 33개 |
| `tests/test_school_formats.py` | 네 형식 실제 복원·HTML/ZIP 보존 회귀 |
| `README.md`, 이 문서 | 비밀 없는 사용·DB 계약·검증 설명 |

실행 폴더의 `pytest/`에 격리 DB/객체/백업, `formats-full/`, `formats-final/`, `formats-commit/`에 네 형식 시험 증거,
`browser-data/`, `browser-final/browser-data/`에 합성 브라우저 자료가 있다.
JUnit XML, `BROWSER_RESULT.json`, `screenshots/`, 브라우저 스크립트/프로필/캐시/다운로드,
`tmp/`, `cache/`도 같은 외장 실행 폴더 아래에 보존한다. 이 실행 자료는 Git에 올리지 않는다.

기존 `codex/moa-autoqa-v1` / [PR #1](https://github.com/h19h29-design/moa/pull/1)에 코드·테스트·비밀 없는 문서만
정상 commit/push한다. force push·main merge·NAS 운영 배포는 하지 않는다. 최종 커밋/CI는 완료 보고와 PR checks로 확인한다.
