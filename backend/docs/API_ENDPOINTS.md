# Meminisse Backend API 명세서

Base URL(로컬): `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs` (모든 엔드포인트를 여기서 직접 호출·확인 가능)

## 공통 사항

- **인증: 아직 없음.** 이 프로젝트 어떤 라우터에도 인증 의존성(OAuth2/APIKey Security)이
  걸려 있지 않다. 즉 `/docs`의 모든 엔드포인트는 이미 별도 로그인/토큰 없이 바로 호출된다.
  나중에 실제 인증이 도입되면 `/sandbox/*`는 의도적으로 계속 무인증으로 남겨두는 것을
  권장한다(프롬프트 튜닝 도구는 사용자 데이터를 만들지 않으므로 인증 게이트가 필요 없다).
- **DI**: DB/S3가 필요한 엔드포인트는 전부 `GatewaysDep`(`app/gateways/factory.py`)을 통해
  주입받는다. `.env`의 `GATEWAY_BACKEND=mock|postgres`로 실제 구현체가 바뀐다.
- **Upstage API 오류 처리**: Solar/Embeddings 호출이 실패하면(잘못된 키, 요금 초과, 모델
  비호환 등) `app/main.py`에 등록된 전역 핸들러가 원본 트레이스백 대신
  `{"detail": "Upstage API 오류: ..."}` 형태의 JSON을 Upstage가 반환한 HTTP 상태 코드
  그대로 돌려준다.

---

## 1. Users — `/api/v1/users`

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/api/v1/users` | 사용자 생성. 이메일 중복 시 409 |
| GET | `/api/v1/users/{user_id}` | 사용자 조회. 없으면 404 |

**UserCreate**: `email`(EmailStr), `name`(str), `birth_year`(int, optional), `hometown`(str, optional)
**UserRead**: 위 필드 + `id`, `current_stage`(`onboarding|interview|publishing|published`)

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
탐지 → 이벤트 분할·라벨 추출(Structured Outputs) → 임베딩 → `verified=true` 저장까지
이어진다. 이 파이프라인의 각 단계를 개별적으로 테스트하려면 아래 8절의 샌드박스를 쓴다.

---

## 3. Media Assets — `/api/v1/media-assets`

| Method | Path | 설명 |
| --- | --- | --- |
| POST | `/api/v1/media-assets` | 미디어 원본 업로드 (multipart/form-data) → S3 저장 |

**요청 (Form fields)**: `file`(업로드 파일), `user_id`, `session_id`(optional), `asset_type`(기본 `image`), `age_at_time`, `location_at_time`, `people_at_time`, `user_comment`
**MediaAssetRead**: `id, user_id, session_id, s3_url, asset_type, age_at_time, location_at_time, people_at_time, life_period_mapped, analysis_track, user_comment, created_at`

---

## 4. Autobiographies — `/api/v1/autobiographies`

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/api/v1/autobiographies/{user_id}` | 자서전 조회. 없으면 자동 생성(`get_or_create`) |

**AutobiographyRead**: `id, user_id, title, status(in_progress|consolidated|published), toc_data, created_at, updated_at`

---

## 5. Health

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | `{"status": "ok"}` — DB/S3/Upstage 어느 것도 건드리지 않는 순수 liveness 체크 |

---

## 6. 샌드박스 (프롬프트 튜닝 전용, 무인증) — `/api/v1/sandbox`

**목적**: 프롬프트 담당 팀원이 `app/agents/prompts.py`의 문구를 고친 뒤, 프론트엔드나 실제
사용자 데이터 없이 Swagger UI에서 곧바로 Upstage Solar(`solar-pro3`)를 호출해 결과를 확인한다.
`app/services/*`가 프로덕션에서 호출하는 것과 **동일한** 프롬프트 빌더 함수 + `app/clients/solar.py`
를 그대로 재사용하므로, 여기서 통과한 프롬프트는 실제 서비스에서도 같은 동작을 보장한다.

이 라우터는 DB/S3를 전혀 쓰지 않으므로(`GatewaysDep` 의존성 없음) `.env`에 실제 Supabase나
S3 자격증명이 없어도, `UPSTAGE_API_KEY`만 있으면 동작한다.

### 빠른 시작

```bash
cd backend
uvicorn app.main:app --reload
# 브라우저에서 http://localhost:8000/docs 접속 → "sandbox (dev-only, no auth)" 태그
```

1. `prompts.py`의 문구를 고치면 `--reload` 덕분에 서버가 자동 재시작된다 — 이 시점부터
   `system_prompt_override`를 비워두고 호출하면 방금 고친 문구가 그대로 반영된다.
2. 파일을 고치지 않고 워딩만 빠르게 실험하고 싶다면, 요청 바디의 `system_prompt_override`에
   임시 문구를 넣어 호출한다. 파일 저장·리로드 대기 없이 즉시 비교할 수 있다.
3. 모든 응답에 `messages_sent`(실제로 Solar에 전송된 메시지 배열 원문)가 포함되어 있어,
   "내가 생각한 프롬프트가 실제로 어떻게 조립돼서 나갔는지"를 바로 눈으로 확인할 수 있다.

### 엔드포인트 목록

| Method | Path | 튜닝 대상 (prompts.py) | 비고 |
| --- | --- | --- | --- |
| GET | `/sandbox` | — | 시나리오 목록 요약 |
| POST | `/sandbox/interview-turn` | `INTERVIEW_PERSONA_SYSTEM_PROMPT` | 다음 질문 생성 |
| POST | `/sandbox/slot-gating` | `SLOT_GATING_SYSTEM_PROMPT` | Structured Outputs (`newly_filled_slots`) |
| POST | `/sandbox/followup` | `FOLLOWUP_SYSTEM_PROMPT` | 예산(`MAX_FOLLOWUP_PER_EVENT`) 초과 시 400 |
| POST | `/sandbox/safeguard-check` | `TIER1_BUFFER_SYSTEM_PROMPT` / `TIER2_CRISIS_RESPONSE` | 위기 키워드 매치 시 LLM 호출 없이 고정 문구 반환 |
| POST | `/sandbox/prose-reassembly` | `PROSE_REASSEMBLY_SYSTEM_PROMPT` | 대화 로그 → 1인칭 산문 |
| POST | `/sandbox/event-extraction` | `EVENT_EXTRACTION_SYSTEM_PROMPT` | **핵심** — 이벤트 1급 객체화, Structured Outputs |
| POST | `/sandbox/ocr-validity-check` | `OCR_VALIDITY_CHECK_SYSTEM_PROMPT` | Document Parse 결과 1차 검증 |

공통 옵션 필드:
- `system_prompt_override` (모든 엔드포인트, optional): 위 표의 시스템 프롬프트 상수 대신
  이 문자열을 사용.
- `generation` (optional): `{"model": "...", "reasoning_effort": "low|medium|high", "temperature": 0.8}`
  — 미지정 시 시나리오별 기본값(`solar-pro3`, 각 파이프라인 단계에 맞는 reasoning_effort).

### 6-1. `POST /sandbox/event-extraction` (핵심 파이프라인)

기획안 원칙 1 "이벤트 1급 객체화"를 구현한 Structured Outputs 호출. 하나의 산문 입력이
여러 개의 독립된 사건 객체로 분할되며, 각 사건은 시기·장소·인물·감정·한 줄 요약과 사건 간
관계(`relations`)를 포함한다.

**요청**
```json
{
  "session_prose": "저는 1978년에 부산에서 태어났습니다. 고등학교 때 서울로 유학을 왔는데, 그때 정말 외롭고 힘들었습니다."
}
```

**응답** (`events[]`는 `EVENT_EXTRACTION_SCHEMA`와 1:1 대응)
```json
{
  "messages_sent": [...],
  "events": [
    {
      "one_line_summary": "부산 출생",
      "prose_paragraph": "저는 1978년에 부산에서 태어났습니다.",
      "place": "부산", "occurred_at_label": "1978년", "people": null,
      "emotion_tag": null, "emotion_intensity": null, "emotion_inferred": false,
      "values_reflected": null, "source_quote": "저는 1978년에 부산에서 태어났습니다.",
      "place_confidence": 0.95, "occurred_at_confidence": 0.95
    },
    {
      "one_line_summary": "서울 유학의 외로움",
      "prose_paragraph": "고등학교 때 서울로 유학을 왔는데, 그때 정말 외롭고 힘들었습니다.",
      "place": "서울", "occurred_at_label": "고등학교 시절", "people": null,
      "emotion_tag": "외로움", "emotion_intensity": 4, "emotion_inferred": true,
      "values_reflected": null, "source_quote": "그때 정말 외롭고 힘들었습니다.",
      "place_confidence": 0.9, "occurred_at_confidence": 0.7
    }
  ],
  "relations": [{"from_index": 0, "to_index": 1, "relation_type": "followed_by"}]
}
```

### 6-2. 발견한 스펙 충돌과 구현한 대안 (요청 4번 관련)

`upstage_solar_api_docs.txt` 안에서 `response_format`(Structured Outputs) 지원 모델 범위가
**문서 자체적으로 세 군데가 서로 다르게** 적혀 있다:

1. 파라미터 표: "all solar models (solar-pro3 포함)"이 지원한다고 명시
2. Structured Outputs 예제 코드: `model="solar-pro3"`로 실제 동작하는 예시를 보여줌
3. `response_format` 필드 상세 설명: "only compatible with the `solar-pro-2` model"이라고 명시

이벤트 추출을 포함한 이 프로젝트의 핵심 파이프라인 전체가 Structured Outputs에 의존하므로,
실제 키로 검증할 때까지 기다리지 않고 **자동 폴백을 코드로 구현**했다
(`app/clients/solar.py::structured_completion`): 기본값인 `solar-pro3`로 먼저 시도하고,
Upstage가 `response_format` 관련 400 에러를 반환하면 `solar-pro2`로 1회 자동 재시도한다.
실제 `UPSTAGE_API_KEY`로 두 모델 다 확인해본 뒤, solar-pro3가 항상 성공한다면 이 폴백
분기는 영구히 실행되지 않는 죽은 코드가 되므로 그때 제거해도 무방하다 — 지금은 "모델
선택을 잘못 짚어서 파이프라인 전체가 막히는 상황"을 막기 위한 안전장치로 남겨두었다.

(참고: 임베딩 벡터 차원(`EMBEDDING_DIM` 4096 vs 1024) 문서 모순은 별개 사안으로,
`backend/app/gateways/README.md`에 이미 기록되어 있다 — DB 담당 팀원이 실제 스키마 확정
전에 확인해야 할 항목이라 그쪽 문서에 두었다.)
