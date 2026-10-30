# 학교별 통신문 수신·관리 0–1단계

2026-10-22. 기존 MOA를 보존한 **맥미니 외장 SSD 격리 구현·시험**이다.
NAS 운영 배포/DB 적용, 실제 학교 홈페이지 수집·공개, 맥북 LAYA 학습은 하지 않는다.

## 저장장치와 기존 환경

- 작업본: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`.
- 신규 실행 자료: `/Volumes/KIOXIA-2TB/Cache/MOA/school-service-20261022`.
- UUID `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, 실제 APFS 외장 마운트,
  소유권·쓰기 가능·개별 검증 작업본과 `dev-storage`의 SMART/연결 기준선 검사 통과.
  시작 시 여유 약 **1.4TiB**. 마운트/UUID/I/O 보호 실패 시 내부 폴더로 우회하지 않는다.
- 전역 `development_ready=false`, `physical_stability_resolved=false`는 그대로다.
  과거 분리 이력의 영구 해결을 뜻하지 않으며, 정책이 허용한 **검증된 개별 작업본**만 사용했다.
  독립 백업 필요 경고를 숨기거나 readiness를 변경하지 않았다.
- 기존 `.venv312` Python 3.12.13과 설치된 파서를 재사용했다. 패키지 재설치·모델 다운로드 없음.
- 임시·pytest·Python 바이트코드·XDG 캐시는 모두 위 실행 자료 폴더로 지정했다.
  문서 파서 하위 프로세스도 TMPDIR/XDG/Python 캐시 경로를 상속하도록 회귀 테스트를 추가했다.
- 기준 HEAD `35fe25dc2d4a26413bd6a519d6b17b9a9bbde94f`, 기존 브랜치
  `codex/moa-autoqa-v1`과 PR #1을 사용한다. 기존 미추적 가상환경/설명서 4항목은 보존한다.

## 구현 계약

| 항목 | 동작 |
|---|---|
| 학교·담당자 | 학교 코드/명칭, 활성·직접 업로드 허용 설정. 학교+subject별 manager/uploader/reader, 비활성화 즉시 세션 권한 재검사 |
| 직접 업로드 | 서버에 묶인 학교 신원으로 게시글 키·제목·본문·여러 파일 저장. 파일 역할은 HTML 변환/원본 첨부로 명시 |
| 출처 | 학교별 manual/test_adapter만 허용. 테스트 어댑터도 HTTPS origin+경로 경계를 검사하며 교차 호스트·경로 이탈·인코딩된 상위 경로를 거부 |
| 중복 | `(학교, 출처, 게시글 키)`가 게시글 ID. 같은 게시글의 내용 fingerprint 재수신은 기존 버전 반환. 같은 첨부 SHA는 한 번만 보관. 안내 파일 순서는 내용에 포함하고 단순 첨부 순서는 무시하며, 동일 SHA의 충돌하는 역할은 거부 |
| 정정 | 내용이 달라지면 새 수신 버전·순번·이전 버전 연결·수신자/시각을 기록. 과거 내용 재전송은 최신 포인터를 되돌리지 않음 |
| 통신문 묶음 | 본문 HTML과 첨부 여러 개를 단일 수신 버전/mobile notice로 관리. 원본 SHA 객체는 불변 |
| 수신 상태 | 출처별 마지막 시도/성공·상태·안전한 오류 종류·커서. 오류 메시지 원문/비밀값은 작업 기록에 저장하지 않음 |
| 수신 큐 | 기존 SQLite jobs의 `school_receive`/`school_convert` 전용 종류. 연구용 analyse/mobile/캠페인/사용량/백필을 발행·초기화하지 않음 |
| 재시도 | 원자적 단일 워커 점유, 5회 제한·지수 대기, 권한 있는 같은 학교의 명시 재시도. 저장 직후 중단은 작업 토큰으로 같은 변환 버전을 재사용. 진행 중 같은 요청은 재사용하고 다른 편집 요청은 명시 거부 |
| 비공개·권한 | 학교 A/B의 목록·버전·원본·HTML·ZIP·변환·재시도 모두 소유권 검사. SHA만으로 접근하는 공개 경로 없음 |
| 기존 규칙·학습 | 학교 변환은 다른 학교/연구의 승인 사례를 참조하지 않고 학습 export도 호출하지 않음. `split=eval`은 기존 추천에서 격리하는 기술적 구분이며, 모델 평가 실행을 뜻하지 않음 |
| 홈페이지 확장 | `Adapter.fetch(source, cursor) -> ReceiveBatch` 인터페이스와 인메모리/JSON fixture만 구현. 실제 HTTP 수집기·스케줄러·자동 공개는 없음 |

기존 전국 연구용 **400건/일 설정과 백필 코드는 수정하지 않았다**.
공유하는 것은 기존 SHA 저장소·로컬 파서·모바일 렌더러·원본 ZIP·SQLite jobs 기반이다.
기존 전역 검수 웹은 기존 관리자용으로 유지하며 학교 담당자 서버와 인증 경로를 섞지 않는다.
기존 목록·추천·변환·원본 경로에서도 학교 소유 자료는 제외한다. 다만 연구 자료가 독립적으로 참조한
동일 SHA 원본의 기존 다운로드 권한은 유지하고, 학교 격리용 `eval` 표지가 연구 승인 사례에 전파되지 않게 했다.
실제 연구 평가 예약의 누출 방지 동작은 그대로 유지한다.

느린 문서 파싱은 별도 Store 연결의 워커에서 전역 DB 잠금 없이 수행한다.
결과 저장 직전 같은 트랜잭션에서 현재 학교 권한·작업 점유권·기준 변환 버전을 다시 검사하므로
파싱 도중 권한이 회수되거나 작업이 만료되면 결과를 저장하지 않는다.

## DB: 명시적 추가형 마이그레이션

`moa.school_service.migrate(store)` 또는 별도 CLI `migrate`가 호출될 때만 적용한다.
기존 Store/collector/review 기동은 학교 스키마를 자동 적용하지 않는다.
WAL을 포함한 SQLite online backup으로 고유한 사전 백업을 만든 뒤 DDL을 트랜잭션으로 적용한다.
기존 core `PRAGMA user_version=5`, 기존 테이블/행과 인덱스는 유지한다.

- `school_service_migrations`: 학교 스키마 버전 1.
- `school_service_schools`: 코드·명칭·활성/업로드 설정.
- `school_service_members`: 학교+subject PK, 역할·활성.
- `school_service_sources`: 학교+출처 PK, 종류·허용 범위·상태/커서.
- `school_service_posts`: 학교/출처/게시글 키 UNIQUE, 최신 수신 버전.
- `school_service_versions`: 게시글별 순번/fingerprint UNIQUE, 이전 버전, mobile ID,
  원출처 URL·수신자·시각, `visibility='private'` CHECK.

학교/게시글/버전 간 복합 FK는 다른 학교나 다른 게시글의 정정 이력을 연결하지 못하게 한다.
학교 원본은 기존 `/objects/<SHA 앞2자>/<SHA>`에만 저장한다.
동일 bytes를 공유해도 권한은 학교별 수신 버전으로 따로 판정한다.
NAS의 `/volume2/moa/data/db/moa.sqlite3`에는 **학교 테이블을 적용하지 않았다**.

## 격리 실행 방법

아래의 `--data`는 NAS/실자료 DB가 아닌 외장 SSD의 합성 시험 디렉터리를 지정한다.
`serve`는 학교 migration/계정 구성을 자동 생성하지 않으며 loopback에만 바인딩한다.
다음은 기능 사용법이며, NAS에서 실행하라는 배포 지시가 아니다.

```sh
/Users/mac-mini/.local/bin/dev-ssd check
/Users/mac-mini/.local/bin/dev-storage run Relocated-20261017/work/moa-push -- \
  .venv312/bin/python -m moa.school_cli --data /Volumes/KIOXIA-2TB/Cache/MOA/school-service-20261022/demo migrate

# 관리자가 준비한 학교/구성 JSON만 적용한다. 실제 학교를 임의 등록하지 않는다.
# configure --config <외장 SSD의 격리 설정 JSON>
# receive-fixture --school <code> --subject <subject> --source <fixture-id> --fixture <합성 JSON>
# drain           # school_convert 1건, 모델 호출 없음
# recover-stale   # 1시간 이상 만료된 school job만 복구
# serve --credentials <보호된 계정 파일 경로> --port 8791
```

학교 구성 JSON은 `schools: [{code,name,enabled,upload_enabled,members:[{subject,role,active}],sources:[{id,kind,allowed_url,enabled}]}]`이다.
fixture JSON은 `posts: [{key,title,body_html,source_url,files:[{filename,text,role}]}], cursor`이다.
직접 업로드는 multipart `guide`/`attachments`, `post_key`, `title`, `body`, `csrf`를 받는다.

계정 파일은 기존 검수 암호/.env를 변경하지 않는 **별도** owner-only(0600) 파일이며
`users: {username: {school,subject,salt,password_hash}}` 형식이다.
`web_school.password_record()`는 PBKDF2-HMAC-SHA256 600,000회·16바이트 salt를 사용한다.
실제 암호는 getpass/보호 파일로만 전달하고 명령행·문서·로그·Git에 넣지 않는다.
해시/계정 파일도 Git 밖의 보호된 인증 경로에 유지한다.
학교 전용 `moa_school_session` 쿠키는 기존 검수 쿠키와 충돌하지 않는다.
HttpOnly/SameSite=Strict, 쓰기에는 CSRF/Origin, 로그인에는 시도 제한이 적용된다.
기존 검수 bearer는 학교 권한을 부여하지 않는다.

## 실제 검증

- 변경 전 전체: **1,220 passed, 16 skipped**.
- 신규 학교 서비스/HTTP/CLI/형식 시험: **39개**(HWP/HWPX/텍스트 PDF 실제 HTTP 흐름 3개 포함).
- 학교·기존 corpus 관련 최종 집중 회귀: **65 passed**.
- 변경 후 최종 전체: **1,259 passed, 16 skipped**, 40.68초.
  기존 skip은 맥미니에 없는 선택적 Torch 학습 환경 관련이며 이를 위해 학습 환경을 설치하지 않았다.
- `python -m compileall -q moa`, `sh -n scripts/install-nas.sh`도 종료 코드 0.
- 합성 학교 A/B의 업로드·중복·정정·이전본 재전송·독립 조회·다운로드·비공개·계정 회수·reader 쓰기 차단,
  외부 Origin/CSRF/bearer/기존 관리자 route 차단, 오류 재시도·동시 점유·변환 저장 후 복구를 시험했다.
- 파일 순서·역할 충돌·진행 중 편집 충돌, 파싱 후 권한/점유권 재검사, 느린 변환 중 UI 응답,
  학교/연구 SHA 공유 시 기존 연구 다운로드·승인 사례 보존, 두 앱 세션 공존까지 검증했다.
- migration 사전 백업·재실행, 기존 usage/core 버전 유지, `foreign_key_check`와 `quick_check=ok`를 검증했다.
- 별도 읽기 전용 코드 검토에서 발견한 격리·동시성 문제를 회귀 시험으로 수정했고 최종 검토는 Ready다.

| 형식 | 시험 자료 | 파싱 | 안내 블록 | HTTP 업로드/HTML/ZIP | ZIP 원본 SHA |
|---|---|---|---:|---|---|
| HWP | 이미 취득한 비공개 원본 1개 | extracted, 1,611자 | 34 | 200 / 200 / 200 | 일치 |
| HWPX | 새 합성 문서 | extracted, 53자 | 2 | 200 / 200 / 200 | 일치 |
| 텍스트 PDF | 새 합성 문서 | extracted, 99자 | 2 | 200 / 200 / 200 | 일치 |

이 시험은 변환 경로·파일 보존 확인이다. 전체 문서의 의미 정확도나 사람 승인, 모델 학습 성적을 뜻하지 않는다.
원문·비공개 HTML·ZIP·DB는 외장 SSD의 비공개 시험 자료에만 보관하며 Git에 넣지 않는다.

## NAS 보존과 복구 확인

- 기존 `/volume2/moa/app`, `/volume2/moa/data` 및 objects/documents/extracted/registry/reports/백업 폴더를 읽기 확인.
- `app-review-1`/`app-collector-1` 실행, collector healthy. 기존 이미지와 2026-10-18 시작 시각 유지.
  로컬 review HTTP는 비인증 **401**로 인증 보호 확인.
- 운영 앱 파일·compose 해시, `.env` 메타데이터(값은 읽거나 출력하지 않음), DB 스키마의 전후 **동일 확인을 통과**했다.
  학교 테이블 미적용, 기존 경로·컨테이너 이미지/시작 시각 유지, review 인증 보호·collector healthy도 확인했다.
  운영 수집기는 계속 동작하므로 일상적인 데이터/WAL 변화와 개발 변경을 혼동하지 않는다.
- 기존 DB 백업 `/volume2/moa/data/db/backups/moa-20261017-202252-schema-v4-to-v5.sqlite3`를
  외장 SSD `backup-restore/nas-existing-backup.sqlite3`로 읽기 복사했다(279,158,784 bytes, mode 0600).
  사본의 `quick_check=ok`, v4 스키마와 notices 6,261/cases 20,734/mobile_notices 6,263 읽기를 검증했다.
  NAS 복원·초기화·덮어쓰기는 하지 않았다. 이 백업은 현재 운영 DB의 최신 스냅샷이라는 뜻이 아니다.
- 기존 app 복구 아카이브와 DB 사본은 `/volume2/moa/data/deploy-backups/continue-20261018T160000`에도 존재한다.
- NAS에서 임시 파일을 만들지 않는 읽기 스트림으로 기존 `objects`/`documents`를 외장 SSD
  `backup-restore/nas-originals-readcopy.tar`에 보관했다(3,522,549,760 bytes, mode 0600).
  객체 **16,881개**의 SHA와 내용이 모두 일치하고 문서 파일 **7,350개**를 확인했다.
  위 과거 DB 백업이 참조하는 객체 **14,474개**, 문서 기록 **6,261개**의 누락은 각각 0이다.
  아카이브는 추출·운영 복원하지 않았고 검증 영수증은 Git 밖에 보관한다.
- 이번 점검만으로 **최신 운영 DB와 모든 원본을 묶은 독립 전체 복구가 검증됐다고 주장하지 않는다**.
  운영 적용 전 최신 일관 DB+모든 원본의 완전성 및 관리자의 복구 절차 확인이 별도 필요하다.

## 파일별 위치와 결과물

소스/테스트/설명서는 모두 기존 외장 작업본 아래에 있다.

- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/moa/school_service.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/moa/web_school.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/moa/school_cli.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/tests/test_school_service.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/tests/test_web_school.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/tests/test_school_cli.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/tests/test_school_formats.py`
- `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push/docs/MOA_SCHOOL_SERVICE_2026-10-22.md`

기존 파일 수정은 위 외장 작업본의 `moa/mobile.py`, `moa/corpus.py`, `moa/learn.py`, `moa/web.py`,
`moa/web_auth.py`, `moa/web_mobile.py`, `README.md`다.
실행 자료 기준 폴더는 `/Volumes/KIOXIA-2TB/Cache/MOA/school-service-20261022`:

- `pytest/`: 모든 격리 SQLite DB·마이그레이션 사전 백업·HTTP 시험 자료.
- `formats/{hwp,hwpx,pdf}/`: original, candidate.html, originals.zip, 비밀 없는 RESULT.json.
- `full-junit.xml`, `formats-junit.xml`: 실제 시험 결과.
- `nas-before.json`, `nas-after.json`, `NAS_COMPARISON.json`: 비밀값 없는 보호 점검.
- `backup-restore/`: 기존 DB 백업·원본 아카이브 읽기 사본, RESTORE_CHECK.json,
  ORIGINALS_CHECK.json(운영 복원 아님).
- `nas_readonly_probe.py`, `nas_compare.py`, `backup_read_verify.py`: 비밀을 읽지 않는 점검/읽기 사본 검증 보조 파일.
- `tmp/`, `cache/`: 임시/바이트코드/pytest 캐시.
- `PROGRESS.md`: 비공개 실행 경과. 이 전체 실행 폴더는 Git 밖에 유지한다.

## 2단계로 넘기는 경계

학교별 원본·정정·파일 역할·비공개 권한의 기반에서 문서 구조 분석을 이어갈 수 있다.
다음은 헤더/각주/여러 첨부의 관계·문서 구역 계약과 사람 검수 근거를 정교화하는 단계다.
모델 성적 개선, 실제 홈페이지 어댑터 가동, 운영 인증 연결·스키마 적용·배포는 별도 범위다.
학교 서비스의 운영 적용을 이번 개발 완료와 혼동하지 않는다.
