# MOA 2단계 계획 — 대량 수집·표 사례 학습 기반

기준 커밋: d337c1e 이후. 기존 수집·분석·승인 데이터와 `/volume2/moa` 운영을 보존하면서 확장한다.

## 목표

- 일일 증분: 서울 50, 경기 50, 나머지 15개 시도 각 20 = 400건/일 (04:00 KST).
- 백필: 캠페인 생성일 기준 최근 2년, 기존 자료 포함 고유 문서 10,000건 목표 / 20,000건 상한,
  학교당 캠페인 10건·하루 2건 상한.
- 수집(다운로드)과 분석(표 추출)을 영속 작업 큐로 분리.
- 사람 검수(승인·수정·반려·보류)와 평가셋 분리, 승인 사례만 추천에 사용.

## 스키마 v2 (마이그레이션, 백업 후 적용)

- `notices` + `published_date`, `campaign_id`(NULL=증분), `capture`(full|partial_capture),
  `table_state`(table_present|no_table|unknown).
- `cases` + `family_id`, `split`(train|dev|eval), `review_status`(candidate|approved|rejected|held),
  `queue_reason`, `correction`, `review_history`.
- `school_state`: 학교별 마지막 실패 원인·재확인 시각·발견된 게시판 캐시.
- `campaigns`: 백필 캠페인(기간·목표·상한·시드·상태).
- `campaign_schools`: 학교별 수집 수·일일 수·페이지 커서·상태.
- `jobs`: kind/ref_id/status/attempts/next_attempt/owner — 분석 대기열.
- `usage`: 날짜별 HTTP 요청 수 — 프로세스 간 공유 요청 예산.
- `review_queue`: 일자별 우선검수 선정 결과(점수·사유).

## 구현 단위

1. core: 스키마 버전·백업·마이그레이션, 공유 요청 예산, jobs/school_state/campaign 도우미.
2. crawl: 목록 페이지네이션(pageIndex/page/goPage 등), 학교 상태 캐시 조회·기록.
3. extract: PARSER_VERSION 상수, 추출 캐시 키에 버전 포함, table_state 분류.
4. learn: 분석 작업 큐 소비, template_family(헤더 의미·역할·병합·단위), 검수 큐 생성,
   평가 그룹 분리, review(승인/수정승인/반려/보류/승인취소).
5. backfill: 캠페인 생성/dry-run/배치 실행/체크포인트/일시중지·재개/상태.
6. app: 3단 목표(서울/경기/기타), 수집 시 분석 enqueue, 스케줄러가 증분 우선·백필 배치 양보,
   새 CLI(backfill, review-queue, review, analyse --batch), status 확장.
7. 문서·.env.example·테스트.

## 제한 유지

robots/로그인/CAPTCHA 우회 금지, IP 기준 요청 간격·동일 서버 동시성 1, 공개 GET만,
내부망·리다이렉트·파일 크기 검사 유지, 외부 유료 AI 기본 비활성(호출 0건 보고).
