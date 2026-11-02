# 이전 개인 입력을 새 임시 버전으로 가져오기 — 14단계, 2026-10-25

## 담당자 사용 흐름

13단계에서 조회·비교한 과거 개인 입력을 명시적으로 선택해 새 임시 버전으로 추가한다.
기존 입력을 덮어쓰지 않고, 가져온 원래 저장본으로 이동할 수 있는 출처를 남긴다.

1. 임시 입력 이력·비교·읽기 전용 화면에서 **이전 입력 가져오기 전 확인**을 연다.
2. 가져올 텍스트·구역 종류·개인 사유 또는 미완성 첨부 관계를 확인한다.
   최신 개인 입력과 비교하는 링크, 두 저장본의 순번·시각도 제공한다.
3. **정식 검수에 반영되지 않음** 확인란을 직접 선택하고 **새 임시 버전으로 가져오기**를 누른다.
4. 새 임시 입력에서 검수를 계속한다. **가져온 원래 입력 확인** 링크는 이전 저장본을 가리킨다.
   이후 수정·정식 검수 저장·HTML 생성은 기존 흐름을 따른다.

같은 학교·담당자·수신본·변환·항목에 속한 과거 입력만 허용한다.
입력 당시 구조와 정식 검수 기준이 현재와 같고, 현재 최신 개인 입력도 같은 기준이어야 한다.
기준이 바뀌었거나 다른 탭이 먼저 저장하면 중단한다. 현재 기준으로 자동 재해석·병합하지 않는다.
이미 최신인 입력은 기존 복구 기능을 사용한다.

미완성 관계의 빈 ID와 개인 사유·공백·개행을 그대로 보존한다.
이 기능은 개인 입력만 추가하며 관계 추가/제거·정식 확인·검수 HTML을 변경하지 않는다.
JavaScript가 꺼진 브라우저에서도 확인·가져오기 동작을 사용할 수 있다.

## DB·권한·충돌 계약

| 경로 | 계약 |
|---|---|
| `GET /structure-draft/restore?id=…` | 한 SQLite 읽기 시점에서 입력·기준·최신본을 확인하는 비변경 미리보기 |
| `POST /structure-draft/restore` | 인증·CSRF·Origin·명시적 확인 후 새 임시 버전 1개와 출처 1개를 함께 저장 |

- 명시적 학교 schema **v5**가 `school_review_draft_restores(draft_id, source_id)`를 추가한다.
  v4 개인 스냅샷은 그대로 재사용한다. 출처는 UPDATE/DELETE 금지 trigger로 보존한다.
  INSERT trigger는 학교·담당자·수신/변환/항목/동작·기준·순번·동일 payload를 확인한다.
- 기존 명시적 마이그레이션에서 먼저 SQLite backup을 생성하고 추가형 변경을 트랜잭션으로 적용한다.
  v4→v5와 신규 DB, 반복 실행, 알 수 없는 미래 버전 거부를 검증했다.
  core `PRAGMA user_version`은 바꾸지 않는다. 서버 시작·GET·저장 요청은 마이그레이션을 수행하지 않는다.
  v5 미적용 DB에서는 안내만 표시하고 가져오기 저장을 차단한다.
- `BEGIN IMMEDIATE` 아래에서 소속·쓰기 권한·원래 저장본·최신 입력 ID·현재 구조와 검수 기준을 재검사한다.
  다른 저장과 경합하거나 동일 POST를 재전송하면 409이며 추가 입력을 만들지 않는다.
  새 입력 INSERT 뒤 출처 INSERT가 실패해도 둘 다 rollback한다.
- 요청에는 입력 본문을 받지 않는다. 서버의 불변 원본 payload만 검증 후 복사한다.
  허용된 정확한 필드와 확인값만 받으며 중복/추가 필드·잘못된 UTF-8을 거부한다.
  GET query와 POST body는 각각 4,096자/바이트로 제한한다.
- 학교/담당자 소유권 확인 후에만 출처를 조회한다. 같은 학교 manager도 타인의 입력을 가져올 수 없다.
  reader는 미리보기만 가능하다. 로그인·소속 활성 여부·CSRF·Origin·no-store·CSP·HTML escape를 유지한다.

## 변경 파일과 저장 위치

소스·테스트·설명서는 기존 작업본
`/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`에 있다.

- `moa/school_draft_restore.py`: 명시적 가져오기, 충돌 검사, v5 출처 테이블·불변성.
- `moa/school_draft_restore_web.py`: 미리보기·확인·출처 표시.
- `moa/school_service.py`: 명시적 v5 추가형 마이그레이션.
- `moa/school_review_drafts.py`: 소유권 확인 후 출처 조회.
- `moa/web_school.py`, `moa/school_draft_web.py`, `moa/school_draft_compare_web.py`,
  `moa/school_draft_history_web.py`, `moa/school_review_web.py`: 인증 라우트·기존 화면 연결.
- `tests/test_school_draft_restore.py`: 새 계약·권한·동시성·보존 검사.
- `tests/test_school_formats.py`: 실제 형식 흐름에서 가져오기와 원본/기존 HTML 보존 검사.
- 기존 draft/autosave/compare/history 테스트 4개: v3 격리 fixture에서 v5 테이블도 제거하도록 수정.
- `README.md`, 이 문서: 사용법과 검증 결과.

SQLite 시험 DB·백업·임시파일·캐시·로그·형식 결과·브라우저 프로필/스크린샷은 모두
`/Volumes/KIOXIA-2TB/Cache/MOA/school-draft-restore-20261025` 아래에 있다.
기존 `.venv312`, Node, Playwright, Chrome을 재사용했고 패키지·모델을 설치하거나 다운로드하지 않았다.

## 실제 검증

- 새 기능 테스트 **42개**: 원본/이력 보존, 부분 관계, 출처 불변성·범위, 동시 저장·재전송,
  미리보기 중 자동저장, 기준/권한 변경, rollback, 명시적 마이그레이션·백업, CSRF/Origin/입력 제한.
- 기존 관련 회귀 **145개 통과**. Node 자동저장 **7개 통과**.
  두 JS 구문·Python compileall·`git diff --check`와 NAS 설치 스크립트 `sh -n` 검사 통과.
  설치 스크립트는 실행하지 않았다.
- 전체 pytest **1,687개 통과 / 16개 skip / 실패 0, 260.74초** (`full.xml`).
  신규 42개가 전체 실행에도 포함됐다. 선택적 Torch/맥북 검사는 기존 조건에 따라 생략했으며
  모델·패키지를 설치하지 않았다.
- 기취득 비공개 HWP와 합성 HWPX·텍스트 PDF·DOCX의 실제 parser/HTTP 흐름 **4개 모두 통과**.
  업로드·역할·HTML·원본 ZIP·검수 이력에 이어 이전 입력 가져오기를 시험했다.
  새 입력/출처 외 DB 행과 원본/추출 hash·mtime, 이전 원본/검수 HTML이 동일함을 확인했다.
  비공개 원문의 내용·파일명·식별 hash는 보고서/Git에 넣지 않는다.
- 별도 구현 검토에서 스냅샷·잠금·소유권·마이그레이션·출처·부분 관계를 확인했다.

Browser 플러그인이 없어 기존 Playwright 1.62.1·설치된 Chrome을 사용했다.
외장 SSD의 합성 학교 A/B와 loopback 서버에서 desktop 1280×900, mobile 390×844,
JavaScript-off를 검증했다. 네트워크는 해당 시험 origin으로만 제한했다.

- `browser-final/BROWSER_RESULT.json`: **16개 검사 모두 통과**.
  페이지 식별·빈 화면/오류 overlay 없음·콘솔 건강, 이력→가져오기 확인→새 임시 입력→출처,
  확인란 필수·재전송 차단·다른 탭 자동저장 경합·검수 기준 변경 차단·재로그인·학교/담당자 분리,
  JavaScript-off 미완성 관계 가져오기·모바일 가로 넘침 없음을 확인했다.
- desktop-preview, mobile-preview, desktop-blocked 스크린샷을 직접 확인했다.
  의도한 404/409와 favicon 응답 외 관련 콘솔/런타임 오류는 없었다.
- 초기 UI 시험의 동적 ID 선택자가 자동저장 후 바뀌어 대기 실패했다.
  시험 선택자를 수정하고 새 격리 DB에서 전체 흐름을 통과했다. 실패 기록/DB도 보존했다.
- 시험 서버 정상 종료 후 `browser-final/PRESERVATION.json` 확인:
  기대한 **개인 입력 3개 / 가져오기 출처 2개**만 추가됐다(가져오기 2회, 경합용 자동저장 1회).
  기존 개인 입력·나머지 DB 전체 행·원본/추출 hash와 mtime이 동일하고 quick_check=ok, FK 오류 0이다.
  이 작업이 만든 시험 서버와 브라우저는 종료했다.

실제 학교 사용자의 승인 시험·다른 브라우저·운영 NAS 검증 또는 배포를 의미하지 않는다.

## 저장 장치·운영 보호·Git

- UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, 실제 `/Volumes/KIOXIA-2TB` 마운트,
  검증 작업본·캐시 경로·쓰기·여유 약 1.3TiB를 확인했다.
  `dev-storage`의 검증 작업본 보호를 통과했다.
  `development_ready=false`, `physical_stability_resolved=false`는 그대로이며 물리 연결 위험은 해소됐다고 주장하지 않는다.
- NAS에 접속하지 않았으며 `/volume2/moa/app`, `/volume2/moa/data`의 운영 DB·서비스·원본을 변경하지 않았다.
  v5는 외장 SSD 격리 시험 DB에서만 적용했다.
- 원본·기존 검수/개인 입력 이력·이전 HTML/ZIP은 보존한다.
  학습·모델 추론·유료/외부 모델·운영 모델 교체·배포·자동작업 변경은 없다.
- 코드·테스트·비밀 없는 설명서만 기존 `codex/moa-autoqa-v1`과
  [PR #1](https://github.com/h19h29-design/moa/pull/1)에 정상 commit/push한다.
  무관한 미추적 가상환경/보고서·원본·DB·모델·캐시·비공개 실행 기록은 포함하지 않는다.
  force push·main 병합은 하지 않는다.
