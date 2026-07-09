from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=False,
    # Supabase direct connection(포트 5432) 사용 시 불필요하나,
    # 만약 Pooler(포트 6543)로 전환할 경우 prepared statement 캐시 충돌 방지를 위해 필수.
    connect_args={"statement_cache_size": 0},
)

# 세션 생명주기(획득 → try/rollback on error)는 app/gateways/factory.py의
# gateways_context()가 관리한다. 여기서는 엔진/세션팩토리만 제공한다.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
