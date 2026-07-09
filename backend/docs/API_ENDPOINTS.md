# Meminisse Backend API 명세서

Base URL(로컬): `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs` (모든 엔드포인트를 여기서 직접 호출·확인 가능)

프롬프트 튜닝 전용 샌드박스(`/api/v1/sandbox/*`) 사용법은 이 문서가 아니라
**[SANDBOX_GUIDE.md](./SANDBOX_GUIDE.md)** 를 참고할 것. 이 문서는 실제 서비스 API만 다룬다.

## 공통 사항

- **인증: 아직 없음.** 이 프로젝트 어떤 라우터에도 인증 의존성(OAuth2/APIKey Security)이
  걸려 있지 않다. 즉 `/docs`의 모든 엔드포인트는 이미 별도 로그인/토큰 없이 바로 호출된다.
- **DI**: DB/S3가 필요한 엔드포인트는 전부 `GatewaysDep`(`app/gateways/factory.py`)을 통해
  주입받는다. `.env`의 `GATEWAY_BACKEND=mock|postgres`로 실제 구현체가 바뀐다.
- **Upstage API 오류 처리**: Solar/Embeddings 호출이 실패하면(잘못된 키, 요금 초과, 모델
  비호환 등) `app/main.py`에 등록된 전역 핸들러가 원본 트레이스백 대신
  `{"detail": "Upstage API 오류: ..."}` 형태의 JSON을 Upstage가 반환한 HTTP 상태 코드
  그대로 돌려준다.
- **비동기 트리거(202)**: Phase 3/4의 무거운 연산(consolidate/write/finalize)은 Celery
  워커에 위임하고 즉시 202를 반환한다. 완료 여부는 해당 리소스의 GET 엔드포인트를
  폴링해 `status` 필드 변화로 확인한다.

---

## 1. Users — `/api/v1/users`

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/api/v1/users` | 사용자 생성. 이메일 중복 시 409 |
| GET | `/api/v1/users/{user_id}` | 사용자 조회. 없으면 404 |
| POST | `/api/v1/users/{user_id}/consents` | 동의 기록 생성 (기획안 5절 동의 주체 분리) |
| GET | `/api/v1/users/{user_id}/consents` | 이 사용자의 동의 기록 전체 조회 |

**UserCreate**: `email`(EmailStr), `name`(str), `birth_year`(int, optional), `hometown`(str, optional)
**UserRead**: 위 필드 + `id`, `current_stage`(`onboarding|interview|publishing|published`)
**ConsentCreate**: `consent_type`(`data_collection|disclosure_realname|retention_extension`), `notice_version`(str), `granted_by`(`self|guardian`)
**ConsentRead**: `id, user_id, consent_type, notice_version, granted_by, granted_at, revoked_at`

`disclosure_realname` 동의는 나중에 4절 `retain-real-name` 엔드포인트의 필수 선행 조건이다.

---

## 2. Interview Sessions — `/api/v1/interview-sessions`

기획안의 인터뷰 페르소나·슬롯 게이팅·꼬리 질문 로직이 실제로 실행되는 곳
(`app/services/interview_service.py`, 프롬프트는 `app/agents/prompts.py`).

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/api/v1/interview-sessions` | 세션 생성 (`session_type`: `photo` \| `fixed_question`) |
| GET | `/api/v1/interview-sessions/{session_id}` | 세션 조회. 없으면 404 |
| POST | `/api/v1/interview-sessions/{session_id}/messages` | 사용자 발화 전송 → 에이전트 응답 생성 |
| POST | `/api/v1/interview-sessions/{session_id}/complete` | 세션 종료 (Celery 후처리 트리거) |

**SessionCreate**: `user_id`, `session_type`, `question_id`(optional), `linked_media_asset_id`(optional)
**SessionRead**: `id`, `user_id`, `session_type`, `question_id`, `linked_media_asset_id`, `status`(`open|completed|skipped`), `slots_filled`(dict[str,bool]), `followup_count`, `is_must_include`, `started_at`, `completed_at`
**ChatMessageCreate**: `content`(str)
**TurnResponse**: `user_message`, `assistant_message`(둘 다 `ChatMessageRead`: `id,session_id,role,content,turn_index,created_at`), `session`(갱신된 `SessionRead`)

세션 `complete` 호출 시 Celery 태스크(`app/workers/tasks.py`)가 비동기로
`event_extraction_service.process_completed_session`을 실행 — 산문 재조립 → (자리표시자) 왜곡
탐지 → 이벤트 분할·라벨 추출(Structured Outputs) → 임베딩 → `verified=true` 저장까지 이어진다.

---

## 3. Media Assets — `/api/v1/media-assets`

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/api/v1/media-assets` | 미디어 원본 업로드 (multipart/form-data) → S3 저장 |

**요청 (Form fields)**: `file`(업로드 파일), `user_id`, `session_id`(optional), `asset_type`(기본 `image`), `age_at_time`, `location_at_time`, `people_at_time`, `user_comment`
**MediaAssetRead**: `id, user_id, session_id, s3_url, asset_type, age_at_time, location_at_time, people_at_time, life_period_mapped, analysis_track, user_comment, created_at`

업로드된 이미지는 즉시 듀얼 트랙(`media_service.py`)으로 분석된다 — 텍스트가 유의미하게
검출되면 Document Parse → Solar 1차 타당성 검증을 거쳐 `verified=false` Event로 스테이징되고,
아니면 순수 추억 사진(`pure_memory`)으로 분류된다.

---

## 4. Autobiographies — `/api/v1/autobiographies`

Phase 3(이벤트 병합·중요도 산정·스타일 바이블)과 Phase 4(동적 목차·하향식 집필·팩트체크·
근거검증·등장인물 검토)가 이 리소스 아래에 모여 있다(`app/services/autobiography_service.py`,
`app/services/character_service.py`).

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/api/v1/autobiographies/{user_id}` | 자서전 조회. 없으면 자동 생성(`get_or_create`) |
| POST | `/api/v1/autobiographies/{user_id}/consolidate` | **202.** Phase 3 트리거(이벤트 병합·중요도 산정·스타일 바이블) |
| POST | `/api/v1/autobiographies/{autobiography_id}/toc/generate` | 목차 후보 3안 생성(동기 호출, LLM 1회) |
| POST | `/api/v1/autobiographies/{autobiography_id}/toc/select` | 목차 후보 확정 → 챕터 초안 생성 + 책 시놉시스 |
| GET | `/api/v1/autobiographies/{autobiography_id}/chapters` | 챕터 초안 목록 조회 |
| POST | `/api/v1/autobiographies/{autobiography_id}/chapters/{chapter_draft_id}/write` | **202.** 챕터 단위 하향식 집필 트리거 |
| POST | `/api/v1/autobiographies/{autobiography_id}/finalize` | **202.** 전 챕터 완료 후 통일성 윤문 패스 트리거 |
| GET | `/api/v1/autobiographies/{autobiography_id}/characters` | 등장인물(제3자) 목록 조회 |
| POST | `/api/v1/autobiographies/{autobiography_id}/characters/{character_id}/retain-real-name` | 전수 가명화 opt-out — 실명 유지 전환 |

**AutobiographyRead**: `id, user_id, title, status(in_progress|consolidated|published), toc_data, style_bible, book_synopsis, final_content, created_at, updated_at`
**TocCandidateSelect**: `candidate_index`(int)
**ChapterDraftRead**: `id, autobiography_id, chapter_index, title, chapter_synopsis, content, source_event_ids, factcheck_report, groundedness_report, status(draft|reviewed|finalized), created_at, updated_at`
**CharacterRead**: `id, autobiography_id, display_name, real_name, relation_to_user, risk_classification(none|negative_portrayal|conflict|crime_mention), real_name_retained, disclosure_notice_version, disclosure_acknowledged_at, created_at`
**RetainRealNameRequest**: `notice_version`(str)

**진행 순서**: `complete`(세션 종료) 반복 → `consolidate`(Phase 3) → `toc/generate` → `toc/select` →
챕터마다 `chapters/{id}/write` → 전 챕터 완료 후 `finalize`.

`retain-real-name`은 전수 가명화 기본값(opt-out)을 뒤집는 유일한 경로이며, 해당 사용자의
`disclosure_realname` 동의(1절 참조)가 없으면 409로 거부된다.

---

## 5. Health

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok"}` — DB/S3/Upstage 어느 것도 건드리지 않는 순수 liveness 체크 |

---

## 6. 샌드박스 — `/api/v1/sandbox/*`

`app/agents/prompts.py`의 모든 프롬프트를 DB 없이 즉시 테스트하는 무인증 개발자 도구.
전체 시나리오 목록과 사용법은 **[SANDBOX_GUIDE.md](./SANDBOX_GUIDE.md)** 참조.

---

## 부록: 발견한 Upstage 문서 자체 모순과 구현한 대안

`upstage_solar_api_docs.txt` 안에서 `response_format`(Structured Outputs) 지원 모델 범위가
**문서 자체적으로 세 군데가 서로 다르게** 적혀 있다:

1. 파라미터 표: "all solar models (solar-pro3 포함)"이 지원한다고 명시
2. Structured Outputs 예제 코드: `model="solar-pro3"`로 실제 동작하는 예시를 보여줌
3. `response_format` 필드 상세 설명: "only compatible with the `solar-pro-2` model"이라고 명시

이벤트 추출을 포함한 이 프로젝트의 핵심 파이프라인 전체(Phase 2~4 대부분)가 Structured
Outputs에 의존하므로, 실제 키로 검증할 때까지 기다리지 않고 **자동 폴백을 코드로 구현**했다
(`app/clients/solar.py::structured_completion`): 기본값인 `solar-pro3`로 먼저 시도하고,
Upstage가 `response_format` 관련 400 에러를 반환하면 `solar-pro2`로 1회 자동 재시도한다.
실제 `UPSTAGE_API_KEY`로 두 모델 다 확인해본 뒤, solar-pro3가 항상 성공한다면 이 폴백
분기는 영구히 실행되지 않는 죽은 코드가 되므로 그때 제거해도 무방하다 — 지금은 "모델
선택을 잘못 짚어서 파이프라인 전체가 막히는 상황"을 막기 위한 안전장치로 남겨두었다.

(참고: 임베딩 벡터 차원(`EMBEDDING_DIM` 4096 vs 1024) 문서 모순은 별개 사안으로,
`backend/app/gateways/README.md`에 이미 기록되어 있다 — DB 담당 팀원이 실제 스키마 확정
전에 확인해야 할 항목이라 그쪽 문서에 두었다.)
