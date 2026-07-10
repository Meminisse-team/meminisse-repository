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
"""

import os

os.environ.setdefault("GATEWAY_BACKEND", "mock")
