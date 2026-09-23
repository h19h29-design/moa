# MOA — 가정통신문 자동 수집·표 사례 축적기

매일 **서울 50건, 경기 50건, 나머지 15개 시도교육청 각각 20건(총 400건)**의 고유
가정통신문을 목표로 수집하고, 별도 **백필 캠페인**으로 최근 2년 과거자료를 채웁니다.
자료·DB·표 추출 결과는 **Synology NAS `/volume2/moa/data`**에 보관합니다.

> v0.2: 수집기, 중복 방지, 표 구조 추출, 분석·검수 대기열, 백필 캠페인, 양식 family,
> 일일 스케줄러입니다. 지원되지 않는 게시판은 다음 학교로 넘기고, 목표를 채우지
> 못하면 `partial`과 부족 건수·사유를 기록합니다.
> **모델 가중치를 자동 학습시키거나 AI의 답을 정답으로 자동 승인하지 않습니다.**
> 외부 유료 AI 호출은 기본 비활성이며 이번 단계에서 사용하지 않습니다.

## 1. 실행 흐름

```text
NEIS 학교목록 (7일마다 갱신)
  → 실제 교육청 코드별 학교 그룹
  → 날짜별 무작위·학교급 순환 추출
  → 공개 가정통신문 게시판 탐색
  → 게시글 본문 + 첨부파일 수집
  → SHA-256 단일 객체 저장소
  → 분석 대기열(jobs) → PDF / HWPX / HTML 표 구조 추출
  → 양식 family·미검수 후보 집계 → 우선검수 큐 → 사람 승인 사례
  → 일일 증분 결과와 백필 캠페인 실적을 분리 보고
```

- 서울 50, 경기 50, 기타 각 20은 **학교 수/첨부 수가 아닌 신규 고유 통신문 수**입니다.
- 한 학교에서 기본 하루 1건. 여러 학교·학교급의 양식을 확보합니다.
- 교육청은 NEIS가 반환한 실제 코드와 이름을 사용합니다. 특정 17개 코드 목록에 고정하지 않습니다.
- 기존에 본 통신문, 차단된 사이트, 실패한 다운로드는 신규 목표 실적에 포함하지 않습니다.
- 같은 날 다시 실행하면 SQLite의 당일 실적을 확인해 부족분만 모읍니다.
- 첨부 3개가 있는 글도 통신문 1건입니다. 본문을 읽었는데 첨부가 사이트 쪽에서 거부되면 통신문은
  저장하고 `attachments_failed`(거부/뷰어 응답) 또는 `attachments_skipped_robots`(robots 차단)로
  사실을 남깁니다. 본문도 없고 첨부도 확보하지 못한 글만 수집 실패로 셉니다.
- 최근 365일 게시글을 대상으로 합니다. 게시일을 알 수 없는 경우 게시일을 추정하지 않습니다.
- 학교/게시글은 무작위로 고르지만, 공개·접근 가능한 지원 게시판에 한정된 표본입니다.
  통계적으로 전국 전체를 대표하는 무편향 표본이라는 의미는 아닙니다.

## 2. NAS 최초 설치

### A. 준비

1. DSM **제어판 → 공유 폴더**에서 `moa`를 만들고 저장 위치는 **볼륨2**로 선택합니다.
2. **Container Manager**가 설치되어 있어야 합니다. 기존 Docker 저장소나 다른 앱 설정은 변경하지 않습니다.
3. 제공된 `moa-notice-collector.zip`을 압축 해제하여 소스가
   `/volume2/moa/app/compose.yaml`에 오도록 놓습니다. 저장소에 전체 코드가 반영된 이후에는
   GitHub의 **Code → Download ZIP** 또는 Git clone도 사용할 수 있습니다.
4. `.env.example`을 `.env`로 복사하고 `NEIS_API_KEY=` 뒤에 키를 넣습니다.

학교목록 API 키는 [나이스 교육정보 개방 포털](https://open.neis.go.kr/)의
**활용가이드 → 인증키 신청**에서 발급합니다. 인증키 없는 호출은 샘플 5건으로 제한되므로
정식 키 없이는 전국 학교목록을 만드는 기능을 실행하지 않습니다.
가정통신문 자체를 NEIS에서 받는 것은 아닙니다. NEIS는 학교명·교육청·홈페이지 주소 확보에 사용합니다.

### B. SSH에서 설치

기존 NAS SSH 접속을 이용합니다. SSH 포트포워딩이나 공인 인터넷 노출을 추가하지 않습니다.

```sh
# 저장소에 전체 소스가 올라와 있고 Git이 설치된 경우에만 사용합니다.
# 제공 ZIP을 app에 풀었다면 이 두 줄은 생략합니다.
cd /volume2/moa
git clone https://github.com/h19h29-design/moa.git app

cd /volume2/moa/app
# 이미 .env가 있으면 덮어쓰지 않습니다.
test -f .env || cp .env.example .env
# File Station 텍스트 편집기 또는 vi로 NEIS_API_KEY를 입력합니다.
vi .env

sudo sh scripts/install-nas.sh
```

설치 스크립트는 실제 `/volume2` 마운트, 경로의 심볼릭 링크, 데이터 디렉터리를 확인합니다.
볼륨2가 없거나 경로가 볼륨1을 가리키면 중단하며 다른 경로로 대체하지 않습니다.
SSH 사용자의 UID/GID를 컨테이너에 적용합니다. 신규 빈 데이터 폴더만 소유권을 설정하며
기존 데이터의 권한을 재귀 변경하지 않습니다. 기존 폴더 권한이 안 맞으면 관리자가 확인해야 합니다.

**GitHub에 코드가 올라온 것만으로 NAS에 설치되지는 않습니다. 위 설치 단계를 수행해야 합니다.**

### C. 기동 후 동작

첫 기동 시 즉시 한 번 실행하고, 이후 기본 **매일 한국시간 04:00**에 실행합니다.
NAS가 꺼졌다가 실행 시간 이후 켜지면 해당 날짜 작업을 수행합니다.
이미 완료/부분완료/오류로 종료된 날짜는 스케줄러가 자동 반복하지 않으며 아래 수동 실행으로 재시도합니다.
전날 놓친 양을 다음날 자동 합산하지 않습니다.
`run --office B10` 같은 단일 교육청 점검은 전국 일일 실행 완료로 취급하지 않습니다.

```sh
cd /volume2/moa/app
sudo docker compose logs --tail=100 collector
sudo docker compose exec collector python -m moa status
```

`NAS_DATA_DIR` 바인드 마운트로 원문·DB·사례·보고서는 볼륨2에 보관합니다.
Docker 이미지/레이어와 제한된 엔진 로그의 저장 위치는 **기존 Container Manager 설정**을 따릅니다.
이 프로그램이 Docker 시스템 저장소를 볼륨2로 강제 이동하지는 않습니다.

## 3. 주요 명령

아래는 `/volume2/moa/app`에서 실행합니다. 스케줄러가 같은 작업을 실행 중이면 잠금으로 거부합니다.

```sh
# 설정·쓰기 권한 확인. 키 값은 출력하지 않습니다.
sudo docker compose exec collector python -m moa doctor

# 오늘 부족분 수동 수집. partial/error이면 종료 코드 2입니다.
sudo docker compose exec collector python -m moa run

# 서울만 실제 연결 점검: 당일 서울 목표 범위 내에서 수집합니다.
sudo docker compose exec collector python -m moa run --office B10

# 학교 목록 강제 갱신
sudo docker compose exec collector python -m moa sync-schools

# 분석 대기열 처리(또는 --id로 단건). 미지원 형식은 계속 대기 상태입니다.
sudo docker compose exec collector python -m moa analyse
sudo docker compose exec collector python -m moa analyse --batch 100

# 후보 포함 사례 검색; 기본 search는 사람 승인 사례만 조회합니다.
sudo docker compose exec collector python -m moa search 준비물 --candidates

# 백필 캠페인: 계획 확인 → 생성 → 수동 배치 → 상태/일시중지/재개
sudo docker compose exec collector python -m moa backfill plan
sudo docker compose exec collector python -m moa backfill start
sudo docker compose exec collector python -m moa backfill run --batch 30 --max-minutes 20
sudo docker compose exec collector python -m moa backfill status
sudo docker compose exec collector python -m moa backfill pause
sudo docker compose exec collector python -m moa backfill resume

# 오늘의 우선검수 후보(기본 20건)
sudo docker compose exec collector python -m moa review-queue

# 검수: 승인/수정승인/반려/보류/승인취소(candidate)
sudo docker compose exec collector python -m moa review CASE_ID \
  --status approved --layout key_value_cards --reviewer "관리자" \
  --rights-reviewed --privacy-reviewed

# 실행 중지 / 재개 (데이터 삭제 없음)
sudo docker compose stop
sudo docker compose up -d
```

이미지/스캔 PDF/구형 HWP는 원문만 보관하거나 일부 본문만 추출된 상태로 남습니다.
`analyse`만 반복한다고 OCR/비전/HWP 전용 엔진이 자동 설치되거나 동작하지 않습니다.

## 4. 저장 위치와 중복 정책

```text
/volume2/moa/
  app/                              소스·Docker 설정·로컬 .env
  data/
    db/moa.sqlite3                  통신문·당일 실적·출처·표 사례
    registry/schools.json           실제 학교목록 (자동)
    registry/overrides.json         특수 게시판/허용 호스트 설정 (선택)
    objects/ab/<SHA-256>            원본 파일은 전역에서 한 벌
    documents/<교육청코드>/<날짜>/   학교명·원문 주소·원본 참조·정정본 관계
    extracted/                     문서/표 추출 JSON·미지원 상태
    learning/candidates.jsonl       미검수 표 사례(학습/개발 분할만)
    learning/patterns.jsonl         구조 패턴별 누적 건수
    learning/families.jsonl         양식 family별 누적 건수
    learning/approved.jsonl         사람이 검수·승인한 사례만(평가셋 제외)
    learning/eval.jsonl             고정 평가용 사례(검색·규칙 생성에서 제외)
    reports/latest.json            최근 증분 실행의 지역별 실적·오류
    reports/backfill-latest.json   최근 백필 배치 결과·캠페인 누적
    reports/latest.html            같은 내용을 보는 로컬 보고서
    reports/<날짜>-<실행ID>.json     실행 이력
```

**완전 중복은 나중에 원본을 지우는 대신 처음부터 추가 저장하지 않습니다.**
파일명이 다르더라도 바이트가 동일하면 SHA-256 경로 하나만 사용합니다.
게시글이 달라도 제목·본문 구조·첨부 해시가 같으면 출처 별칭만 추가합니다.
양식이 비슷하거나 표 모양이 같다는 이유로 원문을 삭제하지 않습니다.
날짜·금액·첨부가 바뀐 정정본은 새 버전으로 남기고 이전 버전과 연결합니다.
NAS의 기존 다른 폴더를 스캔하거나 삭제하는 기능은 없습니다.

`latest.json`의 `regions`에서 `target`, `total_today`, `shortfall`, `duplicates`, `errors`를 확인하세요.
`status=complete`는 **수집 목표 충족**입니다. 모든 첨부의 표 분석/정답 검수 완료를 의미하지 않습니다.
`analysis_pending_total`을 함께 보세요. 실패와 부족량은 가짜 자료로 채우지 않습니다.

## 5. 여기서 말하는 '학습'

현재는 별도 AI 키나 외부 LLM 없이 아래를 수행합니다.

- HTML/HWPX의 표 좌표·병합 범위와 PDF 텍스트/선 기반 표를 추출합니다.
- 정보형 카드·시간순 목록·학년별 카드·읽기 전용 신청서·스크롤 표를 **규칙 기반 후보**로 분류합니다.
- 같은 구조의 패턴을 집계하고, 사람이 승인한 동일 패턴이 있으면 이후 사례의 추천에 참고합니다.
- 추천이 생겨도 새 사례는 자동 승인되지 않습니다. 모델 가중치는 변경되지 않습니다.
- 이미지/스캔 문서, 불확실한 PDF 병합 관계는 정답으로 확정하지 않습니다.

PDF에는 실제 셀 경계/원시 그리드를 저장하며 병합·헤더 확정은 보수적으로 처리합니다.
추후 비전 모델/HWP 파서와 검수 화면을 추가할 기반을 만드는 단계입니다.
개인정보 정규식 가림은 보조 기능이며 이름·건강정보 등을 완전히 탐지한다는 보장은 없습니다.
모든 수집 자료는 비공개 NAS에만 두고, 모델 학습·재배포 권한은 별도로 확인하세요.

원문과 추출된 표를 대조하고 이용권한·개인정보를 검토한 뒤 승인할 수 있습니다.

```sh
# CASE_ID는 search 결과 또는 candidates.jsonl의 id 전체 값으로 바꿉니다.
sudo docker compose exec collector python -m moa approve CASE_ID \
  --layout key_value_cards --reviewer "관리자" \
  --rights-reviewed --privacy-reviewed
```

`approved.jsonl`은 검수된 구조/레이아웃 사례입니다. 완성된 HTML 정답 세트나
바로 파인튜닝 가능한 학습 파일을 만들어냈다는 뜻은 아닙니다.
사례는 양식 family 단위로 학습/개발/평가(약 80/10/10)에 배정되며,
평가셋은 사례 검색·규칙 생성·추천에서 제외됩니다(`learning/eval.jsonl` 별도 파일).

## 6. 백필 캠페인과 검수 흐름

- `backfill start`는 캠페인 기간(생성일 기준 최근 2년)·목표(1만)·상한(2만)·시드를
  고정 저장하고, 이후 배치는 학교/게시판/페이지 단위 체크포인트에서 이어집니다.
- 학교당 캠페인 전체 10건·하루 2건 상한. 중단·재부팅 후에도 실적과 커서가 유지됩니다.
- 백필 실적은 일일 증분 목표와 분리 집계되며(`campaign_id`), 파일 중복 방지는 공유합니다.
- 스케줄러는 04:00 증분 실행을 우선하고, 남은 요청 예산으로 백필 배치를 돌립니다.
  cron을 별도 등록하지 않습니다.
- 접근 가능한 후보가 소진되면 `coverage_exhausted`로 멈추고 사유를 남깁니다.
- 매일 `review-queue`가 우선검수 후보(기본 20건)를 고르고, `review` 명령으로
  승인·수정승인(`--correction`)·반려·보류·승인취소를 기록합니다.

## 7. 게시판 차이와 어댑터

기본 어댑터는 서버가 제공하는 HTML 링크를 읽습니다.
`/M.../view/<번호>`, `selectNttList.do → selectNttInfo.do`, `boardCnts/view.do`,
`nttSn/boardSeq/wr_id/articleSeq`가 있는 직접 링크를 인식합니다.
JavaScript 전체 실행, 로그인/캡차, POST 전용 다운로드 등은 우회하지 않습니다.
현재 모든 교육청의 CMS를 실서비스에서 검증한 상태는 아닙니다.

특정 학교는 `data/registry/overrides.json`에서 설정할 수 있습니다.
키는 실제 학교목록의 `교육청코드:학교코드`입니다. 아래 주소/코드는 예시입니다.

```json
{
  "B10:실제학교코드": {
    "board_urls": ["https://실제학교호스트/가정통신문게시판경로"],
    "allowed_hosts": ["실제첨부호스트"],
    "post_selector": "a.실제게시글클래스",
    "body_selector": ".실제본문클래스"
  }
}
```

선택자를 모르면 해당 항목을 생략합니다. HTTPS 인증서 오류/차단은 보안 검사를 끄지 말고
원인과 공식 주소를 확인하세요. 수집 제외는 같은 학교 항목에 `"disabled": true`를 지정합니다.
학교목록 CSV/JSON을 별도로 확보한 경우 `docs/REGISTRY.md`를 참고하세요.

## 8. 안전·운영 제한

공개 게시판이라도 저작물 이용조건·개인정보 제한이 사라지지 않습니다.
robots.txt 존중은 수집 예절/기술 제어이며 저작권 허락을 대신하지 않습니다.
재배포·외부 AI 전송·가중치 학습은 기본 구현에 없습니다.

HTTP GET만 사용하며 내부/특수 IP 차단, 검증된 IP로 연결 고정, TLS 검증,
리다이렉트 호스트 재검사, 요청 간격, 크기 제한을 적용합니다.
학교별 외부 CDN/첨부 도메인은 운영자가 `allowed_hosts`로 명시해야 합니다.
첨부 20MB/개, 50MB/통신문, 12개/통신문, PDF 60페이지의 제한이 있습니다.
문서 분석은 별도 제한 프로세스에서 수행하지만 이것만으로 완전한 악성파일 방어를 보장하지 않습니다.
컨테이너는 비root·읽기전용 시스템·공개 포트 없음으로 실행합니다. 기존 NAS 보안 정책을 유지하세요.

백업할 때는 `docker compose stop` 후 `data` 전체를 백업하고 다시 시작하거나 SQLite의
정식 백업 절차를 사용하세요. 실행 중 `moa.sqlite3` 파일 하나만 복사하면 WAL 내용이 빠질 수 있습니다.
업데이트는 `.env`와 `data`를 유지한 채 새 코드를 받고 `docker compose up -d --build`를 실행합니다.

## 9. 개발·테스트

Python 3.12/Linux 기준입니다. 아래 테스트는 네트워크 모의 응답과 합성 문서로 실행합니다.
실제 학교 문서/비밀키를 저장소에 넣지 않습니다.

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q moa
sh -n scripts/install-nas.sh
```

검증: **48개 테스트 통과** (2026-09-21 NAS 실측 반영분 포함). 세부 범위와 남은 한계는
`docs/VERIFICATION.md`에 있습니다.
GitHub Actions 설정은 푸시·PR 시 같은 테스트를 실행하도록 제공됩니다.

## 10. 실제 운영에서 확인된 제약 (2026-09-21 NAS 실측)

전국 실사이트를 직접 확인한 결과이며, 코드로 우회하지 않고 사실대로 기록합니다.

**게시판 탐색은 이제 다음을 처리합니다.** 학교 홈페이지/게시판이
`location.href` 또는 meta refresh 껍데기 페이지로 다른 주소(같은 학교의 새 도메인 포함)를
알려주면 그 주소로 이동해 탐색합니다. Jinhak 계열(`selectNttList.do`/`selectNttInfo.do`,
`/M0103xx/`, `/view/숫자`), `boardCnts` 계열(`list.do`/`view.do` + `javascript:goView(...)`),
`usm-*` 계열 상세 본문(`td.tch-ctnt`)까지 읽습니다.

**robots.txt가 수집을 막는 곳은 수집하지 않습니다.**

| 확인된 제약 | 영향 | 처리 |
| --- | --- | --- |
| `*.sen.ms.kr/.es.kr/.hs.kr/.sc.kr` robots `Disallow: /` | 서울 1,410교 중 1,325교 | 수집 제외, 실패 사유로 기록 |
| `school.gyo6.net` robots `Disallow: /` | 경북 936교 중 917교 | 수집 제외 |
| `school.jbedu.kr` robots `/_cmm/fileDownload/*` 차단 | 전북 첨부 | 본문만 저장, 첨부는 `attachments_skipped_robots`로 기록 |
| `school.use.go.kr` robots `/files/` 차단 | 울산 본문 이미지 | 위와 동일 |
| `*.djsch.kr` robots `Disallow: /boardCnts/` | 대전 게시판 경로 | 수집 제외(우회하지 않음) |
| 광주 `xhomenews/xboard` 게시판 로그인 요구 | 광주 일부 | 우회하지 않고 `로그인/인증이 필요한 게시판`으로 기록 |
| 세종 `*.sjedums.kr` TLS 체인 미완성 | 세종 | 인증서 검증을 끄지 않고 실패로 기록 |
| 일부 school 홈페이지 DNS 소멸/방화벽 차단 | 개별 학교 | 실패 사유 그대로 기록 |

여러 교육청 방화벽이 브라우저 토큰 없는 User-Agent를 연결 단계에서 끊습니다. 그래서 기본 UA는
표준 형식 `Mozilla/5.0 (compatible; MoaNoticeBot/0.1; +https://github.com/h19h29-design/moa)`를
사용합니다. 봇 신원과 연락처는 그대로 밝히며 `MOA_USER_AGENT`로 교체할 수 있습니다.

NEIS `schoolInfo`는 전체 건수와 실제 고유 학교 수가 다릅니다(예: 12,669건 중 고유 코드 12,564개,
학교코드가 빈 행 반복). 동기화는 헤더가 알려준 **건수**로 끝내고, 17개 교육청 미만이면 부분 응답으로
보고 기존 목록을 유지합니다.

`V10`(재외한국학교교육청)은 17개 시도교육청이 아니므로 기본 하루 목표에서 제외해 총 90건을 유지합니다.
포함하려면 `.env`의 `EXCLUDE_OFFICES=`를 비우세요.

## 참고 문서

- [NEIS 학교기본정보 API](https://open.neis.go.kr/portal/data/service/selectServicePage.do?infId=OPEN17020190531110010104913&infSeq=2)
- [NEIS 개발자 가이드](https://open.neis.go.kr/portal/guide/apiGuidePage.do)
- [Python robots parser](https://docs.python.org/3/library/urllib.robotparser.html)
- [pdfplumber](https://github.com/jsvine/pdfplumber)
- [한컴 HWPX 파싱 안내](https://tech.hancom.com/python-hwpx-parsing-1/)
- [Docker Compose bind mount 설정](https://docs.docker.com/reference/compose-file/services/)
