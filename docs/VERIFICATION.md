# 검증 기록 — 2026-09-23 (3단계)

# 추가 기록 — 2026-09-30 (기존 수집 자료 활용 분류)

- 기준 원격/로컬 `5865c4c`, NAS 전체 코드 SHA256 일치를 확인하고 기존 변경을 보존.
- SWE-2(`devin/swe-2`)에 공개 범위의 합성 명세·테스트만 전달해 규칙/테스트 초안을 생성.
  실자료·운영 DB·인증정보는 개발 모델에 전달하지 않음. 부모 에이전트가 안전 조건을 수정·통합·검증.
- additive 스키마 v4: 파일별 자동/사용자 분류, 원문/파서 fingerprint, 변경 이력.
  활용 후보/원본 첨부/보류/제외/평가를 사람 승인 및 train/dev/eval 분할과 구분.
- 기존 사람이 수정·승인한 짧은 표의 검색/내보내기를 보존하되 누락·이전 파서·개인정보 차단 유지.
- 제외/역할 변경 후 추천/내보내기 갱신, 과거 승인 이력 보존, 새 역할은 새 후보 버전으로 변환.
  동일 물리 파일의 다른 게시글/업로드를 통한 평가자료 재유입 차단. 미분류도 활용 전 보류.
- 최신 화면 fingerprint·세션 인증·CSRF/Origin·HTML escaping·실제 원본 존재 검사.
  개인정보 후보 전환은 사용자의 원문 대조와 확인 결과 메모가 필요하며 콘텐츠 승인은 아님.
- 원자적 단건 작업 claim, bounded 배치 및 재시작 이어하기. 수집 잠금/HTTP 예산 사용 안 함.
- `pytest -q` **165 passed**; compileall, JS 구문 검사, 설치 셸 검사, diff/manifest 검사 통과.
- 로컬 합성 자료에서 분류 수정/보류 이력/후보 전환/미승인 표시/실제 첨부 다운로드 검증.
  데스크톱 1360×900·모바일 393×852 모두 가로 넘침 없음, 앱 콘솔 오류 0.
- NAS 사전 온라인 DB 백업 quick_check=ok, 원본/문서 hardlink 스냅샷 및 앱/환경설정 별도 아카이브.
  실제 /volume2, 환경설정 바이트/600 권한 보존. 일일 04:00 50/50/20 및 백필 active 유지.
- 실제 HTTPS `/corpus`: 기존자료 목록/보류 필터/파일 역할·사유·이력/원본 다운로드 확인.
  익명 접근 401, 앱 콘솔 오류 0. CF 분석 beacon 차단은 기존 CSP를 유지하고 별도 기록.
- 기존 통신문 **3,702건 전부 1차 분류**, 후보 607/보류 2,580/평가 515.
  파일 기준 후보 1,505/첨부 55/보류 3,987/평가 1,287. 분류 때문에 수집 실적이 증가하지 않음.
- 표 사례 14,289·사람 승인 0·운영 수동 분류 0 보존. jobs.curate done=3,702, 오류 0.
- 멘토링 HWP의 17개 카드·신청 일시/조건·원본 SHA/HTML·ZIP 다운로드 재검증.
  미제공 뒷페이지 이미지는 needs_vision/incomplete 유지. 실자료는 대신 승인하지 않음.
- 자료 분류는 로컬 규칙/기존 추출만 사용하며 운영 외부 AI 호출/파인튜닝 없음.
  요청한 SWE-2 개발 호출은 별도 기존 계정 정책을 따르며 공급자 과금액은 확인하지 못함.

## 이번 범위의 남은 항목

전수 자동 분류의 사람 정답률은 미검증. 보류 사유(중복 가능): 이전 파서 1,678문서,
내용 부족 1,129, 부분 수집 1,171, 미분석/실패 562, 비전 필요 499, 혼합 경계 349,
개인정보 대조 32, 파서 필요 2. 각 파일을 재변환/원문 대조한 뒤 사람이 분류·콘텐츠를 확인해야 한다.
자동 분류 완료를 모델 훈련이나 실자료 학습 완료라고 보고하지 않는다.

---

# 추가 기록 — 2026-09-30 (업로드 중심 모바일 안내문 변환)

- 기준 원격·로컬·NAS `4b17f98` 일치 및 기존 테스트 71개 통과를 먼저 확인.
- HWP 외곽 표가 중첩 학과 목록을 누락하던 결함 수정. 실제 HWP에서 전체 본문 순서,
  1~17번 학과/교실 연결, 신청 일정·선착순·미신청 조건·문의처를 자동 파싱 후 대조.
- 원문과 역할·추출·후보 HTML·사람 승인 버전 분리. 새 파일/역할/내용/파서 결과는 재검수.
- 업로드→역할 확인→영속 변환 jobs→원문 비교→수정→검수→HTML/실제 첨부 ZIP 제공.
- 이미지·스캔·미지원은 needs_vision/needs_parser로 유지. 뒷페이지 미제공 이미지는 incomplete.
- 승인 구조·표현만 참고. 합성 사례에서 수정 승인 레이아웃 재사용, 새 날짜/금액/학교/링크 유지,
  평가자료 제외, 승인취소 후 검색·내보내기 제외, 파서 갱신 후 새 후보 생성 검증.
- 모든 원문·후보·다운로드 인증, Secure/HttpOnly/SameSite 쿠키, CSRF/Origin·용량/파일 시그니처 검사.
- NAS 온라인 SQLite 백업 quick_check=ok. 기존 원문·환경설정 백업. 스키마 v3 이후 기존 notices/cases 보존.
- 브라우저 로컬 및 실제 HTTPS 도메인, 데스크톱 1360×900·모바일 393×852에서 실물 HWP 흐름 확인.
- 실제 원본 HWP의 다운로드/ZIP 바이트 일치. HTML은 선택 가능한 텍스트이며 학과 카드 17개 표시.
- 익명 원본/후보/첨부/HTML/ZIP 접근은 401. 2MB 잘못된 PDF 입력은 앱 400으로 거부, 기록 생성 없음.
- 콘솔 앱 오류 0. Cloudflare 삽입 분석 beacon은 기존 CSP에 의해 차단되는 메시지로 별도 확인.
- 실자료 승인 0. 브라우저 승인/보류/취소 루프는 폐기 가능한 로컬 합성 자료에서만 실행.
- 외부 AI/파인튜닝/유료 모델 호출 없음. 기존 04:00 50/50/20·백필 active·NAS 볼륨2 유지.

## 검증 명령

`python -m pytest -q`, `python -m compileall -q moa`, `node --check moa/mobile_ui.js`,
`sh -n scripts/install-nas.sh`, `git diff --check`.

기존 71 + 모바일 회귀 19 = **90 tests**. 원문·DB·키·브라우저 시험용 파일은 Git에 넣지 않았다.

## 남은 범위

이미지 OCR·스캔 PDF·암호화 HWP, 복잡한 도형·수식·페이지 배치, 광범위한 다른 실물 양식은 미검증/미지원.
운영 승인 사례가 아직 없으므로 실자료 기반 추천 성과는 주장하지 않는다.
최종 MD 첨부 파일 자체는 제공 경로에서 발견되지 않아 사용자 메시지의 최종 요구사항으로 구현했다.

---

---

# 추가 기록 — 2026-09-29 #2 (HWP·DOCX 파서 + 사이트 이전 robots 추적)

## 발견·수정한 결함

1. **HWP5 이진 문서와 DOCX가 미분석(needs_parser)으로만 남았다** — partial 1,578건 중
   hwp 314·image 204·pdf 51·hwpx 11개 자산(표본 400건 기준). 순수 Python HWP5
   레코드 파서를 구현해 OLE BodyText 스트림에서 표 셀(주소·병합·본문 텍스트)을 읽는다.
   중첩 표는 nested 플래그로 표시하고 셀 텍스트에 섞지 않는다. DOCX는 표준 XML에서
   gridSpan/vMerge를 해석한다. image는 여전히 needs_vision(로컬 OCR 없음),
   암호화 HWP는 needs_parser(note=encrypted)로 남긴다. PARSER_VERSION=v3.
2. **robots.txt 조회가 학교의 호스팅 이전 리다이렉트에서 차단됐다** — 광주 283교가
   gen.*.kr → jge.*.kr 같은 공식 이전을 공지하는데, robots 조회가 다른 호스트로
   넘어가면 '승인되지 않은 외부 호스트'로 실패해 학교 전체가 캐시 차단됐다.
   robots 조회만 이전 호스트를 최대 2개까지 따라가며, 대상 호스트는 이후 페이지
   fetch가 같은 이전을 따르도록 학교 호스트 집합에 합류한다.
3. 잔여: 전북 일부 서버의 구형 TLS 협상 거부(SSLV3_ALERT_HANDSHAKE_FAILURE)와
   sen 플랫폼 robots는 정책상 우회하지 않는다.

## 배포·검증

- pytest 71개 통과 (HWP 셀 파싱·robots 이전 추적·CA 병합 테스트 추가)
- 실물 HWP(울산고운고 평가계획)에서 4x3 병합 표 1개와 한글 셀 텍스트 추출 확인
- jg.gen.ms.kr → jg.jge.ms.kr 이전 추적 후 HTTP 200 확인
- school_state의 광주 '외부 호스트' 283행 next_retry 해제
- 재분석: partial/pending 1,579건을 analyse 큐에 재등록

---

# 추가 기록 — 2026-09-29 (학교 서버 TLS 체인 결함 대응)

## 발견·수정한 결함

1. **세종 109교 전체가 CERTIFICATE_VERIFY_FAILED로 차단** — sjedu 도메인 서버가
   리프 인증서에 엉뚱한 체인(DigiCert 중간)을 붙여 보내고 정작 발급 중간 인증서인
   'Sectigo Public Server Authentication CA DV R36'을 보내지 않아 정상 검증이
   불가능했다. 브라우저는 AIA/캐시로 중간 인증서를 보완하지만 정적 번들만 쓰는
   수집기는 실패했다. 전수 조사에서 실패 122개 학교의 리프 발급자는 3개뿐이었다:
   Sectigo DV R36(109), Sectigo RSA OV(12), Let's Encrypt YE2(1).
2. **MOA_EXTRA_CA로 공인 중간/루트 인증서를 번들에 병합** — 각 리프의 AIA
   caIssuers에서 받은 공인 인증서만 사용하고, openssl verify로 신뢰 루트까지의
   서명을 확인한 뒤 certifi 루트와 합친다. 체인·호스트명 검증은 그대로 유지되며
   우회가 아니다. ISRG Root YE는 아직 certifi에 없는 공인 LE 루트라 함께 넣었다.
   배치: /volume2/moa/data/certs/extra-issuers.pem (4개 인증서).
3. 잔여 한계: SSLV3_ALERT_HANDSHAKE_FAILURE(전북 일부)는 서버의 구형 TLS
   협상 거부라 번들로 해결되지 않고, 외부 호스트 리다이렉트 차단은 별도 정책
   사안이라 그대로 둔다.

## 배포·검증

- pytest 69개 통과 (ca_bundle 병합 테스트 추가)
- NAS 재배포 후 컨테이너에서 https://gowoon.sjeduhs.kr/ 실측 → HTTP 200
- school_state 'tls' 122행 next_retry 해제로 당일 재시도 허용

---

# 추가 기록 — 2026-09-27 (수집 공백 원인 수정)

## 발견·수정한 결함

1. **백필이 새벽에 당일 요청 예산을 소진** — 요청 예산(MAX_HTTP_REQUESTS)은 날짜 기준으로
   초기화되는데 백필 배치 루프가 00:00~04:00 사이에도 계속 돌아 5,000회를 다 썼다.
   04:00 증분 실행은 남은 예산 0으로 시작해 9/25·9/26 이틀간 신규 0건(전 지역
   "일일 HTTP 요청 상한")이었다. 스케줄러에서 **당일 증분 완료(attempted) 전에는
   백필 배치를 실행하지 않도록** 가드를 추가했다.
2. **예산 소진 오류가 학교 차단 캐시에 1일 TTL로 저장** — '일일 HTTP 요청 상한' 예외가
   _block_kind의 'error'(1일)로 기록돼 9,038개 학교가 다음 날에도 차단된 채였다.
   예산 소진은 학교 고유 실패가 아니므로 'budget' 종류·재시도 즉시로 바꾸고,
   지역 루프에서 예산 소진 시 나머지 학교를 건너뛰게 했다. 오염된 9,038행은
   DB 백업 후 next_retry를 해제했다.
3. **백필 커서 미저장 KeyError** — 캠페인 행이 없는 학교는 cs={}로 시작하는데
   신규 수집 0건이면 cs['collected'] 자체가 없어 KeyError → 체크포인트가 저장되지 않아
   같은 게시판을 매 배치 재스캔했다. setdefault로 보완.

## 배포·검증

- pytest 68개 통과
- NAS 재배포 후 수동 증분 실행으로 실제 수집 재개 확인
- DB 백업: /volume2/moa/data/db/backups/moa-20260927-pre-budgetfix.sqlite3

---

# 이전 기록 — 2026-09-23 (3단계 당시)

## 실행 결과
- `python -m pytest -q`: 68 passed (2단계 62 + 3단계 6)
- 대상: Synology DS925-Home, `app-collector-1` healthy, `app-review-1` up

## 3단계로 검증된 항목
- 검수 웹 화면(`python -m moa serve`, compose `review` 서비스):
  NAS `127.0.0.1:8321` 바인드만(공인 포트 없음). `GET /` 우선검수 큐 200,
  `GET /case?id=` 원문+첨부 링크+추출 표+수정 textarea+모바일 미리보기 iframe+이력 200,
  `GET /preview?id=` 모바일 HTML 200, `GET /obj/<sha>` 첨부 다운로드 200,
  잘못된 sha 400, 경로탐색 404, 토큰 없는 `POST /review` 403.
- 모바일 HTML 렌더러(`moa/render.py`): key_value_cards/grade_cards/timeline/scroll_table,
  rowspan·colspan 보존, 셀 텍스트 verbatim, 미지원 구간은 `<details>` 원문 표 fallback.
  실데이터 확인: 부산 버스킹 신청서 표가 병합 구조 그대로 렌더됨.
- 정합성: 파서 버전 변경 시 candidate 사례만 payload/pattern/family 갱신,
  approved/rejected/held는 불변. 사람 수정본(`correction`)이 화면·검색·추천·export에서
  일관 적용(`effective_table()`), export는 correction을 `table`로 승격하고 원 추출을
  `extracted_table`로 보존. 승인취소(candidate) 시 캐시·집계에 반영.
- family 과분할 완화: 정규화에서 학교명·날짜·금액 제거 → 같은 양식이 학교/날짜별로
  쪼개지지 않음(테스트: 학교명만 다른 두 사례가 같은 family).
- 작업 done과 추출 성공 구분: jobs.done=354 중 analysis.extracted=185,
  partial=169, pending=4로 별도 집계.

## NAS 실측 (2026-09-23, 3단계 배포 후)
- 문서 358(당일 증분 55, 백필 신규 누적 193), 표 사례 341, family 296, 승인 0(전부 candidate)
- 검수 화면 실접속 확인: `ssh -L 8321:localhost:8321 ds925-home` → http://localhost:8321
- compose `review` 서비스 command 수정: ENTRYPOINT가 `python -m moa`이므로
  command는 `serve ...`만(처음 배포에서 `python -m moa` 중복으로 재시작 루프 → 수정)
- 외부 AI 호출 0건, 비용 0

## 아직 미검증
- 사람이 화면에서 실제 승인·수정한 뒤의 이력/캐시 반영(실자료 승인은 사용자 몫)
- 모바일 렌더의 브라우저 시각 확인(HTML 구조·보존은 검증, 픽셀 단위는 미확인)
- 400건/일 목표 달성 여부, 10,000건 백필 완주

---

# 이전 기록 — 2026-09-23 (2단계)

## 실행 결과
- `python -m pytest -q`: 62 passed (1단계 48 + 2단계 14)
- 대상: Synology DS925-Home, 컨테이너 `app-collector-1` healthy

## 2단계로 검증된 항목
- 스키마 v0→v2 자동 마이그레이션 + 사전 온라인 백업(`db/backups/`, `db/moa-pre-v2-manual.sqlite3`)
- 서울/경기/기타 3단 목표(50/50/20, 합계 400), V10 제외, 경기 전용 목표 독립
- 백필 캠페인 생성·학교당 상한(캠페인 10/일 2)·페이지 커서 체크포인트·중단 후 이어하기
- 백필/증분 실적 분리(`campaign_id`), SHA-256 객체 중복 방지 공유
- 목록 페이지네이션(pageIndex/page/goPage 류), 반복 페이지·전면 구간 종료
- 분석 영속 큐(jobs): enqueue→claim→done/error, stale 재큐, 파서 버전별 재분석
- table_state(table_present/no_table/unknown), 미분석 HWP/이미지를 no_table로 오기하지 않음
- 양식 family(헤더 의미·역할·병합·단위), 같은 크기 다른 의미 표는 다른 family
- 우선검수 큐(신규 양식→사례 부족→위험→부족 지역), review 승인/반려/보류/승인취소·이력
- 평가 그룹 분리: eval split은 검색·추천·approved.jsonl에서 제외, eval.jsonl 별도
- 일일 요청 예산을 usage 테이블로 프로세스 간 공유
- 학교 실패 원인 캐시(robots 7일·로그인 30일·dns/tls 1일·게시판 없음 7일)

## NAS 실측 (2026-09-23)
- 배포 후 자동 마이그레이션 확인, 기존 165건·승인 기록 보존
- 백필 캠페인 #1 생성(창: 2024-09-23~2026-09-23), 스케줄러 자동 배치로 실수집 확인
- 실수집 예: 부산/대구 학교 게시판에서 게시일·첨부·campaign_id 기록, partial_capture 구분
- 분석 큐 168건 처리 완료, 우선검수 큐 실데이터 생성 확인
- 외부 AI 호출 0건(기능 없음), 비용 0

## 아직 미검증
- 400건/일 목표의 실제 달성 여부(robots 차단 지역은 물리적으로 불가할 수 있음)
- 10,000건 도달 — 후보 소진 시 coverage_exhausted로 멈춤
- HWP(구형)·스캔 PDF·이미지의 표 추출(needs_parser/needs_vision으로만 분류)
- 사람 검수 UI 없음 — CLI `review`/`review-queue`만 제공

---

# 검증 기록 — 2026-09-21

## 실행 결과
- `python -m pytest -q`: 30 passed
- `python -m compileall -q moa`: 통과
- `sh -n scripts/install-nas.sh`: 통과
- Compose YAML 구조 검사: /volume2 기본 바인드, 자동 경로 생성 금지, 공개 포트 없음 확인
- 실행 패키지 버전: beautifulsoup4 4.14.3, certifi 2026.5.20, defusedxml 0.7.1,
  pdfplumber 0.11.9, urllib3 2.7.0, pytest 9.0.2

## 테스트 범위
한국시간·할당량, 같은 파일의 이름 변경 중복, 날짜/첨부 수정본 보존, 출처 별칭,
프로세스 잠금, HTML rowspan/colspan, HWPX XML 셀 좌표, XML 외부 엔티티 차단,
파일 매직 검사, URL 인증정보/내부망/특수포트 차단, DNS 결과 검사,
robots 차단/오류 fail-closed, 리다이렉트 허용 호스트 재검사,
무작위 재현성, 가정통신문 메뉴·CMS 상세 링크 추출, 로그인 페이지 제외,
NEIS 인증키 누락/반복 샘플 거부, 후보와 승인 사례 분리,
합성 PDF 실파서 표 추출, 이미지 미분석 상태, 서울10건 모의 end-to-end,
당일 재실행0건/추가 HTTP0회, 기타 지역5건 목표·첨부 실패에 따른 partial,
학교별 하루 상한의 재시작 후 유지, 단일 교육청 점검과 전국 스케줄 분리,
추출 실패 캐시 재시도, 표 개수 초과시 실패(조용한 잘림 방지), JSONL 원자적 스트리밍.

## 하지 못한 검증
실행 컨테이너는 외부 DNS/HTTP 연결이 불가능하고 Docker 실행기가 없다.
따라서 실제 학교 문서 다운로드, NEIS 사용자 키 인증, Docker build/up,
Synology NAS 실제 설치·04:00 실행 여부는 검증하지 않았다.
학교 공식 페이지와 API 설명은 웹 도구로 확인했지만 이를 수집기 실동작 성공으로 간주하지 않는다.
전국 모든 CMS 자동 지원, 일일 목표 100% 충족, 표 의미 정확도/완전 자동 학습을 주장하지 않는다.

## 운영 첫 확인
NAS 설치 후 `doctor`, `run --office B10`, `status`와 `reports/latest.json`을 확인한다.
선택자 미지원 오류는 override나 CMS 어댑터를 보완한다. 차단/권한 페이지는 우회하지 않는다.

---

# 2026-09-21 NAS 실배포 검증

## 설치 환경 (실측)

- 대상: Synology DS925-Home, DSM Container Manager, Docker 24.0.2, Compose v2.20.1
- 소스: `/volume2/moa/app` (체크섬 일치 확인), 데이터: `/volume2/moa/data`
- `/volume2` 실제 마운트 확인(8.8T 중 7.3T 여유). 기존 컨테이너·공유폴더·데이터는 변경하지 않았다.
- 컨테이너: 비root(1026:100), `read_only: true`, 공개 포트 없음, `restart: unless-stopped`, TZ Asia/Seoul
- NEIS 학교목록 동기화: **12,434개 학교 홈페이지** (18개 교육청, 전체 12,669 레코드)
- `python -m pytest -q`: **48개 테스트 통과** (Python 3.12)

## 고친 결함 (전국 실행에서 발견)

1. NEIS 목록 종료 조건: 전체 12,669건 중 고유 코드가 12,564개(학교코드 공백 행 중복)라
   "고유 키=전체 건수" 조건이 성립하지 않아 14페이지에서 `INFO-200`으로 실패했다.
   헤더의 건수 기준으로 끝내고, 17개 교육청 미만이면 부분 응답으로 보고 기존 목록을 유지한다.
2. 홈페이지/게시판이 `location.href`·meta refresh 껍데기 페이지로 실제 주소를 알려주는 경우를
   따라가지 못했다(부산·제주·대전·강원·인천·대구 등 다수). 껍데기 추적을 추가했다.
3. 교차 호스트 이전(예: `iriseo.es.kr → school.jbedu.kr`)이 차단됐다. 학교가 스스로 알린
   이전에 한해 최대 2개 호스트까지 허용한다.
4. `boardCnts` CMS(`javascript:goView('boardID','boardSeq')`)와 Jinhak JS 게시글 링크를 읽지 못했다.
5. 교육청 공통 공지의 첨부가 교육청 포털(`www.<office>.go.kr`)에 있어 차단됐다.
   게시글이 스스로 링크한 첨부 호스트를 학교별 3개까지 허용한다.
6. 첨부 하나가 거부되면 통신문 전체를 버렸다. 본문이 있으면 저장하고
   `attachments_failed`/`attachments_skipped_robots`로 사실을 남기도록 바꿨다.
7. 일시 오류로 끝난 날은 스케줄러가 하루를 포기했다. 오류는 `RETRY_MINUTES`(기본 60분) 간격으로
   다시 시도하고, 완료/부분완료 날짜는 다시 실행하지 않는다.
8. 봇 User-Agent를 그대로 쓰는 요청을 여러 교육청 방화벽이 연결 단계에서 끊었다.
   표준 형식(`Mozilla/5.0 (compatible; MoaNoticeBot/0.1; +URL)`)으로 봇 신원을 유지한 채 바꿨다.
9. 사용자 요청의 17개 시도교육청 목표(10+16×5=90)와 달리 재외교육청(V10)까지 5건씩 잡혀 95건이었다.
   `EXCLUDE_OFFICES=V10` 기본값으로 90건을 맞추고, 필요하면 풀 수 있게 했다.
10. 경기 `goe*.kr`은 게시글 본문을 JS(DEXT5 업로더)로 그리지만 첨부의 실제 경로·원본 파일명은
    HTML 안 JS 호출에 있었다. 제목(`.bbs_ViewA h3`)과 첨부를 정적으로 읽도록 보강했다.
11. 대구 `dge.*.kr`은 리다이렉트 URL의 `;jsessionid=...` 경로 파라미터 때문에 방화벽이
    차단했다. 공개 GET에 세션 ID가 필요 없으므로 경로 파라미터를 제거한다.
12. 한 서버를 여러 학교가 공유하는 플랫폼(예: 대전)에서 호스트별 간격만 두다 보니 같은 서버에
    연속 요청이 몰려 연결이 끊겼다. 요청 간격을 **서버 IP 기준**으로 계산하도록 바꿨다.
13. 레거시 http 주소의 `/robots.txt` 요청을 서버가 리셋하는 경우(대전) robots를 읽지 못해
    fail-closed로 실패했다. http 실패 시 https robots로 다시 확인하고, 그마저 실패하면 계속 차단한다.
14. 서버가 첫 연결을 끊는 경우가 흔해(리셋/타임아웃) 같은 요청을 2회까지 재시도한다.

## 실제 수집 결과

- 서울 표본 실측: 비플랫폼 서울 학교 85개 중 표본 24개 → 성공 3개(`명덕외국어고등학교`,
  `오디세이학교`, `명지초등학교`), robots 차단 7, 게시판 미탐색 9, DNS 소멸 3, 교차 호스트 차단 2.
  서울 1,410교 중 1,325교가 `*.sen.*.kr` 플랫폼의 `Disallow: /` robots를 쓴다.
- 전국 일일 실행(지역별 최대 50교) 결과: **17개 지역 중 11개 지역이 목표 달성(각 5건)**.
  경남·충북·인천·부산·강원·울산·전북·경기·제주·전남·대구가 5/5, 서울·경북·충남·대전은 robots,
  세종은 접속 불가, 광주는 게시판 로그인 요구로 0건이다. 목표를 못 채운 양은 `shortfall`로 남는다.
- 하루 고유 통신문 **55건**, 원본 객체 95개(원문 페이지 55 + 첨부 40), 표 사례 30건(전부 미검수).
- 수집 데이터는 원문 URL·학교·본문·첨부(해시·바이트·형식)·수집시각을 함께 저장한다.
  실측 예: 부산솔빛학교 인플루엔자 안내(PDF 246KB), 울산고운고 평가계획(HWP 135KB, 본문 9,102자),
  강릉중부설방송통신중 안전 안내(PNG 115KB), 마령초 가족캠프 안내(PDF 2.7MB).
- 표/양식 사례: `learning/candidates.jsonl`(미검수), `learning/patterns.jsonl`,
  `learning/approved.jsonl`(사람 승인 전까지 비어 있음) — 자동 승격 없음을 확인.
- 재실행 검증: 이미 목표를 채운 부산에 같은 명령을 다시 실행 → 신규 0건, HTTP 요청 0회,
  `before=5, total_today=5`로 그대로 유지됐다. 같은 파일은 SHA-256 경로 하나만 남는다.
