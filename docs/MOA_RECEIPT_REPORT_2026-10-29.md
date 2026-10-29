# 다운로드 영수증 기간 검색·CSV — 22단계, 2026-10-29

## 담당자 흐름

저장한 확인 완료본 묶음 → 보관·복원 이력 → 다운로드 영수증에서 기록일 시작/끝을 검색한다.
한국 시간의 **영수증 기록일** 기준으로 양 끝 날짜를 포함한다. 목록 저장일과는 다르며,
페이지를 이동해도 기간 조건을 유지한다. 기간 초기화는 해당 묶음의 전체 영수증을 다시 보여준다.

**검색된 영수증 전체 CSV 다운로드**는 현재 페이지에 국한하지 않고 검색 결과 전체를
최근 회차부터 내보낸다. 조회 시점의 기존 기록만 읽으며 CSV 다운로드 자체는 새 영수증을 만들지 않는다.
영수증이 없으면 열 제목만 있는 CSV를 반환하고, 화면에서는 기록 없음과 기간 내 검색 결과 없음을 구별한다.

원본·수신/변환 버전·검수 이력·저장 목록/고정 항목·보관/복원 이력·영수증은 수정하지 않는다.
보관하거나 문서 버전이 바뀌어도 과거 영수증을 읽을 수 있다. 실제 문서 ZIP은 기존의 버전 변경 차단을 유지한다.
영수증은 검증된 ZIP을 내보내기 직전에 남긴 기록이며 수신 완료 증명은 아니다.

## HTTP·CSV·권한 계약

| 경로 | 허용 입력과 동작 |
|---|---|
| `GET /confirmed-export/saved/receipts` | 필수 `id`, 선택 `from`/`to`/`page`: 기간 검색·페이지 이동 |
| `GET /confirmed-export/saved/receipts.csv` | 필수 `id`, 선택 `from`/`to`: 해당 기간의 전체 영수증 CSV |

- CSV에는 묶음 ID·묶음 이름·다운로드 회차·기록 시각(한국 시간)·문서 수·ZIP 바이트·ZIP SHA-256을 담는다.
  소속/계정 식별자·원문·첨부·검수 내용은 담지 않는다. ZIP SHA는 원래 영수증 값을 그대로 사용한다.
- UTF-8 BOM, CRLF, CSV 표준 인용을 사용한다. 수식으로 해석될 수 있는 문자열은 CSV에만
  작은따옴표 접두사를 붙이며, 저장된 묶음 이름·ID 등 원래 값은 바꾸지 않는다.
- CSV는 최대 5,000건이다. 한도+1건까지만 읽어 한도 초과를 감지하고, 초과 시 400과 기간 축소 안내를 반환한다.
  일부 행만 성공한 파일로 내보내지 않는다. 파일명은 고정된 `confirmed-export-receipts.csv`다.
- 소유자·현재 소속을 읽기 트랜잭션 안에서 확인한다. 목록 메타데이터와 CSV 행은 같은 시점을 읽는다.
  화면의 count·페이지 행도 같은 읽기 시점을 사용한다. 조회 중 다른 연결이 새 영수증을 추가해도 섞이지 않는다.
- 타 학교·같은 학교의 다른 담당자·없는 목록은 404, 미로그인·소속 해제·학교 비활성은 HTTP 인증에서 차단한다.
  reader로 바뀐 소유자는 자기 영수증을 읽을 수 있다. POST로 CSV를 요청해도 내보내지 않는다.
- 조회 문자열 4,096자, 단일 필드·허용 필드만 허용한다. 날짜는 `YYYY-MM-DD` 정규형·실제 달력값·시작≤끝을 검사한다.
  CSV에는 `page`를 받지 않으므로 화면의 한 페이지만 전체 결과로 오인해 내보내지 않는다.
- `private, no-store`, attachment, UTF-8 CSV Content-Type, `nosniff`, 기존 CSP를 적용한다.
  화면 이름·입력값·링크는 escape하며 JavaScript 없이도 검색·내보내기가 동작한다.

## DB·저장 경계

기존 DB v9을 그대로 재사용한다. 새 테이블·마이그레이션·트리거 변경이 없다.
v9이 없으면 화면은 설치 안내를 유지하고 CSV는 409로 차단한다. 조회로 DB를 자동 변경하지 않는다.
CSV는 한 요청의 메모리에서 만들며 서버에 별도 파일을 적재하지 않는다.
문서 분석·변환·모델 호출·원본 재읽기·ZIP 재생성은 실행하지 않는다.

소스: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`

- `moa/school_confirmed_export_downloads.py`: 기간 검증·메타데이터 검색·제한된 CSV 내보내기.
- `moa/school_confirmed_export_downloads_web.py`: 기간 폼·CSV 링크·오류 안내.
- `moa/school_confirmed_export_saved_web.py`, `moa/school_confirmed_export_web.py`, `moa/web_school.py`: 기존 인증 경로 연결.
- `tests/test_school_confirmed_export_receipt_report.py`: 신규 기간/CSV·소유자·보존·동시성·HTTP 검증.
- `README.md`, 이 문서: 사용 흐름·경계·실행 결과. 21단계 문서의 기록 시점 표현도 실제 코드와 일치시켰다.

합성 DB·CSV·테스트 로그/JUnit·임시 파일·브라우저 프로필/캐시·스크린샷·실행 결과는 모두
`/Volumes/KIOXIA-2TB/Cache/MOA/school-receipt-report-20261029` 아래에 둔다.

`dev-ssd check`로 UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`와 실제 마운트를 확인했고,
`dev-storage` 건강/개별 작업본 보호 검사를 통과했다. source/cache 실경로·쓰기 권한·약 1,331GiB 여유 공간을 확인했다.
과거 물리 안정성 미해결과 전역 readiness 표시는 해제하지 않았다. 기존 가상환경·Playwright·Chrome을 재사용했다.

NAS `/volume2/moa/app`, `/volume2/moa/data`에 연결하거나 운영 DB·서비스·원본을 변경하지 않았다.
학습·유료/외부 모델·운영 모델 교체·배포를 하지 않았다. Git에는 코드·테스트·비밀 없는 문서만 반영한다.

## 실제 검증

- 신규 39개와 기존 영수증 57개: **96개 통과 / 실패 0, 42.81초** (`targeted.xml`).
  KST 자정/일말·날짜 양 끝·편측 기간·잘못된 날짜, 전체 페이지/빈 CSV/초과 한도,
  한국어·쉼표/따옴표·수식 접두사, 현재 권한·학교 격리, 재로그인/보관/오래된 고정 버전,
  동시 추가 중 읽기 스냅샷, v9 미설치 무변경을 실제 실행했다.
- 기존 Browser plugin/skill이 제공되지 않아 기존 Playwright/Chrome으로 loopback 합성 서버를 검사했다
  (`Browser plugin not available`). 데스크톱 1280×900, 모바일 390×844, JavaScript 비활성에서
  **15개 확인 항목 통과**: 페이지 식별·내용/오류 화면·콘솔, 기간 검색→2페이지→전체 22건 CSV,
  재로그인 후 동일 파일, 기간 초기화·빈 결과·잘못된 기간, 권한, 화면 넘침·스크린샷.
- 실제 내려받은 CSV들을 비교했고 재로그인·모바일·JavaScript 비활성의 동일 기간 결과가 바이트 단위로 같았다.
  별도의 CSV 파서로 열 수·회차·원래 ZIP SHA·문서 수·바이트를 확인했다.
- 브라우저 DB는 `query_only=ON`으로 실행했다. 모든 DB 행, 고정 목록/항목, 원본 hash/mtime을 보존했다.
  영수증 **24→24건**, quick_check=ok, FK 오류 0. 소유한 시험 서버·브라우저를 정상 종료했다.
- 전체 pytest **2,129개 통과 / 17개 skip / 실패 0, 538.22초** (`full.xml`). skip은 기존 선택적 의존성·맥북 전용 검사·비공개 HWP 자료 조건 17개다.
- `compileall moa tests`, `sh -n scripts/install-nas.sh`, `git diff --check` 통과. 변경 파일의 비밀 패턴 검출 0건.
- 기존 `codex/moa-autoqa-v1`과 [PR #1](https://github.com/h19h29-design/moa/pull/1)에 코드·테스트·설명서를 정상 commit/push한다. force push·main 병합은 하지 않는다.

격리 합성 자료의 개발 검증이며 NAS 운영 적용·실사용 학교 계정/자료 검증은 포함하지 않는다.
