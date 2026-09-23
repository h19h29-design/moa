# 검증 기록 — 2026-09-23 (3단계)

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
