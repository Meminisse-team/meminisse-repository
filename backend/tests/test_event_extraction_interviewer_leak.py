"""
인터뷰어(assistant) 발화가 화자의 사건으로 오추출되는 버그의 회귀 테스트.

배경: 합성 페르소나 벤치마크 파일럿(evals/results/pilot_2026-07-12/p02_park_youngsoo.json)
에서, 인터뷰 에이전트의 마무리 인사("말씀해주셔서 감사해요. 다음 이야기로 넘어가
볼까요?")가 산문 재조립 단계(PROSE_REASSEMBLY_SYSTEM_PROMPT)의 "assistant 턴은
제외하라"는 지시를 뚫고 새어 나가, "인터뷰어에게 감사 인사 전달"이라는 가짜 narrator
사건으로 추출됐다. 프롬프트만으로는 LLM이 항상 지키리라 보장할 수 없으므로,
event_extraction_service._filter_interviewer_leakage가 source_quote가 assistant
턴 원문에 그대로 들어있는 이벤트를 코드 레벨에서 걸러낸다(_passes_distortion_check가
이미 쓰는 role 기반 필터링과 같은 발상).
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from app.agents import prompts
from app.gateways.dto import EventCreateData, SessionCreateData, UserCreateData
from app.gateways.factory import _build_mock_gateways
from app.models.enums import EventSourceType, MessageRole, SessionType
from app.services import event_extraction_service
from app.services.event_extraction_service import (
    _filter_interviewer_leakage,
    _persist_relations,
    _strip_leaked_assistant_sentences,
)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


_LEAKED_ASSISTANT_LINE = "말씀해주셔서 감사해요. 다음 이야기로 넘어가 볼까요?"
_USER_CONTENT = "스무 살 때 혼자 부산으로 내려갔어요. 국밥집 아주머니가 밥을 챙겨주셨죠."
# 재조립 LLM이 지시를 어기고 assistant 턴을 산문 끝에 그대로 흘려보낸 상황을 재현한다.
_LEAKED_PROSE = f"스무 살 때 혼자 부산으로 내려갔다. 국밥집 아주머니가 밥을 챙겨주셨다. {_LEAKED_ASSISTANT_LINE}"

_EXTRACTED_EVENTS_WITH_LEAK = [
    {
        "one_line_summary": "부산 국밥집",
        "prose_paragraph": "스무 살 때 혼자 부산으로 내려갔다. 국밥집 아주머니가 밥을 챙겨주셨다.",
        "place": "부산", "occurred_at_label": "스무 살 때", "people": "국밥집 아주머니",
        "event_subject": "narrator", "emotion_tag": "고마움", "emotion_intensity": 4,
        "emotion_inferred": False, "values_reflected": None, "reason": None, "process": None,
        "gratitude": None, "regret": None, "turning_point": None, "pride": None, "belief": None,
        "message": None, "source_quote": "국밥집 아주머니가 밥을 챙겨주셨다.",
        "place_confidence": 0.9, "occurred_at_confidence": 0.8,
    },
    {
        "one_line_summary": "인터뷰어에게 감사 인사 전달",
        "prose_paragraph": _LEAKED_ASSISTANT_LINE,
        "place": None, "occurred_at_label": "현재 (인터뷰 중)", "people": "인터뷰어",
        "event_subject": "narrator", "emotion_tag": "Gratitude", "emotion_intensity": 5,
        "emotion_inferred": False, "values_reflected": "appreciation", "reason": None, "process": None,
        "gratitude": "말씀해주셔서 감사해요", "regret": None, "turning_point": None, "pride": None,
        "belief": None, "message": None, "source_quote": "말씀해주셔서 감사해요.",
        "place_confidence": 0.0, "occurred_at_confidence": 0.0,
    },
]

_CHAT_TURNS = [
    {"role": "assistant", "content": "그때 이야기를 좀 더 해주시겠어요?"},
    {"role": "user", "content": _USER_CONTENT},
    {"role": "assistant", "content": _LEAKED_ASSISTANT_LINE},
]


def test_strip_leaked_assistant_sentences_removes_leaked_question_from_prose() -> None:
    """실사용 중 재현된 사례(2026-07-14): 세션 완료 시 마지막 assistant 턴에 다음
    고정 질문 전체 문장이 그대로 담기는데("다음 질문으로 넘어가 볼까요?\\n\\n{질문}",
    interview_service.py:add_user_turn), 산문 재조립 LLM이 이를 사용자 발화로
    착각해 session_prose 중간에 그대로 끼워 넣는 문제가 있었다."""
    next_question = "부모님을 떠올리면 가장 먼저 생각나는 강렬한 장면은 무엇인가요?"
    leaked_assistant_turn = f"말씀해주셔서 감사해요. 다음 질문으로 넘어가 볼까요?\n\n{next_question}"
    prose_with_leak = (
        f"{_USER_CONTENT} {leaked_assistant_turn}"
    )
    chat_turns = [
        {"role": "user", "content": _USER_CONTENT},
        {"role": "assistant", "content": leaked_assistant_turn},
    ]

    cleaned = _strip_leaked_assistant_sentences(prose=prose_with_leak, chat_turns=chat_turns)

    assert _USER_CONTENT in cleaned
    assert "다음 질문으로 넘어가 볼까요" not in cleaned
    assert next_question not in cleaned


def test_strip_leaked_assistant_sentences_keeps_prose_untouched_without_leak() -> None:
    chat_turns = [
        {"role": "assistant", "content": "그때 이야기를 좀 더 해주시겠어요?"},
        {"role": "user", "content": _USER_CONTENT},
    ]
    cleaned = _strip_leaked_assistant_sentences(prose=_USER_CONTENT, chat_turns=chat_turns)
    assert cleaned == _USER_CONTENT


def test_filter_interviewer_leakage_drops_assistant_leaked_event_and_remaps_index() -> None:
    kept, index_map = _filter_interviewer_leakage(
        extracted=_EXTRACTED_EVENTS_WITH_LEAK, chat_turns=_CHAT_TURNS
    )

    assert len(kept) == 1
    assert kept[0]["one_line_summary"] == "부산 국밥집"
    # 원본 인덱스 0(살아남음) -> 새 인덱스 0. 원본 인덱스 1(폐기)은 맵에 없어야 한다.
    assert index_map == {0: 0}


def test_filter_interviewer_leakage_keeps_legitimate_events_untouched() -> None:
    only_legit = [_EXTRACTED_EVENTS_WITH_LEAK[0]]
    kept, index_map = _filter_interviewer_leakage(extracted=only_legit, chat_turns=_CHAT_TURNS)
    assert kept == only_legit
    assert index_map == {0: 0}


def test_filter_interviewer_leakage_ignores_short_quotes_to_avoid_false_positives() -> None:
    """"네", "그렇군요" 같은 짧은 source_quote는 assistant 발화와 우연히 겹칠 위험이
    커서(_MIN_INTERVIEWER_LEAK_QUOTE_LENGTH 미만) 유출 판정에서 제외해야 한다."""
    short_quote_event = {**_EXTRACTED_EVENTS_WITH_LEAK[0], "source_quote": "네"}
    kept, index_map = _filter_interviewer_leakage(
        extracted=[short_quote_event], chat_turns=[{"role": "assistant", "content": "네, 알겠습니다."}]
    )
    assert len(kept) == 1
    assert index_map == {0: 0}


@pytest.mark.asyncio
async def test_persist_relations_remaps_indices_and_drops_relations_referencing_dropped_events() -> None:
    gateways = _build_mock_gateways()
    user = await gateways.users.create(
        UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
    )
    events = await gateways.events.bulk_create(
        [
            EventCreateData(
                user_id=user.id,
                source_type=EventSourceType.SESSION_CHAT,
                one_line_summary="A",
                prose_paragraph="A",
                verified=True,
            )
        ]
    )
    await gateways.commit()

    # 원본 extracted 배열에서 인덱스 0(폐기됨, index_map에 없음)과 인덱스 1(살아남아 새
    # 인덱스 0)을 가리키는 관계 — 폐기된 쪽을 참조하는 관계는 버려져야 한다.
    relations = [{"from_index": 0, "to_index": 1, "relation_type": "cause"}]
    index_map = {1: 0}  # 원본 인덱스 0은 폐기됨(맵에 없음), 원본 인덱스 1이 새 인덱스 0으로.

    await _persist_relations(gateways, events=events, relations=relations, index_map=index_map)
    # from_index(0)가 index_map에 없어 폐기됐어야 한다 — 예외 없이 조용히 스킵되면 통과.


@pytest.mark.asyncio
async def test_process_completed_session_drops_interviewer_leaked_event_end_to_end() -> None:
    """PROSE_REASSEMBLY 단계에서 LLM이 지시를 어기고 assistant 턴을 흘려보내도(재현을
    위해 일부러 그런 산문을 fake_chat_completion으로 반환), event_extraction_service가
    최종적으로 그 인터뷰어 발화를 사건으로 저장하지 않아야 한다."""

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion(_LEAKED_PROSE)

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "event_extraction":
            return {"events": _EXTRACTED_EVENTS_WITH_LEAK, "relations": []}
        raise AssertionError(f"unexpected schema_name: {schema_name}")

    async def _fake_classify_entailment_batch(*, premise: str, hypotheses: list[str]):
        return [{"entailment": 0.9, "neutral": 0.08, "contradiction": 0.02} for _ in hypotheses]

    async def _fake_embed_passages(texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.nli.classify_entailment_batch", new=_fake_classify_entailment_batch),
        patch("app.clients.embeddings.embed_passages", new=_fake_embed_passages),
    ):
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content="그때 이야기를 좀 더 해주시겠어요?"
        )
        await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content=_USER_CONTENT)
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content=_LEAKED_ASSISTANT_LINE
        )
        await gateways.commit()

        events = await event_extraction_service.process_completed_session(gateways, session.id)

        assert len(events) == 1
        assert events[0].one_line_summary == "부산 국밥집"
        assert all("인터뷰어" not in (e.people or "") for e in events)


def test_build_event_extraction_prompt_prepends_question_context_without_touching_prose() -> None:
    """session_prose가 짧아도(예: "서울대학교에 다녔다.") 이벤트 추출이 이 세션이
    다룬 질문을 참고해 one_line_summary를 명확하게 쓸 수 있도록, 질문 맥락을
    산문과 분리된 별도 줄로 앞에 붙인다(2026-07-15 피드백 — session_prose 자체는
    왜곡 탐지 대상이라 손대면 안 되므로 이벤트 추출 입력에만 얹는다)."""
    with_context = prompts.build_event_extraction_prompt(
        session_prose="서울대학교에 다녔다.", question_context="대학을 어디 다녔나요?"
    )
    user_message = with_context[1]["content"]
    assert "대학을 어디 다녔나요?" in user_message
    assert "서울대학교에 다녔다." in user_message

    without_context = prompts.build_event_extraction_prompt(session_prose="서울대학교에 다녔다.")
    assert without_context[1]["content"] == "서울대학교에 다녔다."


@pytest.mark.asyncio
async def test_process_completed_session_threads_opening_question_into_extraction() -> None:
    """세션의 첫 chat_log(role=assistant, interview_service.py:_resolve_opening_
    content가 생성 시점에 저장)가 이벤트 추출 단계에 질문 맥락으로 전달돼야 한다."""
    captured_messages: list = []

    async def _fake_chat_completion(messages, **kwargs) -> _FakeCompletion:
        return _FakeCompletion("서울대학교에 다녔다.")

    async def _fake_structured_completion(messages, *, schema_name, json_schema, **kwargs):
        if schema_name == "event_extraction":
            captured_messages.extend(messages)
            return {
                "events": [
                    {
                        "one_line_summary": "서울대학교 진학",
                        "prose_paragraph": "서울대학교에 다녔다.",
                        "place": "서울대학교", "occurred_at_label": "대학 시절", "people": "혼자",
                        "event_subject": "narrator", "emotion_tag": None, "emotion_intensity": None,
                        "emotion_inferred": False, "values_reflected": None, "reason": None,
                        "process": None, "gratitude": None, "regret": None, "turning_point": None,
                        "pride": None, "belief": None, "message": None,
                        "source_quote": "서울대학교에 다녔다.",
                        "place_confidence": 1.0, "occurred_at_confidence": 0.9,
                    }
                ],
                "relations": [],
            }
        raise AssertionError(f"unexpected schema_name: {schema_name}")

    async def _fake_classify_entailment_batch(*, premise: str, hypotheses: list[str]):
        return [{"entailment": 0.9, "neutral": 0.08, "contradiction": 0.02} for _ in hypotheses]

    async def _fake_embed_passages(texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    with (
        patch("app.clients.solar.chat_completion", new=_fake_chat_completion),
        patch("app.clients.solar.structured_completion", new=_fake_structured_completion),
        patch("app.clients.nli.classify_entailment_batch", new=_fake_classify_entailment_batch),
        patch("app.clients.embeddings.embed_passages", new=_fake_embed_passages),
    ):
        gateways = _build_mock_gateways()
        user = await gateways.users.create(
            UserCreateData(id=uuid.uuid4(), email=f"{uuid.uuid4()}@test.local", name="테스터")
        )
        session = await gateways.sessions.create(
            SessionCreateData(user_id=user.id, session_type=SessionType.FIXED_QUESTION)
        )
        await gateways.sessions.add_chat_log(
            session.id, role=MessageRole.ASSISTANT, content="대학을 어디 다녔나요?"
        )
        await gateways.sessions.add_chat_log(session.id, role=MessageRole.USER, content="서울대")
        await gateways.commit()

        await event_extraction_service.process_completed_session(gateways, session.id)

        user_message = captured_messages[1]["content"]
        assert "대학을 어디 다녔나요?" in user_message
