# P3 — 정량 평가체계

기획안 슬라이드 자료(예: "정보 보존율 recall/precision 곡선")의 근거가 될 정량 평가
4가지 중 1~3번(합성 페르소나 벤치마크, DeepEval 라벨추출 정확도, G-Eval 서사일관성)은
구현·실행까지 끝났다(2026-07-13, n=1~2 파일럿). **4번(SUS 사용성)만 미착수로 남아있다**
— 코드로 대신할 수 없고 팀이 실제 시니어 사용자를 대상으로 별도 설문을 진행해야 하는
항목이라(4절 참조), 자동화 가능한 범위 밖이다. 1~3번도 n이 작아(2~5명) 슬라이드에 쓸
"곡선"을 그릴 단계는 아니다 — 30명으로 늘리기 전에 1절의 재현 테스트를 먼저 거칠 것을
권한다.

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

   **2026-07-13 부분 조치(근본 원인 해결 아님, 지연 증폭 요인 하나 제거)**:
   `get_upstage_client()`가 SDK 기본값(`max_retries=2`)을 그대로 쓰고 있었다 —
   호출 하나가 최악의 경우 90초 타임아웃 × 3회(원 시도 + 재시도 2회) ≈ 270초까지
   걸릴 수 있었고, 이게 벤치마크의 120초 스테이지 타임아웃과 겹치면서 "멈춘 호출이
   실패로 확정되기까지 체감상 오래 걸린다"는 증상을 부분적으로 설명할 수 있었다.
   `app/clients/base.py`에서 `max_retries=0`으로 명시해 이 중복 재시도를 없앴다.
   **여전히 남은 것**: 애초에 왜 개별 Solar/NLI 호출이 간헐적으로 몇 분씩 걸리는지는
   여전히 미상이다 — 30명 파일럿 직전에 같은 5명을 팀 공용 서버/다른 머신에서 돌려
   재현 여부를 대조하는 단계(위 문단)가 여전히 필요하다. 이 조치만으로 그 단계를
   생략해도 된다고 판단하지 말 것.

**실제로 완주한 2명의 결과에서 발견한 것(추출 파이프라인 자체의 이슈, 벤치마크 인프라
문제와는 별개):**
- `p01_kim_soonja`는 사건 하나가 6개의 세부 이벤트로 쪼개졌는데, `place`/`emotion_tag`
  가 대부분 `null`로 비어 있었다 — 원문(재조립 산문)에는 "셋방", "불안" 같은 정보가
  분명히 있는데도 추출 단계에서 슬롯을 못 채운 경우가 있다는 뜻. 이게 바로 P3가
  측정하려는 "정보 보존율"이 낮게 나올 수 있는 구체적 사례다 — DeepEval 단계(2절)에서
  정량화할 첫 대상으로 삼기 좋다.
- `p02_park_youngsoo`는 마지막 추출 이벤트가 "인터뷰어에게 감사 인사 전달"이었다 —
  이건 페르소나(유저)의 사건이 아니라 인터뷰 에이전트(assistant)가 세션을 마무리하며
  한 말("말씀해주셔서 감사해요...")이 사건으로 잘못 추출된 것이었다.

  **2026-07-13 수정 완료.** 원인: `PROSE_REASSEMBLY_SYSTEM_PROMPT`가 "질문(assistant
  턴)은 산문에 포함하지 말라"고만 지시했는데, 마무리 인사는 질문형이 아니라서 LLM이
  제외 대상으로 인식하지 못하고 산문에 그대로 흘려보냈다. 이후 `EVENT_EXTRACTION_
  SYSTEM_PROMPT`에는 인터뷰어 발화를 배제하라는 지시 자체가 아예 없어서, 새어 나온
  그 문장이 `event_subject: narrator`인 정식 사건으로 추출됐다. 두 단계로 고쳤다:
  1. `PROSE_REASSEMBLY_SYSTEM_PROMPT`를 "질문뿐 아니라 맞장구·감사 인사·화제 전환
     등 assistant 턴 전체를 제외하라"로 강화(`app/agents/prompts.py`).
  2. (LLM이 그래도 지키지 않을 경우를 대비한 코드 레벨 backstop)
     `event_extraction_service._filter_interviewer_leakage`가 추출된 이벤트의
     `source_quote`가 assistant 턴 원문에 그대로 들어있으면 그 이벤트를 폐기한다 —
     `_passes_distortion_check`가 이미 쓰는 role 기반 필터링과 같은 발상. 이벤트를
     걸러내며 `relations`의 인덱스가 밀리는 문제까지 함께 처리했다(`_persist_relations`
     의 `index_map` 파라미터). 회귀 테스트: `tests/test_event_extraction_interviewer_
     leak.py`(5건 — 필터 단위 테스트, 관계 인덱스 리매핑, `process_completed_session`
     엔드투엔드).

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

## 2. DeepEval 라벨추출 정확도 — 구현 완료 (2026-07-13, n=2)

`evals/solar_judge_model.py`가 `DeepEvalBaseLLM`을 상속해 `app/clients/solar.py`의
`chat_completion`/`structured_completion`을 감싼 판정 모델(`SolarJudgeModel`)을
제공한다(판정 모델을 Upstage Solar로 통일 — 이 프로젝트에 없던 OpenAI API 키 의존성을
새로 추가하지 않기 위함, 2026-07-12 결정). `evals/deepeval_label_accuracy.py`가 이
판정 모델로 `evals/results/<타임스탬프>/*.json`의 `ground_truth`와 `extracted_events`를
슬롯 단위로 비교한다.

**GEval이 아니라 SolarJudgeModel을 직접 쓴 이유**: 이건 "산문이 얼마나 일관적인가" 같은
열린 채점(3절이 그 용도)이 아니라 "정답 슬롯 값이 추출 결과 어딘가에 의미상 존재하는가"
라는 사실 판정에 가까워서, GEval의 루브릭 프레임보다 스키마 기반 직접 판정이 더 적합하고
저렴하다.

**측정 방식**: 세션 하나 = 정답 사건 하나(`GroundTruthEvent`)지만 실제 추출은 여러 개의
세부 `Event`로 쪼개질 수 있다(p01은 6개). 그래서 슬롯 하나당 "이 페르소나의 추출된
이벤트 전체에서 이 슬롯 값을 찾을 수 있는가"로 recall을, "추출된 값들이 정답 맥락과
모순 없이 부합하는가"로 precision을 낸다 — 값 하나하나가 아니라 슬롯 단위 이진 판정으로
단순화했다(Upstage Structured Outputs가 평평한 스키마만 다루기 쉬워, 스코프를 의도적으로
축소한 것).

**실행**: `cd backend && ../venv/Scripts/python -m evals.deepeval_label_accuracy`
(최신 결과 디렉터리를 자동으로 찾는다. 특정 디렉터리를 지정하려면 인자로 경로를 넘긴다.)
결과는 `evals/results/<타임스탬프>/label_accuracy_report.json`에 저장된다.

**실제 파일럿(n=2, p01·p02) 결과**:

| 슬롯 | recall | precision |
| --- | --- | --- |
| 장소(place) | 1.00 (2/2) | 0.50 (1/2) |
| 시기(time) | 1.00 (2/2) | 0.50 (1/2) |
| 핵심 사건 내용(event) | 1.00 (2/2) | 0.50 (1/2) |
| 감정(emotion) | 0.50 (1/2) | 1.00 (1/1) |
| 가치관(values) | 0.50 (1/2) | 1.00 (1/1) |
| 동행(companion) | 1.00 (2/2) | **0.00 (0/2)** |
| 감사(gratitude) | **0.00 (0/1)** | N/A |
| 후회(regret) | **0.00 (0/1)** | N/A |
| 자부심(pride) | 1.00 (1/1) | 1.00 (1/1) |
| 전환점(turning_point) | 1.00 (1/1) | 1.00 (1/1) |

n=2라 곡선을 그릴 정도는 아니지만(30명으로 늘려야 원래 목표한 "정보 보존율 recall/
precision 곡선" 슬라이드가 나온다), 이미 눈에 띄는 신호가 있다:

- **감사/후회 슬롯은 완전히 유실됐다(recall 0/1 각각)** — ②로 제보됐던 "place/emotion이
  대부분 null" 문제가 실제로는 더 넓게(암묵적 감정 반영이 필요한 슬롯 전반) 걸쳐 있을
  가능성을 시사한다. p01의 ground_truth 감사("밤새 바느질하던 어머니")·후회("철없이
  그때는 어머니 고생을 몰랐던 것")는 대화에 명시적으로 언급되지 않아, 추출이 아니라
  페르소나 시뮬레이션(persona_agent.py)이 애초에 그 내용을 발화하지 않았을 가능성도
  있다 — 원인이 추출 프롬프트 쪽인지 페르소나 발화 쪽인지는 n=2로는 구분할 수 없다.
- **동행(companion) precision이 0.00** — 세부 이벤트로 쪼개지면서 각 조각의 `people`
  필드가 "이 문장에 누가 언급됐는가"를 나타내지, "정답이 묻는 동행이 누구인가"와는 다른
  질문에 답하고 있어서 발생하는 구조적 불일치로 보인다(예: 정답 동행이 "어머니와 남동생
  둘"인데, 다른 하위 이벤트의 `people`이 "아버지"·"혼자"인 것도 정답과 안 맞는 값으로
  집계됨 — 실제로는 그 하위 이벤트가 다루는 문장이 아버지 얘기라서 당연한 값이다). 다만
  p02의 "인터뷰어"라는 동행 값은 순수한 결함으로, 이건 이 세션의 인터뷰어 발화 오추출
  버그(위 문단, 2026-07-13 수정)의 흔적이다 — 새로 벤치마크를 돌리면 이 값은 사라진다.
- **30명으로 늘리기 전에**: 위 구조적 불일치(companion 슬롯의 세부-이벤트 vs 전체-사건
  granularity 차이)를 먼저 손보지 않으면 companion precision이 계속 낮게 나올 것 —
  세부 이벤트별 `people`이 아니라 세션 전체에서 언급된 동행 인물 집합과 비교하는 방식으로
  바꾸는 게 더 공정한 측정일 수 있다.

## 3. G-Eval 서사일관성 — 구현 완료 (2026-07-13, n=1 성공/2 시도)

인터뷰(Phase 1~2)가 아니라 **자서전 챕터 집필(Phase 4)** 결과물을 평가하는 지표라,
`evals/deepeval_narrative_coherence.py`가 저장된 `extracted_events`를 Mock 게이트웨이에
재구성한 뒤(인터뷰를 다시 돌리지 않음 — 비용/시간, 그리고 아래 1절의 간헐적 지연 문제에
불필요하게 다시 노출되는 걸 피하기 위함) `autobiography_service`의 목차 생성 → 챕터
집필 → 통일성 윤문까지 실제로 돌려 최종 완성본(`autobiography.final_content`)을
얻는다. 판정 LLM은 2절과 동일하게 `SolarJudgeModel`을 재사용하고, DeepEval의 `GEval`
메트릭(criteria 기반 자유 채점 — "정답이 있는가"가 아니라 "이 산문이 얼마나
일관적인가"를 묻는 이 지표에 GEval의 루브릭 프레임이 적합)을 그대로 쓴다.

개별 챕터의 `chapter.content`가 아니라 `final_content`를 평가 대상으로 삼은 이유:
`finalize_manuscript`의 통일성 윤문 패스는 인접 챕터 경계·문체를 다듬어 `final_content`
에만 반영하고 챕터별 `content`는 그대로 두므로(`autobiography_service.finalize_
manuscript` 참조), "완성된" 서사일관성을 재려면 실제로 완성된 그 결과물을 봐야 한다.

**실행**: `cd backend && ../venv/Scripts/python -m evals.deepeval_narrative_coherence`
결과는 `evals/results/<타임스탬프>/narrative_coherence_report.json`에 저장된다.

**실제 실행 결과**:
- `p02_park_youngsoo`: 서사일관성 점수 **1.00(통과)**. GEval 판정 사유: 시간순 사건
  전개(야간조 근무 → 학원 → 검정고시 합격)와 감정 아크(고통 → 안정 → 연대 → 자신감 →
  감사)가 일관되게 이어지고, 화자 특유의 말투가 유지된다고 평가했다.
  **주의**: 이 실행은 재구성 비용을 아끼려고 `evals/results/pilot_2026-07-12/`의
  **기존(2026-07-13 인터뷰어 오추출 수정 이전) 데이터**를 그대로 재사용했다 —
  그래서 GEval의 판정 사유에 "the interview gratitude moment"(인터뷰어의 마무리
  인사가 사건으로 잘못 섞여 들어간 것)가 실제로 언급된다. 즉 이 파일럿 출력은 위 5절
  "인터뷰어 발화 오추출" 버그가 최종 완성 원고에까지 흘러든 실제 사례이기도 하다 —
  그 버그가 왜 고쳐야 했는지 보여주는 근거 자료로 남겨둔다. 수정 이후 새로 벤치마크를
  돌리면 이 오염은 사라진다.
- `p01_kim_soonja`: **`APITimeoutError('Request timed out.')`로 실패** — 1절에서
  문서화한 간헐적 후처리 지연 문제가 Phase 3/4에서도 똑같이 재현된 사례다. 다만
  `app/clients/base.py`의 `max_retries=0` 조치(1절 참조) 덕분에, 예전처럼 몇 분씩
  원인 불명으로 멈추는 대신 90초 만에 명확한 예외로 빠르게 실패했다 — 근본 원인 해결은
  아니지만, 최소한 "멈춘 건지 진행 중인지 알 수 없는" 상태에서 "실패했다는 걸 즉시 아는"
  상태로는 개선됐다.
- n=1(성공 기준)이라 이 지표로 일반화된 결론을 낼 단계는 아니다 — 30명 파일럿 때는
  ①의 재현 테스트(1절)를 먼저 거쳐 안정적인 환경을 확보한 뒤 돌리는 걸 권한다.

## 4. SUS 사용성 (미착수 — 코드로 대신할 수 없음)

SUS(System Usability Scale)는 표준 10문항 설문에 **실제 사람**이 응답해야 나오는
지표다 — 이 항목은 합성 데이터로 대체할 수 없다. 지금 미리 준비해 둘 수 있는 것은
설문지(한국어 번역 10문항)와 점수 산식(0~100 스케일) 정도이며, 실제 데이터 수집은
팀이 시니어 사용자 대상으로 별도 사용성 테스트를 진행해야 한다.
