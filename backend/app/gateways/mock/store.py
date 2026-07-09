"""
프로세스 메모리에 상주하는 가짜 DB/오브젝트 스토리지. 팀원의 Postgres/S3 연동이
붙기 전까지 로컬 개발·데모·테스트에서 실제 인프라 없이 전체 흐름을 굴려볼 수 있게 한다.

MockStore 인스턴스 하나가 "가짜 데이터베이스 커넥션 풀" 역할을 한다. 앱 프로세스
전역에서 하나를 공유하면 요청 간에 데이터가 유지되고(GATEWAY_BACKEND=mock으로
로컬 데모할 때 유용), 테스트에서는 매 테스트마다 새 MockStore()를 만들어 완전히
격리할 수 있다.
"""

from __future__ import annotations

from uuid import UUID

from app.gateways.dto import (
    AutobiographyRecord,
    EventRecord,
    EventRelationCreateData,
    InterviewSessionRecord,
    MediaAssetRecord,
    UserRecord,
)


class MockStore:
    def __init__(self) -> None:
        self.users: dict[UUID, UserRecord] = {}
        self.sessions: dict[UUID, InterviewSessionRecord] = {}
        self.events: dict[UUID, EventRecord] = {}
        self.event_relations: list[EventRelationCreateData] = []
        self.media_assets: dict[UUID, MediaAssetRecord] = {}
        self.autobiographies_by_user: dict[UUID, AutobiographyRecord] = {}
        self.objects: dict[str, bytes] = {}


# 프로세스 전역 공유 인스턴스. REPOSITORY_BACKEND=mock일 때 factory.py가 기본으로 사용한다.
# 테스트는 이 싱글턴을 쓰지 말고 MockStore()를 직접 생성해 서로 격리할 것.
default_store = MockStore()
