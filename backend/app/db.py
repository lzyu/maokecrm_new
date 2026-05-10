from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, future=True)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        import app.models  # noqa: F401

        await conn.run_sync(SQLModel.metadata.create_all)
    from app.services.accounting import ensure_accounting_type_schema

    await ensure_accounting_type_schema()
