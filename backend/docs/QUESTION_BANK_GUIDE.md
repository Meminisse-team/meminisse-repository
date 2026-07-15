# 생애주기별 질문 큐 / 사진 세션 오케스트레이션

## 1~4절: 고정 질문 큐 — 구현 완료 (2026-07-12)

유년기~노년기 100개(생애주기별 25개) 고정 질문 배선은 끝났다. `Question` 테이블 시드
(`alembic/versions/006_seed_questions.py`, `app/data/question_bank.py`),
`QuestionGateway`(`get_next_unasked` — `sequence_order` 전역 순서로 다음 미배정
질문 하나), `interview_service.create_session`(question_id 미지정 시 자동 배정),
`add_user_turn`(세션의 슬롯이 다 채워지면 자동 완료 + 다음 항목 제시)까지 전부
실제로 동작한다.

## 5. 사진(PHOTO) 세션 오케스트레이션 — 구현 완료 (2026-07-12)

**참고**: 이 부분은 처음에 "기존 대화 중간에 사진/문서 OCR 내용을 예/아니오로
확인하는 질문 하나 끼워 넣기"로 잘못 구현했다가 롤백한 이력이 있다. 아래가 최종
구현이다.

### 핵심 개념: 사진 = 별도의 독립된 인터뷰 세션 주제

고정 질문(`FIXED_QUESTION`)과 사진(`PHOTO`)은 서로 다른 세션이다. `interview_
service._resolve_next_item`(고정 질문 큐와 사진 큐를 합쳐 다음 항목 하나를
고르는 함수)이 사진 차례라고 판단하면, 그 사진 자체가 독립된 `PHOTO` 세션
(`InterviewSession.session_type=PHOTO`, `linked_media_asset_id=그 사진`)으로
열린다. 시작 질문은 `prompts.build_photo_session_opening`이 만들며, 이후 대화는
일반 인터뷰와 동일하게 슬롯 게이팅·꼬리질문·자동 완료가 적용된다.

### 스케줄링 규칙 (구현: `interview_service._resolve_next_item`)

`MediaAssetGateway.list_uninterviewed(user_id, life_period=...)`로 "아직 PHOTO
세션이 없는 사진"을 조회한다. `life_period`가:

- **주어지면**: 그 생애주기로 매핑된(`MediaAsset.life_period_mapped`) 사진만.
- **`None`이면**: 시기 미확정(`life_period_mapped IS NULL`) 사진만.

알고리즘: `QuestionGateway.get_next_unasked`로 다음 고정 질문을 구한다.
**그 생애주기 질문이 이 유저에게 하나도 배정된 적 없을 때만**(=지금 막 그
생애주기로 넘어온 시점, `QuestionGateway.has_assigned_question_in_period`로
판정) 바로 앞 생애주기의 미인터뷰 사진이 있는지 확인해 있으면 먼저 내어준다.
고정 질문이 하나도 안 남았으면 생애주기별로, 그다음 시기 미확정으로 순서대로
사진을 내어준다.

**뒤늦게 업로드된 사진**(사진은 언제든 업로드 가능하므로, 이미 몇 생애주기를
더 진행한 뒤에야 예전 시기 사진이 들어올 수 있다): 위 "하나도 배정된 적 없을
때만" 조건 덕분에, 그 생애주기 경계를 이미 지나 다른 질문을 진행 중이면 뒤늦게
들어온 사진이 지금 대화에 끼어들지 않는다 — 그 시기 경계에서 "아직 아무 질문도
안 나간" 그 한 번의 기회를 놓치면, 고정 질문을 전부 마친 뒤 몰아보기 단계에서
(시기 불명 사진들과 마찬가지로) 다뤄진다. `tests/test_photo_session_
orchestration.py::test_late_uploaded_photo_does_not_interrupt_a_later_period_
already_in_progress`가 이 케이스를 검증한다.

상태를 별도로 저장하지 않고 "이 사진에 이미 세션이 있는가"/"이 생애주기 질문이
이미 배정된 적 있는가"만 확인하므로 멱등하다 — 세션 완료 직후 미리보기 문구를
만들 때와 실제로 다음 세션을 생성할 때 같은 함수를 그대로 재사용한다.

### 사진 캡션 + 텍스트 인식 — Azure Vision으로 교체 (2026-07-15)

원래는 Upstage Document Parse(텍스트만 읽는 OCR)로 사진을 분석했다 — 글자가
없는 순수 추억 사진(예: 집 앞에서 찍은 가족사진)에서는 아무 단서도 얻지 못해
"이 사진에 대해 더 자세히 이야기를 들려주시겠어요?" 같은 일반적인 질문만 던질
수 있었다. Azure AI Vision의 Image Analysis API는 `features=caption,read`
하나의 호출로 캡션(사진의 시각적 내용을 설명하는 문장, 예: "집 앞에서 5명이
함께 찍은 사진")과 사진 속 텍스트(손글씨/인쇄 메모, 예: "1990년 집 앞에서
가족들과.")를 동시에 지원해서(`app/clients/azure_vision.py`), Document Parse를
완전히 대체했다. 캡션은 텍스트 유무와 무관하게 항상 얻으므로, 순수 추억 사진도
이제 캡션 기반의 구체적인 오프닝 질문을 받는다.

`media_service._run_dual_track_analysis`가 분석 결과를
`MediaAsset.image_caption`/`image_ocr_text`에 직접 저장하고(사진 속 텍스트가
`_MIN_TEXT_LENGTH_FOR_DOCUMENT_TRACK` 이상이면 `TEXT_DOCUMENT` 트랙, 아니면
`PURE_MEMORY`), `interview_service._photo_session_opening_text`가 이 필드를
그대로 읽어 `build_photo_session_opening(image_caption=..., ocr_text=...)`로
오프닝 질문을 만든다("집 앞에서 5명이 함께 찍은 사진인 것 같은데, 맞나요? 이
사진에 대해 더 자세한 이야기를 들려주시겠어요?") — 예전처럼 `Event`를 거쳐
`quoted_text`를 조회하지 않는다(`EventGateway.get_pending_document_
confirmation`은 이제 없다).

사진 속 텍스트가 검출되면 여전히 `Event(source_type=DOCUMENT)`로도 스테이징해
대화가 아직 일어나지 않아도 검색 가능한 사실이 되게 한다 — 다만 더 이상 "대기
중 확인" 상태로 특별 취급하지 않는다. PHOTO 세션이 완료돼도 이 이벤트를 별도로
삭제하지 않고, 실제 대화에서 비슷한 사실이 다시 추출되면 Phase 3 중복 병합
(`autobiography_service._merge_duplicate_events`)이 임베딩 유사도 + LLM
판정으로 자연스럽게 흡수한다(별도의 예/아니오 확인 게이트를 만들지 않는다는
원칙은 그대로 유지 — 아래 "핵심 개념" 참조).

### 사진 속 텍스트로 사진 시기 자동 추정 — 구현 완료 (2026-07-13, 2026-07-15 Azure Vision으로 전환)

`life_period_mapped`는 원래 사용자가 입력한 `age_at_time`으로만 채워졌다
(`media_service.map_age_to_life_period`). 이제 사용자가 그걸 입력하지 않은
사진은, `media_service._run_dual_track_analysis`가 TEXT_DOCUMENT 트랙으로
분류할 때(Azure Vision이 읽어낸 텍스트가 `_MIN_TEXT_LENGTH_FOR_DOCUMENT_TRACK`
이상일 때) 그 텍스트에서 시기 단서를 뽑아
`media_service._guess_life_period_from_ocr_text`로 추정을 시도한다 — 이
로직 자체는 어떤 엔진이 텍스트를 추출했는지와 무관하므로 Azure Vision 전환
이후에도 그대로다.

- Solar(`prompts.build_ocr_date_extraction_prompt` /
  `OCR_DATE_EXTRACTION_SCHEMA`)에게 텍스트 안의 **명시적** 연도("1975년")나
  나이("19살 때")만 뽑게 한다 — 종이 재질이나 문체 같은 애매한 추측은 명시적으로
  금지했다. 둘 다 없으면 `found=false`.
- 나이가 직접 있으면 그걸로, 연도만 있으면 `User.birth_year`로 나이를 역산해서
  `map_age_to_life_period`에 넘긴다. `birth_year`가 없거나 단서가 전혀 없으면
  `None`(시기 불명으로 남겨 몰아보기 단계에서 다룸)을 반환한다 — 잘못 매핑해서
  사진 세션 오케스트레이션이 엉뚱한 생애주기 경계에서 끼어드는 것보다 안전한
  쪽을 택했다.
- 사용자가 이미 `age_at_time`을 입력해 `life_period_mapped`가 채워져 있으면 이
  추정 자체를 시도하지 않는다(`MediaAssetGateway.update_analysis`의
  `life_period_mapped=None` 파라미터는 "건드리지 않는다"는 뜻인 기존 부분
  갱신 관례를 그대로 따른다 — `UserGateway.update(current_stage=...)`와 동일
  패턴). 사용자 입력을 OCR 추정이 덮어쓸 일은 없다.
- `tests/test_media_ocr_date_estimation.py`가 4가지 경로(연도+birth_year,
  나이 직접, 단서 없음, 이미 사용자 입력으로 매핑된 경우 스킵)를 검증한다.

### 프론트엔드 — 구현 완료 (2026-07-13)

`ChatOverlay.tsx`가 세션의 `session_type`이 `photo`면 `linked_media_asset_id`를
`GET /media-assets/{id}`(새로 추가한 단건 조회 엔드포인트 — 기존엔 목록만 있어서
개별 조회가 없었다, `app/api/v1/media.py`)로 가져와 대화 목록 위에 이미지로
띄운다. 세션을 이어보기(`resumeSessionId`)로 열든, 첫 발화로 새로 만들든
(`interviewsApi.create` 응답이 서버가 자동 전환한 `session_type`/
`linked_media_asset_id`를 그대로 담아 온다) 양쪽 경로 모두에서 사진 상태를
갱신한다. `photos/page.tsx`(사진첩)와 동일하게 `next/image`가 아닌 일반
`<img>`를 쓴다(S3 원본 도메인이 아직 `remotePatterns`에 없음).

### 아직 없는 것 / 알아두면 좋은 것

- **테스트 환경에서 주의**: `interview_service.complete_session`은 세션마다
  Celery `.delay()`로 브로커(Redis) 연결을 시도한다. 브로커가 없는 환경에서
  세션을 여러 개 연속으로 완료시키는 테스트를 작성하면 연결 시도 자체가 누적
  지연을 일으켜 체감상 멈춘 것처럼 보인다(`tests/test_photo_session_
  orchestration.py`에서 실제로 재현 — `process_session_completion.delay`를
  모킹해 해결했다). 이 패턴이 필요한 새 테스트를 쓸 때 참고할 것.
