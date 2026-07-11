# 프롬프트 튜닝 샌드박스 사용 설명서

`/api/v1/sandbox/*` — `app/agents/prompts.py`의 **모든** 프롬프트를 DB/S3 없이, 인증 없이,
Swagger UI에서 곧바로 Upstage Solar에 호출해 결과를 확인하는 개발자 전용 도구.

이 문서는 20개 엔드포인트 각각에 대해 **(1) 무엇을 검증하기 위한 것인지, (2) 실제
서비스 코드의 어느 함수와 동일한지, (3) 입력 필드마다 무슨 값을 넣어야 하는지, (4) 응답
필드가 각각 무엇을 의미하는지**를 빠짐없이 정리한다. 필드 타입/기본값은
`app/schemas/sandbox.py`, 실제 프롬프트·스키마 정의는 `app/agents/prompts.py`를 기준으로
한다.

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
20개 엔드포인트가 보인다. `GET /sandbox`를 먼저 호출하면 전체 시나리오 요약을 한 번에 볼 수
있다.

## 모든 요청에 공통되는 필드

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `system_prompt_override` | `string \| null` | 해당 시나리오의 시스템 프롬프트 상수 대신 이 문자열을 그대로 사용한다. 생략(`null`)하면 `prompts.py`에 실제로 정의된 문구가 쓰인다. `prompts.py` 파일을 건드리지 않고 Swagger 입력창에 바로 문구를 붙여넣어 즉시 결과를 비교할 수 있게 하기 위한 필드다. |
| `generation` | `GenerationOverrides \| null` | 아래 참조. 생략하면 시나리오별 기본값이 쓰인다. |

`GenerationOverrides`의 하위 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `model` | `string \| null` | 미지정 시 `solar-pro3`(`app.clients.solar.DEFAULT_MODEL`). |
| `reasoning_effort` | `"low" \| "medium" \| "high" \| null` | 미지정 시 시나리오별 기본값(아래 각 항목에 명시) 사용. |
| `temperature` | `float \| null` | 미지정 시 Solar 기본값. |

모든 응답에 공통으로 있는 `messages_sent`: Solar에 실제로 전송된 메시지 배열
(`[{"role": "system"/"user", "content": "..."}]`) 원문 — "내가 생각한 프롬프트가 실제로
어떻게 조립되어 나갔는지"를 항상 눈으로 확인할 수 있다. LLM을 호출하지 않는 3개 엔드포인트
(`safeguard-check`의 tier2 분기, `ocr-confirmation-question`, `life-milestone-classification`)
만 이 필드가 없거나 `null`이다.

## 두 가지 반복 방식

1. **파일을 고치고 재시작 대기**: `prompts.py`의 상수(예: `INTERVIEW_PERSONA_SYSTEM_PROMPT`)를
   직접 수정하면 `--reload` 옵션 덕분에 서버가 자동 재시작된다. 이후 `system_prompt_override`를
   **비워두고** 호출하면 방금 고친 문구가 그대로 반영된다. 실제로 배포될 문구를 확정할 때
   쓰는 방식.
2. **요청 바디에 직접 붙여넣기**: 파일을 건드리지 않고, 요청 바디의 `system_prompt_override`
   필드에 임시 문구를 넣어 호출한다. 저장·리로드를 기다릴 필요 없이 즉시 여러 버전을
   비교할 수 있다. 워딩을 빠르게 실험할 때 쓰는 방식.

---

## Phase 1/2 — 인터뷰 루프

### 1. `POST /sandbox/interview-turn` — 다음 질문 생성 (페르소나)

**목적**: 인터뷰 에이전트의 "성격"(`INTERVIEW_PERSONA_SYSTEM_PROMPT`)이 대화 맥락 속에서
자연스러운 다음 질문을 생성하는지 확인한다. 실제 서비스에는 이 정확한 시나리오를 그대로
호출하는 지점이 없다(실제 `interview_service.add_user_turn`은 슬롯 게이팅→꼬리질문 조합을
쓴다) — 이 엔드포인트는 "페르소나 문구 자체"를 독립적으로 시험해보는 용도다.

**입력 (`InterviewTurnRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `user_name` | `string` | ❌(기본 `"테스트 사용자"`) | 시스템 프롬프트에 `"현재 인터뷰 대상: {user_name}님..."`으로 삽입됨. |
| `life_period_label` | `string` | ❌(기본 `"성인기"`) | 이번 세션이 다루는 생애주기 설명. 예: `"유년기 (1950년대)"`. |
| `style_bible` | `string \| null` | ❌ | Phase 3 완료 후에만 존재하는 화자 스타일 요약. 주면 시스템 프롬프트 끝에 `"[화자 스타일 바이블]\n{style_bible}"`로 추가된다. |
| `chat_history` | `[{role, content}]` | ❌(기본 `[]`) | 지금까지의 대화 턴(직전 발화들). `role`은 `"user"` 또는 `"assistant"`. |
| `latest_user_message` | `string` | ✅ | 방금 사용자가 한 말. 이번 호출에서 에이전트가 응답할 대상. |
| `system_prompt_override` | `string \| null` | ❌ | 위 공통 필드. |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: Solar 기본(미지정). |

**출력 (`InterviewTurnResponse`)**

| 필드 | 설명 |
| --- | --- |
| `system_prompt_used` | 실제로 사용된 시스템 프롬프트 전문(override 반영 결과). |
| `messages_sent` | Solar에 전송된 전체 메시지 배열(system + chat_history + latest_user_message). |
| `assistant_reply` | Solar가 생성한 다음 질문/응답 텍스트. |
| `model_used` | 실제로 응답한 모델명(폴백이 발생했다면 `solar-pro2`로 찍힘). |

---

### 2. `POST /sandbox/slot-gating` — 슬롯 충족 여부 판별

**목적**: `interview_service._run_slot_gating`과 동일한 호출. 사용자의 답변 하나가
11개 슬롯(`place, time, event, emotion, values, gratitude, regret, turning_point, pride,
belief, message`) 중 어떤 것을 새로 채웠는지 저비용으로 판별하는 분류기 프롬프트
(`SLOT_GATING_SYSTEM_PROMPT`)를 검증한다. 이 결과는 실제 서비스에서 즉시 폐기되고
다음 질문 게이팅에만 쓰인다는 점을 기억할 것 — 영속 저장되는 라벨이 아니다.

**입력 (`SlotGatingRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `latest_answer` | `string` | ✅ | 판별 대상 답변. 예: `"그때 정말 기뻤어요, 아들이 태어난 날이었거든요."` |
| `slots_filled` | `dict[string, bool]` | ❌(기본: 11개 슬롯 전부 `false`) | 현재까지 채워진 슬롯 상태. 키는 `place, time, event, emotion, values, gratitude, regret, turning_point, pride, belief, message`. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`SlotGatingResponse`)**

| 필드 | 설명 |
| --- | --- |
| `messages_sent` | 전송된 메시지(시스템 프롬프트 + "아직 채워지지 않은 슬롯: ...\n방금 답변: ..."). |
| `newly_filled_slots` | `latest_answer` 하나로 새로 채워졌다고 판단된 슬롯 키 배열. 예: `["place", "emotion"]`. Structured Outputs(`SLOT_GATING_SCHEMA`)로 강제되므로 항상 위 11개 키 중에서만 나온다. |

---

### 3. `POST /sandbox/followup` — 사건 단위 꼬리 질문

**목적**: `interview_service._generate_followup_question`과 동일한 호출. 필수 슬롯 5개
(`place, time, event, emotion, values`) 중 비어 있는 것만 겨냥해 짧은 꼬리 질문 하나를
생성하는 프롬프트(`FOLLOWUP_SYSTEM_PROMPT`)를 검증한다.

**입력 (`FollowupRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `event_summary` | `string` | ✅ | 지금 다루고 있는 사건에 대한 요약/맥락. 예: `"아들이 태어난 날의 기억"`. |
| `missing_required_slots` | `string[]` | ✅ | 비어 있는 **필수** 슬롯 키 목록(`place, time, event, emotion, values` 중에서). 예: `["place", "emotion"]`. |
| `followup_count` | `int` | ❌(기본 `0`, `≥0`) | 이 사건에 대해 이미 사용한 꼬리 질문 횟수. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: Solar 기본(미지정). |

**출력 (`FollowupResponse`)**: `messages_sent`, `followup_question`(생성된 꼬리 질문 텍스트 1개).

**오류**: `followup_count`가 예산(`MAX_FOLLOWUP_PER_EVENT = 2`) 이상이면 Upstage를 호출하지
않고 즉시 `400 Bad Request`를 반환한다 — 실제 서비스와 동일한 예산 가드가 여기서도 걸린다.
이 엔드포인트로 튜닝할 때는 `followup_count`를 0, 1, 2로 바꿔가며 2에서 정확히 막히는지
확인할 것.

---

### 4. `POST /sandbox/safeguard-check` — 다층 감정 세이프가드 (1층 완충 / 2층 위기 대응)

**목적**: 기획안의 "다층 감정 세이프가드" 중 1층(완충)과 2층(위기 대응)이 실제로 어떻게
갈리는지 확인한다. `interview_service.add_user_turn` 안의 위기 키워드 분기와 동일한 판정
로직(`prompts.contains_crisis_keyword`)을 쓰되, 1층(`TIER1_BUFFER_SYSTEM_PROMPT`)까지
샌드박스에서 직접 호출해볼 수 있다는 점이 실제 서비스와의 차이다(실제 서비스는 현재
2층만 구현되어 있고 1층은 어떤 서비스 코드에서도 호출되지 않는다 — 이 엔드포인트가 1층
프롬프트를 확인할 수 있는 유일한 경로다).

**입력 (`SafeguardCheckRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `latest_answer` | `string` | ✅ | 판정 대상 답변. 위기 키워드(`"죽고 싶", "자살", "그만 살고 싶", "살기 싫", "사라지고 싶", "극단적 선택"`) 포함 여부로 분기가 갈린다. |
| `system_prompt_override` | `string \| null` | ❌ | `TIER1_BUFFER_SYSTEM_PROMPT` 대신 사용(2층 분기에는 적용되지 않음 — 2층은 애초에 LLM을 호출하지 않으므로). |
| `generation` | `GenerationOverrides \| null` | ❌ | — |

**분기 로직**:
- `latest_answer`에 위기 키워드가 **포함되면** → **Upstage를 호출하지 않고** 고정 문구
  (`TIER2_CRISIS_RESPONSE`)를 그대로 반환한다(`messages_sent: null`).
- 포함되지 **않으면** → 1층 완충 프롬프트를 실제로 Solar에 호출해 응답을 받는다.

두 티어를 각각 테스트하려면 `latest_answer`에 위기 키워드 포함 여부를 바꿔가며 호출하면
된다.

**출력 (`SafeguardCheckResponse`)**

| 필드 | 설명 |
| --- | --- |
| `tier` | `"tier1_buffer"` \| `"tier2_crisis"` — 어느 분기가 탔는지. |
| `crisis_keyword_matched` | 위기 키워드 매치 여부(불리언). |
| `response_text` | 실제 사용자에게 보여줄 응답 텍스트(1층은 LLM 생성, 2층은 고정 문구). |
| `messages_sent` | 1층이면 전송 메시지 배열, 2층이면 `null`(호출 자체가 없었으므로). |

---

### 5. `POST /sandbox/prose-reassembly` — 세션 산문 재조립

**목적**: `event_extraction_service.process_completed_session`의 1단계와 동일한 호출.
세션의 대화 로그(질문+답변 전체)를 화자의 1인칭 산문으로 재조립하는 프롬프트
(`PROSE_REASSEMBLY_SYSTEM_PROMPT`)를 검증한다. 문장 병합·재배열·요약 없이 어미/추임새만
정돈하고, 화자의 말투를 최대한 보존하며, 질문(assistant 턴)은 제외하고 사용자 발화만
이어붙이는 것이 규칙이다.

**입력 (`ProseReassemblyRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `chat_turns` | `[{role, content}]` | ✅(최소 1개) | 재조립할 전체 대화 턴. `role`은 `"user"` 또는 `"assistant"`(assistant 턴도 입력에는 포함시켜 문맥을 주되, 프롬프트가 산문 결과에는 반영하지 않도록 지시함). |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`ProseReassemblyResponse`)**: `messages_sent`, `session_prose`(재조립된 1인칭 산문 전문).

---

### 6. `POST /sandbox/event-extraction` — 이벤트 분할·라벨 추출 (핵심 파이프라인)

**목적**: 기획안 원칙 1 "이벤트 1급 객체화"의 실제 구현. `event_extraction_service`의
이벤트 분할·라벨 추출 단계와 완전히 동일한 호출로, 하나의 산문 입력 안에 섞여 있는 여러
사건을 독립 레코드로 분리하고 사건 간 관계까지 추출한다. `EVENT_EXTRACTION_SCHEMA`로
Structured Outputs가 강제된다(array of events + array of relations).

**입력 (`EventExtractionRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `session_prose` | `string` | ✅ | 사건 분할 대상 산문 원문. 예: `"저는 1978년에 부산에서 태어났습니다. 아버지는 어부셨고... 고등학교 때 서울로 유학을 왔는데, 그때 정말 외롭고 힘들었습니다."` (보통 `prose-reassembly` 출력물을 그대로 여기에 넣어 파이프라인을 이어본다.) |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"medium"`. |

**출력 (`EventExtractionResponse`)**

| 필드 | 설명 |
| --- | --- |
| `messages_sent` | — |
| `events` | `EventItemOut[]`. 아래 참조. |
| `relations` | `RelationItemOut[]`. 아래 참조. |

`EventItemOut` 필드 (사건 하나):

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `one_line_summary` | `string` | 이 사건의 한 줄 요약. |
| `prose_paragraph` | `string` | 이 사건에 대응하는 산문 문단(원본 산문에서 그대로 발췌 — Event 레코드의 `prose_paragraph`가 됨, RAG 검색 소스). |
| `place` | `string \| null` | 장소. 불명확하면 `null`. |
| `occurred_at_label` | `string \| null` | 시기. 확정 연도가 없으면 `"고등학교 시절"` 같은 상대적 표현도 허용. |
| `people` | `string \| null` | 등장 인물. |
| `emotion_tag` | `string \| null` | 감정 태그. |
| `emotion_intensity` | `int \| null` | 감정 강도 1~5. |
| `emotion_inferred` | `bool` | 명시적 발화 없이 정황상 추론된 감정이면 `true`(최종 집필 시 단정적 서술의 근거로 쓰지 않기 위한 플래그). |
| `values_reflected` | `string \| null` | 이 사건에 드러난 가치관. |
| `source_quote` | `string` | `prose_paragraph` 내 근거가 되는 축어 구간(로컬 문자열 대조로 실재 여부 검증됨). 서비스 레이어가 `Event.source_span={"quoted_text": ...}`로 저장한다. |
| `place_confidence` | `float` (0~1) | 장소 추출의 신뢰도. |
| `occurred_at_confidence` | `float` (0~1) | 시기 추출의 신뢰도. |

`RelationItemOut` 필드 (사건 간 관계 하나):

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `from_index` | `int` | `events` 배열 내 인덱스(원인/선행 사건). |
| `to_index` | `int` | `events` 배열 내 인덱스(결과/후행 사건). |
| `relation_type` | `"cause" \| "overcome" \| "followed_by" \| "related"` | 원인 / 극복 / 시간상 연속 / 기타 연관. |

**예시**:

```bash
curl -X POST http://localhost:8000/api/v1/sandbox/event-extraction \
  -H "Content-Type: application/json" \
  -d '{
    "session_prose": "저는 1978년에 부산에서 태어났습니다. 고등학교 때 서울로 유학을 왔는데, 그때 정말 외롭고 힘들었습니다."
  }'
```

이 입력은 독립된 사건 두 개(부산 출생 / 서울 유학의 외로움)로 분할되고, `relations[]`에
두 사건의 관계(`followed_by`)까지 함께 나온다.

---

### 7. `POST /sandbox/ocr-validity-check` — Document Parse 결과 1차 타당성 검증

**목적**: `media_service._check_ocr_validity`와 동일한 호출. Document Parse로 추출된 텍스트에
문맥상 비정상적인 문자열이나 깨진 글자(OCR 오인식)가 있는지 판별하는 프롬프트를 검증한다.
이 결과가 `Event.verified`의 초기값을 결정한다(의심되면 `false`로 격리).

**입력 (`OcrValidityCheckRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `ocr_text` | `string` | ✅ | 검증 대상 OCR 텍스트. 예: `"나는 1978년 3짐 15일에 태어났따."`(오인식 예시 — "3짐"→"3월", "태어났따"→"태어났다"). |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`OcrValidityCheckResponse`)**: `messages_sent`, `suspicious`(bool, 오인식 의심 여부),
`note`(string, 의심 사유 또는 `"이상 없음"`).

---

## Phase 3 — 이벤트 병합 · 중요도 산정 · 스타일 바이블

### 8. `POST /sandbox/style-bible` — 화자 스타일 바이블 생성

**목적**: `autobiography_service._generate_style_bible`과 동일한 호출. 전체 세션 산문을
분석해 문체·상용 표현·가치관 키워드·전체 감정 아크를 담은 단일 문서를 생성하는 프롬프트를
검증한다. 이 문서는 이후 모든 집필 프롬프트(챕터 시놉시스/본문/윤문)에 전역 상수로
주입된다.

**입력 (`StyleBibleRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `all_session_prose` | `string[]` | ✅(최소 1개) | 이 화자의 모든 완료 세션 산문 목록. 예: `["저는 1978년 부산에서 태어났습니다...", "학창 시절엔 조용한 아이였어요..."]`. 내부적으로 `"\n\n---\n\n"`로 이어붙여 하나의 유저 메시지로 전송된다. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"medium"`. |

**출력 (`StyleBibleResponse`)**: `messages_sent`, `style_bible_content`(생성된 스타일
바이블 전문 텍스트 — 실제 서비스에서는 이 문자열이 `Autobiography.style_bible.content`에
저장된다).

---

### 9. `POST /sandbox/event-merge-judge` — 이벤트 병합 판정

**목적**: `autobiography_service._judge_same_event`와 동일한 호출. 임베딩 거리가 가까운
두 사건이 실제로 같은 사건인지(중복 언급인지) 판정하는 프롬프트를 검증한다.

**입력 (`EventMergeJudgeRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `event_a_summary` | `string` | ✅ | 사건 A의 요약(+시기). 예: `"1990년 첫째 아이 출산 (서울)"`. |
| `event_b_summary` | `string` | ✅ | 사건 B의 요약(+시기). 예: `"1990년경 첫 아이를 낳음 (서울 소재 병원)"`. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`EventMergeJudgeResponse`)**: `messages_sent`, `same_event`(bool), `reasoning`(판정 근거 텍스트).

**튜닝 시 반드시 확인할 것**: 판정이 불확실할 때 `same_event=false`(병합하지 않음)로
나오는 것이 **정상**이다 — 과병합(별개 사건의 소실)은 인쇄 후 회복 불가능하지만 과분리는
사용자 확인으로 즉시 회복 가능하다는 리스크 비대칭이 기본값의 근거다. 프롬프트를 고칠 때
"애매한 케이스가 여전히 false로 떨어지는가"를 회귀적으로 확인할 것.

---

### 10. `POST /sandbox/life-milestone-classification` — 생애 이정표 카테고리 분류 [LLM 미호출]

**목적**: `prompts.classify_life_milestone_category`(Phase 3 중요도 스코어링의 가중치
항목 중 하나)를 미리 확인한다. **Upstage를 전혀 호출하지 않는 결정론적 키워드 매칭**이다
— `system_prompt_override`/`generation` 필드 자체가 요청 스키마에 없다(프롬프트가 아니라
키워드 사전이므로).

**입력 (`LifeMilestoneClassificationRequest`)**: `text: string` — 분류 대상 문장. 예:
`"1990년에 첫째 아이를 출산했다."`

**분류 기준(`LIFE_MILESTONE_KEYWORDS`, 등장 순서상 첫 매치가 채택됨)**:

| 카테고리 | 매칭 키워드 |
| --- | --- |
| `marriage` | 결혼, 혼인, 장가, 시집 |
| `childbirth` | 출산, 태어났, 낳았, 임신 |
| `career_change` | 이직, 취직, 퇴사, 창업, 입사 |
| `illness` | 투병, 수술, 입원, 발병, 진단받 |
| `bereavement` | 돌아가, 장례, 사별, 부고 |
| `relocation` | 이사, 이주, 이민 |
| `retirement` | 은퇴, 정년 |

**출력 (`LifeMilestoneClassificationResponse`)**: `category: string | null` — 일치한 첫
카테고리(위 표의 키). 어느 것도 매치되지 않으면 `null`.

---

## Phase 4 — 동적 목차 · 하향식 집필 · 팩트체크 · 등장인물 검토

### 11. `POST /sandbox/toc-generation` — 동적 목차 후보 생성

**목적**: `autobiography_service.generate_toc_candidates`와 동일한 호출. 사건 요약+중요도
점수 목록을 의미론적으로 군집화해 서로 다른 구성 관점(연대기순/주제별/인물중심 등)의
목차 후보 3안을 생성하는 프롬프트를 검증한다. `TOC_GENERATION_SCHEMA`로 Structured
Outputs가 강제된다.

**입력 (`TocGenerationRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `event_summaries_with_scores` | `string` | ✅ | `"- [중요도 점수] 요약 (시기: ..., 감정: ...)"` 형식의 줄바꿈 목록. 예: `"- [중요도 12.5] 부산 출생 (시기: 1978년, 감정: 미상)\n- [중요도 9.2] 첫 취업 (시기: 2001년, 감정: 설렘)"`. 실제 서비스에서는 `list_unmerged_verified`로 조회한 이벤트들을 이 형식으로 직렬화해 전달한다. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"medium"`. |

**출력 (`TocGenerationResponse`)**: `messages_sent`, `candidates: TocCandidateOut[]`(항상
3개를 목표로 생성됨). 각 `TocCandidateOut`은 `chapters: TocChapterOut[]`를 가지며,
`TocChapterOut`은 `chapter_index(int), title(string), theme_keywords(string[])`로 구성된다.

---

### 12. `POST /sandbox/book-synopsis` — 책 전체 시놉시스

**목적**: `autobiography_service._generate_book_synopsis`와 동일한 호출. 하향식 집필의
최상위 설계도인 책 전체 시놉시스를 생성하는 프롬프트를 검증한다.

**입력 (`BookSynopsisRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `style_bible` | `string` | ✅ | 스타일 바이블 본문(위 8번 엔드포인트의 `style_bible_content` 출력을 그대로 이어 넣으면 실제 파이프라인 순서를 재현할 수 있다). 예: `"간결하고 담담한 문체. 가족과 성실함을 중시함."` |
| `toc` | `string` | ✅ | 선택된 목차를 `"1. 어린 시절 (유년기)\n2. 청춘의 방황 (청년기)"` 형식으로 직렬화한 문자열(위 11번 엔드포인트 출력의 `chapters`를 이 형식으로 변환해 넣으면 됨). |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"medium"`. |

**출력 (`BookSynopsisResponse`)**: `messages_sent`, `book_synopsis`(생성된 시놉시스 전문).

---

### 12-1. `POST /sandbox/book-title` — 책 제목

**목적**: `autobiography_service._generate_book_title`과 동일한 호출. 표지·PDF 조판에
그대로 노출되는 책 제목을 생성하는 프롬프트를 검증한다. Structured Outputs로 `{"title":
string}`만 받는다 — 자유 서술이 아니라 "짧은 책 제목 한 줄"이라는 형식 제약이 핵심이므로,
문체보다 "출력이 실제로 제목 하나만 담고 있는지"(따옴표·접두어 없이)를 확인하는 데 이
샌드박스를 쓰면 된다.

**입력 (`BookTitleRequest`)**: 위 12번(`book-synopsis`)과 필드 구성이 완전히 동일하다
(`style_bible`, `toc`, `system_prompt_override`, `generation` — `reasoning_effort`
기본값만 `"low"`로 다르다, 제목은 시놉시스보다 훨씬 짧은 산출물이라).

**출력 (`BookTitleResponse`)**: `messages_sent`, `title`(생성된 제목 한 줄).

---

### 13. `POST /sandbox/chapter-synopsis` — 챕터 시놉시스

**목적**: `autobiography_service._generate_chapter_synopsis`와 동일한 호출. 책 전체
시놉시스 아래 하향식으로 개별 챕터의 시놉시스를 생성하는 프롬프트를 검증한다.

**입력 (`ChapterSynopsisRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `book_synopsis` | `string` | ✅ | 위 12번 출력을 그대로 넣는다. 예: `"부산에서 태어나 성실하게 삶을 일군 한 사람의 이야기."` |
| `chapter_title` | `string` | ✅ | 이 챕터의 제목. 예: `"1장. 어린 시절"`. |
| `event_summaries` | `string[]` | ✅ | 이 챕터에 배정된 사건들의 한 줄 요약 목록. 예: `["부산 출생", "초등학교 입학"]`. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"medium"`. |

**출력 (`ChapterSynopsisResponse`)**: `messages_sent`, `chapter_synopsis`(생성된 챕터
시놉시스 전문).

---

### 14. `POST /sandbox/chapter-writing` — 챕터 본문 집필 (하향식 집필의 최종 단계)

**목적**: `autobiography_service._generate_chapter_content`와 동일한 호출. 하향식 집필의
마지막 단계 — 스타일 바이블·전체/챕터 시놉시스·직전 챕터 요약·RAG로 소환된 사건 문단을
전부 주입해 챕터 본문을 생성한다.

**입력 (`ChapterWritingRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `style_bible` | `string` | ✅ | 예: `"간결하고 담담한 문체."` |
| `book_synopsis` | `string` | ✅ | 예: `"부산에서 태어나 성실하게 삶을 일군 한 사람의 이야기."` |
| `chapter_synopsis` | `string` | ✅ | 위 13번 출력을 그대로 넣는다. 예: `"유년기의 평온함과 가족의 따뜻함을 그린다."` |
| `previous_chapter_summary` | `string \| null` | ❌ | 직전 챕터 요약(실제 서비스에서는 직전 챕터 본문의 마지막 1000자 근사치). 첫 챕터면 생략(`null`). |
| `retrieved_event_paragraphs` | `string[]` | ✅(최소 1개) | 이 챕터 집필에 소환할 사건 문단들(하이브리드 RAG 검색 결과에 해당). 예: `["저는 1978년 부산에서 태어났습니다."]`. 본문은 이 문단들의 내용을 벗어나지 않도록 프롬프트에 명시되어 있다(사후 팩트체크/근거검증 대상). |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"high"`(집필 품질을 위해 다른 시나리오보다 높음). |

**출력 (`ChapterWritingResponse`)**: `messages_sent`, `chapter_content`(생성된 챕터 본문
전문).

**전체 파이프라인 순서를 그대로 재현하려면**: `toc-generation` → `book-synopsis`/`book-title`
(입력이 동일하므로 순서 무관, 실제 서비스도 같은 시점에 병렬로 생성) → `chapter-synopsis` →
`chapter-writing` 순으로, 이전 단계의 출력을 다음 단계 입력에 손으로 이어 넣으면 된다.

---

### 15. `POST /sandbox/unity-revision` — 통일성 윤문 패스

**목적**: `autobiography_service.finalize_manuscript`와 동일한 호출. 전 챕터 생성 후
인접 챕터 경계부와 스타일 바이블을 함께 검토해 어조·문체 단절만 다듬는 리비전(사실
관계·순서는 변경하지 않음) 프롬프트를 검증한다.

**입력 (`UnityRevisionRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `style_bible` | `string` | ✅ | 예: `"간결하고 담담한 문체."` |
| `full_manuscript` | `string` | ✅ | 전체 원고. 실제 서비스는 `"[{챕터번호}장. {제목}]\n{본문}"`을 챕터마다 이어붙인 형태로 전달한다. 예: `"[1장. 어린 시절]\n저는 부산에서 태어났습니다..."` |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"high"`. |

**출력 (`UnityRevisionResponse`)**: `messages_sent`, `revised_manuscript`(윤문된 전체
원고 — 실패 시 실제 서비스는 원본 `full_manuscript`를 그대로 폴백으로 사용함에 유의).

---

### 16. `POST /sandbox/fact-reextraction` — 원문 대조 팩트체크 (1단계: 재추출)

**목적**: `autobiography_service._run_factcheck`의 **1단계(재추출)만** 보여준다. 챕터
본문에서 핵심 팩트(인명·연도/나이·지명·수량)를 구조화 추출하는 프롬프트를 검증한다.
이후 개체 정규화(연도 절대환산, 지명 정규화 등)와 원천 라벨 대조는
`autobiography_service._run_factcheck`의 **결정론적 로컬 로직**(단순 substring 매칭)이
담당하므로 Upstage 호출 대상이 아니며, 이 샌드박스로는 확인할 수 없다.

**입력 (`FactReextractionRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `chapter_content` | `string` | ✅ | 팩트를 추출할 챕터 본문. 예: `"저는 1978년 부산에서 태어나 스물다섯 되던 해 서울로 왔습니다."`("스물다섯 되던 해"처럼 서술적 표현이어도 팩트 자체(나이 표현)만 뽑아내도록 지시되어 있다.) |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`FactReextractionResponse`)**: `messages_sent`, `facts: FactOut[]`. 각 `FactOut`은
`fact_type("person"|"year_or_age"|"place"|"quantity")`와 `raw_text(string, 추출된 원문
그대로의 팩트 표현)`로 구성된다.

---

### 17. `POST /sandbox/third-party-risk` — 제3자 언급 위해성 분류

**목적**: `character_service._classify_risk`와 동일한 호출. 등장인물이 언급되는 문단의
서술 성격(범죄/비위, 부정적 평가, 갈등 등)을 분류하는 프롬프트를 검증한다.

> **이 분류는 가명 적용 여부를 결정하는 게이트가 아니다.** 전수 가명화 기본값(opt-out)은
> 이 결과와 무관하게 항상 적용되며, 여기서는 실명 유지를 **시도할 때** 표시할 고지문의
> 강도만 조정하는 보조 신호를 산출한다. 즉 이 분류가 틀려도(예: 위험을 놓쳐도) 가명
> 기본값이라는 안전 상태 자체는 깨지지 않는다.

**입력 (`ThirdPartyRiskRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `person_name` | `string` | ✅ | 분류 대상 인물명. 예: `"김철수"`. |
| `chapter_excerpts` | `string[]` | ✅(최소 1개) | 이 인물이 등장하는 문단들. 예: `["김철수와 크게 다툰 적이 있다."]`. |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`ThirdPartyRiskResponse`)**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `messages_sent` | — | — |
| `person_name` | `string` | 입력을 그대로 반영. |
| `risk_detected` | `bool` | 위해성 서술이 감지되었는지. |
| `risk_classification` | `"none" \| "negative_portrayal" \| "conflict" \| "crime_mention"` | 위해성 없음 / 부정적 인물 평가 / 갈등·분쟁 당사자 / 범죄·비위 언급(가장 높은 고지 강도). |
| `risk_reasons` | `string[]` | 분류 근거 목록. |

---

### 18. `POST /sandbox/ner-extraction` — 등장인물 NER 스캔

**목적**: `character_service.scan_and_classify_chapter`의 1단계(NER 스캔)와 동일한 호출.
챕터 본문에서 구술자 본인을 제외한 실명 등장인물을 찾아내는 프롬프트를 검증한다.

**입력 (`NerExtractionRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `chapter_content` | `string` | ✅ | 스캔 대상 챕터 본문. 예: `"김철수와 함께 학교를 다녔다. 그의 어머니도 종종 뵈었다."` |
| `system_prompt_override` | `string \| null` | ❌ | — |
| `generation` | `GenerationOverrides \| null` | ❌ | `reasoning_effort` 기본값: `"low"`. |

**출력 (`NerExtractionResponse`)**: `messages_sent`, `people: PersonOut[]`. 각 `PersonOut`은
`name(string)`과 `relation_to_narrator(string | null, 예: "어머니의 친구", 불명확하면
null)`로 구성된다. 같은 인물을 가리키는 다른 표현(별칭·호칭)은 하나의 항목으로 묶여
대표 이름 하나로 나오도록 프롬프트에 지시되어 있다. 지명·단체명·구술자 본인은 제외된다.

---

### 19. `POST /sandbox/ocr-confirmation-question` — OCR 확인 질문 문구 미리보기 [LLM 미호출]

**목적**: Upstage를 전혀 호출하지 않는 순수 문자열 포맷팅. Phase 1에서 OCR 오인식 의심으로
`verified=false` 검증 대기 큐에 격리된 항목이, 실제 인터뷰 중 확인 질문으로 어떻게
표현되는지 문구만 미리 보는 용도다(`prompts.build_ocr_confirmation_question`).

> 참고: 이 문구가 실제 인터뷰 턴에 자동으로 삽입되는 로직 자체는 아직 서비스 코드에
> 연결되어 있지 않다(TODO) — 이 엔드포인트는 어디까지나 "연결됐을 때 어떤 문구가 나올지"의
> 프리뷰다.

**입력 (`OcrConfirmationQuestionRequest`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `suspected_text` | `string` | ✅ | 오인식 의심 원문 조각. 예: `"1975년 부산"`. |
| `guessed_value` | `string` | ✅ | 사람이 읽었을 때 추정되는 의미. 예: `"1975년에 부산에 사신 것"`. |

**출력 (`OcrConfirmationQuestionResponse`)**: `question: string` — 고정 템플릿
`'일기장에 "{suspected_text}"라고 적혀 있는 것 같은데, {guessed_value}가 맞으신가요?'`로
조립된 확인 질문 문구.

---

## 부록: Upstage 문서 자체 모순

`upstage_solar_api_docs.txt`가 `response_format`(Structured Outputs) 지원 모델을 문서
내에서 서로 다르게 적어놓은 문제와, `app/clients/solar.py`에 넣어둔 solar-pro3 → solar-pro2
자동 폴백에 대해서는 [API_ENDPOINTS.md의 부록](./API_ENDPOINTS.md#부록-발견한-upstage-문서-자체-모순과-구현한-대안)을 참고할 것.
이 샌드박스의 Structured Outputs 엔드포인트(`slot-gating`, `event-extraction`,
`ocr-validity-check`, `event-merge-judge`, `toc-generation`, `fact-reextraction`,
`third-party-risk`, `ner-extraction`)는 전부 그 폴백 로직을 거친다 — 즉 이 8개 중 어느
것을 호출하든, solar-pro3가 400을 반환하면 자동으로 solar-pro2가 대신 응답하고
`InterviewTurnResponse`류가 아닌 응답에는 실제 사용된 모델명이 노출되지 않으므로(이
8개는 `model_used` 필드가 없음), 폴백이 실제로 발생했는지 궁금하면 서버 로그를 확인해야
한다.
