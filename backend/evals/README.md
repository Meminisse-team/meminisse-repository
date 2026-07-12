# P3 — 정량 평가체계

기획안 슬라이드 자료(예: "정보 보존율 recall/precision 곡선")의 근거가 될 정량 평가
4가지 중, **지금은 1번(합성 페르소나 벤치마크)만** 만들었다. 나머지 3개는 이 벤치마크가
만들어내는 데이터가 있어야 시작할 수 있거나(DeepEval), 별도 판단이 필요해서(SUS는 실제
사람이 필요) 미착수 상태로 남겨둔다 — 아래 "다음 단계"에 각각 무엇을, 어떻게 이어붙이면
되는지 적어둔다.

## 1. 합성 페르소나 벤치마크 (하네스 완성, 5명 파일럿은 2/5만 성공 — 2026-07-12)

`evals/personas.py`에 정의한 시니어 페르소나 5명이 각자 인생의 사건 하나씩을 실제
인터뷰 파이프라인(`app/services/interview_service.py`)에 그대로 들려주고, 세션 종료 후
실제 Phase 2 후처리(`app/services/event_extraction_service.py`)까지 거쳐 Event로
추출되는 전 과정을 검증한다.

**파일럿 실행 결과(중요 — 30명으로 늘리기 전에 읽을 것):** 5명 중 인터뷰 대화 자체(턴
진행·슬롯 게이팅·꼬리 질문)는 5명 전원 정상 동작을 확인했지만, 세션 종료 후 처리
(`process_completed_session` — NLI 왜곡 탐지 + Solar 이벤트 추출)가 이 개발 환경에서
간헐적으로 몇 분~수십 분씩 멈추는 문제가 있어 실제로 끝까지 완주해 결과 파일을 남긴
건 2명(`p01_kim_soonja`, `p02_park_youngsoo`, `evals/results/pilot_2026-07-12/`)뿐이다.
원인을 좁혀보니 두 갈래였다:
1. **NLI 로컬 추론이 문장당 8~18초로 비정상적으로 느렸다** — 문장별로 개별 호출하던
   것을 배치 처리(`nli.classify_entailment_batch`)로 바꿔 4~6배 개선했다(프로덕션
   `event_extraction_service._passes_distortion_check`도 함께 개선됨 — Phase 2 후처리
   전체의 실제 성능 개선이라 이 벤치마크만이 아니라 실사용에도 이득이다).
2. **그래도 간헐적으로 특정 호출(NLI 배치 또는 Solar 이벤트 추출 API 호출)이 응답 없이
   멈췄다** — `app/clients/base.py`의 Upstage 클라이언트에 타임아웃이 아예 없던 걸
   발견해 90초로 추가했고, `evals/run_benchmark.py`에도 단계별 120초 안전장치
   (`_stage`)를 넣었다. 두 안전장치 모두 "영영 멈추는 것"은 막았지만(결국엔 실패
   처리되고 다음 페르소나로 넘어감), 멈춘 호출 하나가 실제로 실패로 확정되기까지
   체감상 120초보다 훨씬 오래 걸리는 경우가 있었다 — `asyncio.wait_for`가 이미 실행
   중인 스레드풀 작업(NLI)이나 저수준 소켓 read를 즉시 끊지 못하고, 그 작업이
   자연 종료될 때까지 기다렸다가 취소를 전달하는 것으로 보인다. **근본 원인(왜 이
   환경에서 Solar/NLI 호출이 간헐적으로 오래 걸리는지)은 이번 세션에서 확정하지
   못했다** — 다음에 이 벤치마크를 30명으로 돌릴 때는 시간을 넉넉히 잡거나, 더
   안정적인 환경(예: 팀 공용 서버)에서 실행하는 걸 권한다.

**실제로 완주한 2명의 결과에서 발견한 것(추출 파이프라인 자체의 이슈, 벤치마크 인프라
문제와는 별개):**
- `p01_kim_soonja`는 사건 하나가 6개의 세부 이벤트로 쪼개졌는데, `place`/`emotion_tag`
  가 대부분 `null`로 비어 있었다 — 원문(재조립 산문)에는 "셋방", "불안" 같은 정보가
  분명히 있는데도 추출 단계에서 슬롯을 못 채운 경우가 있다는 뜻. 이게 바로 P3가
  측정하려는 "정보 보존율"이 낮게 나올 수 있는 구체적 사례다 — DeepEval 단계(2절)에서
  정량화할 첫 대상으로 삼기 좋다.
- `p02_park_youngsoo`는 마지막 추출 이벤트가 "인터뷰어에게 감사 인사 전달"이었다 —
  이건 페르소나(유저)의 사건이 아니라 인터뷰 에이전트(assistant)가 세션을 마무리하며
  한 말("말씀해주셔서 감사해요...")이 사건으로 잘못 추출된 것으로 보인다. 세션 산문
  재조립·이벤트 추출 프롬프트가 assistant 턴을 사건 후보에서 확실히 배제하는지
  점검이 필요해 보인다(이번 세션에서는 원인 조사·수정까지는 하지 않았다 — 발견만
  기록).

**페르소나 쪽 발화는 누가 만드나?** `evals/persona_agent.py`가 Solar에게 "이 사람이
되어 인터뷰에 답하라"는 역할을 맡긴다 — 실제 인터뷰 에이전트(`INTERVIEW_PERSONA_SYSTEM_
PROMPT`)와 대칭되는 반대편 역할이라고 보면 된다. 각 페르소나는 사건의 슬롯 중 1~2개를
첫 발화에서 일부러 숨기도록 설계했다(`GroundTruthEvent.withhold_on_first_turn`) —
그래야 꼬리 질문(`FOLLOWUP_SYSTEM_PROMPT`) 경로도 함께 검증된다.

### 실행 방법

```powershell
cd backend
..\venv\Scripts\python -m evals.run_benchmark
```

- **DB는 항상 Mock이다.** 스크립트 최상단에서 `GATEWAY_BACKEND`를 강제로 `mock`으로
  덮어쓴다(`.env`가 `postgres`로 돼 있어도 무시된다) — 합성 페르소나가 팀이 쓰는 실제
  개발 DB에 섞여 들어가는 걸 막기 위한 안전장치다.
- **Solar/임베딩 API 호출은 진짜다.** DB만 목업이고 나머지(페르소나 발화 생성, 슬롯
  게이팅, 꼬리 질문, 산문 재조립, NLI 왜곡 탐지, 이벤트 추출, 임베딩)는 전부 실제
  Upstage API를 호출하는 실동작 검증이다 — 실행할 때마다 API 비용이 발생한다.
- Celery 워커 없이 동기로 직접 실행한다(`event_extraction_service.process_completed_
  session`을 바로 호출) — 지금 필요한 건 결과를 즉시 파일로 받는 것이지 비동기 큐잉
  자체를 검증하는 게 아니기 때문이다.
- 실행 하나가 오래(수십 분) 걸릴 수 있다 — 위 "파일럿 실행 결과" 참고. 페르소나별로
  독립적으로 실패 처리되니(`[실패]` 로그 + 다음 페르소나로 진행) 일부만 실패해도
  스크립트 자체는 끝까지 돈다.

### 출력

`evals/results/<UTC 타임스탬프>/`에:

- `<persona_id>.json` — 페르소나별 상세 결과. 필드:
  - `ground_truth`: 이 세션이 실제로 뽑아냈어야 할 정답(`GroundTruthEvent`의 모든 필드).
  - `transcript`: 실제로 오간 대화 전체(user=페르소나, assistant=인터뷰 에이전트).
  - `session_prose`: Phase 2 산문 재조립 결과.
  - `extracted_events`: 실제로 추출된 `Event` 레코드(전체 필드 — place/people/
    emotion_tag/labels 등).
  - `followup_count_used`: 꼬리 질문이 몇 번 발동했는지(0이면 그 페르소나 설계에서
    `withhold_on_first_turn`이 의도대로 작동 안 했다는 신호일 수 있음 — 점검 필요).
- `summary.json` — 페르소나별 소요 시간·추출 이벤트 수 요약.

실제 성공 사례 2건은 `evals/results/pilot_2026-07-12/`에 남겨뒀다 — 새로 실행하기
전에 출력 형식이 어떤 모습인지 먼저 보고 싶으면 이 폴더를 참고.

### 30명으로 늘리려면

`evals/personas.py`의 `PERSONAS` 리스트에 같은 형식으로 `Persona`를 추가하면 된다.
다른 코드는 손댈 필요 없다. 다만 30명 전체를 돌리면 Solar API 호출이 5명 파일럿의
6배가 되니, 먼저 파일럿 결과로 페르소나 설계(사건 내용, withhold 슬롯)가 의도대로
작동하는지 확인한 뒤 늘리는 것을 권한다.

## 2. DeepEval 라벨추출 정확도 (미착수)

**선행 조건인 1번이 이제 준비됐으니 바로 시작 가능하다.** 할 일:

1. `pip install deepeval`을 `requirements.txt`에 추가.
2. `deepeval`의 `GEval`/커스텀 메트릭이 기본으로 OpenAI를 판정 LLM으로 쓰므로,
   `DeepEvalBaseLLM`을 상속한 커스텀 래퍼를 만들어 `app/clients/solar.py`의
   `chat_completion`을 감싼다(판정 모델을 Upstage Solar로 통일하기로 결정,
   2026-07-12 — 이 프로젝트에 없던 OpenAI API 키 의존성을 새로 추가하지 않기 위함).
3. `evals/results/<타임스탬프>/*.json`을 읽어, 각 파일의 `ground_truth`(정답)와
   `extracted_events`(실제 추출) 슬롯을 슬롯 키별로 매칭한다 — 필드명 매핑은 위 1절
   ground_truth 필드 주석(`evals/personas.py`의 `GroundTruthEvent` docstring) 참조.
   문자열 완전 일치가 아니라 의미적으로 같은 내용인지(패러프레이즈 허용)를 Solar
   judge에게 판정시켜야 한다 — 그래서 DeepEval의 LLM-as-judge 메트릭이 필요하다.
4. 슬롯 단위로 precision(추출된 것 중 맞는 비율)/recall(정답 중 실제로 추출된 비율)을
   내고, 페르소나 수를 늘려가며(5→10→20→30) 곡선을 그리면 "정보 보존율 recall/
   precision 곡선" 슬라이드 자료가 된다.

## 3. G-Eval 서사일관성 (미착수)

이건 인터뷰(Phase 1~2)가 아니라 **자서전 챕터 집필(Phase 4)** 결과물을 평가하는
것이라, 지금 만든 벤치마크만으로는 부족하다 — 같은 합성 페르소나 데이터를 이어받아
`autobiography_service`의 목차 생성 → 챕터 집필 → 통일성 윤문까지 마저 돌려야 평가
대상(완성된 챕터 본문)이 나온다. 판정 LLM은 2번과 동일하게 Solar로 통일한다.
DeepEval 패키지에 `GEval` 메트릭이 이미 포함돼 있으므로 2번 작업(Solar judge 래퍼)을
그대로 재사용할 수 있다.

## 4. SUS 사용성 (미착수 — 코드로 대신할 수 없음)

SUS(System Usability Scale)는 표준 10문항 설문에 **실제 사람**이 응답해야 나오는
지표다 — 이 항목은 합성 데이터로 대체할 수 없다. 지금 미리 준비해 둘 수 있는 것은
설문지(한국어 번역 10문항)와 점수 산식(0~100 스케일) 정도이며, 실제 데이터 수집은
팀이 시니어 사용자 대상으로 별도 사용성 테스트를 진행해야 한다.
