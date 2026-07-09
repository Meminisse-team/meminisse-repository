# app/gateways — DB/S3 연동 담당자 가이드

이 폴더는 "DB/S3라는 외부 자원에 실제로 접근하는 코드"와 "그 위에서 돌아가는 비즈니스
로직(`app/services/`)"을 분리하는 경계선이다. 이 문서는 Postgres(pgvector)와 S3 연동을
맡은 팀원을 위한 것이다.

## 지금 상태

- `interfaces.py` — 각 계층이 지켜야 하는 "계약서"(ABC, 추상 기반 클래스). 뭘
  입력받아 뭘 반환해야 하는지만 정의되어 있고, 실제 구현은 없다.
- `dto.py` — 계약서에서 주고받는 데이터의 모양(순수 dataclass, DB 기술과 무관.
  "DTO" = Data Transfer Object, 로직 없이 데이터만 담는 상자).
- `sqlalchemy_gateways.py` — **지금 실제로 동작하는 임시 구현체.** 기존에 서비스
  코드에 직접 박혀 있던 SQLAlchemy 호출을 그대로 옮겨온 것이라, 프로덕션급으로
  다듬어진 코드는 아니다. 팀원이 이 파일을 그대로 이어받아 고도화하거나, 완전히
  새로 짜서 교체해도 된다 — 계약서(`interfaces.py`)만 지키면 된다.
- `s3_gateway.py` — 마찬가지로 `app/clients/s3.py`(boto3 얇은 래퍼)를 감싼 임시 구현체.
- `mock/` — DB/S3 없이 인메모리로 동작하는 구현체. 팀원 연동이 끝나기 전까지
  다른 팀원들이 로컬에서 전체 API를 돌려볼 수 있게 해준다.
- `factory.py` — 어떤 구현체를 쓸지 결정하는 조립 지점. `.env`의
  `GATEWAY_BACKEND=mock|postgres` 하나로 전환된다.

## 이번 작업으로 팀원이 처음 만들었던 DB 연동 코드에 생긴 변화

이 브랜치(및 직전 `feature/architecture-setup` 브랜치)에서 팀원이 원래 작성했던
`app/config.py`, `app/database.py`, `alembic/`에 아래와 같은 변화가 생겼다. 원본
설계 의도를 최대한 존중하면서 필요한 부분만 손댔다.

- **`app/database.py`: `get_db()` 함수 삭제.** 원래 FastAPI 라우터가 `Depends(get_db)`로
  직접 받아쓰던 세션 획득 함수였다. 지금은 `app/gateways/factory.py`의
  `gateways_context()`가 세션 획득 + 예외 시 rollback을 전담하므로 완전히 같은 역할을
  하는 코드가 두 곳에 있을 이유가 없어져 제거했다. `engine`/`AsyncSessionLocal`
  (커넥션 풀 설정, Direct connection 강제 등 팀원의 원래 설정)은 전혀 손대지 않았다.
- **`app/config.py`: `GATEWAY_BACKEND` 설정 추가.** 기존 필드는 그대로 두고 새 필드만
  추가했다.
- **`app/models/`, `alembic/`: 이번 브랜치에서는 변경 없음.** (직전 브랜치
  `feature/architecture-setup`에서 `Event`/`EventRelation` 테이블 신설,
  임베딩 벤더를 OpenAI→Upstage로 전환 등의 변경이 있었고, 그 내용은 해당 브랜치의
  PR 설명에 정리되어 있다. 이번 브랜치는 그 위에 게이트웨이 계층만 추가했다.)

## DB(Postgres + pgvector)를 구축할 때 주의할 점

1. **마이그레이션은 이미 작성되어 있다.** `backend/alembic/versions/001_initial_schema.py`
   (초기 스키마)와 `002_event_first_class_object.py`(이벤트 1급 객체화)를 그대로
   `alembic upgrade head`로 적용하면 스키마가 맞춰진다. 스키마를 직접 새로
   설계하지 말고, 이 마이그레이션 두 개를 검토한 뒤 필요하면 `003_...`으로 이어서
   수정할 것 — 이미 적용된 마이그레이션 파일 자체를 고치지 말 것(다른 사람 환경에
   이미 반영되어 있을 수 있음).

2. **Direct connection(5432) 사용, Pooler(6543) 금지.** `app/database.py`에
   `connect_args={"statement_cache_size": 0}`로 이미 방어해뒀지만, Pooler로 연결하면
   asyncpg의 prepared statement 캐시와 충돌해 런타임 오류가 난다. Supabase 대시보드에서
   반드시 "Direct connection" 문자열을 쓸 것.

3. **`EMBEDDING_DIM`(현재 4096, `app/models/base.py`에 정의) 값을 실제 Upstage API
   응답으로 검증할 것.** Upstage Embeddings 문서 자체가 벡터 차원을 4096(서술부)과
   1024(공식 스펙부)로 서로 다르게 적어놔서, 실제 `UPSTAGE_API_KEY`로 1회 호출해
   응답 벡터 길이를 확인해야 한다. 값이 다르면 `app/models/base.py`의
   `EMBEDDING_DIM`과 `alembic/versions/002_...`의 `vector(N)`을 함께 고쳐야
   한다(아직 실데이터가 없으니 지금 고치는 게 제일 싸다).

4. **`Event.verified` 플래그의 의미를 반드시 이해할 것.** 이 프로젝트의 핵심
   안전장치다.
   - `verified=True`인 이벤트만 임베딩(`embedding` not null)을 가지며, RAG 검색
     대상이 된다.
   - `verified=False`는 "아직 사용자가 확인하지 않은 데이터"(주로 OCR 오인식 의심
     구간)를 뜻하며, 임베딩도 없고 검색 결과에도 나오면 안 된다.
   - `EventGateway.search_verified()`가 이 규칙을 강제하는 지점이다. 팀원이 새
     구현체를 짤 때 이 WHERE 조건(`verified = true AND embedding IS NOT NULL`)을
     빠뜨리면 안 된다 — `backend/tests/test_event_gateway_gating.py`가 이 계약을
     검증하는 테스트이니, 새 구현체에도 동일한 시나리오의 테스트를 추가해서 통과시킬 것.

5. **커밋 시점은 게이트웨이가 아니라 `Gateways.commit()`이 담당한다.** 각 게이트웨이
   메서드는 `session.flush()`까지만 하고 `session.commit()`은 하지 않는다(여러
   게이트웨이 호출을 하나의 트랜잭션으로 묶기 위해서). 새 구현체를 짤 때도 이 규칙을
   따를 것 — 메서드 안에서 커밋하지 말 것.

## S3를 구축할 때 주의할 점

- 버킷/리전은 `.env`의 `AWS_S3_BUCKET`, `AWS_REGION`으로 주입된다.
- Layer 0(불변 원천) 원칙: 업로드된 원본은 절대 덮어쓰거나 삭제하지 않는다(보존
  기간 정책 적용 전까지).
- `ObjectStorageGateway.get_presigned_url()`은 제한 시간 접근 URL을 발급하는
  용도다 — 원본을 영구 공개 URL로 노출하지 말 것.

## 완성 후, 실제로 바꿔야 하는 부분

원칙적으로 **`app/gateways/factory.py` 한 파일**만 바꾸면 된다. 서비스/라우터 코드는
전혀 건드릴 필요가 없다.

```python
# app/gateways/factory.py

def _build_postgres_gateways(session) -> Gateways:
    from app.gateways.s3_gateway import S3ObjectStorageGateway
    from app.gateways.sqlalchemy_gateways import (
        SqlAlchemyAutobiographyGateway,
        SqlAlchemyEventGateway,
        SqlAlchemyInterviewSessionGateway,
        SqlAlchemyMediaAssetGateway,
        SqlAlchemyUserGateway,
    )
    return Gateways(
        users=SqlAlchemyUserGateway(session),
        sessions=SqlAlchemyInterviewSessionGateway(session),
        events=SqlAlchemyEventGateway(session),
        media_assets=SqlAlchemyMediaAssetGateway(session),
        autobiographies=SqlAlchemyAutobiographyGateway(session),
        storage=S3ObjectStorageGateway(),
        _commit=session.commit,
    )
```

새 클래스 이름이 무엇이든(`SqlAlchemyEventGateway`를 그대로 고도화했든,
`AsyncpgEventGateway`처럼 완전히 새로 짰든) `app/gateways/interfaces.py`의 ABC를
상속하기만 하면, 위 함수의 **임포트문과 생성자 호출 몇 줄**만 그 클래스 이름으로
바꿔치기하면 끝이다.

### 교체 전 체크리스트

- [ ] 새 클래스가 `interfaces.py`의 모든 `@abstractmethod`를 구현했는가
      (하나라도 빠지면 객체 생성 시점에 바로 에러가 나므로 확인은 쉽다)
- [ ] `search_verified`가 `verified=True AND embedding IS NOT NULL` 조건을
      실제 쿼리에서 강제하는가 (`test_event_gateway_gating.py`와 동일한 시나리오로
      직접 검증할 것)
- [ ] `alembic upgrade head`가 실제 Supabase 인스턴스에 적용되었는가
- [ ] `EMBEDDING_DIM`이 실제 Upstage 응답과 일치하는가
- [ ] `.env`의 `GATEWAY_BACKEND=postgres`로 설정하고 `/health` 및 유저 생성
      플로우가 실제 DB에 기록되는지 확인했는가
