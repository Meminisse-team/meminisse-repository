# 생애주기별 질문 큐 / 사진 세션 오케스트레이션

## 1~4절: 고정 질문 큐 — 구현 완료 (2026-07-12)

유년기~노년기 39개 고정 질문 배선은 끝났다. `Question` 테이블 시드
(`alembic/versions/006_seed_questions.py`, `app/data/question_bank.py`),
`QuestionGateway`(`get_next_unasked` — `sequence_order` 전역 순서로 다음 미배정
질문 하나), `interview_service.create_session`(question_id 미지정 시 자동 배정),
`add_user_turn`(세션의 슬롯이 다 채워지면 자동 완료 + 다음 질문 제시, 큐를 다
마치면 `POST /interview-sessions`가 `409`)까지 전부 실제로 동작한다. 이 문서의
과거 버전(1~4절)에 있던 "아직 구현 안 됨" 안내는 더 이상 유효하지 않다.

## 5. 사진(PHOTO) 세션 오케스트레이션 — 아직 미구현, 설계만 확정

**중요**: 2026-07-12에 이 부분을 "기존 대화 중간에 사진/문서 OCR 내용을 예/아니오로
확인하는 질문 하나 끼워 넣기"로 잘못 구현했다가 롤백했다. 실제 의도는 그게 아니라
아래 설계다 — 다음에 이 작업을 다시 시작할 때는 반드시 이 설계를 따를 것.

### 핵심 개념: 사진 = 별도의 독립된 인터뷰 세션 주제

고정 질문(`FIXED_QUESTION`)과 사진(`PHOTO`)은 서로 다른 세션이다. 사진에 대한
이야기를 다른 세션의 대화 중간에 끼워 넣지 않는다 — **그 사진 자체가 하나의
질문 주제가 되어 독립된 `PHOTO` 세션**(`InterviewSession.session_type=PHOTO`,
`linked_media_asset_id=그 사진`)으로 열린다. 그 세션을 여는 화면에는 사진을
띄우고 "이 사진에 대해 더 자세히 이야기를 들려주시겠어요?" 같은 시작 질문을
보여준 뒤, 이후 대화는 일반 인터뷰(슬롯 게이팅·꼬리질문)와 동일하게 진행된다.

### 스케줄링 규칙

사진이 언제 세션으로 제시되는지는 그 사진의 "시기"를 알 수 있는지에 달렸다.
`MediaAsset.life_period_mapped`(사용자가 입력한 `age_at_time`으로 이미 계산됨,
`media_service.map_age_to_life_period` 참조 — OCR로 시기를 추정하는 로직은
아직 없음, 필요하면 이때 함께 추가)가:

- **채워져 있으면(시기가 확정됨)**: 그 생애주기의 고정 질문 39개 중 해당 구간이
  **전부 완료된 직후**, 다음 생애주기로 넘어가기 전에 그 사진의 `PHOTO` 세션을
  먼저 연다. 예: `age_at_time=19`(청소년기)로 확인된 사진이 있다면, 청소년기
  고정 질문이 모두 끝난 시점에 — 바로 다음 생애주기(장년기) 첫 질문으로 넘어가는
  게 아니라 — 그 사진 세션이 먼저 제시된다.
- **비어 있으면(시기 불명)**: **모든 생애주기의 고정 질문이 전부 끝난 뒤**, 남은
  미확정 시기 사진들을 한꺼번에 몰아서 사진마다 세션 하나씩 순서대로 진행한다.

### 구현 시 손댈 자리 (다음에 이 작업을 시작할 때)

1. **"이 생애주기 고정 질문이 방금 끝났다"를 감지**: `interview_service.
   add_user_turn`에서 `complete_session` 후 `gateways.questions.get_next_unasked`
   로 다음 질문을 가져오는 지점(`QUESTION_BANK_GUIDE.md` 1~4절 구현부) — 그
   다음 질문의 `life_period`가 방금 막 완료한 질문의 `life_period`와 달라졌다면
   "생애주기 경계를 막 넘었다"는 신호다. `QuestionRecord`에 이미 `life_period`
   필드가 있으므로 비교만 하면 된다.
2. **그 생애주기에 아직 세션이 없는 사진 조회**: `MediaAssetGateway`에 새 메서드가
   필요하다 — 예: `list_uninterviewed(user_id, life_period)`. "세션이 아직
   없다"의 판정 기준은 `InterviewSession(session_type=PHOTO, linked_media_asset_id=
   그 사진)`이 하나라도 존재하는지로 잡으면 된다(교재의 `QuestionGateway.
   get_next_unasked`가 세션 존재 여부로 "배정됨"을 판정하는 것과 동일한 패턴).
3. **생애주기 경계에서 사진이 있으면 먼저 제시, 없으면 다음 질문 그대로**: 위
   1번 신호가 뜨고 2번 조회 결과가 있으면, 다음 고정 질문 대신 `PHOTO` 세션을
   먼저 만들어 그 사진과 시작 질문을 보여준다. 그 사진 세션들을 다 마친 뒤에야
   다음 생애주기의 첫 고정 질문으로 넘어간다.
4. **전체 고정 질문 종료 시점(큐를 다 마쳐 `NoRemainingQuestionsError`가 나는
   지점)**: 마찬가지로 시기 불명 사진(`life_period_mapped IS NULL`)이 남아있는지
   확인해, 있으면 그것부터 순서대로 `PHOTO` 세션으로 제시한다.
5. **OCR로 오인식 의심 텍스트가 있는 문서**(`Event(source_type=DOCUMENT,
   verified=false)`, `media_service.py` 참조)의 승격도 이 틀 안에서 처리한다 —
   그 문서/사진의 `PHOTO` 세션을 열 때 `prompts.build_ocr_confirmation_question`
   로 만든 문구를 시작 질문에 실마리로 녹여 넣고("일기장에 '1975년 결혼'이라고
   적혀 있던데, 이때 이야기를 들려주시겠어요?"), 그 세션 안에서 오간 대화 내용을
   바탕으로 이벤트 추출·검증을 다시 돌리면 된다 — 별도의 예/아니오 게이트가
   아니라 자연스러운 인터뷰 대화 자체가 확인 절차를 대신한다.
