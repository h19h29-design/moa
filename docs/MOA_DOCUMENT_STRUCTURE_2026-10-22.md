# 학교 문서 구조 분석 2단계

2026-10-22. 기존 학교별 수신 기반 위에 **로컬 구조 분석과 비공개 근거 조회**를 구현했다.
맥미니 외장 SSD에서만 개발·시험했다. NAS 접속/DB 적용/서비스 재배포/원본 변경,
맥북 학습, 모델 추론·유료 API, 운영 모델 교체는 하지 않았다.

## 저장 위치와 보호

- 작업본: `/Volumes/KIOXIA-2TB/Projects/Relocated-20261017/work/moa-push`.
- 실행 자료: `/Volumes/KIOXIA-2TB/Cache/MOA/school-structure-20261022`.
- `dev-ssd check`와 기존 `dev-storage run`으로 UUID
  `961CA3AE-AE5E-4C5C-B78C-84474F4C8DD8`, 실제 외장 APFS 마운트,
  소유권/쓰기 가능·개별 검증 작업본·연결/SMART 보호 기준을 확인했다. 여유 약 1.4TiB.
- 전역 `development_ready=false`, `physical_stability_resolved=false`는 변경하지 않았다.
  과거 분리 이력이 영구 해결됐다는 뜻이 아니다. 정책이 허용한 기존 개별 검증 작업본만 사용하며
  UUID/마운트/I/O 보호 실패 시 내부 폴더로 우회하지 않는다. 독립 백업 필요 경고도 유지한다.
- 기존 `.venv312`와 설치된 파서/시험 도구를 재사용했다. 패키지 재설치·모델 다운로드 없음.
  TMPDIR, XDG, Python 바이트코드, pytest 캐시와 격리 SQLite DB는 모두 위 실행 자료 안에 둔다.
- NAS는 이번 단계에서 연결하지 않았다. `/volume2/moa/app`, `/volume2/moa/data`,
  `/volume2/moa/data/db/moa.sqlite3`에 대한 쓰기·마이그레이션·배포 명령을 실행하지 않았다.
  기존 운영 수집기의 일상적인 변화까지 없었다는 주장은 하지 않는다.
- 기존 `codex/moa-autoqa-v1` / PR #1을 이어간다. 기준 HEAD는
  `93145b86f2602c3dedc9565edd17b88a4f9fd79c`이며 기존 미추적 가상환경/문서 4항목을 보존한다.

## 구현과 DB 계약

학교 전용 `moa.document_structure`가 모든 안내/원본 첨부를 분석한다.
파일 역할을 바꾸거나, 원본을 합쳐 덮어쓰거나, 사람이 승인한 것으로 만들지 않는다.
학교 수신본의 기존 `school_convert` 작업에서 HTML 변환과 함께 분석하고,
기존 불변 `mobile_revisions.data`의 **`structure` 필드**에 저장한다.

새 DB 테이블/마이그레이션은 없다. 기존 core `user_version=5`, 학교 스키마 버전 1을 유지한다.
정정 수신본과 재변환 revision은 각각 자기 SHA·파일 목록·분석을 보유한다.
저장 직전 기존 권한/작업 점유/기준 revision 재검사를 거치며 연구용 큐·학습 export를 호출하지 않는다.

기존 연구 파서 `moa-local-v8-native-controls`와 그 캐시 형식은 수정하지 않았다.
학교 분석 버전은 **`moa-school-structure-v1`**, 별도 캐시는 기존 데이터 root의
`extracted/<원본 SHA>-moa-school-structure-v1-<형식>.json`이다.
원본 SHA와 캐시 버전/구조를 검사하며, 손상 캐시는 다시 분석하고 원본 손상은 안전한 실패로 기록한다.
분석 캐시는 학교 간 권한을 부여하는 경로가 아니다. 조회는 반드시 학교/수신본 소유권을 거친다.

기존 revision에 분석이 없으면 조회만으로 재변환하거나 새 작업을 발행하지 않는다.
담당자가 기존 변환 버튼으로 명시 재변환할 때 새 revision에 분석이 생긴다.

## 구조 근거 계약

`structure`는 다음 정보를 갖는다. 텍스트/위치는 원문 근거이며 자동 정답/승인이 아니다.

| 필드 | 의미 |
|---|---|
| version / method | 분석 버전 / `local_rules_and_native_inventory` |
| human_approved | 항상 false. 기존 사람 검수 기록을 만들지 않음 |
| files | 파일 ID·이름·SHA·수신 역할·처리 상태·확인 가능한 구역 범위·블록 수 |
| blocks | 파일별 헤더/제목/본문/푸터/각주/표, 텍스트·원본 위치·관찰 근거·확실성 |
| relations | 수신 역할, 헤더의 소속, 표 머리글, 명시 각주 참조, 구역 내 표의 소속, 첨부 참조 |
| warnings / complete | 미확정·누락 경고 / 관찰 결과에 경고가 없는지. 의미 정확도나 검수 완료를 뜻하지 않음 |

블록 ID는 `<파일 ID>/bN`, 파일 노드는 `file:<파일 ID>`다.
`locator`는 실제 관찰한 HTML 경로, XML part/요소 경로/문단 segment,
PDF page/line/표 bbox, 또는 HWP 원파서 block/table index만 기록한다.
원본에서 확인하지 못한 페이지·각주 소속을 만들어 넣지 않는다.

- `observed`: 텍스트/표와 그 순서 관찰.
- `explicit`: HTML header/각주 markup, HWPX native control, DOCX에서 실제 참조한 part/note 등.
- `candidate`: 학교명 머리말, 반복되는 PDF 첫 줄, ※ 등의 각주 후보, 의미 라벨 기반 표 머리글,
  파일명/붙임 제목이 일치한 첨부 후보. 사람 확인이 필요하다.

### 각주와 여러 첨부의 관계

- `footnote_for`는 **참조한 본문/표 블록 → 같은 파일의 각주 블록**이다.
  HTML 내부 anchor, HWPX inline note control, DOCX note ID가 유일하게 연결될 때만 확정한다.
  다른 첨부의 동명 note ID를 빌려 연결하지 않는다.
- 참조 없는 ※ 문구를 가장 가까운 표에 자동 귀속하지 않는다.
  누락/중복 note anchor와 미연결 각주는 경고로 남긴다.
- 표로만 구성된 각주도 빈 각주 컨테이너와 별도 표를 보존하며 `part_of`로 연결한다.
  표 텍스트를 본문/상위 표/각주 컨테이너에 중복해서 넣지 않는다.
- 본문과 첨부는 하나의 기존 수신본에 속하지만, 원본 파일을 하나로 평탄화하지 않는다.
  `guide_of`/`attachment_of`는 담당자가 정한 수신 역할의 관찰이다.
- `attachment_reference`는 붙임/첨부 제목 또는 상대 파일 링크가
  **같은 수신본의 정규화 파일명에 유일하게 일치**한 후보만 기록한다.
  정규화는 공백/문장부호/확장자/붙임 번호/`1부` 표기 등 제한된 처리를 한다.
  업로드 순서로 붙임 번호를 추측하거나 유사 문자열 검색으로 강제 연결하지 않는다.
  파일 누락과 같은 이름의 여러 파일은 미확정으로 남긴다. 외부 URL은 방문하지 않는다.
- 표는 셀 좌표/병합 범위/텍스트를 보존한다. HTML `th`와 DOCX의 네이티브
  `w:tblHeader`(표의 연속된 앞쪽 머리글 행)는 명시 머리글이며
  의미 라벨이 맞는 첫 행은 후보다. 숫자 데이터 행을 단지 첫 행이라는 이유로 머리글로 만들지 않는다.
  DOCX의 비연속 머리글 표시·불명확한 병합은 경고로 남긴다.
  [DOCX 머리글 행 속성의 공식 설명](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.tableheader?view=openxml-3.0.1)을 기준으로 한다.
  중첩 표와 문단 내 표의 원본 순서를 유지하고 셀 본문/각주를 별도 관찰한다.

## 형식별 분석 범위

| 형식 | 확인하는 구역·위치 | 유지하는 한계 |
|---|---|---|
| HTML | header/footer/heading/명시 각주·anchor, DOM 위치, 표·셀 | class/CSS 시각 배치를 렌더링해 확정하지 않음. 문서 코드/스크립트 실행 없음 |
| HWPX | native header/footer/footNote/endNote, section XML 경로, 문단/표 순서·표 안의 각주 | 렌더링 페이지 번호/반복 헤더의 실제 페이지 범위는 미확정 |
| DOCX | 본문 순서, 문서가 참조한 header/footer part·footnote/endnote ID와 내부 표, 네이티브 표 머리글 행 | 참조하지 않은 orphan part 제외. 실제 페이지/구역별 렌더링 소속은 미확정 |
| 텍스트 PDF | 실제 page/line, 반복 첫 줄 후보, 표 bbox | 네이티브 각주 구역 없음. 표와 페이지 텍스트가 겹칠 수 있음을 coverage에 표시 |
| 이진 HWP | 기존 파서의 본문/표·block index, 텍스트 후보 | 네이티브 헤더/각주 inventory를 확보했다고 주장하지 않고 경고 유지 |
| 이미지·지원 외 형식 | 파일 존재·SHA·역할 | `needs_vision`/`needs_parser`, 미확정. OCR/모델 실행 없음 |

파일당 25MiB, 기존 업로드 묶음 50MiB/10파일 제한을 유지한다.
기존 ZIP/XML 폭탄/엔티티 방어를 재사용하며 파서는 별도 프로세스에서
55초 timeout/CPU 40초/출력 파일 제한을 적용한다(Linux만 기존 AS 700MiB 제한도 적용).
묶음 분석 블록 2,000개/관계 4,000개/경고 2,000개/JSON 6MiB 제한을 생성 중에 검사한다.
관계 중복 확인은 집합을 사용하고, 각 항목의 직렬화 크기를 누적 계산해 한도 초과 후 남은 항목을 처리하지 않는다.
초과 시 조용히 잘라 성공 처리하지 않고 명시 미확정으로 남긴다.
형식별 원본과 파일 목록은 보존한다. 하위 파서는 허용된 환경 변수와 외장 캐시 경로만 상속한다.

## 비공개 조회

기존 학교 담당자 서버의 수신 상세에 **문서 구조 분석 · 미검수** 링크를 추가했다.

- `GET /structure?id=<학교 수신 버전>`: 현재 분석의 파일·구역·원본 위치·관계·경고.
- `GET /structure?id=...&revision=<변환 revision>`: 그 수신본에 속한 과거 분석.
- `GET /structure?id=...&revision=...&format=json`: 동일한 근거 JSON.
- 응답에 `notice_version`, `conversion_revision`을 함께 명시한다.
- 기존 학교 로그인·멤버십 검사, reader 조회, 권한 회수 즉시 반영을 유지한다.
  비로그인 401, 다른 학교/무관 revision 404, 응답은 private/no-store다.
  파일명/텍스트/관계는 escape하고 원본 HTML을 조회 화면에서 실행하지 않는다.
- 연구 관리자 bearer/공개 SHA 경로로 학교 분석을 노출하는 경로는 추가하지 않았다.

## 실제 검증

- 변경 전 전체: **1,259 passed, 16 skipped**.
- 신규 분석·학교 통합·실제 형식 경로 집중 시험: **57 passed**.
- 변경 후 전체: **1,313 passed, 16 skipped**, 45.35초.
  skip은 기존 선택적 Torch 학습 환경 관련 16개다. 이를 위해 모델 환경을 설치하지 않았다.
- `python -m compileall -q moa`, `sh -n scripts/install-nas.sh`, `git diff --check` 종료 코드 0.
- 헤더/각주 분리, 표·본문 순서, 표 안 각주, 중첩 표, 빈 표, orphan part 제외,
  파일명 중복/누락/업로드 순서, 파일별 note ID 격리, 실제 PDF 페이지, 빈/이미지 문서 미확정을 시험했다.
- 캐시 재사용·손상 복구·원본 SHA 불일치·timeout·XML 엔티티·블록/관계/경고/점진적 바이트 한도,
  구버전 조회의 무변경, 연구 경로 미실행, 학교 A/B·reader·계정 회수·XSS 방어를 확인했다.
- 독립 읽기 전용 검토에서 발견한 네이티브 표/각주 누락을 RED→GREEN으로 수정하고 재검토를 통과했다.
  HTML 셀·헤더 안의 명시 각주, DOCX 콘텐츠 컨트롤로 감싼 행·셀까지 포함한다.
- 실제 학교 HTTP 업로드 → 역할 → 변환/구조 분석 → 구조 HTML/JSON → 원본 ZIP까지 시험했다.

| 형식 | 시험 자료 | 분석 블록 | 구조/HTML/ZIP HTTP | 원본 SHA | 확인된 범위 |
|---|---|---:|---|---|---|
| HWP | 기존 취득 비공개 원본 | 34 | 200 / 200 / 200 | 일치 | 본문/표, native zones 미확정 |
| HWPX | 합성 header/inline footnote 포함 | 4 | 200 / 200 / 200 | 일치 | 명시 헤더·각주 참조 |
| DOCX | 합성 referenced header/footnote 포함 | 4 | 200 / 200 / 200 | 일치 | 실제 참조한 part/note |
| 텍스트 PDF | 합성 2페이지/반복 머리말/각주 후보 | 6 | 200 / 200 / 200 | 일치 | 실제 1·2페이지, 후보 표시 |

위 네 형식 시험은 지원 외 텍스트 신청서도 함께 묶었다. 그 첨부를 읽었다고 허위로 처리하지 않아
묶음의 `complete=false`가 의도적으로 유지된다. 이는 기존 변환·다운로드의 성공과 구분한다.
이 결과는 새 블라인드·실물 정답률/정상 오탐률·학습 성적·사람 승인 검증이 아니다.

## 파일별 외장 SSD 위치

기존 작업본 아래 코드/테스트/설명서만 Git에 반영한다.

- `moa/document_structure.py`: 학교 전용 순수 파싱·관계 분석·별도 캐시/워커.
- `moa/school_service.py`: 학교 변환과 revision 조회 연결.
- `moa/web_school.py`: 인증된 구조 HTML/JSON 조회.
- `tests/test_document_structure.py`, `tests/test_school_structure.py`: 합성 구조·권한·보존·실패 시험.
- `tests/test_school_formats.py`: 기존 변환 HTTP 시험에 네 형식 구조 분석 검증 추가.
- `docs/MOA_DOCUMENT_STRUCTURE_2026-10-22.md`, `README.md`: 계약·증거·한계.

Git 밖 실행 자료는 `/Volumes/KIOXIA-2TB/Cache/MOA/school-structure-20261022` 아래다.

- `pytest/`: 격리 DB·마이그레이션 사전 백업·합성 업로드·변환 revision.
- `formats/{hwp,hwpx,pdf,docx}/`: original, candidate.html, originals.zip, structure.json, RESULT.json.
- `baseline-junit.xml`, `formats-junit.xml`, `full-junit.xml`, `final-full-junit.xml`,
  최종 `final-accepted-junit.xml`과 RED→GREEN 시험 XML.
- `tmp/`, `cache/`: 임시·pytest·Python 캐시. `PROGRESS.md`: 실행 경과.

원문·운영 DB·모델·인증·비공개 분석 JSON·실행 기록을 Git에 넣지 않는다.
단계 2 완료는 **관찰 가능한 구조와 미확정 관계를 안전하게 보존·조회하는 개발 기반**의 완료다.
문서 의미 검증·학교별 사람 검수 흐름 확장·실제 홈페이지 수신·운영 적용은 별도 범위다.
