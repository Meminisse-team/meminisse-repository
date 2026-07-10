# Meminisse Backend API 명세서

Base URL(로컬): `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs` (모든 엔드포인트를 여기서 직접 호출·확인 가능)

프롬프트 튜닝 전용 샌드박스(`/api/v1/sandbox/*`)는 이 문서가 아니라
**[SANDBOX_GUIDE.md](./SANDBOX_GUIDE.md)** 를 참고할 것. 이 문서는 실제 서비스 API(DB에
실제로 데이터를 쓰는 엔드포인트)만 다룬다.

이 문서의 목표는 "경로와 한 줄 설명"이 아니라, **각 엔드포인트가 왜 존재하고, 무엇을
입력하면, 내부에서 무슨 일이 일어나며, 정확히 무엇을 돌려받는지**를 코드를 보지 않고도
알 수 있게 하는 것이다. 필드 하나하나의 타입·필수여부·의미, 그리고 "이 엔드포인트를
먼저 호출하지 않으면 저 엔드포인트가 실패한다" 같은 순서 의존성까지 명시한다.

## 공통 사항

- **인증: Supabase Auth 세션 토큰(2026-07-10 추가).** `POST /api/v1/users`(회원가입)와
  `POST /api/v1/auth/login`·`/refresh`를 제외한 **모든** 엔드포인트(`/api/v1/sandbox/*`와
  `/health` 제외)는 `Authorization: Bearer <access_token>` 헤더가 없으면 401/403으로
  거부된다. 상세는 아래 "0. Authentication" 절 참조.
- **DI**: DB/S3가 필요한 엔드포인트는 전부 `GatewaysDep`(`app/gateways/factory.py`)을 통해
  주입받는다. `.env`의 `GATEWAY_BACKEND=mock|postgres`로 실제 구현체가 바뀐다. 이 문서의
  모든 동작 설명은 두 백엔드 모두에서 동일하게 성립한다(게이트웨이 인터페이스 계약).
- **Upstage API 오류 처리**: Solar/Embeddings/Document Parse 호출이 실패하면(잘못된 키,
  요금 초과, 모델 비호환 등) `app/main.py`에 등록된 전역 핸들러가 원본 트레이스백 대신
  `{"detail": "Upstage API 오류: ..."}` 형태의 JSON을 Upstage가 반환한 HTTP 상태 코드
  그대로 돌려준다. 이 오류는 아래 각 엔드포인트 설명에서 "Upstage 호출 실패 시" 공통으로
  적용되므로 항목마다 반복하지 않는다.
- **비동기 트리거(202)**: Phase 3/4의 무거운 연산(consolidate/write/finalize)은 API
  요청 스레드에서 실행하지 않고 Celery 워커에 위임한 뒤 즉시 `202 Accepted`를 반환한다.
  **이 202는 "성공적으로 끝났다"가 아니라 "큐에 넣었다"는 뜻이다.** 완료 여부·성공 여부는
  해당 리소스의 GET 엔드포인트를 폴링해 상태 필드 변화(또는 값이 채워졌는지)로 직접
  확인해야 한다 — 실패 시 클라이언트에게 통보하는 별도 알림/웹훅 메커니즘은 아직 없다.
  각 202 엔드포인트 설명에 "무엇이 채워지면 성공, 어떤 조건이면 실패하는지"를 구체적으로
  적어두었다.
- **`current_stage`는 자동으로 갱신되지 않는다.** `User.current_stage`는 생성 시
  `onboarding`으로 고정되며, 어떤 서비스 로직도 이 값을 `interview`/`publishing`/`published`로
  전환하지 않는다(전체 코드베이스에 갱신 지점이 없음). 프론트엔드가 세션 진행 상황이나
  `Autobiography.status`를 보고 UI 단계를 자체 판단하거나, 향후 이 필드를 실제로 갱신하는
  로직을 추가해야 한다.

---

## 0. Authentication — `/api/v1/auth`, `POST /api/v1/users`

기획안에는 없던 기능이지만, 실서비스로 나가려면 "이 요청을 보낸 사람이 누구인지"를
서버가 알아야 다른 사람의 자서전·인터뷰·사진에 접근하지 못하게 막을 수 있다.

**직접 비밀번호를 해싱하거나 JWT를 발급하지 않는다.** 이 Supabase 프로젝트에는
`auth`/`storage`/`realtime` 스키마가 이미 프로비저닝되어 있음을 DB 실연동 검증 중
확인했다 — 즉 이메일 인증·비밀번호 재설정·소셜 로그인까지 지원하는 완전한 인증
서비스(Supabase Auth/GoTrue)가 같은 프로젝트에 이미 존재한다. 그래서 자체 인증
체계 대신 이를 그대로 쓴다(`app/clients/supabase_auth.py`가 그 REST API를 감싸는
얇은 래퍼, `app/core/security.py`가 Supabase가 발급한 세션 토큰의 서명을 검증하는
역할). 비밀번호는 이 프로젝트 DB 어디에도 저장되지 않는다.

**가입과 로그인은 두 단계로 분리되어 있다** — REST 관례상 "유저를 만드는 것" 자체가
가입이므로, 가입은 별도 `/auth/signup`이 아니라 기존 `POST /api/v1/users`가 그대로
맡는다(요청 바디에 `password` 필드가 새로 추가됨). 로그인·토큰 갱신은
`/api/v1/auth/login`·`/api/v1/auth/refresh`에서 처리한다.

### `POST /api/v1/users` — 회원가입 (인증 불필요)

1절의 사용자 생성 엔드포인트와 동일하다. `UserCreate`에 `password`(평문, 최소 8자)가
필수로 추가됐다. 내부적으로 다음 순서로 처리된다(`app/services/user_service.py`):

1. Supabase Auth **Admin API**(`service_role` 키)로 `auth.users` 계정을 즉시
   생성한다(`email_confirm=true`로 이메일 인증 절차를 건너뛴다 — 이 프로젝트는
   아직 이메일 발송 인프라가 없다).
2. Supabase Auth가 발급한 `id`(UUID)를 그대로 이 프로젝트의 `public.users.id`로
   써서 프로필 행(이름·출생연도·고향 등 앱 고유 필드)을 만든다 — 두 테이블은
   `id` 하나로 1:1 연결되며, `public.users.id → auth.users.id` FK(ON DELETE
   CASCADE)가 걸려 있다(alembic 004).

응답 어디에도 `password`/`hashed_password`는 노출되지 않는다(애초에 이 서버가
저장하지 않으므로).

### `POST /api/v1/auth/login` — 로그인 (인증 불필요)

**요청 바디 (`LoginRequest`)**: `email`(EmailStr), `password`(string)

내부적으로 Supabase Auth의 `grant_type=password` 토큰 엔드포인트를 그대로
호출한다 — 이 서버는 비밀번호를 대조하지 않는다.

**응답 `200 OK` (`TokenResponse`)**: `access_token`(string, 이후 모든 요청의
`Authorization: Bearer <access_token>` 헤더에 넣을 값 — Supabase가 발급한 JWT를
그대로 전달), `refresh_token`(string, 만료 후 재로그인 없이 새 토큰을 받는 데
사용), `token_type`(항상 `"bearer"`), `expires_in`(초 단위 만료까지 남은 시간,
Supabase 프로젝트 설정을 따르며 기본값은 3600초=1시간으로 자체 JWT보다 짧다)

**오류**: 이메일이 존재하지 않거나 비밀번호가 틀리면 **항상 동일하게** `401
Unauthorized`(`"이메일 또는 비밀번호가 올바르지 않습니다."`) — 어느 쪽이 원인인지
구분해 응답하면 존재하는 이메일 주소를 추측할 수 있는 사용자 열거(user enumeration)
공격의 단서가 되므로 의도적으로 구분하지 않는다.

### `POST /api/v1/auth/refresh` — 토큰 갱신 (인증 불필요, refresh_token으로 대신 인증)

**요청 바디 (`RefreshRequest`)**: `refresh_token`(string, 로그인/이전 갱신 응답에서
받은 값)

`access_token`이 만료된 뒤(기본 1시간) 재로그인 없이 새 토큰 쌍을 받는다. 내부적으로
Supabase Auth의 `grant_type=refresh_token` 엔드포인트를 호출한다.

**응답**: 로그인과 동일한 `TokenResponse`(새 `access_token`/`refresh_token` 쌍 —
Supabase는 리프레시 토큰을 1회용으로 순환시키므로 이전 `refresh_token`은 이 호출
이후 재사용할 수 없다).

**오류**: `refresh_token`이 유효하지 않거나 만료됐으면 `401 Unauthorized`
(`"리프레시 토큰이 유효하지 않거나 만료되었습니다. 다시 로그인해 주세요."`).

### `GET /api/v1/auth/me` — 내 정보 조회 (인증 필요)

토큰이 가리키는 로그인 계정의 `public.users` 프로필을 반환한다. 응답 스키마는
`UserRead`와 동일(1절 참조). 프론트엔드가 "현재 로그인 상태인지, 누구로 로그인했는지"를
앱 시작 시점에 확인하는 용도로 쓰면 된다.

### 인증이 걸린 나머지 엔드포인트에서 실패하는 방식

- `Authorization` 헤더 자체가 없으면 `403 Forbidden`(FastAPI `HTTPBearer`의 기본 동작).
- 헤더는 있지만 토큰이 위조/만료됐으면 `401 Unauthorized`
  (`"인증 토큰이 유효하지 않거나 만료되었습니다. 다시 로그인해 주세요."`).
- 토큰은 유효하지만 **경로의 `user_id`가 본인이 아니면** `403 Forbidden`
  (`"본인의 리소스만 접근할 수 있습니다."` — `GET /users/{user_id}`,
  `GET /autobiographies/{user_id}` 등 `user_id`가 경로에 직접 노출되는 엔드포인트).
- 토큰은 유효하지만 **`session_id`/`autobiography_id`/`character_id` 등으로 가리키는
  리소스가 존재하지 않거나 남의 것이면** `404 Not Found`로 응답한다(403이 아니라
  404인 이유: "존재하는데 당신 것이 아니다"라는 정보조차 노출하지 않기 위함 —
  `interview-sessions`, `autobiographies/{autobiography_id}/*` 계열 전부 이 정책).

---

## 1. Users — `/api/v1/users`

사용자(원칙적으로 자서전의 "주인공"인 시니어 본인) 계정과, 그 사용자에 대한 동의 기록을
다룬다. 자녀가 온보딩을 대신 세팅하더라도 계정의 `email`/`name`은 정보주체(부모) 기준으로
생성하는 것을 전제로 설계되어 있다(기획안 5절 "동의 주체 분리"). **다만 인증 체계는
"계정 하나 = 로그인 하나"를 전제로 한다 — 자녀가 부모 명의 계정에 로그인해 온보딩을
대신 진행하는 시나리오는 현재 같은 토큰(같은 계정)으로 단순화되어 있고, 자녀 전용
별도 로그인·"가족 초대" 같은 다중 사용자 접근 제어는 아직 설계되지 않았다.**

### `POST /api/v1/users` — 사용자 생성(=회원가입, 인증 불필요)

새 자서전 주인공 계정을 만든다. 이 뒤에 이어지는 모든 리소스(인터뷰 세션, 미디어, 자서전)는
`user_id`로 이 계정에 귀속된다. 이제 이 엔드포인트가 곧 회원가입이다 — 자세한 내용은
바로 위 "0. Authentication" 절 참조.

**요청 바디 (`UserCreate`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `email` | `EmailStr` | ✅ | 로그인 식별자 겸 유니크 키. 이미 등록된 이메일이면 `409 Conflict`. |
| `name` | `string` | ✅ | 표시 이름. |
| `password` | `string` | ✅ | 평문 비밀번호(최소 8자). Supabase Auth로 그대로 전달되며 이 서버는 저장하지 않는다. |
| `birth_year` | `int` \| `null` | ❌ | 출생연도. 미디어 업로드 시 `age_at_time`(당시 나이)과 함께 생애주기(`LifePeriod`) 자동 매핑의 기준이 된다. |
| `hometown` | `string` \| `null` | ❌ | 고향. 현재는 프로필 표시용 메타데이터로만 저장되고, 다른 로직에서 참조되지 않는다. |

**응답 `201 Created` (`UserRead`)**: `id`, `email`, `name`, `birth_year`, `hometown`,
`current_stage`(항상 초기값 `"onboarding"`으로 생성됨 — 위 공통 사항 참조). `password`/
`hashed_password`는 어떤 필드명으로도 응답에 포함되지 않는다.

**오류**: 이메일 중복 시 `409 Conflict`.

---

### `GET /api/v1/users/{user_id}` — 사용자 조회 (인증 필요, 본인만)

`user_id`로 프로필을 조회한다. 로그인한 본인이 아니면 `403`, 존재하지 않으면 `404`.
응답 스키마는 위 `UserRead`와 동일.

---

### `POST /api/v1/users/{user_id}/consents` — 동의 기록 생성 (인증 필요, 본인만)

기획안 5절(동의 주체 분리)·6절(주의의무 이행 증빙)의 실체. "누가, 언제, 어떤 버전의 고지문에
동의했는지"를 영구 기록으로 남긴다. 이 엔드포인트 자체는 UI에서 실제로 동의를 받는 절차를
대체하지 않는다 — 프론트엔드가 고지문을 보여주고 사용자가 동의 버튼을 누른 **직후**
호출해서 그 사실을 기록하는 용도다.

**요청 바디 (`ConsentCreate`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `consent_type` | `enum` | ✅ | 아래 표 참조 |
| `notice_version` | `string` | ✅ | 사용자가 실제로 확인한 고지문의 버전 문자열(자유 형식, 예: `"v1.2"`). 나중에 "그 시점에 어떤 문구였는지" 추적하는 근거가 된다. |
| `granted_by` | `enum` | ✅ | `self`(정보주체 본인) \| `guardian`(보호자/자녀 대리) |

`consent_type` 값과 용도:

| 값 | 언제 호출하나 | 관련 엔드포인트 |
| --- | --- | --- |
| `data_collection` | 온보딩 첫 세션에서 정보주체 본인의 데이터 수집·이용 동의를 받을 때 | — |
| `disclosure_realname` | 등장인물 실명 유지 고지문에 동의할 때 | 이 동의가 **선행되어야만** `POST /autobiographies/{id}/characters/{id}/retain-real-name`이 성공한다(없으면 409). |
| `retention_extension` | 원문 로그(Layer 0) 보관 기간 연장에 옵트인할 때 | — (현재 자동 삭제 배치 자체는 미구현이며, 이 동의는 기록만 되고 아직 삭제 로직에서 소비되지 않는다) |

**응답 `201 Created` (`ConsentRead`)**: `id, user_id, consent_type, notice_version, granted_by, granted_at, revoked_at(null)`

**오류**: `user_id`가 존재하지 않으면 `404 Not Found`.

---

### `GET /api/v1/users/{user_id}/consents` — 동의 기록 전체 조회 (인증 필요, 본인만)

이 사용자의 모든 `ConsentRead` 레코드를 배열로 반환한다(철회된 것 포함, `revoked_at`으로
구분). 정렬 순서는 게이트웨이 구현에 위임되어 있으며 API 계약으로 보장되지 않는다.

---

## 2. Interview Sessions — `/api/v1/interview-sessions`

기획안의 인터뷰 페르소나·슬롯 게이팅·꼬리 질문·감정 세이프가드 로직이 실제로 실행되는
곳(`app/services/interview_service.py`, 프롬프트는 `app/agents/prompts.py`). 하나의
`InterviewSession`은 "사진 한 장" 또는 "고정 질문 하나"에 대해 열리는 대화 단위다.

**슬롯 11개**(대화가 충분히 진행됐는지 판단하는 기준): 필수 5개 —
`place`(장소), `time`(시기), `event`(사건 내용), `emotion`(감정), `values`(가치관) —
와 선택 6개 — `gratitude`(감사), `regret`(후회), `turning_point`(전환점), `pride`(자부심),
`belief`(신념), `message`(후대에 남기고 싶은 말).

### `POST /api/v1/interview-sessions` — 세션 생성 (인증 필요)

**요청 바디 (`SessionCreate`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `session_type` | `"photo"` \| `"fixed_question"` | ✅ | 아래 참조. |
| `question_id` | `UUID` \| `null` | 조건부 | `session_type="fixed_question"`일 때 사용(어떤 고정 질문에 대한 대화인지). Pydantic 레벨에서는 선택 필드지만, 논리적으로 이 타입이면 채우는 것이 맞다 — 현재 API가 타입-필드 일치를 강제 검증하지는 않는다. |
| `linked_media_asset_id` | `UUID` \| `null` | 조건부 | `session_type="photo"`일 때 사용(어떤 사진에 대한 대화인지). 마찬가지로 강제 검증은 없다. |

`user_id`는 요청 바디에 없다 — 인증 토큰(`Authorization: Bearer ...`)의 로그인 사용자로
항상 고정된다(다른 사람 명의로 세션을 만들 수 없도록 서버가 강제). 내부 동작:
`slots_filled`를 11개 슬롯 모두 `false`로 초기화해 세션을 생성한다.

**응답 `201 Created` (`SessionRead`)**: `id, user_id, session_type, question_id,
linked_media_asset_id, status("open"), slots_filled(전부 false), followup_count(0),
is_must_include(false), started_at, completed_at(null)`

---

### `GET /api/v1/interview-sessions/{session_id}` — 세션 조회 (인증 필요, 본인 세션만)

`SessionRead`(위와 동일 스키마)를 반환한다. 세션이 없거나 본인 소유가 아니면 `404`
(둘을 구분하지 않는다 — 아래 "0. Authentication" 절 참조).

> **주의**: 이 응답에는 대화 로그(`ChatLog`) 목록이 포함되지 않는다 — 세션의 메타데이터
> (상태·슬롯 충족 현황·꼬리질문 횟수)만 보인다. 현재 이 프로젝트에는 "세션의 전체 대화
> 이력을 조회하는" 별도 엔드포인트가 없다. 프론트엔드는 `POST .../messages` 호출마다
> 돌아오는 `TurnResponse`를 클라이언트 측에 누적해서 화면의 대화창 히스토리를 구성해야
> 한다(페이지 새로고침 시 히스토리가 사라진다는 뜻이므로, 향후 `GET
> .../messages`류의 엔드포인트 추가가 필요할 수 있다).

---

### `POST /api/v1/interview-sessions/{session_id}/messages` — 사용자 발화 전송 (인증 필요, 본인 세션만)

인터뷰 루프의 핵심. 유저가 메시지 하나를 보내면, 에이전트가 (a) 위기 신호를 먼저 검사하고,
아니라면 (b) 저비용 슬롯 판별로 다음에 무엇을 물을지 결정한다. **여기서는 정밀한 이벤트
추출(사건 분할·라벨링)을 하지 않는다** — 그건 세션 종료 후 Celery 워커가 별도로 수행한다.

**요청 바디 (`ChatMessageCreate`)**: `content: string` (사용자가 방금 입력/발화한 텍스트)

**내부 처리 순서**:

1. 사용자 메시지를 `ChatLog(role=user)`로 저장.
2. `content`에 위기 키워드(`CRISIS_KEYWORDS`: "죽고 싶", "자살", "그만 살고 싶", "살기 싫",
   "사라지고 싶", "극단적 선택")가 하나라도 포함되면 **Upstage를 호출하지 않고** 고정 문구
   (`TIER2_CRISIS_RESPONSE` — 자살예방상담전화 등 안내 포함)를 그대로 응답으로 사용하고,
   세션 상태를 즉시 `completed`로 전환한다.
   > **중요**: 이 분기는 세션 **상태만** `completed`로 바꿀 뿐, Phase 2 후처리(이벤트 추출)를
   > 큐에 넣지 않는다. 위기 신호로 세션이 자동 종료된 경우에도, 실제 이벤트 추출을 실행하려면
   > `POST .../complete`를 **별도로 명시 호출**해야 한다.
3. 위기 신호가 없으면 슬롯 게이팅을 수행한다: 현재까지 채워진 슬롯 상태 + 방금 답변을
   Solar Structured Outputs에 넣어 "이 답변으로 새로 채워진 슬롯"만 저비용 판별
   (`reasoning_effort="low"`)한다. 이 판별 결과는 다음 질문을 고르는 데만 쓰이고
   영속 라벨로 저장되지 않는다.
4. 필수 슬롯(5개) 중 아직 비어 있는 게 있고, `followup_count < 2`(예산 남음)면 꼬리
   질문을 생성해 반환한다. 예산이 소진됐거나 필수 슬롯이 다 채워졌으면
   `"말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"` 고정 문구를 반환한다(생애주기별
   다음 질문 오케스트레이션 자체는 아직 미구현 — TODO).
5. 에이전트 응답을 `ChatLog(role=assistant)`로 저장.

**응답 (`TurnResponse`)**:

```
{
  "user_message":      { "id", "session_id", "role": "user",      "content", "turn_index", "created_at" },
  "assistant_message": { "id", "session_id", "role": "assistant", "content", "turn_index", "created_at" },
  "session": { ...갱신된 SessionRead (slots_filled/followup_count 최신 반영) }
}
```

**오류**: 세션이 없거나 본인 것이 아니면 `404`.

---

### `POST /api/v1/interview-sessions/{session_id}/complete` — 세션 종료 (인증 필요, 본인 세션만)

세션을 `completed` 상태로 전환하고, **Phase 2 후처리(이벤트 추출 파이프라인)를 Celery
큐에 넣는다.** 위 위기 대응 분기와 달리, 이 엔드포인트는 호출될 때마다 무조건
`process_session_completion` 태스크를 큐에 넣는다(이미 `completed` 상태였어도 재실행됨 —
멱등성은 보장되지 않으므로 중복 호출에 주의).

큐에 들어간 태스크(`app/workers/tasks.py` → `app/services/event_extraction_service.py`)가
백그라운드에서 순서대로 수행하는 일:

1. 세션의 `chat_logs`(user 턴만)를 1인칭 산문으로 재조립 → `InterviewSession.session_prose`에 저장(Layer 2)
2. 재조립본-원문 왜곡 탐지 (**현재 자리표시자 — 항상 통과**, 아래 "알려진 한계" 참조)
3. 산문을 사건 단위로 분할하고 라벨 추출(Structured Outputs, `reasoning_effort="medium"`)
4. 추출된 각 사건을 `Event(source_type="session_chat", verified=true)`로 저장 + 임베딩 계산(`embedding-passage`)
5. 사건 간 관계(`cause`/`overcome`/`followed_by`/`related`)를 `EventRelation`으로 저장

**응답 (`SessionRead`)**: 즉시 반환되며(Celery 큐잉은 논블로킹), `status="completed"`가 이미
반영된 상태다. 이벤트 추출 자체의 완료 여부는 이 응답으로 알 수 없다 — 확인하려면 이후
`GET /autobiographies/{user_id}`나 챕터 목차 생성 시도가 성공하는지로 간접 확인해야 한다
(사건 조회를 위한 별도 엔드포인트는 현재 없음).

---

## 3. Media Assets — `/api/v1/media-assets`

### `POST /api/v1/media-assets` — 미디어 원본 업로드 (인증 필요)

Phase 1(기록물 대량 스캔)의 진입점. 사진/문서를 S3(Layer 0, 불변 원천)에 저장하고,
이미지인 경우 즉시 듀얼 트랙 분석을 실행한다.

**요청 (`multipart/form-data`)**

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `file` | 업로드 파일 | ✅ | 원본 바이너리. |
| `session_id` | `UUID` \| `null` (Form) | ❌ | 이 업로드가 특정 인터뷰 세션 도중 이루어졌다면 그 세션 ID. |
| `asset_type` | `image`\|`audio`\|`video`\|`document` (Form) | ❌(기본 `image`) | **`image`가 아니면 아래 분석 파이프라인이 아예 실행되지 않는다** — 현재 듀얼 트랙 분석은 이미지 전용이며, audio/video/document는 S3 저장만 되고 텍스트 추출·이벤트 스테이징은 수행되지 않는다. |
| `age_at_time` | `int` \| `null` (Form) | ❌ | 사진 속 당시 나이. `map_age_to_life_period()`가 이 값을 생애주기로 자동 변환한다: `<13`→`childhood`, `13~19`→`youth`, `20~59`→`adulthood`, `60+`→`senior`. |
| `location_at_time` | `string` \| `null` (Form) | ❌ | 당시 장소(사용자 입력). |
| `people_at_time` | `string` \| `null` (Form) | ❌ | 당시 함께 있던 인물(사용자 입력). |
| `user_comment` | `string` \| `null` (Form) | ❌ | 사용자가 이 사진에 남긴 짧은 코멘트. 순수 추억 사진(`pure_memory`) 트랙에서는 이 코멘트가 유일한 문맥 자료가 된다. |

`user_id`는 Form 필드로 받지 않는다 — 인증 토큰의 로그인 사용자로 고정된다(다른 사람
명의로 업로드하는 경로 차단). `session_id`를 지정한 경우 그 세션이 실제로 본인 소유인지는
아직 이 엔드포인트에서 교차검증하지 않는다(TODO — 현재는 세션 라우터 쪽 소유권 검증에만
의존).

**내부 처리 (asset_type=image일 때)**:

1. S3에 원본 업로드 → `MediaAsset` 레코드 생성(이 시점까지는 항상 성공).
2. `document_parse.parse_document_sync(...)`로 텍스트/레이아웃 추출 시도(동기 호출, 서버측 최대 5분).
3. 추출된 텍스트가 **20자 미만**이면 `analysis_track="pure_memory"`로 확정하고 종료 — 이벤트는
   생성되지 않는다(사용자 코멘트만 남는다).
4. 20자 이상이면 `analysis_track="text_document"`로 확정, Document Parse 원시 응답을
   `pre_extracted_labels`에 캐시 저장. 이어서 Solar Structured Outputs로 1차 타당성 검증
   (오인식/깨진 텍스트 의심 여부)을 수행한다.
5. 검증 결과에 따라 `Event(source_type="document")`를 생성한다: 의심 없음(`suspicious=false`)이면
   `verified=true`로 즉시 저장하고 임베딩까지 계산. 의심되면 `verified=false`로 저장하고
   임베딩은 생성하지 않는다(RAG에서 완전히 제외됨 — Layer 1 검증 게이트).
   > 이 `verified=false` 사건이 실제 인터뷰 확인 질문으로 유저에게 제시되어 `verified=true`로
   > 승격되는 로직은 아직 인터뷰 턴(`interview_service`)에 연결되어 있지 않다(TODO). 즉
   > 현재는 격리는 되지만 승격 경로가 없다 — 격리된 채로 영구히 남는다.

**응답 `201 Created` (`MediaAssetRead`)**: `id, user_id, session_id, s3_url, asset_type,
age_at_time, location_at_time, people_at_time, life_period_mapped, analysis_track,
user_comment, created_at`

**소요 시간 참고**: 이 엔드포인트 하나가 (S3 업로드 → Document Parse → Solar 검증 →
임베딩) 최대 4개의 외부 API를 동기적으로 순차 호출할 수 있으므로, 대형 이미지/문서일수록
응답이 느릴 수 있다(현재 이 경로는 Celery로 분리되어 있지 않음).

---

## 4. Autobiographies — `/api/v1/autobiographies`

Phase 3(이벤트 병합·중요도 산정·스타일 바이블)과 Phase 4(동적 목차·하향식 집필·팩트체크·
근거검증·등장인물 검토)가 이 리소스 아래에 모여 있다
(`app/services/autobiography_service.py`, `app/services/character_service.py`).

**진행 순서(엄격한 선행 조건이 있음)**:

```
세션 complete 반복
   → POST /{user_id}/consolidate            (Phase 3, 202)
      → POST /{id}/toc/generate             (Phase 4-1, 동기 200)
         → POST /{id}/toc/select             (Phase 4-2, 동기 200)
            → 챕터마다 POST /{id}/chapters/{cid}/write   (Phase 4-3, 202) × N
               → POST /{id}/finalize          (Phase 4-4, 202)
```

각 단계는 이전 단계의 산출물을 전제로 하며, 건너뛰면 아래 명시된 오류가 발생한다.
**아래 전 엔드포인트는 인증이 필요하다.** `{user_id}`가 경로에 직접 있는 두 엔드포인트
(`GET /{user_id}`, `POST /{user_id}/consolidate`)는 본인이 아니면 `403`, 그 외
`{autobiography_id}`로 접근하는 엔드포인트(목차/챕터/등장인물)는 존재하지 않거나 본인
소유가 아니면 `404`로 응답한다(둘 다 실제 DB 조회 후 소유자를 비교하는 공통 헬퍼
`_require_own_autobiography`를 거친다 — `app/api/v1/autobiographies.py`).

### `GET /api/v1/autobiographies/{user_id}` — 자서전 조회 (get-or-create, 인증 필요·본인만)

이 사용자의 `Autobiography`를 조회한다. **아직 없으면 자동으로 하나 생성**한다
(`status="in_progress"`, 나머지 필드는 전부 `null`). 즉 이 엔드포인트는 절대 404를 반환하지
않는다 — 존재 확인용이 아니라 "이 유저의 자서전 레코드를 가져오거나 만든다"는 의미다.

**응답 (`AutobiographyRead`)**: `id, user_id, title, status(in_progress|consolidated|published),
toc_data, style_bible, book_synopsis, final_content, created_at, updated_at`

---

### `POST /api/v1/autobiographies/{user_id}/consolidate` — Phase 3 트리거

**`202 Accepted`**. 응답: `{"detail": "Phase 3 consolidation queued"}` — 이 시점에는
아무 것도 아직 계산되지 않았다는 뜻이다.

큐에 들어간 `consolidate_autobiography` 태스크가 순서대로 수행하는 일(순서가 결과에
영향을 준다 — 중복 이벤트를 먼저 병합해야 반복 언급 횟수가 중요도 점수에 정확히 반영됨):

1. **열람용 원본 조립**: 완료된 세션들의 `session_prose`를 시간순으로 이어붙여
   `consolidated_content`에 저장(이 텍스트는 이후 LLM 입력으로 재사용되지 않는다 — 사람이
   훑어보는 용도).
2. **중복 이벤트 병합**: 임베딩 코사인 거리 0.2 이내인 쌍을 후보로 뽑아, 각 쌍을 LLM에게
   "같은 사건인가" 물어본다. 판정이 불확실하면 병합하지 않는 것이 기본값(과병합은 인쇄 후
   회복 불가, 과분리는 사용자 확인으로 회복 가능하다는 비대칭 때문).
3. **중요도 스코어링**: `길이 z-score×1.0 + 감정강도×0.5 + (반복언급횟수-1)×1.5 +
   (생애이정표매칭×2.0) + (꼭넣기지정×1000.0)`의 가중합으로 각 `Event.importance_score`를
   계산하고, 근거를 `importance_signals`에 스냅샷으로 남긴다.
4. **스타일 바이블 생성**: 전체 세션 산문을 한 번에 Solar에 넣어 문체/가치관/감정 아크를
   요약한 문서를 생성해 `style_bible`에 저장.
5. `Autobiography.status`를 `"consolidated"`로 전환.

**전제 조건**: 이벤트가 하나도 없어도(=아직 어떤 세션도 완료되지 않았어도) 에러 없이
진행되며, 그 경우 `style_bible=null`인 채로 `status="consolidated"`까지 그냥 전환된다 —
즉 이 엔드포인트는 "충분한 데이터가 쌓였는지" 자체를 검증하지 않는다. 이후 `toc/generate`
단계에서 이벤트가 없으면 그때 비로소 실패한다(아래 참조).

---

### `POST /api/v1/autobiographies/{autobiography_id}/toc/generate` — 목차 후보 생성

`consolidate`와 달리 **동기 처리**(LLM 호출 1회로 끝나므로 Celery로 위임하지 않음).
`verified=true`이고 병합으로 흡수되지 않은 이벤트를 전부 모아 `"[중요도 12.5] 부산 출생
(시기: 1978년, 감정: 미상)"` 형식의 목록으로 만든 뒤, Structured Outputs로 서로 다른 구성
관점(연대기순/주제별/인물중심 등)의 목차 후보 3안을 받는다.

**응답 (`AutobiographyRead`)**: `toc_data`가 다음 형태로 채워진다.

```json
{
  "generated_at": "2026-...",
  "candidates": [
    {"chapters": [{"chapter_index": 1, "title": "...", "theme_keywords": ["..."]}, ...]},
    {"chapters": [...]},
    {"chapters": [...]}
  ],
  "selected_candidate_index": null
}
```

**오류**: 대상 이벤트가 하나도 없으면(Phase 3 미실행 또는 이벤트가 실제로 0개) `409 Conflict`
— `"목차를 생성하려면 먼저 Phase 3(consolidate_autobiography)이 완료되어야 합니다."`

---

### `POST /api/v1/autobiographies/{autobiography_id}/toc/select` — 목차 확정

**요청 바디 (`TocCandidateSelect`)**: `candidate_index: int` — 위에서 받은 후보 배열의
인덱스(0, 1, 2 중 하나).

**내부 동작**: 선택된 후보의 챕터 배열로 `ChapterDraft` 레코드들을 생성한다(`content`는 아직
`null`). **재호출 시 이전 챕터 초안을 전부 대체**한다(멱등적 — 여러 번 호출해도 안전하며,
매번 그 시점의 선택으로 덮어써짐). 이어서 스타일 바이블 + 선택된 목차를 바탕으로 책 전체
시놉시스(`book_synopsis`, 하향식 집필의 최상위 설계도)를 생성한다.

**응답 (`AutobiographyRead`)**: `toc_data.selected_candidate_index`가 채워지고
`book_synopsis`에 본문이 채워진 상태.

**오류**:
- 아직 `toc/generate`를 호출하지 않았으면 `409 Conflict`(`"먼저 목차 후보를 생성해야
  합니다."`)
- `candidate_index`가 후보 개수(보통 3) 범위를 벗어나면 `409 Conflict`

---

### `GET /api/v1/autobiographies/{autobiography_id}/chapters` — 챕터 초안 목록

`chapter_index` 오름차순으로 `ChapterDraftRead` 배열을 반환한다. `toc/select` 직후에는
`content`, `factcheck_report`, `groundedness_report`가 전부 `null`이고 `status="draft"`다.

**응답 스키마 (`ChapterDraftRead`)**: `id, autobiography_id, chapter_index, title,
chapter_synopsis, content, source_event_ids, factcheck_report, groundedness_report,
status(draft|reviewed|finalized), created_at, updated_at`

---

### `POST /api/v1/autobiographies/{autobiography_id}/chapters/{chapter_draft_id}/write` — 챕터 집필 트리거

**`202 Accepted`**. 챕터 하나를 하향식 집필 파이프라인 전체(시놉시스→RAG 소환→본문→
팩트체크→근거검증→등장인물 스캔)에 통과시킨다. 챕터마다 개별 호출해야 한다(일괄 처리
엔드포인트 없음).

큐에 들어간 `write_chapter` 태스크가 수행하는 일:

1. 챕터 제목을 쿼리로 삼아 **하이브리드 검색**(임베딩 유사도 상위 10개 + 제목 단어의
   키워드 정확 매칭 상위 10개를 합쳐 최대 10개로 제한)으로 이 챕터에 소환할 `Event`들을
   결정한다. 둘 다 `verified=true`이고 병합 흡수되지 않은 이벤트만 대상(Layer 1 게이트).
2. 책 시놉시스 + 소환된 사건 요약으로 챕터 시놉시스 생성.
3. 직전 챕터 본문의 마지막 1000자(요약 대신 근사치)를 함께 프롬프트에 주입.
4. [스타일 바이블 + 책 시놉시스 + 챕터 시놉시스 + 직전 챕터 말미 + 소환된 사건 문단들]로
   본문 집필(`reasoning_effort="high"`).
5. **팩트체크**: 생성된 본문에서 인명/연도·나이/지명/수량 팩트를 재추출한 뒤, 소환된
   원천 이벤트들의 `place`/`people`/`occurred_at_label` 값과 **대소문자 무시 부분 문자열
   매칭**으로 대조한다. 일치하는 게 없으면 `flags`에 추가. `quantity`(수량) 타입은 `Event`
   모델에 대조할 필드 자체가 없어 검증하지 못하고 `unchecked_facts`로만 집계된다
   (기획안이 요구하는 "연도 절대환산·지명 정규화·인명 별칭 매핑" 같은 개체 정규화는
   미구현 — 현재는 단순 문자열 포함 여부만 본다).
6. **근거검증(Groundedness Check)**: **현재 자리표시자** — 실제 NLI 판정 없이
   `{"checked": false, "flags": [], "note": "NLI 로컬 모델 미연동 — 자리표시자, 항상 통과 처리", ...}`
   를 그대로 저장한다. `checked=false`가 이 검증이 아직 실행되지 않았다는 신호다.
7. **등장인물 스캔**: 본문에서 NER로 인물 후보를 뽑아 `Character` 레코드를 만들고
   (동일 `autobiography` 내 동일 실명이면 기존 레코드 재사용), 각 인물이 등장하는 문단의
   서술 성격(범죄/부정적 평가/갈등 등)을 분류해 `risk_classification`을 갱신한다. 이
   분류는 가명화 여부를 결정하지 않는다 — `real_name_retained`는 이 단계와 무관하게
   항상 `false`로 시작한다.
8. 챕터 `status`를 `"reviewed"`로 전환.

**응답**: `{"detail": "Chapter writing queued"}` (202) — 완료 여부는
`GET .../chapters`로 폴링해 `content`가 채워지고 `status`가 바뀌었는지 확인해야 한다.

**오류**:
- `chapter_draft_id`가 해당 `autobiography_id`에 속하지 않거나 존재하지 않으면 요청
  시점에 즉시 `404 Not Found`.
- **`book_synopsis`가 없으면(=`toc/select`를 아직 안 했으면) 태스크 내부에서
  `ValueError`가 발생하지만, 이미 202로 응답한 뒤이므로 HTTP 레벨로는 이 실패가 전혀
  전달되지 않는다.** 클라이언트는 `GET .../chapters`를 폴링했을 때 `content`가 계속
  `null`인 것으로만 실패를 간접 추정할 수 있다 — 현재 실패 알림/재시도 메커니즘은 없다.

---

### `POST /api/v1/autobiographies/{autobiography_id}/finalize` — 통일성 윤문 트리거

**`202 Accepted`**. 모든 챕터의 집필이 끝난 뒤, 전체 원고와 스타일 바이블을 함께 Solar에
넣어 인접 챕터 경계부의 어조·문체 단절만 다듬는 리비전을 1회 수행한다(사실관계·순서는
변경하지 않도록 프롬프트에 명시됨). 완료되면 모든 챕터의 `status`가 `"finalized"`로
바뀌고, `Autobiography.final_content`에 윤문된 전체 원고가 저장된다.

**응답**: `{"detail": "Manuscript finalization queued"}`

**오류**: 자서전 자체가 없거나 본인 소유가 아니면 요청 시점에 즉시 `404`(인증 작업 이전에는
이 사전 조회 자체가 없어 존재하지 않는 `autobiography_id`를 넣어도 무조건 202가 나갔다 —
소유권 검증을 추가하면서 함께 바로잡았다). 챕터가 하나도 없거나, 하나라도 `content`가
`null`인 챕터가 있으면 태스크 내부에서 `ValueError`가 발생한다(위 챕터 집필과 동일하게
**202 응답 이후의 실패라 HTTP로 전달되지 않는다** — 반드시 모든 챕터의 `write`가 끝난 뒤
호출할 것).

---

### `GET /api/v1/autobiographies/{autobiography_id}/characters` — 등장인물 목록

`write_chapter` 과정에서 자동 스캔된 제3자 인물 목록을 반환한다(구술자 본인은 제외).

**응답 (`CharacterRead[]`)**: `id, autobiography_id, display_name, real_name,
relation_to_user, risk_classification(none|negative_portrayal|conflict|crime_mention),
real_name_retained, disclosure_notice_version, disclosure_acknowledged_at, created_at`

`display_name`은 원고에 실제로 노출되는 이름(기본은 가명), `real_name`은 NER로 확보된
실제 이름(내부 참조·본인 확인용, 원고에는 노출 안 됨), `real_name_retained`는 전수
가명화 기본값(opt-out)이 뒤집혔는지 여부.

---

### `POST /api/v1/autobiographies/{autobiography_id}/characters/{character_id}/retain-real-name` — 실명 유지 전환

전수 가명화 기본값(opt-out)을 뒤집는 **유일한** 경로. 인물 한 명을 가명 대신 실명으로
원고에 남기고 싶을 때 호출한다.

**요청 바디 (`RetainRealNameRequest`)**: `notice_version: string` — 사용자가 확인한 법적
책임 고지문의 버전(주의의무 이행 증빙으로 저장됨).

**전제 조건**: 이 인물이 속한 `autobiography`의 소유자(`user_id`)가 `ConsentType
.disclosure_realname` 동의를 **먼저** 기록해두어야 한다(`POST /users/{id}/consents`로).

> **알려진 한계**: 기획안은 "인물 단위" 법적 책임 고지 동의를 요구하지만, 현재 동의
> 게이트는 인물 단위가 아니라 **"이 사용자가 실명 유지 고지에 최소 1회 동의했는가"**로
> 완화되어 있다(`ConsentRecord`가 아직 인물별로 세분화되어 있지 않음 — DB 스키마 확장
> 필요). 즉 한 인물에 대해 동의하면, 같은 사용자의 다른 인물에 대해서도 이 게이트를
> 통과할 수 있다.

**응답 (`CharacterRead`)**: `real_name_retained=true`로 갱신된 레코드.

**오류**:
- `character_id`가 해당 `autobiography_id`에 속하지 않거나 없으면 `404`.
- 유효한 `disclosure_realname` 동의가 없으면 `409 Conflict`
  (`"인물 '...' 실명 유지 전 DISCLOSURE_REALNAME 동의가 필요합니다."`)

---

## 5. Health

### `GET /health`

`{"status": "ok"}` 고정 응답. DB/S3/Upstage 어느 것도 건드리지 않는 순수 liveness
체크(프로세스가 살아있는지만 확인. 의존 서비스 정상 여부는 보장하지 않음).

---

## 6. 샌드박스 — `/api/v1/sandbox/*`

`app/agents/prompts.py`의 모든 프롬프트를 DB 없이 즉시 테스트하는 무인증 개발자 도구.
19개 엔드포인트 각각의 목적·입출력은 **[SANDBOX_GUIDE.md](./SANDBOX_GUIDE.md)** 에
정리되어 있다.

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

## 부록: 현재 알려진 한계 요약 (다시 보기 편하도록 한 곳에 모음)

| 한계 | 위치 | 영향 |
| --- | --- | --- |
| 왜곡 탐지(NLI 함의검증)가 항상 통과하는 자리표시자 | `event_extraction_service._passes_distortion_check` | `verified=true` 승격이 실질적 검증을 거치지 않음 |
| 근거검증(Groundedness)이 항상 통과하는 자리표시자 | `autobiography_service._run_groundedness_check_placeholder` | 챕터 본문의 "출처 없는 서술" 탐지가 동작하지 않음(`checked=false`로 표시는 됨) |
| 팩트체크가 단순 substring 매칭 | `autobiography_service._run_factcheck` | 개체 정규화(연도 환산 등) 없이 표기가 조금만 달라도 오탐/누락 가능, `quantity`는 대조 불가 |
| 위기 대응 시 이벤트 추출이 자동 큐잉되지 않음 | `interview_service.add_user_turn` 위기 분기 | `/messages`에서 세션이 자동 종료돼도 `/complete`를 별도 호출해야 후처리가 실행됨 |
| Phase 4 202 엔드포인트의 실패가 HTTP로 전달 안 됨 | `chapters/{id}/write`, `finalize` | 선행 조건(`book_synopsis`/전 챕터 집필 완료) 미충족 시 Celery 태스크 내부에서만 실패, 클라이언트는 폴링으로만 간접 확인 가능. (자서전 자체가 없거나 남의 것인 경우는 인증 작업 때 사전 조회로 막아 즉시 404를 받도록 고쳤다 — 이 항목은 그 이후 단계, 즉 "존재는 하지만 아직 준비가 안 된" 경우에만 해당) |
| 등장인물 실명 동의가 인물 단위가 아닌 사용자 단위 | `character_service.retain_real_name` | 한 인물에 대한 동의로 같은 사용자의 다른 인물도 게이트 통과 가능 |
| `current_stage` 미갱신 | `User` 모델 전반 | 항상 `onboarding`으로 고정, 다른 값으로 전환하는 로직 없음 |
| OCR 확인 질문이 인터뷰 턴에 미연결 | `media_service` 모듈 docstring 명시 | `verified=false`로 격리된 문서 유래 이벤트가 승격될 경로가 없음 |
| 세션 히스토리 조회 엔드포인트 없음 | `interviews.py` | 대화 전체 이력을 서버에서 다시 불러오는 API가 없어 프론트가 클라이언트 측에 누적해야 함 |
| 계정 = 로그인 하나로 단순화됨(2026-07-09 인증 추가분) | `app/api/deps.py`, `users.py` | 자녀가 부모를 대신해 온보딩/동의하는 기획안의 "동의 주체 분리" 시나리오가, 현재는 자녀가 부모 계정에 직접 로그인해 대신 조작하는 것으로만 구현 가능하다 — 자녀 전용 별도 계정으로 부모 계정에 위임 접근하는 "가족 초대" 흐름은 미구현 |
| 미디어 업로드 시 `session_id` 소유권 교차검증 없음 | `app/api/v1/media.py` | 업로드한 사람이 다른 사람 소유의 `session_id`를 값으로 넣어도 서버가 막지 않는다(세션 자체를 열람/조작하는 것은 여전히 세션 라우터의 소유권 검증으로 막힘) |
