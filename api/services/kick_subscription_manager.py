"""
Kick webhook subscription management helper
Handles creating/deleting webhook subscriptions with reference counting
"""
import asyncio
import aiohttp
import logging
import sys
from pathlib import Path
from typing import Optional, Tuple

# Ensure bot path is in sys.path
current_dir = Path(__file__).parent.parent.parent
bot_project_path = current_dir.parent / "acosmibot"
if str(bot_project_path) not in sys.path:
    sys.path.insert(0, str(bot_project_path))

from Dao.KickSubscriptionDao import KickSubscriptionDao
from Services.kick_service import KickService
from api.services.kick_webhook_service import KickWebhookService

logger = logging.getLogger(__name__)


class KickSubscriptionManager:
    """Manages Kick webhook subscriptions with reference counting"""

    def __init__(self):
        self.kick_service = KickService()
        self.webhook_service = KickWebhookService()

    async def subscribe_to_streamer(
        self,
        username: str,
        guild_id: int
    ) -> Tuple[bool, str]:
        """
        Subscribe to streamer's events (or increment reference count)

        Creates webhook subscription via Kick API for livestream status updates.

        Args:
            username: Kick username (slug)
            guild_id: Guild ID adding this streamer

        Returns:
            (success, message)
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Get channel info to get broadcaster_user_id
                channel_info = await self.kick_service.get_channel_info(session, username)
                if not channel_info:
                    return False, f"Kick channel '{username}' not found"

                # Extract user_id - different API endpoints return different field names
                broadcaster_user_id = str(
                    channel_info.get('broadcaster_user_id') or
                    channel_info.get('user_id') or
                    channel_info.get('id') or
                    ''
                )

                if not broadcaster_user_id or broadcaster_user_id == 'None':
                    return False, f"Could not determine user ID for Kick channel '{username}'"

                broadcaster_username = channel_info.get('slug') or username

                subscription_dao = KickSubscriptionDao()

                # Check if we're already subscribed to this channel
                existing = subscription_dao.get_subscription_by_broadcaster(broadcaster_user_id)

                if existing:
                    # Subscription exists - just increment reference count
                    subscription_dao.add_guild_to_subscription(broadcaster_user_id, guild_id)
                    subscription_dao.close()

                    logger.info(f"Incremented Kick subscription reference for {broadcaster_username} (guild {guild_id})")
                    return True, f"Now tracking {broadcaster_username}"

                # Create new webhook subscription via API
                subscription_id = await self.webhook_service.create_livestream_subscription(
                    session,
                    broadcaster_user_id
                )

                if not subscription_id:
                    subscription_dao.close()
                    return False, f"Failed to create Kick webhook subscription for {broadcaster_username}"

                # Create database record
                subscription_dao.create_subscription(
                    broadcaster_user_id=broadcaster_user_id,
                    broadcaster_username=broadcaster_username,
                    guild_id=guild_id,
                    subscription_id=subscription_id
                )

                # Update subscription status to active
                subscription_dao.update_subscription_status(
                    broadcaster_user_id=broadcaster_user_id,
                    status='active',
                    subscription_id=subscription_id
                )

                subscription_dao.close()

                logger.info(f"Created Kick subscription for {broadcaster_username} (guild {guild_id})")
                return True, f"Successfully subscribed to {broadcaster_username}"

        except Exception as e:
            logger.error(f"Error subscribing to Kick channel {username}: {e}", exc_info=True)
            return False, f"Internal error: {str(e)}"

    async def unsubscribe_from_streamer(
        self,
        username: str,
        guild_id: int
    ) -> Tuple[bool, str]:
        """
        Unsubscribe from streamer (or decrement reference count)

        Deletes webhook subscription when no guilds are tracking anymore.

        Args:
            username: Kick username (slug)
            guild_id: Guild ID removing this streamer

        Returns:
            (success, message)
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Get broadcaster user ID
                channel_info = await self.kick_service.get_channel_info(session, username)
                if not channel_info:
                    # Channel doesn't exist, but we can still try to remove from DB by username
                    subscription_dao = KickSubscriptionDao()
                    existing = subscription_dao.get_subscription_by_username(username)
                    if existing:
                        broadcaster_user_id = existing['broadcaster_user_id']
                    else:
                        subscription_dao.close()
                        logger.warning(f"Kick channel {username} not found in API or DB")
                        return True, f"Removed tracking for {username}"
                else:
                    # Extract user_id - different API endpoints return different field names
                    broadcaster_user_id = str(
                        channel_info.get('broadcaster_user_id') or
                        channel_info.get('user_id') or
                        channel_info.get('id') or
                        ''
                    )

                    if not broadcaster_user_id or broadcaster_user_id == 'None':
                        subscription_dao = KickSubscriptionDao()
                        subscription_dao.close()
                        return True, f"Could not determine user ID, removed from local tracking"

                subscription_dao = KickSubscriptionDao()

                # Get subscription record before removing
                subscription = subscription_dao.get_subscription_by_broadcaster(broadcaster_user_id)
                if not subscription:
                    subscription_dao.close()
                    return True, f"No subscription found for {username}"

                # Remove guild from subscription
                remaining_guilds = subscription_dao.remove_guild_from_subscription(
                    broadcaster_user_id,
                    guild_id
                )

                if remaining_guilds == 0:
                    # No more guilds tracking this channel - delete webhook subscription
                    logger.info(f"No guilds tracking {username}, deleting Kick webhook subscription")

                    # Delete webhook subscription via API
                    if subscription['subscription_id']:
                        delete_success = await self.webhook_service.delete_subscription(
                            session,
                            subscription['subscription_id']
                        )

                        if not delete_success:
                            logger.warning(f"Failed to delete Kick webhook subscription for {username}, but continuing with DB cleanup")

                    # Delete database record
                    subscription_dao.delete_subscription(broadcaster_user_id)
                    subscription_dao.close()

                    return True, f"Fully unsubscribed from {username}"
                else:
                    subscription_dao.close()
                    logger.info(f"Decremented Kick subscription reference for {username} (guild {guild_id}), {remaining_guilds} guilds remaining")
                    return True, f"Removed from guild, {remaining_guilds} guilds still tracking {username}"

        except Exception as e:
            logger.error(f"Error unsubscribing from Kick channel {username}: {e}", exc_info=True)
            return False, f"Internal error: {str(e)}"

    def get_tracked_channels(self) -> list:
        """
        Get all channels currently being tracked

        Returns:
            List of channel info dicts
        """
        try:
            subscription_dao = KickSubscriptionDao()
            subscriptions = subscription_dao.get_all_subscriptions()
            subscription_dao.close()
            return subscriptions
        except Exception as e:
            logger.error(f"Error getting tracked Kick channels: {e}", exc_info=True)
            return []
