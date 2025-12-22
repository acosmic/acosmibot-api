import asyncio
from typing import List, Optional
import sys
from pathlib import Path
import os
import aiohttp

# Add project root to sys.path to allow importing 'acosmibot' module
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from api import get_db_session
from acosmibot.Dao.YoutubeDao import YoutubeDao
from api.services.youtube_websub_service import YouTubeWebSubService
from acosmibot.Services.youtube_service import YouTubeService
import logging
logger = logging.getLogger(__name__)

class YouTubeSubscriptionManager:
    def __init__(self, webhook_callback_url: str):
        self.websub_service = YouTubeWebSubService(webhook_callback_url)

    async def add_subscription(self, guild_id: int, channel_id: str, channel_name: Optional[str] = None) -> bool:
        """
        Adds a YouTube channel subscription for a guild.
        Registers with the WebSub hub if this is the first subscription for the channel.

        Args:
            guild_id: The Discord guild ID
            channel_id: The YouTube channel ID
            channel_name: Optional channel name. If not provided, will be fetched from YouTube API.
        """
        async with get_db_session() as session:
            youtube_dao = YoutubeDao(session)

            # Check if this guild is already subscribed to this channel
            existing_guild_subscription = await youtube_dao.get_youtube_subscription_by_guild_and_channel(
                guild_id, channel_id
            )
            if existing_guild_subscription:
                logger.info(f"Guild {guild_id} is already subscribed to YouTube channel {channel_id}. Skipping.")
                return True

            # Fetch channel name if not provided
            if not channel_name:
                try:
                    youtube_service = YouTubeService()
                    async with aiohttp.ClientSession() as client_session:
                        channel_info = await youtube_service.get_channel_info(client_session, channel_id)
                        if channel_info:
                            channel_name = channel_info.get('title')
                            logger.info(f"Fetched channel name '{channel_name}' for channel ID {channel_id}")
                        else:
                            logger.warning(f"Could not fetch channel info for channel ID {channel_id}")
                except Exception as e:
                    logger.error(f"Error fetching channel name for {channel_id}: {e}")

            # Add the guild's subscription to the database
            await youtube_dao.add_youtube_subscription(guild_id, channel_id, channel_name)
            logger.info(f"Added YouTube subscription for guild {guild_id} to channel {channel_id} (name: {channel_name}) in DB.")

            # Check if this is the first subscription for this channel across all guilds
            subscriptions_count = await youtube_dao.count_subscriptions_for_channel(channel_id)

            if subscriptions_count == 1:
                logger.info(f"First subscription for YouTube channel {channel_id}. Registering with WebSub hub.")
                lease_seconds = 432000  # 5 days
                success = await self.websub_service.subscribe(channel_id, lease_seconds)

                if success:
                    # Update database with active status and lease expiration
                    await youtube_dao.update_websub_status(channel_id, 'active', lease_seconds)
                    logger.info(f"Successfully registered YouTube channel {channel_id} with WebSub hub (lease: 5 days)")
                else:
                    logger.error(f"Failed to register YouTube channel {channel_id} with WebSub hub.")
                    # Update status to failed and roll back the DB subscription
                    await youtube_dao.update_websub_status(channel_id, 'failed', error_message="WebSub subscribe request failed")
                    await youtube_dao.remove_youtube_subscription(guild_id, channel_id)
                    return False
            else:
                logger.info(f"YouTube channel {channel_id} already has {subscriptions_count - 1} active subscriptions. No need to re-register.")
            return True

    async def remove_subscription(self, guild_id: int, channel_id: str) -> bool:
        """
        Removes a YouTube channel subscription for a guild.
        Unregisters with the WebSub hub if this was the last subscription for the channel.
        """
        async with get_db_session() as session:
            youtube_dao = YoutubeDao(session)

            # Remove the guild's subscription from the database
            removed = await youtube_dao.remove_youtube_subscription(guild_id, channel_id)
            if not removed:
                logger.warning(f"Attempted to remove non-existent YouTube subscription for guild {guild_id}, channel {channel_id}.")
                return False
            
            logger.info(f"Removed YouTube subscription for guild {guild_id} to channel {channel_id} from DB.")

            # Check if this was the last subscription for this channel across all guilds
            subscriptions_count = await youtube_dao.count_subscriptions_for_channel(channel_id)

            if subscriptions_count == 0:
                logger.info(f"Last subscription for YouTube channel {channel_id}. Unregistering from WebSub hub.")
                success = await self.websub_service.unsubscribe(channel_id)

                if success:
                    # Update status to indicate unsubscribed (or could delete the row entirely)
                    await youtube_dao.update_websub_status(channel_id, 'pending', error_message=None)
                    logger.info(f"Successfully unregistered YouTube channel {channel_id} from WebSub hub")
                else:
                    logger.error(f"Failed to unregister YouTube channel {channel_id} from WebSub hub.")
                    await youtube_dao.update_websub_status(channel_id, 'failed', error_message="WebSub unsubscribe request failed")
                    return False
            else:
                logger.info(f"YouTube channel {channel_id} still has {subscriptions_count} active subscriptions. Not unregistering from hub.")
            return True

    async def get_active_subscriptions(self) -> List[tuple]:
        """Retrieves all active YouTube channel subscriptions."""
        async with get_db_session() as session:
            youtube_dao = YoutubeDao(session)
            return await youtube_dao.get_all_youtube_subscriptions()

