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
- `python -m pytest -q`: **44개 테스트 통과** (Python 3.12)

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

## 실제 수집 결과

- 서울 표본 실측: 비플랫폼 서울 학교 85개 중 표본 24개 → 성공 3개(`명덕외국어고등학교`,
  `오디세이학교`, `명지초등학교`), robots 차단 7, 게시판 미탐색 9, DNS 소멸 3, 교차 호스트 차단 2.
  서울 1,410교 중 1,325교가 `*.sen.*.kr` 플랫폼의 `Disallow: /` robots를 쓴다.
- 수집 데이터는 원문 URL·학교·본문·첨부(해시·바이트·형식)·수집시각을 함께 저장한다.
  실측 예: 부산솔빛학교 인플루엔자 안내(PDF 246KB), 울산고운고 평가계획(HWP 135KB, 본문 9,102자),
  강릉중부설방송통신중 안전 안내(PNG 115KB), 마령초 가족캠프 안내(PDF 2.7MB).
- 표/양식 사례: `learning/candidates.jsonl`(미검수), `learning/patterns.jsonl`,
  `learning/approved.jsonl`(사람 승인 전까지 비어 있음) — 자동 승격 없음을 확인.
