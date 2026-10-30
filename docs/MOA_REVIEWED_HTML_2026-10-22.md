# 학교 담당자 수정본 HTML·다운로드 — 2026-10-22

## 범위와 사용 흐름

학교 전용 로그인 → 수신본 → 담당자 구조 검수 → **이 검수 버전의 HTML 미리보기·다운로드**.
검수 이력의 **당시 수정본 HTML·다운로드**에서도 이전 버전을 연다.

- 구역 텍스트·종류, 헤더·각주·여러 첨부 관계의 담당자 수정을 반영한다.
  모든 파일을 별도 구역으로 표시하고 관계는 같은 HTML 안의 대상 링크로 제공한다.
- `draft`도 미리보기/다운로드할 수 있지만 **수정 중 · 확인 필요**를 표시한다.
  `confirmed`는 **그 검수 시점의 담당자 확인 완료**이며, 최신 상태·공개 승인으로 표시하지 않는다.
- HTML 단독 다운로드는 독립적인 UTF-8 문서다. 스크립트·원격 폰트·이미지·서버 로그인 링크가 없다.
  원본 파일은 포함하지 않는다. ZIP은 `index.html`, `manifest.json`, `originals/` 안의 원본들을 포함한다.
- 원래 `complete`, 경고, 파일별 처리 상태, 분석되지 않은 첨부를 숨기지 않는다.
  담당자 확인은 문서 완전성·개인정보·공개 허가·운영 승인 또는 학습 정답이 아니다.

이번 기능은 기존 비공개 검수 흐름의 출력 기능이다. 공개 페이지, OCR, 모델 호출,
재학습, 자동 수집, 운영 모델 교체 및 NAS 배포는 하지 않았다.

## 버전·보존 계약

새 DB 스키마나 마이그레이션은 없다. 단계 3의 학교 schema v2와 append-only 검수 이력을 그대로 읽는다.

| 대상 | 처리 |
|---|---|
| 수신 원본·SHA 저장소 | 읽기만 함. ZIP 생성 시 각 원본 SHA256 재검사 |
| 자동 구조 분석·기존 conversion HTML·기존 ZIP | 기존 경로 유지, 덮어쓰거나 재생성하지 않음 |
| 검수 이력 | 수신 ID + 변환 revision + 검수 event ID를 모두 지정 |
| HTML·ZIP GET | DB·객체·캐시·작업 큐·승인 상태에 쓰지 않고 메모리에서 생성 |
| 이전 HTML | 이후 수정·확인 취소·재변환·정정본 수신 후에도 같은 이력에서 조회 |
| 렌더러 | `moa-school-reviewed-html-v1`, HTML·manifest에 표시 |
| ZIP metadata | 고정 entry 시각/권한/순서. 동일 입력/렌더러에서 반복 다운로드 바이트 동일 |

누락된 event를 최신 검수로 대체하지 않는다. 불명확하거나 중복된 파라미터는 400,
다른 학교·다른 수신/변환에 속한 이력은 404, 로그인 없음/권한 회수는 401이다.
구조 원판정 해시 불일치, 원본 누락·변조·심볼릭 링크는 409로 중단한다.
실패 시 일부 원본만 담은 ZIP을 정상 다운로드로 반환하지 않는다.

HTML은 저장된 이력에서 매번 렌더링하며 별도의 영구 HTML 객체를 생성하지 않는다.
원판정과 검수 이력은 보존되며 이번 시험에서 **동일 렌더러의 이전 HTML·ZIP 바이트 보존**을 검증했다.
향후 렌더러 자체가 변경되는 경우까지 바이트 동일성을 약속하지 않는다. 장기 파일 보관은
다운로드한 HTML/ZIP과 그 manifest를 함께 보관한다.

## 렌더링·다운로드 안전

- 학교 membership을 요청마다 재확인한다. 같은 학교 reader는 조회/다운로드 가능하지만 이 요청은 쓰기를 하지 않는다.
- HTML에 사용자 원문 markup을 삽입하지 않고, 텍스트/파일명/검수자/근거를 escape한다.
  헤더·본문·제목·각주·푸터·표는 정해진 태그만 사용한다.
- standalone HTML의 CSP: `default-src 'none'`, inline CSS만 허용, base/form/object 차단.
  미리보기 iframe은 sandbox, 응답은 기존 인증·`no-store`·`nosniff` 보호를 유지한다.
- 표 텍스트가 수정되면 예전 cell 값으로 덮어 보이지 않도록 **수정된 평문 + 표 배치 미확정 안내**로 출력한다.
  표 셀 편집/재구성은 이번 범위가 아니다. 불명확·겹침·과대 셀 배치도 같은 안전 표시로 처리한다.
  표 머리 관계를 제거하면 기존 `header` flag가 남아 있어도 해당 셀을 머리 셀로 표시하지 않는다.
- 원본 위치/셀 참조를 보존하고, 포함 관계는 링크로 표시한다. 임의 중첩·의미 추정을 하지 않는다.
- ZIP 경로는 숫자 + 전체 SHA256 + 검증된 확장자로 만든다. 원래 파일명은 manifest에 보존한다.
  동일 이름, 경로 구분자, `#`, `?`, 운영체제 예약 이름을 archive 경로로 재사용하지 않는다.
- 원본 파일은 정규 파일·NOFOLLOW·개별 25MiB/총 50MiB·기존 파일 개수 제한을 검사한다.
  HTML은 UTF-8 조각을 누적 보관하기 **전에** 32MiB 상한을 검사한다.
  희소 표의 빈 셀 확장은 문서 전체 20,000칸 한도를 공유하며, 초과 표는 경고와 텍스트로 표시한다.
  원본 바이트는 수정하지 않으며 원본 자체의 실행 위험을 안내한다.
- manifest는 수신/변환/검수/렌더러 버전, 검수 시점 상태, 구조 snapshot SHA256,
  HTML SHA256, 원본별 경로·파일명·역할·크기·SHA256, 분석 완전성·경고를 담는다.
  공개 링크나 인증정보, 전체 수정 사유/감사 이력은 넣지 않는다.

## 외장 SSD·운영 보호

- 작업본: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`.
- 실행 자료: `/Volumes/KIOXIA-2TB/Cache/MOA/school-reviewed-html-20261022` (상위 0700).
- `dev-ssd check`: UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, 실제 `/Volumes/KIOXIA-2TB` 마운트 확인.
  시작 시 여유 약 1.4TiB, 작업본·캐시 쓰기 가능. `dev-storage`의 건강/카운터·검증 작업본 보호를 통과했다.
  과거 연결 불안정·전체 readiness 미해결 표시는 그대로다. 새 장치 오류가 생기면 중단한다.
- 기존 `.venv312` Python 3.12.13과 설치된 Chrome/Playwright를 재사용했다. 패키지 설치/모델 다운로드 없음.
- NAS `/volume2/moa/app`, `/volume2/moa/data`와 운영 DB·서비스·원본에 접속/쓰기/배포하지 않았다.
  운영 상태를 재검증했다는 의미가 아니다. MacBook 실행·외부 유료 모델 호출 없음.
- 기존 사용자 미추적 문서와 `.venv-py39-unused/`를 보존한다.

## 실제 검증

새 경로가 없는 상태의 실패를 확인한 뒤 구현했다. 합성 학교 A/B, 격리 SQLite,
실제 loopback HTTP 업로드/검수/다운로드를 사용한다.

- 신규 출력 테스트 **19개**: 반영/학교·회원 권한/버전 고정/GET 무변경/이전 버전 보존/경로·XSS/
  관계·표 수정/원본 손상 차단/희소 표 표시량/UTF-8 출력 조기 제한. 기존 검수 24개와 함께 **43 통과**.
- 전체 회귀 **1,356 통과 / 16 skip / 실패·오류 0**, 72.97초 (`full2.xml`).
  16 skip은 기존 선택적 Torch 및 MacBook tensor 검사(수집 단계 skip 포함)다.
  이번 기능 때문에 모델 의존성을 설치하거나 학습을 실행하지 않았다.
- HWP(기취득 비공개 fixture, 신규 취득 없음)·합성 HWPX/DOCX/텍스트 PDF 각각
  수정 → 확인 → 새 미리보기/HTML/ZIP **HTTP 200**.
  수정 문구 반영, ZIP 원본 SHA256, 기존 분석·자동 HTML·ZIP 내용 보존을 확인했다.
- `python -m compileall -q moa`, `sh -n scripts/install-nas.sh`(구문만), `git diff --check` 통과.
- 독립 읽기 전용 코드 리뷰에서 발견한 표 확장 메모리 문제를 수정하고 재검토 통과했다.
  실제 합성 DOCX 두 개의 희소 표 200개(원본 합계 166,090 bytes)에서
  렌더링 Python `tracemalloc` 피크는 수정 전 254,278,502 → 수정 후 969,267 bytes,
  출력 330,951 bytes, 렌더링 0.023초, GET DB 쓰기 0이었다.
  이 값은 해당 재현의 렌더링 측정이며 전체 프로세스 RSS/모든 입력의 최대 메모리가 아니다.
  처음 표만 격자로 표시하고 나머지는 텍스트·경고·셀 참조를 보존한다.
  두 회귀 테스트의 수정 전 실패(`resource-red.xml`)와 수정 후 통과(`resource-green.xml`)를 확인했다.

### 브라우저 QA

Browser plugin/skill이 세션에 없어 기존 Playwright 1.62.1 + 설치된 Chrome을 사용했다.
시험 서버는 `127.0.0.1` 임시 포트, 합성 계정/합성 문서만 사용했다.
프로필·디스크 캐시·다운로드·스크린샷도 모두 이 단계의 외장 실행 폴더에 저장했다.

흐름: 로그인 → 담당자 폼에서 비용 수정 → 확인 체크/사유 저장 → 고정 버전 미리보기 → HTML·ZIP 실제 다운로드 → 모바일 첨부 링크 이동.

| 검사 | 결과 |
|---|---|
| 페이지 URL/제목·빈 화면·오류 overlay | 통과 |
| 실제 수정 문구, 이전 금액 제거 | 통과 |
| 확인 완료와 비공개/시점 표시 | 통과 |
| HTML 다운로드 바이트 = 미리보기 | 통과 |
| 실제 ZIP 다운로드·이전 draft HTML 유지 | 통과 |
| 데스크톱 1280×900 / 모바일 390×844 | 가로 overflow 없음, 내용 표시 확인 |
| 모바일 첨부 이동·관계 anchor | 정상, 끊어진 anchor 0 |
| JavaScript/문서 앱 오류 | 없음 |

콘솔의 favicon 요청은 전용 endpoint가 없어 로그인 전 401/후 404가 발생한다.
실제 문서 경로 오류와 구분해 기록했다. 오류 0이라고 은폐하지 않았다.
증거: `BROWSER_RESULT.json`, `desktop-preview.png`, `mobile-reviewed-html.png`, `mobile-attachment.png`.
다른 브라우저·NAS 운영 화면·모든 모바일 기기 검증은 수행하지 않았다.

## 파일별 저장 위치와 Git 범위

아래 소스 상대 경로는 모두 위 외장 작업본 아래다.

| 파일 | 역할 |
|---|---|
| `moa/school_review_export.py` | 고정 snapshot, 안전 HTML, 원본 무결성 확인·ZIP |
| `moa/school_export_web.py` | 인증된 미리보기/HTML/ZIP 경로 |
| `moa/web_school.py` | 학교 서버 route 연결 |
| `moa/school_review_web.py` | 현재/이전 검수에서 고정 출력 링크 |
| `tests/test_school_review_export.py` | 출력·권한·보존·보안 HTTP 시험 |
| `tests/test_school_formats.py` | 네 형식 실제 수정본 다운로드 시험 |
| 이 문서, `README.md` | 비밀 없는 계약·결과 |

실행 자료는 `/Volumes/KIOXIA-2TB/Cache/MOA/school-reviewed-html-20261022`의
`pytest/`(격리 DB·백업·합성 원본), `formats/{hwp,hwpx,docx,pdf}/`(형식별 원본·수정 HTML·ZIP·결과),
`browser-data/`, `browser-profile/`, `browser-cache/`, `browser-downloads/`, `tmp/`, `cache/`,
JUnit XML·브라우저 스크립트·스크린샷·QA JSON에만 있다. 실행 자료와 원본은 Git에 넣지 않는다.

`codex/moa-autoqa-v1` / 기존 PR #1에 코드·테스트·비밀 없는 설명만 정상 commit/push한다.
force push, main merge, NAS 배포는 하지 않는다. 최종 커밋/CI 상태는 PR checks와 완료 보고에서 확인한다.
