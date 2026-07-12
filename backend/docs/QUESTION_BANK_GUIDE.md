# 생애주기별 질문 큐 도입 가이드

지금 인터뷰는 한 사건(event)의 슬롯이 다 채워지면 다음 질문으로 넘어가지 않고
그냥 "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"라는 고정 문구만
보여준다(`app/services/interview_service.py`의 `add_user_turn`, 아래 TODO 참조).

```python
# app/services/interview_service.py
if missing_required and session.followup_count < prompts.MAX_FOLLOWUP_PER_EVENT:
    assistant_content = await _generate_followup_question(...)
    ...
else:
    # TODO(향후 작업): 생애주기별 질문 큐/사진 핀셋 배치 오케스트레이션.
    # 지금은 이 사건에 대한 슬롯이 충분히 채워졌다는 것만 알리는 자리표시자.
    assistant_content = "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"
```

실제 질문 문구(생애주기별로 몇 개씩)는 아직 코드베이스에 없다 — 콘텐츠가 정해지는
대로 아래 순서로 넣으면 된다. **이 문서는 안내용이며, 이 작업 자체를 지금 구현하지는
않았다** — 어떤 문구를 몇 개 넣을지, 사진 세션과 어떻게 섞을지가 정해져야 스키마가
과설계/과소설계되지 않는다.

## 1. 스키마는 이미 있다 — `Question` 테이블

`app/models/question.py`:

| 컬럼 | 의미 |
|---|---|
| `sequence_order` | 같은 생애주기 안에서의 순서 (unique) |
| `title` | 질문 짧은 제목 (관리용) |
| `content` | 실제로 유저에게 보여줄 질문 문장 |
| `life_period` | `childhood` / `youth` / `adulthood` / `senior` (`app/models/enums.py`) |
| `is_active` | 노출 여부 토글 |

지금은 `sqlalchemy_gateways.py`에서 SQLAlchemy 매퍼 등록용으로만 import되고 있고,
이 테이블을 읽고 쓰는 게이트웨이·서비스 코드는 아직 없다.

## 2. 데이터 넣기 — Alembic 데이터 마이그레이션

스키마 마이그레이션(`alembic/versions/005_manuscript_pdf.py` 등)과 같은 자리에
데이터 마이그레이션을 하나 추가하면 팀 전체에 자동으로 배포된다:

```python
# alembic/versions/006_seed_questions.py
def upgrade() -> None:
    questions = op.create_table  # 이미 있는 테이블이므로 insert만
    op.bulk_insert(
        sa.table(
            "questions",
            sa.column("id", sa.dialects.postgresql.UUID(as_uuid=True)),
            sa.column("sequence_order", sa.Integer),
            sa.column("title", sa.String),
            sa.column("content", sa.Text),
            sa.column("life_period", sa.String),
        ),
        [
            {
                "id": uuid.uuid4(),
                "sequence_order": 1,
                "title": "가장 오래된 기억",
                "content": "가장 오래전 기억나는 장면은 무엇인가요?",
                "life_period": "childhood",
            },
            # ... 생애주기별 확정된 문구만큼 반복
        ],
    )
```

Mock 백엔드(테스트/로컬 스캐폴딩)에도 같은 데이터가 필요하면
`app/gateways/mock/store.py`에 `questions: dict[uuid.UUID, QuestionRecord]`를 추가하고
`MockStore` 생성 시 같은 목록으로 초기화하면 된다.

## 3. 게이트웨이 계층 추가

`app/gateways/interfaces.py`에 `QuestionGateway` ABC를 추가하고
(`ObjectStorageGateway`/`MediaAssetGateway`와 같은 자리, 같은 스타일):

```python
class QuestionGateway(ABC):
    @abstractmethod
    async def get_next_unasked(
        self, user_id: UUID, *, life_period: LifePeriod
    ) -> QuestionRecord | None:
        """이 유저가 아직 답하지 않은, 해당 생애주기의 질문 중 sequence_order가
        가장 빠른 것. 없으면 None(그 생애주기 질문을 다 마쳤다는 뜻)."""
```

"아직 답하지 않은"의 판정 기준은 `InterviewSession.question_id`로 잡을 수 있다 —
`session_type=FIXED_QUESTION`이고 `status != OPEN`인(즉 시작된) 세션들의
`question_id` 집합을 빼고 남은 것 중 `sequence_order` 최소값을 고르면 된다.
`SqlAlchemyQuestionGateway`(실 구현)와 `MockQuestionGateway`(인메모리) 둘 다
`interfaces.py`의 다른 게이트웨이 구현체와 같은 패턴으로 추가하고,
`app/gateways/factory.py`의 `Gateways` 묶음에 등록한다.

## 4. `interview_service.py` 배선

위 TODO 자리를 다음과 같은 흐름으로 바꾼다:

1. 현재 세션이 `FIXED_QUESTION`이고 `life_period`를 알고 있어야 한다 —
   지금 `InterviewSession`에는 `life_period` 컬럼이 없고 `question_id`를 통해서만
   간접적으로 안다. 세션 생성 시점에 어떤 생애주기를 다룰지 프론트가 정해서
   넘겨야 하므로(`SessionCreate.question_id`), **이 세션을 시작할 때부터** 질문을
   골라 `question_id`를 채워 넣는 편이 지금 구조와 더 잘 맞는다 — 즉 "세션 종료
   시점에 다음 질문을 고르는" 것보다 "다음 세션 시작 시점에 미리 고르는" 방향.
2. 그래서 실제로 손댈 자리는 두 곳이다:
   - **세션 생성 API** (`interview_service.create_session` 호출부,
     `app/api/v1/interviews.py`): `question_id`가 안 넘어왔으면
     `QuestionGateway.get_next_unasked`로 채워 넣는다.
   - 프론트(`ChatOverlay.tsx`의 `interviewsApi.create({ session_type: "fixed_question" })`):
     그대로 두거나, 어떤 생애주기부터 시작할지 UI가 생기면 그때 파라미터를 추가한다.
   - 위 TODO 자리(`add_user_turn`)의 플레이스홀더 문구는, 다음 질문이 있으면
     그 `content`를 그대로 보여주고 세션을 완료 처리(`complete_session`)하도록 바꾼다
     — "한 세션 = 질문 하나"라는 기존 주석(`InterviewSession` 모델 docstring)과
     맞추기 위함이다. 다음 질문이 없으면(그 생애주기 질문을 다 마쳤으면) 지금의
     안내 문구를 유지하거나 "생애주기를 넘어가시겠어요?" 같은 안내로 바꾼다.

## 5. 사진(PHOTO) 세션과의 관계

`session_type=PHOTO`는 이미 스키마에 있지만(`linked_media_asset_id`) 어디서도
생성되지 않는다. "사진 핀셋 배치"(TODO 주석의 표현)는 사진첩에 올라온 사진 중
아직 대화로 안 다뤄진 것을 질문 큐 사이사이에 끼워 넣는 걸 말하는 것으로 보이는데,
이건 큐 오케스트레이션 정책(예: 질문 N개당 사진 1장?) 자체가 아직 정해지지 않았으므로
이번 가이드 범위 밖으로 남겨둔다 — 정책이 정해지면 위 4번의 "다음 질문 고르기" 로직
안에 사진 우선순위 분기를 추가하면 된다.
