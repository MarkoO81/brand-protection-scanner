from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from core.redis_client import get_redis


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_redis_dep():
    return get_redis()
