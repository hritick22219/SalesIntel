import functools
import json
from typing import Callable, Optional, Any
from fastapi import Request

from redis_client import redis_client

def serialize_value(val: Any) -> Any:
    """Helper to convert Pydantic models recursively into JSON-serializable types."""
    if hasattr(val, "model_dump"):
        return val.model_dump()
    if isinstance(val, list):
        return [serialize_value(item) for item in val]
    if isinstance(val, dict):
        return {k: serialize_value(v) for k, v in val.items()}
    return val

def cache_response(ttl_seconds: int = 300):
    """
    Decorator to cache FastAPI endpoint responses.
    Builds a cache key scoped by the user's company_id, path, and query parameters.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            request: Optional[Request] = None
            company_id = None

            # Scan arguments to find the Request and User details
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
            for val in kwargs.values():
                if isinstance(val, Request):
                    request = val
                if hasattr(val, "company_id"):
                    company_id = val.company_id

            # If request object isn't available, bypass caching
            if not request:
                return await func(*args, **kwargs)

            # Generate unique cache key scoped per tenant
            cache_key = f"cache:analytics:{company_id or 'global'}:{request.url.path}:{request.url.query}"

            try:
                cached_data = await redis_client.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception:
                # If Redis is unavailable, log and proceed with DB query directly
                pass

            # Fetch fresh query result from database
            result = await func(*args, **kwargs)

            try:
                serialized = serialize_value(result)
                await redis_client.setex(cache_key, ttl_seconds, json.dumps(serialized))
            except Exception:
                pass

            return result
        return wrapper
    return decorator

async def invalidate_company_cache(company_id: int):
    """Clear all cached analytics data for a specific company tenant."""
    pattern = f"cache:analytics:{company_id}:*"
    try:
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
    except Exception:
        pass
