# MOA 후속 구현·검증 — 2026-10-18

이 문서는 앞선 15:20 보고서 이후의 후속 작업 기록이다. NAS 최종 집계 16:14 KST 기준.
**20건의 현재 리비전 종단 처리, 맥북 후보 학습, 상시 워커 연결을 실제 실행했다.**
모델 품질은 아직 부족하며 실자료 자동 승인·공개와 운영 모델 승격은 계속 금지한다.

## 실제 고친 문제

- HTML 주석 속 편집기 JSON이 37,739자 안내 본문처럼 노출되던 문제를 수정했다.
  실제 HTML 주석만 제외하며 화면에 보이는 같은 문자열은 지우지 않는다. 파서 `moa-local-v5-visible-html`.
- 긴 표를 완전한 행 단위로 나누고 헤더·병합 행 문맥·각주를 반복 제공한다.
  원문/실제 DOM을 같은 행 인덱스로 대조하고, 부모 문서의 전체 행 커버리지를 별도 검사한다.
  잘라낸 구역이나 job done을 문서 완료로 세지 않는다.
- 제목과 빈 작성란이 명확한 신청서는 기계 경계 증거를 남기고 원본 첨부로 유지한다.
  사람의 경계 확인·승인 체크를 대신 만들지 않는다. 애매한 서식은 여전히 예외다.
- 학교 머리말과 전체 폭 안내 문단만 있는 외곽 표는 문단으로 풀되 실제 데이터 행은 표로 보존한다.
  변환기 `moa-notice-v3-masthead-flow`.
- 파서 업그레이드로 새 후보가 생겼을 때 이전 리비전의 완료 실적을 현재 완료로 세던 집계를 수정했다.
  새 버전은 다시 검사하며 이전 원문·후보·모델 결과는 보존한다. `pilot_history`에 이전 manifest도 보관한다.
  사람이 수정한 결과, 평가 자료, 승인 자료를 덮어쓰지 않는다.
- 상시 워커에서 완료 응답 유실을 작업 실패로 바꾸지 않도록 내구성 outbox를 보완했다.
  동일 결과 재전송은 NAS의 idempotent ACK로 확인하며, 만료 lease는 stale로 보관한다.
  v1 ACK의 job/input/idempotency 필드를 필수 검사하고 lease/fence가 추가로 오면 함께 검사한다.
  실제 lease/fence 검증은 NAS 수락 전에 유지한다.
- 모델의 작업 status가 completed여도 질문 판정이 changed/insufficient이면 우선검수 예외로 분류한다.
  하루 최대 2개 예외 + 1개 표본 제한과 이미 배정된 당일 목록의 미보충 원칙은 유지한다.

## 실제 문서 검증

| 구분 | 실제 결과 |
|---|---|
| 현재 수집 통신문 | 6,581건 유지. 이번 작업 때문에 수집 목표를 늘리지 않음 |
| 실제 시험 안내문 | 초기 3건 + 추가 17건 = 20건. 첫 3건 현재 버전 종단 확인 후 3건 이하 묶음으로 확대 |
| HTML·규칙·브라우저 | 20/20건, 실제 360/393/430px 총 60개 화면 폭 검사. 중요 규칙 불일치 0 |
| 원본·다운로드 | 원본 파일 40개 SHA 대조 일치. 20건의 HTML 및 실제 원본 포함 ZIP 다운로드 성공 |
| 맥북 구역 작업 | 현재 리비전 388개 전부 실추론·NAS 수락. claim/heartbeat/complete 각각 388개. 미처리·문맥 초과·truncation 0, 최대 실제 토큰 838/1,024 |
| 문서 단위 machine_checks | 현재 리비전 20건. 구역 작업 수와 합산하지 않음 |
| 모델 질문 원판정 | 총 1,072개 중 preserved 1,003 / changed 18 / insufficient 51. **의심 판정 69개가 실제 오류 69개라는 뜻은 아님** |
| 사람 승인/검수 | 모바일 승인 0, 표 사례 승인 0, 사람 검수 기록 생성 0 |
| 미검수 | 모바일 후보 21건 중 이번 시험 20건. 다른 이미지 후보 1건을 이번 성공 실적에 넣지 않음 |
| 최종 상태 | 실자료 20건 모두 needs_review. 종단 처리 완료 ≠ 모델 품질 통과 ≠ 사람 승인 |

변환 파일 기준 HWP 19개, HTML 19개이며 한 안내문에 두 형식이 함께 있는 경우가 있어 합산하면 안 된다.
원본 첨부 유지 파일도 내려받아 실제 바이트를 검증했다. 전체 자료 일괄 변환·실자료 학습은 하지 않았다.
이전 리비전 12개 manifest는 pilot_history에, 전체 후보 이력 39개는 mobile_revisions에 보존했다.
현재 388개에 옛 리비전의 완료 작업이나 원래 합성 스모크 작업을 더해 실적을 부풀리지 않는다.

최종 증거는 private NAS `coordination/PILOT_20_FINAL_20261018.json`에 리비전/작업 ID와 함께 저장했다.
현재 작업 ID 예: `job-pilot-aff74976a89bfee99c021aed7ee423e2` … `job-pilot-ab290b382ea2a75759ce5f1c7a4266de`.
388개 정확한 ID 목록은 위 manifest의 job_ids가 기준이며 옛 리비전 작업도 별도 보존한다.

검사 범위는 지원 파서가 읽은 원문 inventory와 저장 HTML의 실제 DOM이다.
사람이 원본 HWP의 모든 도형·배치·시각 요소를 검수했다는 뜻은 아니다.
멘토링의 1~17번 학과/교실, 일정·2개 선택·선착순·미신청 조건을 보존한다.

## 맥북 학습 — 실제 실행 완료, 운영 승격 아님

- 묶음 `moa-ko-synthetic-20261018-v1`, 독립 합성 원본 그룹 50개 / 의미 family 10개 / 판정쌍 1,000개.
- 학습 600쌍, 보정용 별도 보관 200쌍, 분리 평가 200쌍. 원본·family·source hash 그룹별 분리.
  기존 평가 전용 자료와 파생본, 미확인 보류, JEV/모델 자기 판정, 실자료는 학습에 넣지 않았다.
- 실제 완료 run: `train-pilot-ef81fadafc79bb2cfdee-resource-retry1`.
  MPS에서 1 epoch, micro batch 1, gradient accumulation 16, **38회 optimizer update**.
  parent 기준 약 140초. encoder 동결 유지, head 값 변경, 저장·재로딩 파라미터 일치 및 재추론 확인.
- 처음 시도는 1회 업데이트 후 메모리 자원 기준으로 중단했다. 기준을 낮추지 않고 미사용 MPS 캐시만
  비우도록 수정해, 보존된 동일 데이터/기준 모델로 1회 자원 재시도했다. 품질 튜닝 반복 아님.
- parent/child 각각 45분 상한을 강제한다. AC·유휴 15분·메모리·디스크·기기 lock,
  주간 최대 2회 예약 제한을 유지한다. 자동 train claim은 켜지 않았다.
- 분리 평가 정확도는 **학습 전 37.5% → 후 37.5%**. 개선을 확인하지 못했다.
  loss 1.1538 → 1.0253, ECE 0.2712 → 0.1530이지만 보정은 미실시이며 성능 보증이 아니다.
  보정 200쌍을 보관했을 뿐 MOA 보정 모델을 맞췄다고 보고하지 않는다.
- 새 후보 모델 SHA-256: `7860c213e812cda568083c29926b2b5df1734de74d6d528511c06f4439c21cc1`.
  맥북 private `candidates/<run_id>/checkpoint/`에만 보관, Git/외부 모델로 전송하지 않는다.
- 현재 추론 기준 모델은 기존 `9d628fd971b700382ac6f65920a86f149777b2e748e0c955fb3b19695aa8f204` 그대로다.
  `champion_changed=false`, `promotion=blocked_insufficient_independent_gold`.
- Book 실제 결과와 parent 증거를 Mini에서 대조하고 NAS `coordination/BOOK_TRAINING_*_20261018.json`에 전달했다.
  수신 확인과 훈련 완료 증거를 구분하며, 존재하지 않는 model-upload API 작업을 만들지 않았다.

## 상시 워커 연결

- 맥북 기존 LAYA source 9개 파일과 모델·환경을 보존했다. 새 모델 설치·Tailscale·공개 포트 없음.
- 기존 `org.moa.laya.worker`의 LaunchAgent만 버전 고정 실행기로 연결했다.
  `incoming/moa-book-worker-8b301ad.py`, RunAtLoad/KeepAlive, background/nice 및 offline 설정 유지.
  이전 plist와 실행 파일을 private backup에 남겼다.
- `daemon_ready=true`, `execution_mode=standing_pull_client`, 준비 ACK 후 같은 코드 지문으로 실제 claim.
  실행 지문 `3fcffcf0bb08498c65b554e5a517159f798e3f47ebbf0b3628d60ce17014ec0d`.
- 기존 `org.moa.mini-tunnel`과 Mini `com.moa.relay` 재사용.
  Book `127.0.0.1:18781` → Mini `127.0.0.1:8781` → 기존 NAS API.
- Book에서 무토큰 401 / 토큰 capabilities 200 / 계약 해시 일치 재확인.
  완료된 pilot 결과를 실제 재전송해 `accepted=true, duplicate=true`를 받았고 새 job은 만들지 않았다.
- 일시정지 상태에서 현재 작업 종료를 확인한 후 MOA LaunchAgent만 교체했고 재개 후 새 작업 처리를 확인했다.
  사용자가 로그인한 맥북이 깨어 있고 LAN/자원 기준이 충족될 때 동작한다. 절전·로그아웃 중 계속 실행되는 서버는 아니다.
- 대기열이 빈 ready 상태에서 MOA 워커만 SIGTERM으로 정상 종료시켜 KeepAlive 자동 재기동을 실제 확인했다.
  PID 3328 → 5616, 같은 코드 지문으로 준비 ACK 복귀. NAS 배포 후 빈 claim 204도 확인했다.
- 장시간 운용·실제 재부팅·절전 복귀는 아직 시험하지 않았다. 기존 완료 합성 작업은 done/fence2 그대로이며 재발행하지 않았다.
- 새 계약/결과 manifest/보고서를 NAS와 Book에 직접 전달하고 Book의 실제 파일·실행기·기준 모델 해시를 대조했다.
  `BOOK_CONTINUATION_HANDOFF_RECEIPT.json`에 20건/388작업/학습 완료/ready/승격 없음의 수신 확인을 남겼다.
  공통 HANDOFF_READY SHA-256 `7f59cbb12dc9fc15a38cc2bd3e0af9edfaaafb5b06c6d5a24b34e047cf600569`.
  사용자가 맥북 Codex에 프롬프트를 반복 복사할 필요는 없다.

## 웹 접속·지원 범위

접속: <https://moa.h19h19.com>. 기존 암호 유지, 검수자 이름 입력 없음.
첫 화면의 하루 최대 3건에서 원본/모바일을 비교하고 **승인** 또는 **보류**를 고르면 된다.
미확인 보류를 오답으로 쓰지 않으며 Codex는 실제 자료를 승인하지 않았다.

원본·후보·HTML·ZIP은 기존 인증을 유지한다. HWP 원본 자체는 내려받아 전용 프로그램으로 연다.
최종 배포 후 로그인·현재 버전·검증 결과 25/25, 12/12, 15/15 구역 표시와 승인/보류 버튼을 실제 브라우저에서 확인했다.
검수자 이름 필드 0, 실자료 승인/보류 제출 0. 비로그인 notice/원본/preview/HTML/ZIP/handoff는 각각 401이다.
지원 경로는 HWP/HTML이며 HWPX·DOCX·텍스트 PDF 파서도 기존 코드에 있지만 이번 20건 실물 종단 검증과 구분한다.
이미지·스캔 PDF·암호화 HWP·불명확한 도형/표 관계·미제공 뒷페이지는 자동 완료로 처리하지 않는다.
자동검증 완료는 사람 승인·학습 정답·자동 공개와 다르다. 전 문서 최종 상태는 `needs_review`다.

## 보존·배포·테스트

- 실제 `/volume2/moa/app`, `/volume2/moa/data`만 사용했다.
- DB 온라인 백업 `deploy-backups/continue-20261018T160000/before.sqlite3`,
  추가 배포 전 `before-masthead.sqlite3`: 각각 quick_check=ok.
  `app-before.tar.gz`와 `.env` 권한/바이트, rollback image도 보존했다. 경로의 시간 문자열은 백업 라벨이다.
- review만 교체했다. collector는 이미지 `9b3978df5a02…`, 시작 `2026-10-17T11:40:57Z`, healthy 그대로다.
  일일 목표 서울50/경기50/나머지 각20, 기존 백필·예산·04:00 설정을 변경하지 않았다.
- 배포 후 DB quick_check=ok, core user_version=5. 현재 notices 6,581건 읽기 및 기존 collector 정상 운용 확인.
  실행 Python 모듈 26개는 작업본과 바이트 해시 일치. 기존 Mac ._ 메타파일은 정리하지 않고 보존했다.
- NAS review image `479217802c4a285c9f23b50e36683cc22c63a6a14b9aec892bdb71fafcaf990d`.
- 기능 커밋: `299dcc2`, `d65477d`, `8b301ad`, `5081f69`, `5a5b5f6`.
  브랜치 `codex/moa-autoqa-v1`, [PR #1](https://github.com/h19h29-design/moa/pull/1). 강제 push/main 병합 없음.
- 전체 회귀 **243개 통과**. compileall, 두 JS syntax, install-nas.sh 구문, git diff 검사 통과.
  새 회귀는 RED를 확인한 뒤 수정해 재실행했다. 합성 단위 테스트 수와 실제 문서 수는 별개다.
- 이번 운영 외부 유료 AI·Muse/JEV 호출·자동 충전 0회. Codex 자체 계정/전력 비용 총액을 뜻하지 않는다.
  브라우저·보안·완료 전 검증 스킬에 따라 실제 DOM/다운로드/인증/저장 증거를 검사했다.

## 확인·일시정지·재개

Mini에서 현재 문서 단위 상태:

```sh
ssh ds925-home 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec app-review-1 python -m moa.pilot --data-dir /data status'
```

맥북에서 워커 잠시 멈춤/재개 (이미 받은 작업 경계에서 적용):

```sh
MOA_RUNTIME="$HOME/Library/Application Support/MOA-LAYA"
cd "$MOA_RUNTIME/app"
"$MOA_RUNTIME/venv-smoke/bin/python" -m workers.laya_local.control pause
"$MOA_RUNTIME/venv-smoke/bin/python" -m workers.laya_local.control resume
```

토큰·암호를 복사하거나 연결을 처음부터 재설치할 필요는 없다.
