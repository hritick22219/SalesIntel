import redis.asyncio as aioredis
from config import settings

# Initialize unified Redis client
redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
