# 전달 상태 — 2026-09-21 (NAS 실배포 완료)

## 포함된 것
수집기 Python 소스, Docker/Compose 설정, NAS 설치 스크립트, 44개 자동 테스트,
설치·운영 문서, 표 사례 축적 로직, 일일 스케줄러를 포함합니다.

## GitHub 반영 상태
대상: https://github.com/h19h29-design/moa (공개)
소스 전체를 기능별 커밋으로 `main`에 반영했습니다. 초기 `.gitignore` 커밋(e8f476a) 위에
저장소·fetcher·NEIS/어댑터·추출/사례·파이프라인·Docker/NAS·테스트·문서 커밋이 올라가 있습니다.
`.env`, API 키, 수집 원문, SQLite 실DB는 커밋하지 않습니다(원문·DB는 NAS 볼륨2에만 존재).

## NAS 설치 상태
- 위치: 소스 `/volume2/moa/app`, 데이터 `/volume2/moa/data` (볼륨2 실제 마운트 확인)
- 실행: `restart: unless-stopped` 컨테이너 1개(비root, 공개 포트 없음), 매일 한국시간 04:00
- 학교목록: NEIS 실키로 12,434개 학교 홈페이지 동기화(18개 교육청)
- 확인 명령: `sudo docker compose logs --tail=100 collector`, `sudo docker compose exec collector python -m moa status`

## 남은 운영 판단
교육청 robots/방화벽 정책 때문에 서울·경북 등 일부 지역은 목표를 채우지 못하고
`shortfall`로 기록됩니다(README 9절, VERIFICATION.md). 원문 재배포·외부 AI 전송·모델 학습은
기본 구현에 없으며, 검수는 `moa approve`로 사람이 승인해야만 쌓입니다.
