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

2. **Direct connection(5432) 사용을 원칙으로 하되, IPv4 전용 환경 등에서는 Supavisor
   Pooler(세션 모드, 포트 5432)도 허용된다 — 단 반드시 트랜잭션 모드(포트 6543)는
   피할 것.** `app/database.py`에 `connect_args={"statement_cache_size": 0}`로 이미
   방어해뒀으므로 asyncpg의 prepared statement 캐시 충돌은 발생하지 않는다(2026-07-09
   실제 Supabase 인스턴스에 연결해 CRUD 전 구간 검증 완료 — 아래 "실연동 검증 이력"
   참조). 현재 `.env`의 `DATABASE_URL`은 `aws-1-ap-northeast-1.pooler.supabase.com:5432`
   (Supavisor 세션 모드)를 쓰고 있다 — 이 문서가 원래 권장하던 `db.[ref].supabase.co:5432`
   Direct connection과는 호스트가 다르다는 점을 인지하고 있을 것. 기능상 문제는 없지만,
   두 방식 중 어느 쪽을 팀 표준으로 삼을지는 DB 담당자가 의식적으로 결정해 이 문단을
   갱신해두는 것을 권장한다(예: IPv6 미지원 네트워크가 있어 의도적으로 Pooler를
   선택했다면 그 사실을 남겨둘 것).

3. **`EMBEDDING_DIM=4096`(`app/models/base.py`)은 실제 Upstage API 응답으로 검증
   완료된 값이다.** Upstage Embeddings 문서 자체는 벡터 차원을 4096(서술부)과
   1024(공식 스펙부)로 서로 다르게 적어놓고 있었으나, 실제 `UPSTAGE_API_KEY`로 1회
   호출해 응답 벡터 길이를 확인한 결과 4096차원이 맞는 것으로 확정되었다(1024 서술은
   문서 쪽 오기로 판단). `alembic/versions/002_event_first_class_object.py`의
   `vector(4096)`도 이 값과 이미 일치한다. 단, pgvector의 HNSW/IVFFlat 근사 인덱스는
   2000~4000차원까지만 지원해 4096차원에는 인덱스를 만들 수 없으므로, `events.embedding`
   유사도 검색은 인덱스 없이 순차 스캔으로 동작한다(하단 "실연동 검증 이력" 및
   `002_event_first_class_object.py` 모듈 docstring 참조) — 이벤트 수가 많아져 성능
   이슈가 생기면 그때 차원 축소/이진 양자화 같은 추가 인덱싱 전략을 검토할 것.

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

6. **`Base`에 `__mapper_args__ = {"eager_defaults": True}`가 설정되어 있다
   (`app/models/base.py`) — 지우지 말 것.** `updated_at`처럼 `onupdate=func.now()`로
   서버가 값을 계산하는 컬럼은, 이 설정이 없으면 UPDATE 후 SQLAlchemy가 그 속성을
   "expired" 상태로 남긴다. 이 상태에서 `flush()` 직후 곧바로 동기적으로 그 속성에
   접근하면(이 프로젝트의 게이트웨이들이 다 그렇게 한다 — 예:
   `SqlAlchemyAutobiographyGateway.update()`가 `flush()` 후 바로
   `_to_autobiography_record(obj)`로 DTO 변환) SQLAlchemy가 지연 로드를 시도하다
   `sqlalchemy.exc.MissingGreenlet`으로 즉시 죽는다. 실제 Supabase에 연결해
   `POST /autobiographies/{id}/toc/select`, Phase 3 `consolidate`, 챕터
   `write_chapter`, `retain-real-name` 경로를 재현했을 때 전부 이 오류로 500이
   났었고, `eager_defaults=True`로 UPDATE에 `RETURNING`을 강제해 해결했다(아래
   "실연동 검증 이력" 참조). Mock 백엔드로 돌리는 기존 pytest 스위트는 실제 SQL을
   전혀 실행하지 않으므로 이 버그를 절대 잡아내지 못한다 — 새 구현체를 짤 때도
   `onupdate`가 걸린 컬럼을 flush 직후 동기 접근한다면 동일한 함정에 빠질 수 있으니
   유의할 것.

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
- [ ] `alembic upgrade head`가 실제 Supabase 인스턴스에 적용되었는가(✅ 2026-07-09
      기준 `alembic_version=003`으로 확인됨 — 새로 스키마를 손대면 004로 이어갈 것)
- [x] `EMBEDDING_DIM`이 실제 Upstage 응답과 일치하는가 — 4096으로 확인 완료(위 3번 참조)
- [ ] `.env`의 `GATEWAY_BACKEND=postgres`로 설정하고 `/health` 및 유저 생성
      플로우가 실제 DB에 기록되는지 확인했는가
- [ ] `onupdate=func.now()` 컬럼을 flush 직후 동기 접근하는 경로가 있다면
      `eager_defaults=True`(또는 명시적 `session.refresh()`)로 방어했는가(위 6번 참조)

## 실연동 검증 이력

**2026-07-09** — 실제 Supabase 인스턴스(`aws-1-ap-northeast-1.pooler.supabase.com`,
PostgreSQL 17.6, pgvector 0.8.2)에 연결해 스키마·제약조건·게이트웨이 전 구간을
교차검증했다.

- 스키마 대조: `alembic_version=003`(로컬 head와 일치), 13개 테이블·16개 enum
  타입·모든 FK의 `ON DELETE` 정책·유니크 제약·인덱스가 `app/models/*.py` 정의와
  정확히 일치함을 `information_schema`/`pg_catalog` 조회로 확인. `events.embedding`은
  `vector(4096)`로 저장되며 예상대로 근사 인덱스 없이 존재.
- Gateway 패턴 전 구간(User/Consent/InterviewSession/ChatLog/Event/EventRelation/
  MediaAsset/Autobiography/ChapterDraft/Character/CharacterMention, 총 36개 연산)을
  실제 DB에 대해 실행하는 스모크 테스트로 CRUD·벡터 코사인 검색·JSONB/UUID[]
  라운드트립·FK CASCADE 삭제까지 전부 통과 확인. 테스트로 생성한 데이터는 종료 시
  전부 삭제해 DB를 빈 상태로 복원했다(현재 프로덕션 데이터 없음).
- **발견 및 수정한 버그**: `eager_defaults` 미설정으로 인한 `MissingGreenlet` —
  위 6번 항목 참조. `app/models/base.py`에 `__mapper_args__` 한 줄 추가로 해결.
  이 버그는 Mock 백엔드 테스트로는 재현 불가능했고, Phase 3 `consolidate`/Phase 4
  `toc/select`·`write_chapter`·`retain-real-name` 등 상태 갱신이 있는 거의 모든
  쓰기 경로에서 500 에러를 냈을 것이므로 실연동 전에 발견된 것이 중요하다.
- **정보 공유(코드 수정 불필요, 팀 결정 필요)**: `.env`의 `DATABASE_URL`이 Direct
  connection이 아니라 Supavisor 세션 모드 Pooler를 가리키고 있음(위 2번 참조) —
  기능상 문제는 없으나 이 문서의 기존 안내와 다르므로 인지하고 있을 것. 또한 현재
  `.env`의 `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`가 비어 있어 미디어 업로드의
  실제 S3 저장 단계(`app/clients/s3.py`)는 아직 검증되지 않았다(DB 쪽 `media_assets`
  테이블 자체의 CRUD는 더미 `s3_key`/`s3_url`로 검증 완료).

## 회원가입/로그인 인증 추가에 따른 DB 작업 (2026-07-10, 팀원이 직접 적용해야 함)

기획안에는 없던 기능이지만, 다른 사람의 자서전·인터뷰·사진에 접근하지 못하도록
막으려면 "누가 요청했는지"를 서버가 알아야 해서 인증을 추가했다. **처음에는 자체
bcrypt 해싱 + JWT 발급으로 구현했다가, 곧바로 Supabase Auth(GoTrue) 연동으로
전면 재작성했다** — 위 "실연동 검증 이력"에서 이 Supabase 프로젝트에 `auth`/
`storage`/`realtime` 스키마가 이미 프로비저닝되어 있음을 확인했기 때문이다(즉
이메일 인증·비밀번호 재설정·소셜 로그인까지 지원하는 완전한 인증 서비스가 이미
같은 프로젝트에 존재했다). 그 결과 이 프로젝트 DB는 비밀번호 관련 값을 전혀
저장하지 않는다 — `users` 테이블에 `hashed_password` 컬럼 같은 건 없다.

새 코드: `app/clients/supabase_auth.py`(Supabase Auth REST API 래퍼),
`app/core/security.py`(Supabase가 발급한 세션 토큰 서명 검증), `app/api/v1/auth.py`,
`app/api/deps.py`. **이번에도 스키마를 바꿔야 해서 실제 Supabase에는 아직 적용하지
않았다** — 마이그레이션 파일과 애플리케이션 코드는 이 브랜치에 전부 포함돼 있으니,
아래 절차대로 실제 DB/환경에 반영해줄 것.

1. **`alembic upgrade head`를 실행해 마이그레이션 004를 적용할 것.**
   `alembic/versions/004_auth.py`가 `public.users.id`에 `auth.users(id)` FK
   (ON DELETE CASCADE) 하나를 건다 — 새 컬럼은 없다. 오프라인 모드(`alembic
   upgrade 003:004 --sql`)로 생성되는 실제 DDL은 다음과 같다(로컬에서 미리 확인
   완료, 실제 DB에는 적용 안 함):
   ```sql
   ALTER TABLE users ADD CONSTRAINT fk_users_auth_users
     FOREIGN KEY(id) REFERENCES auth.users (id) ON DELETE CASCADE;
   ```
   **주의**: `public.users.id`가 그 시점에 `auth.users`에 없는 값을 가진 행이
   하나라도 있으면 FK 생성 자체가 실패한다. 운영 Supabase는 2026-07-09 기준
   `users` 0행이라 문제 없음을 확인했다 — 그 사이 누군가 `GATEWAY_BACKEND=postgres`로
   회원가입을 테스트해 행을 만들어뒀다면(이전 버전 코드로 만들어진 행은 애초에
   Supabase Auth를 거치지 않았으므로) 적용 전에 정리할 것.

2. **`.env`에 Supabase Auth 관련 4개 값을 채울 것.** `.env.example`에 항목을
   추가해 뒀고, `SUPABASE_URL`은 `DATABASE_URL`의 프로젝트 참조로부터 이미
   채워둔 상태다. 나머지 3개(`SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
   `SUPABASE_JWT_SECRET`)는 DB 연결 문자열에서 유도할 수 없어 비워뒀다 —
   Supabase Dashboard → Settings → API에서 확인해 채울 것(`app/config.py`의
   주석에 각 값이 대시보드의 어느 항목에 대응하는지 정리해 뒀다). **채우기
   전까지는 회원가입/로그인을 포함해 인증이 걸린 모든 엔드포인트가 실패한다**
   — DB의 나머지 기능(인터뷰/이벤트/자서전 파이프라인 자체)은 이 값들과 무관하게
   그대로 동작한다. `SUPABASE_SERVICE_ROLE_KEY`는 RLS를 완전히 우회하는 매우
   민감한 키이므로 절대 커밋하거나 프론트엔드에 노출하지 말 것.

3. **적용 후 확인할 것**: `POST /api/v1/users`(가입)로 계정을 만들면 Supabase
   Dashboard → Authentication → Users에 그 계정이 실제로 뜨는지, 동시에
   `public.users`에도 같은 `id`로 프로필 행이 생겼는지 확인할 것. 그 계정으로
   `POST /api/v1/auth/login`을 호출하면 `access_token`/`refresh_token`이 나오고,
   그 토큰으로 `GET /api/v1/auth/me`가 본인 정보를 돌려주는지, `GET /api/v1/users/
   {다른_user_id}`가 `403`으로 막히는지 확인할 것. 새로 추가된
   `backend/tests/test_auth.py`(Supabase Auth 클라이언트를 모킹해 Mock DB로
   이 흐름 전체를 이미 회귀 검증하고 있음)가 이미 로직을 검증했으니, 실제
   환경에서는 "키가 올바르게 연결됐는지"의 스모크 테스트 삼아 한 번 확인하는
   정도면 충분하다.

4. **범위 밖으로 의도적으로 남겨둔 것**: 인물 단위가 아닌 사용자 단위로 남아있는
   `disclosure_realname` 동의 완화(이전부터 있던 한계, 이번 작업으로 새로 생기거나
   악화되지 않음), 그리고 "자녀가 부모 계정에 로그인해 대신 조작"하는 시나리오가
   지금은 계정 하나(=로그인 하나)로 단순화되어 있다는 점(`app/api/v1/users.py`의
   `create_consent` 함수 주석 참조) — 자녀 전용 로그인·가족 초대 기능이 필요해지면
   `users`와는 별도의 "가족 구성원(family_member)" 개념과 권한 위임 테이블 설계가
   추가로 필요하다. 지금 당장 만들지는 않았다. 또한 Supabase Auth의 이메일 인증
   메일 발송(`email_confirm=true`로 현재 건너뛰고 있음), 비밀번호 재설정, 소셜
   로그인은 서비스가 지원은 받지만 이 프로젝트가 아직 그 UI/플로우를 만들지
   않았다 — 프론트엔드 작업 시 참고할 것.
