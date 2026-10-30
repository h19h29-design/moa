# R2 입력·인계 규격 (시험 전용)

2026-10-19. 실행 기준: 사용자가 제공한 `00_R2_PLAN.md`와 R2용 `01_MAC_MINI_CODEX_PROMPT.md`.
이 문서는 데이터나 훈련 결과가 아니다. 운영 worker 계약과 운영 모델은 변경하지 않는다.

## 원천과 독립 정답

`python -m moa.r2_prepare prepare`는 비공개 `--topics`와 평가 제외 목록을 받는다.
원문 SHA·계보·의미 양식 식별자·근접 문구를 연결한 뒤 파티션을 정하고,
원문·HTML·분할을 `SOURCE_FREEZE.json`에 고정한다. `--frozen-splits`는 이미 고정된 배정을 유지한다.

합성 HWPX 구조 픽스처는 별도 XML reader로 읽고 기존 MOA 파서/렌더러로 변환한다.
`scripts/moa-r2-dom.cjs`는 외부 요청을 막은 Chrome에서 실제 DOM을 읽는다.
원문에서 없는 결과 값을 채우지 않으며, DOM에 변형을 적용하고 관찰한 결과를 기록한다.
독립 원문 사실과 기록된 연산을 대조한 뒤에만 `finalize`가 학습 행을 만든다.
검사기 verdict나 LAYA 답은 gold의 원천이 아니다.

현재 생성기는 소수의 구조 패턴을 사용한다. 주제 그룹 수는 실제 학교 원문 수나
독립적인 실물 양식 수가 아니다. 이 합성 묶음만으로 실문서 일반화 성능을 주장하지 않는다.
실물 HWP/HWPX/PDF 학습 원문은 별도 사용 허용 근거와 평가 중복 검사를 통과해야 한다.

## 모델 입력

입력 버전은 `moa.laya.fact-bundle.v1`이다. 전송 JSON의 정렬 순서와 모델 직렬화 순서는 다르다.
실제 설치된 worker의 다음 순서를 **훈련과 추론 모두**에 적용한다.

1. `title`
2. `file_role`
3. `source_complete`
4. `source_blocks`
5. `rendered_blocks`
6. `attachment_manifest`

사실 묶음에는 관련 사실·헤더·공통 단위·필수 조건·각주·병합·고유 키별 관계만 넣는다.
gold, split, 파일명, 변형 이름, 검사기 답, 모델 점수는 state와 질문에 넣지 않는다.
과거와 같은 문맥의 일반 텍스트 대조는 `legacy_state`에 별도로 보관한다.
이 대조는 새 dev의 같은 사실을 표현만 달리한 것이며 기존 31파일 재사용이 아니다.

질문은 기존 공통 질문의 부분집합이고 선택지/기본 gold index는
`preserved=0`, `changed=1`, `insufficient=2`다. 저장된 canonical JSON의
알파벳순 criteria를 곧바로 훈련기에 넘기지 말고 설치된 질문 정의로 복원한다.
옵션을 섞을 때도 label과 marker 대응을 검증한다.

`source_complete=false`는 제공 문맥의 불충분을 뜻한다. worker의 최종 insufficient 강제 처리와
**모델 원판정**은 다르다. 근거 부족 문항에서 이 강제 처리를 모델 적중으로 집계하지 않는다.
1024/256 token 경로를 유지하고, 필수 문맥이 초과하면 자르지 말고 context_overflow로 남긴다.

## 전송과 맥북 실행

`R2_READY.json`은 질문·formatter·생성기·분할·원문 동결·DOM 관찰·shard hash를 고정한다.
`R2_HANDOFF.json`은 전달 파일, R1 체크포인트 전체 파일과 설치 adapter hash를 고정한다.
shard는 최대 512 KiB다. **train/dev만 전달**하며, blind 원문·답·비공개 저작 seed는 미니에 둔다.
blind 내용은 후보/표현/설정 hash를 고정한 맥북 영수증을 받은 뒤 평가 작업에만 제공한다.

맥미니가 SSH로 실행할 수 있는 새 도구는 `moa-r2-preflight.py`의 tokenizer/CPU 검사뿐이다.
이 검사는 가중치를 로딩하지 않고 실제 설치된 `items_from_rows`, `serialize_state`,
`build_sequence`의 token IDs·markers·선택지·gold index를 비교한다.
미니는 S0 추론이나 S1/R2 훈련 프로세스를 시작하지 않는다.

실제 S0/S1/R2-A는 맥북 Codex 소유다. 새 row.v2용 loader는 train만 학습하고
dev는 개발에만 사용해야 한다. 구형 600행 최소 조건을 맞추려고 자료를 복제하거나 평가 자료를 넣지 않는다.
기존 맥북 실행 환경과 운영 standing worker를 덮어쓰지 않는다.

한도: S1 32문항/40 updates/600초, R2-A 한 후보/2 epochs/400 updates/2700초,
총 440 updates/3300초. 각 단계는 먼저 도달한 한도에서 중지한다.
microbatch 1/accumulation 8의 실제 optimizer updates와 epochs를 따로 센다.
새 optimizer/scheduler, encoder 동결, 기존 검증 loss/LR로 R1에서 시작한다.
50 updates 또는 300초의 작업 경계에서 저장하고 **저장 후 별도 프로세스 재로딩 평가**를 수행한다.
S0+최종 평가 총 2700초, 한 조각 최대 600초다. 임의 재시도/추가 후보/운영 승격은 금지한다.

## 성적표와 검수

새 dev/blind와 기존 145 회귀 시험을 섞지 않는다. R1 텍스트 대조/R1 facts/R2 raw/
직접 검사/근거 기반 결합을 각 입력·모델 hash와 함께 따로 기록한다.
전체 분모, 미완료, 3×3 혼동행렬, 오류별 분모, 위험 오판, 정상 오탐과 추가 검수 요청,
원문·양식 그룹 수, 모델이 추가한 실제 오류 검출을 기록한다.
모든 결과를 하나의 '문서 전체 정확도'로 합치지 않는다.

기존 대표 5건은 평가 전용이다. 사람에게 원문 전체·모바일 결과·안내/서식 경계를 보여주되
미니나 맥북이 대신 승인하지 않는다. 브라우저가 HWP 전체를 그리지 못하는 경우 원본 다운로드와
전용 뷰어가 필요함을 표시한다. 기존 LibreOffice 307페이지 실패물을 정답으로 사용하지 않는다.
