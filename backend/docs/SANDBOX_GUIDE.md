# 프롬프트 튜닝 샌드박스 사용 설명서

`/api/v1/sandbox/*` — `app/agents/prompts.py`의 **모든** 프롬프트를 DB/S3 없이, 인증 없이,
Swagger UI에서 곧바로 Upstage Solar에 호출해 결과를 확인하는 개발자 전용 도구.

## 왜 있는가

프롬프트 담당 팀원이 `prompts.py`의 문구를 고칠 때마다:
1. 실제 서비스 흐름(유저 생성 → 세션 생성 → 여러 턴 대화 → 세션 종료 → Celery 대기...)을
   전부 다시 밟아야 겨우 자기가 고친 프롬프트 하나의 결과를 볼 수 있다면, 그 자체가
   튜닝의 병목이 된다.
2. 반대로 프롬프트 로직을 서비스 코드와 별개로 재구현해서 빠르게 테스트하면, "샌드박스에서
   통과한 프롬프트가 실제로는 다르게 동작"하는 이원화 문제가 생긴다.

이 샌드박스는 **`app/services/*.py`가 프로덕션에서 호출하는 것과 완전히 동일한**
`prompts.py`의 `build_*` 함수 + `app/clients/solar.py`를 그대로 재사용한다. 그래서 여기서
확인한 프롬프트 동작은 실제 서비스에서도 100% 동일하게 재현된다 — 로직이 두 곳에 나뉘어
있지 않다.

DB/S3를 전혀 쓰지 않으므로(`GatewaysDep` 의존성 없음) `.env`에 실제 Supabase나 S3 자격증명이
없어도 `UPSTAGE_API_KEY`만 있으면 전부 동작한다. 인증도 필요 없다 — 이 프로젝트 자체에
아직 인증 계층이 없고, 나중에 인증이 도입되어도 사용자 데이터를 전혀 만들지 않는 이 라우터는
계속 무인증으로 남겨두는 것이 맞다.

## 빠른 시작

```bash
cd backend
cp .env.example .env   # UPSTAGE_API_KEY만 채우면 충분 (DATABASE_URL/AWS_* 불필요)
uvicorn app.main:app --reload
```

브라우저에서 `http://localhost:8000/docs` 접속 → **"sandbox (dev-only, no auth)"** 태그 아래
19개 엔드포인트가 보인다. `GET /sandbox`를 먼저 호출하면 전체 시나리오 요약을 한 번에 볼 수
있다.

## 두 가지 반복 방식

1. **파일을 고치고 재시작 대기**: `prompts.py`의 상수(예: `INTERVIEW_PERSONA_SYSTEM_PROMPT`)를
   직접 수정하면 `--reload` 옵션 덕분에 서버가 자동 재시작된다. 이후 `system_prompt_override`를
   **비워두고** 호출하면 방금 고친 문구가 그대로 반영된다. 실제로 배포될 문구를 확정할 때
   쓰는 방식.
2. **요청 바디에 직접 붙여넣기**: 파일을 건드리지 않고, 요청 바디의 `system_prompt_override`
   필드에 임시 문구를 넣어 호출한다. 저장·리로드를 기다릴 필요 없이 즉시 여러 버전을
   비교할 수 있다. 워딩을 빠르게 실험할 때 쓰는 방식.

모든 응답에는 `messages_sent`(Solar에 실제로 전송된 메시지 배열 원문)가 포함된다 — "내가
생각한 프롬프트가 실제로 어떻게 조립되어 나갔는지"를 항상 눈으로 확인할 수 있다.

## 공통 요청 필드

| 필드 | 설명 |
| --- | --- |
| `system_prompt_override` | 해당 시나리오의 시스템 프롬프트 상수 대신 이 문자열을 사용. 생략하면 `prompts.py`의 실제 값 사용 |
| `generation.model` | 기본값 `solar-pro3`(`app.clients.solar.DEFAULT_MODEL`) |
| `generation.reasoning_effort` | `low`\|`medium`\|`high`. 생략하면 시나리오별 기본값(아래 표) 사용 |
| `generation.temperature` | 생략하면 Solar 기본값 |

## 전체 시나리오

### Phase 1/2 — 인터뷰 루프

| Endpoint | 튜닝 대상 | 호출 방식 | reasoning_effort 기본값 |
| --- | --- | --- | --- |
| `POST /sandbox/interview-turn` | `INTERVIEW_PERSONA_SYSTEM_PROMPT` | 일반 채팅 | 미지정(Solar 기본) |
| `POST /sandbox/slot-gating` | `SLOT_GATING_SYSTEM_PROMPT` | Structured Outputs | `low` |
| `POST /sandbox/followup` | `FOLLOWUP_SYSTEM_PROMPT` | 일반 채팅 | 미지정 |
| `POST /sandbox/safeguard-check` | `TIER1_BUFFER_SYSTEM_PROMPT` / `TIER2_CRISIS_RESPONSE` | 조건부(아래 참조) | 미지정 |
| `POST /sandbox/prose-reassembly` | `PROSE_REASSEMBLY_SYSTEM_PROMPT` | 일반 채팅 | `low` |
| `POST /sandbox/event-extraction` | `EVENT_EXTRACTION_SYSTEM_PROMPT` | **Structured Outputs** | `medium` |
| `POST /sandbox/ocr-validity-check` | `OCR_VALIDITY_CHECK_SYSTEM_PROMPT` | Structured Outputs | `low` |

`safeguard-check`는 `latest_answer`에 위기 키워드(`CRISIS_KEYWORDS`)가 매치되면 **Upstage를
아예 호출하지 않고** `TIER2_CRISIS_RESPONSE` 고정 문구를 그대로 반환한다(`messages_sent: null`).
매치되지 않으면 1층 완충 응답(`TIER1_BUFFER_SYSTEM_PROMPT`)을 실제로 호출해 확인한다 —
두 티어를 각각 테스트하려면 `latest_answer`에 위기 키워드 포함 여부를 바꿔가며 호출한다.

`followup`은 `followup_count`가 예산(`MAX_FOLLOWUP_PER_EVENT`, 현재 2)을 넘으면 400을
반환한다 — 실제 서비스와 동일한 예산 가드가 여기서도 걸린다.

### Phase 3 — 이벤트 병합 · 중요도 산정 · 스타일 바이블

| Endpoint | 튜닝 대상 | 호출 방식 | reasoning_effort 기본값 |
| --- | --- | --- | --- |
| `POST /sandbox/style-bible` | `STYLE_BIBLE_SYSTEM_PROMPT` | 일반 채팅 | `medium` |
| `POST /sandbox/event-merge-judge` | `EVENT_MERGE_JUDGE_SYSTEM_PROMPT` | Structured Outputs | `low` |
| `POST /sandbox/life-milestone-classification` | `LIFE_MILESTONE_KEYWORDS` | **[LLM 미호출]** 키워드 매칭 | — |

`event-merge-judge`는 판정이 불확실할 때 `same_event=false`(병합하지 않음)로 나오는 것이
**정상**이다 — 과병합은 인쇄 후 회복 불가능하지만 과분리는 사용자 확인으로 즉시 회복
가능하다는 리스크 비대칭이 기본값의 근거다. 이 엔드포인트로 프롬프트를 튜닝할 때
"애매하면 false로 떨어지는가"를 반드시 확인할 것.

`life-milestone-classification`은 Upstage를 전혀 부르지 않는 결정론적 키워드 매칭
(`prompts.LIFE_MILESTONE_KEYWORDS`)이다 — 카테고리별 키워드 목록을 고친 뒤 바로 결과를
확인하는 용도. `system_prompt_override`/`generation` 필드 자체가 없다(프롬프트가 아니라
키워드 사전이기 때문).

### Phase 4 — 동적 목차 · 하향식 집필 · 팩트체크 · 등장인물 검토

| Endpoint | 튜닝 대상 | 호출 방식 | reasoning_effort 기본값 |
| --- | --- | --- | --- |
| `POST /sandbox/toc-generation` | `TOC_GENERATION_SYSTEM_PROMPT` | **Structured Outputs** | `medium` |
| `POST /sandbox/book-synopsis` | `BOOK_SYNOPSIS_SYSTEM_PROMPT` | 일반 채팅 | `medium` |
| `POST /sandbox/chapter-synopsis` | `CHAPTER_SYNOPSIS_SYSTEM_PROMPT` | 일반 채팅 | `medium` |
| `POST /sandbox/chapter-writing` | `CHAPTER_WRITING_SYSTEM_PROMPT` | 일반 채팅 | `high` |
| `POST /sandbox/unity-revision` | `UNITY_REVISION_SYSTEM_PROMPT` | 일반 채팅 | `high` |
| `POST /sandbox/fact-reextraction` | `FACT_REEXTRACTION_SYSTEM_PROMPT` | Structured Outputs | `low` |
| `POST /sandbox/third-party-risk` | `THIRD_PARTY_RISK_SYSTEM_PROMPT` | Structured Outputs | `low` |
| `POST /sandbox/ner-extraction` | `NER_EXTRACTION_SYSTEM_PROMPT` | Structured Outputs | `low` |
| `POST /sandbox/ocr-confirmation-question` | `build_ocr_confirmation_question` | **[LLM 미호출]** 문자열 포맷팅 | — |

`chapter-writing`이 하향식 집필의 마지막 단계다 — 스타일 바이블·책 전체 시놉시스·챕터
시놉시스·직전 챕터 요약·RAG로 소환된 사건 문단을 전부 입력받아 본문을 생성한다. 실제
파이프라인 순서(`autobiography_service.write_chapter`)를 그대로 재현하려면
`toc-generation` → `book-synopsis` → `chapter-synopsis` → `chapter-writing` 순으로 이전
단계의 출력을 다음 단계 입력에 손으로 이어 넣으면 된다.

`fact-reextraction`은 원문 대조 팩트체크의 **1단계(재추출)만** 보여준다 — 이후 개체
정규화(연도 절대환산, 지명 정규화 등)와 라벨 대조는 `autobiography_service._run_factcheck`의
결정론적 로컬 로직이 담당하므로 Upstage 호출 대상이 아니다.

`ocr-confirmation-question`은 Upstage를 부르지 않는다 — Phase 1에서 검증 대기 큐에 격리된
OCR 의심 구간이 인터뷰 중 확인 질문으로 어떻게 표현되는지 문구만 미리 보는 용도다.

## 예시: 이벤트 1급 객체화 (핵심 파이프라인)

```bash
curl -X POST http://localhost:8000/api/v1/sandbox/event-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "session_prose": "저는 1978년에 부산에서 태어났습니다. 고등학교 때 서울로 유학을 왔는데, 그때 정말 외롭고 힘들었습니다."
  }'
```

응답의 `events[]`는 `EVENT_EXTRACTION_SCHEMA`와 1:1 대응하며, 하나의 산문 입력이 독립된
사건 두 개(부산 출생 / 서울 유학의 외로움)로 분할되고 `relations[]`에 두 사건의 관계
(`followed_by`)까지 함께 나온다. 이 구조가 기획안 원칙 1 "이벤트 1급 객체화"의 실제 구현이다.

## 부록: Upstage 문서 자체 모순

`upstage_solar_api_docs.txt`가 `response_format`(Structured Outputs) 지원 모델을 문서
내에서 서로 다르게 적어놓은 문제와, `app/clients/solar.py`에 넣어둔 solar-pro3 → solar-pro2
자동 폴백에 대해서는 [API_ENDPOINTS.md의 부록](./API_ENDPOINTS.md#부록-발견한-upstage-문서-자체-모순과-구현한-대안)을 참고할 것.
이 샌드박스의 Structured Outputs 엔드포인트(`slot-gating`, `event-extraction`,
`ocr-validity-check`, `event-merge-judge`, `toc-generation`, `fact-reextraction`,
`third-party-risk`, `ner-extraction`)는 전부 그 폴백 로직을 거친다.
