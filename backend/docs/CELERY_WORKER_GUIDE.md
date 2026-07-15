# Celery 워커 구축·실행 가이드

`app/workers/tasks.py`에 정의된 5개 비동기 태스크(세션 후처리, Phase 3 병합, 챕터 집필,
최종본 윤문, PDF 조판)는 API 서버가 아니라 **별도의 Celery 워커 프로세스**가 실행한다.
워커가 떠 있지 않으면 API는 태스크를 Redis 큐에 넣고 202를 즉시 반환하지만, 아무도 그
큐를 소비하지 않으므로 실제 작업은 영원히 실행되지 않는다(예: "대화 종료"는 성공하지만
이벤트가 추출되지 않고, "최종본 만들기"를 눌러도 `final_content`가 채워지지 않음).

이 문서는 로컬(Windows)에서 워커를 실제로 띄우고 검증하는 방법과, 상용 배포 시 참고할
내용을 정리한다. 2026-07-11~12에 실제로 Redis→워커→회원가입~최종본~PDF 실물 출판까지
전 구간을 라이브로 검증했고, 그 과정에서 발견한 버그들도 이미 코드에 수정 반영했다(6절
참고).

## 1. Redis(메시지 브로커) 준비 — Docker Compose

Celery는 태스크를 큐에 넣고 빼는 데 Redis를 쓴다(`app/workers/celery_app.py`,
`CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND`, 기본값 `redis://localhost:6379/0`,`/1`).

`backend/docker-compose.yml`에 Redis 서비스를 정의해뒀다. WSL에 개인적으로
`apt install redis-server`하는 방식도 가능은 하지만, 그건 이 컴퓨터에만 있는 워크어라운드라
팀원과 공유되지 않고 OS(Mac/Linux)마다 재현 방법이 달라진다 — Docker Compose는 버전이
고정되고(`redis:7-alpine`) 저장소에 커밋되어 있어 누구나 동일하게 띄울 수 있으므로 이 방식을
기본으로 한다.

```powershell
cd backend
docker compose up -d
docker compose exec redis redis-cli ping   # PONG이 나오면 성공
```

Docker Desktop이 설치되어 있지 않다면 `winget install Docker.DockerDesktop`으로 설치한 뒤
Docker Desktop 앱을 한 번 실행해 엔진을 띄워야 한다(최초 실행 시 라이선스 동의 필요할 수
있음). 컨테이너를 내리려면 `docker compose down`(데이터까지 지우려면
`docker compose down -v`).

## 2. 워커 실행

Celery 5.x의 기본 워커 풀(`prefork`)은 `os.fork()`를 쓰는데 Windows는 이를 지원하지 않는다
— Windows에서 바로 실행하면 `billiard`가 죽거나 태스크가 조용히 멈춘다. **반드시**
`--pool=solo`(또는 `--pool=threads`)를 지정해야 한다.

```powershell
cd backend
..\venv\Scripts\celery.exe -A app.workers.celery_app worker --loglevel=info --pool=solo
```

- `cd backend`가 중요하다 — `app/config.py`의 `env_file=".env"`는 **프로세스의 현재 작업
  디렉터리** 기준 상대 경로라서, 다른 위치에서 실행하면 `.env`를 못 찾아 `DATABASE_URL`
  등이 기본값(로컬 더미 Postgres)으로 떨어진다. uvicorn을 띄울 때와 동일한 위치에서
  실행할 것.
- `--pool=solo`는 동시성이 1이라 태스크가 순차 처리된다 — 로컬 개발/데모용으로는 충분하지만
  운영 환경에는 부적합하다(7번 참고). 실측 결과 `write_chapter` 하나가 **70~130초**
  걸린다(RAG 검색 + 본문 집필 + 팩트체크 + 근거검증 + 인물 스캔까지 Solar를 여러 번 호출하기
  때문) — 챕터 4개를 한 번에 큐잉하면 순차 처리로 5~8분 걸릴 수 있다는 뜻이니, 프론트에서
  폴링 타임아웃을 너무 짧게 잡지 말 것.
- 정상 기동 시 콘솔에 6개 태스크가 등록된 것이 보여야 한다:
  `process_session_completion`, `consolidate_autobiography`, `write_chapter`,
  `finalize_manuscript`, `generate_manuscript_pdf`, `analyze_media_asset`.
- `generate_manuscript_pdf`가 등록조차 안 되고 워커 시작 자체가 죽는다면 GTK3 런타임이
  없는 것이다(8절 참고) — `app/workers/tasks.py`가 모듈 최상단에서
  `from app.services import ... pdf_service`를 import하고, `pdf_service.py`가 다시
  `from weasyprint import HTML`을 import하므로, WeasyPrint import 자체가 실패하면 워커
  프로세스 전체가 못 뜬다(PDF 기능만 못 쓰는 게 아니라 6개 태스크 전부 등록 실패).

## 3. 정상 동작 확인

워커를 띄운 채로 별도 터미널에서:

```powershell
cd backend
..\venv\Scripts\celery.exe -A app.workers.celery_app inspect registered
..\venv\Scripts\celery.exe -A app.workers.celery_app inspect ping
```

`registered`에 위 6개 태스크가 보이고 `ping`이 `pong`을 반환하면 준비 완료다. 이제 백엔드
API(`uvicorn app.main:app`)가 별도 프로세스로 함께 떠 있어야 하며, 두 프로세스 모두 같은
`.env`(같은 `DATABASE_URL`/`CELERY_BROKER_URL`)를 봐야 한다.

## 4. API 호출 → 태스크 매핑

| API 엔드포인트 | 태스크 | 무엇을 하는가 |
| --- | --- | --- |
| `POST /interview-sessions/{id}/complete` | `process_session_completion` | 세션 산문 재조립 + 이벤트 분할·라벨 추출(Phase 2 후처리) |
| `POST /autobiographies/{user_id}/consolidate` | `consolidate_autobiography` | 중복 이벤트 병합 + 중요도 스코어링 + 스타일 바이블 생성(Phase 3) |
| `POST /autobiographies/{id}/chapters/{chapter_draft_id}/write` | `write_chapter` | 챕터 시놉시스·본문 집필 + 팩트체크 + 근거검증(Phase 4, 챕터 단위) |
| `POST /autobiographies/{id}/finalize` | `finalize_manuscript` | 전체 챕터를 합쳐 통일성 윤문 → `final_content` 채움 |
| `POST /autobiographies/{id}/pdf/generate` | `generate_manuscript_pdf` | Jinja2+WeasyPrint로 국판(A5) PDF 조판 → S3 업로드 → `pdf_url` 채움(Phase 5) |
| `POST /media-assets` (이미지 업로드 시 자동) | `analyze_media_asset` | Azure Vision(캡션 + 텍스트 인식) 동기 API 호출 + Solar 타당성 검증(Phase 1 듀얼 트랙 분석). 별도 엔드포인트가 아니라 업로드 응답을 반환한 직후 자동으로 큐잉된다 — `app/services/media_service.py` 상단 docstring 참조 |

`toc/generate`, `toc/select`는 Celery를 쓰지 않는다 — API 요청 안에서 동기적으로 Solar를
호출하고 바로 결과를 반환한다(워커 없이도 동작).

## 5. 전체 파이프라인을 처음부터 끝까지 테스트하는 순서

```
회원가입/로그인
  → 인터뷰 세션 진행 (POST /interview-sessions, .../messages)
  → 대화 종료 (POST .../complete)                → [워커] process_session_completion
  → (세션을 몇 개 더 반복)
  → 이야기 정리하기 (POST /autobiographies/{user_id}/consolidate) → [워커] consolidate_autobiography
  → 목차 만들기 (POST /autobiographies/{id}/toc/generate)          → (동기, 워커 불필요)
  → 목차 선택 (POST /autobiographies/{id}/toc/select)              → (동기, 워커 불필요)
  → 각 장 집필 시작 (POST .../chapters/{chapter_draft_id}/write ×N) → [워커] write_chapter
  → 최종본 만들기 (POST /autobiographies/{id}/finalize)            → [워커] finalize_manuscript
  → 책으로 만들기 (POST /autobiographies/{id}/pdf/generate)        → [워커] generate_manuscript_pdf
```

`/dashboard/autobiography` 화면이 이 순서를 그대로 따라간다 — "이야기 정리하기"와 "책으로
만들기" 두 단계 모두 다른 폴링 단계들과 동일한 패턴(202 큐잉 → 자동 갱신)으로 구현돼 있다.
이벤트가 하나도 없는 상태에서 "이야기 정리하기"를 누르면 실질적으로 할 게 없어 바로 "목차
만들기" 단계로 넘어가고, 거기서 이벤트가 없으면 409로 "대화를 조금 더 나눠주세요" 안내가
뜬다.

## 6. 실제 검증 중 발견해 고친 버그 3건

1. **`torch`/`transformers`/`sentencepiece`가 venv에 실제로 설치돼 있지 않았음.**
   `requirements.txt`에는 있었지만 아무도 `pip install -r requirements.txt`를 재실행하지
   않아, `app/clients/nli.py`가 지연 임포트하는 `torch`가 워커에서 처음 호출되는 순간에야
   `ModuleNotFoundError`로 죽었다(FastAPI 부팅 시점엔 안 걸림 — 지연 임포트라). 게다가 이
   프로젝트 경로가 매우 길어(`...\meminisse-repository\venv\...`) torch의 라이선스 파일
   경로와 합쳐지면 Windows MAX_PATH(260자)를 넘어서 설치 자체가 중간에 깨지는 문제도 있었다
   — Windows 긴 경로 지원(`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled=1`,
   관리자 권한 필요)을 켜서 해결했다. 새로 이 프로젝트를 세팅하는 사람은 긴 경로 지원을 먼저
   켜고 `pip install -r requirements.txt`를 돌릴 것.
2. **워커가 두 번째 태스크부터 `RuntimeError: Event loop is closed`로 죽던 문제.**
   `app/database.py`의 SQLAlchemy 엔진(연결 풀)은 프로세스 전역 싱글턴인데, 각 Celery
   태스크는 `asyncio.run()`으로 매번 새 이벤트 루프를 만들고 끝나면 그 루프를 닫는다. 풀에
   반납된 asyncpg 커넥션이 첫 태스크의 (이미 닫힌) 루프에 묶여 있어서, 두 번째 태스크가 새
   루프에서 그 커넥션을 재사용하려는 순간 죽었다. `app/workers/tasks.py`에 `_run()` 헬퍼를
   추가해 각 태스크가 끝날 때(성공/실패 무관) 같은 루프 안에서 `engine.dispose()`로 풀을
   비우도록 고쳤다 — 다음 태스크는 새 루프에서 커넥션을 처음부터 새로 연다.
3. **PDF 조판 QA가 실제로는 임베딩된 폰트를 "임베딩 안 됨"으로 오판.** 한글처럼 문자
   수가 많은 폰트는 WeasyPrint가 `Type0`(CID 합성) 폰트로 내보내는데, 이 경우
   `FontDescriptor`가 폰트 객체 최상위가 아니라 `DescendantFonts[0]` 안에 있다.
   `_run_pdf_qa`가 처음엔 최상위만 확인해 실제로는 정상 임베딩된 PDF를 QA 경고로
   잘못 표시했다(실제 생성된 PDF를 pypdf로 직접 열어 폰트 딕셔너리 구조를 확인하고
   재현, 2026-07-12). `Type0`이면 `DescendantFonts`를 한 단계 더 따라가도록 고쳤다.

## 7. 로그·디버깅

- 워커를 띄운 터미널에 각 태스크의 실행/완료/예외가 그대로 로그로 찍힌다 — 태스크가 실패하면
  거기서 전체 스택트레이스를 볼 수 있다.
- 현재 태스크들은 재시도 정책(`autoretry_for`, `max_retries`)이 없다 — 한 번 실패하면 그대로
  끝난다. Solar/Supabase 일시 장애로 실패했다면 해당 API를 다시 호출해 태스크를 재큐잉해야
  한다(예: `write_chapter`를 다시 POST).
- 모니터링 UI가 필요하면 Flower를 추가로 설치해서 쓸 수 있다(선택, 이 저장소엔 아직
  미포함):
  ```powershell
  ..\venv\Scripts\pip.exe install flower
  ..\venv\Scripts\celery.exe -A app.workers.celery_app flower
  ```
  `http://localhost:5555`에서 큐 상태·태스크 이력을 볼 수 있다.

## 8. GTK3 런타임(WeasyPrint 전용 환경 의존성)

`generate_manuscript_pdf` 태스크(PDF 조판)는 WeasyPrint를 쓰는데, WeasyPrint는
`pip install`만으로 Windows에서 동작하지 않는다 — pango/gobject 등 네이티브 라이브러리를
CFFI로 로드하는데 이게 시스템에 없으면 **import 시점에** `OSError: cannot load library
'libgobject-2.0-0'`로 죽는다. `app/workers/tasks.py`가 모듈 최상단에서 `pdf_service`를
import하므로, 이 라이브러리가 없으면 PDF 기능만 빠지는 게 아니라 **워커 프로세스 전체가
못 뜬다**(5개 태스크 전부).

```powershell
winget install tschoonj.GTKForWindows
```

설치 후 `C:\Program Files\GTK3-Runtime Win64\bin`이 PATH에 들어간다. **주의**: 이미 열려
있는 터미널/셸은 이 PATH 변경을 자동으로 반영하지 않는다 — 새 터미널을 열거나, 그 세션
안에서만 임시로 PATH에 추가해야 한다:

```powershell
$env:Path = "C:\Program Files\GTK3-Runtime Win64\bin;" + $env:Path
```

이 PATH는 **백엔드 API 서버(uvicorn)와 Celery 워커 둘 다**에 필요하다 — API 서버도
`/pdf/generate` 엔드포인트가 (지연 임포트로) `app.workers.tasks`를 import하는 순간 같은
체인을 타기 때문이다. 리눅스 배포 환경에서는 `apt install libpango-1.0-0
libpangoft2-1.0-0 libgdk-pixbuf2.0-0` 등으로 대체한다(Windows 전용 우회책이 아니라 모든
환경에 필요한 시스템 의존성이며, Windows만 별도 러너 설치가 필요한 것).

## 9. 상용 배포 시 참고 (지금 당장 할 필요는 없음)

- 리눅스 배포 환경에서는 `--pool=solo`를 빼고 기본 `prefork`(또는 `--concurrency=N`)를 쓸 수
  있다 — `--pool=solo`는 Windows 로컬 개발 한정 우회책이다.
- 워커는 API 서버와 별도 프로세스/컨테이너로 배포하고 systemd나 supervisor 등으로 상시
  기동·재시작을 관리한다.
- `write_chapter`/`consolidate_autobiography`는 Solar 호출이 여러 번 겹치는 무거운
  작업이므로, 동시에 여러 유저가 몰릴 상황을 가정하면 워커 concurrency와 Upstage API
  레이트리밋을 함께 고려해야 한다.
- 주기적으로 실행해야 하는 작업(Celery Beat)은 현재 없다 — 4개 태스크 모두 API 요청에 의해
  1회성으로 큐잉되는 구조라 Beat 스케줄러는 필요 없다.
