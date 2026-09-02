from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from core.config import settings

async_engine = create_async_engine(settings.database_url, pool_size=5, max_overflow=15)
session_factory = async_sessionmaker(
    async_engine, expire_on_commit=False, class_=AsyncSession
)
