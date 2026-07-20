"""
전체 테스트 스위트가 실제 Supabase에 절대 접속하지 않도록 강제한다.

`app/config.py`의 `settings = Settings()`는 모듈이 처음 import될 때 딱 한 번
`.env`/환경변수를 읽는다. 팀원 로컬 `.env`의 `GATEWAY_BACKEND`가 `postgres`인 채로
누군가 `pytest`를 그냥 실행하면(이 프로젝트 `.env` 기본값이 실제로 `postgres`다),
`tests/test_auth.py`의 FastAPI TestClient 기반 테스트들이 실제 라우터 의존성 주입
경로(`app/api/deps.py:GatewaysDep → get_gateways`)를 그대로 타므로 진짜 Supabase에
쓰기 시도를 하게 된다 — 반면 `tests/test_autobiography_phase34_pipeline.py`,
`tests/test_event_gateway_gating.py`는 `_build_mock_gateways()`/`MockEventGateway`를
직접 생성해 이 설정과 무관했다. 이 conftest는 그 차이를 없애 테스트 스위트 전체가
`.env` 내용과 무관하게 항상 오프라인으로 동작하도록 보장한다.

`setdefault`를 쓰는 이유: 누군가 실제 Postgres에 대고 통합 테스트를 돌리고 싶어
`GATEWAY_BACKEND=postgres pytest ...`처럼 환경변수를 명시적으로 주면 그 의도를
존중한다 — 이 conftest는 "아무 설정도 안 했을 때의 안전한 기본값"만 강제한다.

같은 이유로 `AUTOBIOGRAPHY_LLM_PROVIDER`도 강제한다(2026-07-20) — billgates 계정
실험을 위해 `.env`에 `AUTOBIOGRAPHY_LLM_PROVIDER=gemini`를 켜둔 채 pytest를 돌리면,
`app.clients.solar.*`를 패치해둔 기존 테스트(test_autobiography_phase34_pipeline.py
등)가 그 패치를 우회해 실제 Gemini API를 호출해버린다(실제로 재현: 429
RESOURCE_EXHAUSTED로 테스트 실패). 이 프로젝트 테스트는 전부 Solar 라우팅을
전제로 목(mock)을 걸어뒀으므로 기본값을 고정한다.
"""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("GATEWAY_BACKEND", "mock")
os.environ.setdefault("AUTOBIOGRAPHY_LLM_PROVIDER", "solar")


class _AlwaysAcquiringRedisClient:
    """세션 단위 phase2 중복 재큐잉 방지 락(app/workers/enqueue.py, 2026-07-19)이
    기본적으로 실제 Redis에 접속하지 않도록 한다 — 위 GATEWAY_BACKEND/Supabase와
    같은 이유로, 테스트 스위트는 로컬에 실행 중인 Redis가 있든 없든(이 프로젝트
    docker-compose.yml이 host:6379에 매핑해두므로 개발자 로컬에 실제로 떠 있는
    경우가 흔하다) 항상 같은 결과가 나와야 한다. 이 클라이언트는 항상 락 획득에
    성공한 것으로 응답해, 예전(락 도입 전)과 동일하게 큐잉이 그대로 진행되게
    한다 — 락의 실제 동작(획득 실패·네트워크 실패 폴백)을 검증하는 테스트
    (test_enqueue.py, test_admin_service.py)는 자신의 테스트 안에서 이 패치를
    다시 덮어써 원하는 시나리오를 재현한다."""

    async def set(self, key, value, *, nx=False, ex=None):
        return True

    async def aclose(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _mock_redis_for_session_phase2_lock():
    with patch(
        "app.workers.enqueue.redis_asyncio.from_url",
        return_value=_AlwaysAcquiringRedisClient(),
    ):
        yield


@pytest.fixture(autouse=True)
def _prevent_real_celery_broker_calls():
    """테스트가 실제 Celery 브로커(Redis)에 태스크를 발행하는 것을 전역적으로
    차단한다.

    `.env`의 `CELERY_BROKER_URL`이 `redis://localhost:6379/0`으로, 이 저장소의
    실제 개발용 docker-compose Redis와 같은 주소를 가리킨다. 위의
    `_mock_redis_for_session_phase2_lock`은 락 확인에 쓰는 `redis.asyncio`
    클라이언트만 가짜로 바꿔줄 뿐, Celery `.delay()`가 내부적으로 쓰는 별도의
    (kombu 기반) 브로커 연결은 그대로 실제 Redis로 나간다 — 그래서
    `interview_service.add_user_turn`/`complete_session`을 호출하면서 개별
    태스크의 `.delay`를 직접 패치하지 않은 테스트(예:
    test_interview_safeguards.py)는 Mock 게이트웨이에만 존재하는(실제 DB에는
    없는) 세션 ID로 실제 큐에 메시지를 발행해왔다. 이 메시지는 워커가 처리할 때
    "InterviewSession not found"로 실패하지만 이미 발행된 뒤라 사라지지 않고
    쌓인다(2026-07-19, 실제 큐 점검 중 유령 `process_session_completion`
    메시지 323개 발견 — 로컬에서 반복 실행한 테스트가 누적 원인이었다).

    Celery의 `Task.delay()`는 `self.apply_async(args, kwargs)`를 호출하는
    얇은 래퍼라, 그 지점을 전역으로 막으면 어떤 태스크·어떤 테스트 파일이든
    빠짐없이 막힌다. 개별 태스크를 통째로 가짜 객체로 치환해 큐잉 여부를
    검증하는 기존 테스트(test_enqueue.py의 _FakeTask 패턴 등)는 애초에 이
    `Task.apply_async`를 거치지 않으므로 이 전역 차단과 충돌하지 않는다."""
    with patch("celery.app.task.Task.apply_async", return_value=None):
        yield
