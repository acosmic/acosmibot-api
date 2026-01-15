"""
  Redis client singleton for pub/sub operations.

  This client is used to publish cache invalidation events to the bot instances.
  """

import redis
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """
    Get or create Redis client singleton.

    Returns None if Redis is unavailable (graceful degradation).
    """
    global _redis_client

    if _redis_client is None:
        try:
            redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
            redis_password = os.getenv('REDIS_PASSWORD', None)

            _redis_client = redis.from_url(
                redis_url,
                password=redis_password,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30
            )

            # Test connection
            _redis_client.ping()
            logger.info(f"✅ Redis client connected to {redis_url}")

        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
            logger.warning("⚠️  Cache invalidation will not work (bot will use TTL fallback)")
            _redis_client = None

    return _redis_client


def publish_cache_invalidation(guild_id: int) -> bool:
    """
    Publish a cache invalidation event for a guild.

    Args:
        guild_id: Discord guild ID

    Returns:
        True if published successfully, False otherwise
    """
    try:
        client = get_redis_client()
        if client is None:
            logger.warning(f"Cannot invalidate cache for guild {guild_id}: Redis unavailable")
            return False

        # Publish to the channel that bot instances are listening to
        subscribers = client.publish('guild_config_invalidate', str(guild_id))
        logger.info(f"⚡ Published cache invalidation for guild {guild_id} to {subscribers} subscriber(s)")
        return True

    except Exception as e:
        logger.error(f"Failed to publish cache invalidation for guild {guild_id}: {e}")
        return False
