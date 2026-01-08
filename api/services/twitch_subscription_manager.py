"""
Twitch EventSub subscription management helper
Handles creating/deleting subscriptions with reference counting
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

from api.services.twitch_eventsub_service import TwitchEventSubService
from Dao.TwitchEventSubDao import TwitchEventSubDao
from Services.twitch_service import TwitchService

logger = logging.getLogger(__name__)


class TwitchSubscriptionManager:
    """Manages EventSub subscriptions with reference counting"""

    def __init__(self):
        self.eventsub_service = TwitchEventSubService()
        self.twitch_service = TwitchService()

    async def subscribe_to_streamer(
        self,
        username: str,
        guild_id: int
    ) -> Tuple[bool, str]:
        """
        Subscribe to streamer's events (or increment reference count)

        Args:
            username: Twitch username
            guild_id: Guild ID adding this streamer

        Returns:
            (success, message)
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Get broadcaster user ID
                user_info = await self.twitch_service.get_user_info(session, username)
                if not user_info:
                    return False, f"Twitch user '{username}' not found"

                broadcaster_user_id = user_info['id']
                broadcaster_username = user_info['login']

                eventsub_dao = TwitchEventSubDao()

                # Check if subscription already exists
                existing = eventsub_dao.get_subscription_by_broadcaster(broadcaster_user_id)

                if existing:
                    # Subscription exists - just increment reference count
                    eventsub_dao.add_guild_to_subscription(broadcaster_user_id, guild_id)
                    eventsub_dao.close()

                    logger.info(f"Incremented subscription reference for {broadcaster_username} (guild {guild_id})")
                    return True, f"Subscription added for {broadcaster_username}"

                # Create new subscriptions
                online_sub_id = await self.eventsub_service.create_stream_online_subscription(
                    session,
                    broadcaster_user_id
                )

                offline_sub_id = await self.eventsub_service.create_stream_offline_subscription(
                    session,
                    broadcaster_user_id
                )

                if not online_sub_id or not offline_sub_id:
                    eventsub_dao.close()
                    return False, f"Failed to create EventSub subscriptions for {broadcaster_username}"

                # Create database record
                eventsub_dao.create_subscription(
                    broadcaster_user_id=broadcaster_user_id,
                    broadcaster_username=broadcaster_username,
                    guild_id=guild_id,
                    online_subscription_id=online_sub_id,
                    offline_subscription_id=offline_sub_id
                )
                eventsub_dao.close()

                logger.info(f"Created new EventSub subscription for {broadcaster_username} (guild {guild_id})")
                return True, f"EventSub subscription created for {broadcaster_username}"

        except Exception as e:
            logger.error(f"Error subscribing to {username}: {e}", exc_info=True)
            return False, f"Internal error: {str(e)}"

    async def unsubscribe_from_streamer(
        self,
        username: str,
        guild_id: int
    ) -> Tuple[bool, str]:
        """
        Unsubscribe from streamer (or decrement reference count)

        Args:
            username: Twitch username
            guild_id: Guild ID removing this streamer

        Returns:
            (success, message)
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Get broadcaster user ID
                user_info = await self.twitch_service.get_user_info(session, username)
                if not user_info:
                    return True, f"User '{username}' not found, skipping"

                broadcaster_user_id = user_info['id']
                broadcaster_username = user_info['login']

                eventsub_dao = TwitchEventSubDao()

                # Get subscription record
                subscription = eventsub_dao.get_subscription_by_broadcaster(broadcaster_user_id)
                if not subscription:
                    eventsub_dao.close()
                    return True, f"No subscription found for {broadcaster_username}"

                # Decrement reference count
                remaining_count = eventsub_dao.remove_guild_from_subscription(
                    broadcaster_user_id,
                    guild_id
                )

                # If no guilds tracking, delete EventSub subscriptions
                if remaining_count == 0:
                    logger.info(f"No guilds tracking {broadcaster_username}, deleting EventSub subscriptions")

                    # Delete online subscription
                    if subscription['online_subscription_id']:
                        await self.eventsub_service.delete_subscription(
                            session,
                            subscription['online_subscription_id']
                        )

                    # Delete offline subscription
                    if subscription['offline_subscription_id']:
                        await self.eventsub_service.delete_subscription(
                            session,
                            subscription['offline_subscription_id']
                        )

                    # Delete database record
                    eventsub_dao.delete_subscription(broadcaster_user_id)
                    eventsub_dao.close()

                    return True, f"Fully unsubscribed from {broadcaster_username}"
                else:
                    eventsub_dao.close()
                    logger.info(f"Decremented subscription reference for {broadcaster_username} (guild {guild_id}), {remaining_count} guilds remaining")
                    return True, f"Removed from guild, {remaining_count} guilds still tracking {broadcaster_username}"

        except Exception as e:
            logger.error(f"Error unsubscribing from {username}: {e}", exc_info=True)
            return False, f"Internal error: {str(e)}"

    async def bulk_subscribe_existing_streamers(self) -> dict:
        """
        Bulk create subscriptions for all existing tracked streamers
        (Used during deployment/migration)

        Returns:
            Stats dict with counts
        """
        from Dao.GuildDao import GuildDao

        stats = {
            'total_streamers': 0,
            'successful': 0,
            'failed': 0,
            'errors': []
        }

        try:
            # Get all guilds and their streaming settings
            guild_dao = GuildDao()
            all_guilds = guild_dao.get_all_guilds()

            # Collect unique streamers across all guilds
            streamer_guilds = {}  # {username: [guild_id1, guild_id2, ...]}

            for guild in all_guilds:
                settings = guild_dao.get_guild_settings(guild.id)
                if not settings:
                    continue

                twitch_settings = settings.get('twitch', {})
                if not twitch_settings.get('enabled'):
                    continue

                for streamer_config in twitch_settings.get('tracked_streamers', []):
                    if streamer_config.get('platform') == 'twitch':
                        username = streamer_config.get('username')
                        if username:
                            username_lower = username.lower()
                            if username_lower not in streamer_guilds:
                                streamer_guilds[username_lower] = []
                            streamer_guilds[username_lower].append(guild.id)

            guild_dao.close()

            stats['total_streamers'] = len(streamer_guilds)
            logger.info(f"Found {stats['total_streamers']} unique Twitch streamers to subscribe")

            # Create subscriptions
            for username, guild_ids in streamer_guilds.items():
                try:
                    # Subscribe with first guild
                    success, message = await self.subscribe_to_streamer(username, guild_ids[0])

                    if success:
                        # Add remaining guilds
                        async with aiohttp.ClientSession() as session:
                            user_info = await self.twitch_service.get_user_info(session, username)
                            if user_info:
                                broadcaster_user_id = user_info['id']
                                eventsub_dao = TwitchEventSubDao()

                                for guild_id in guild_ids[1:]:
                                    eventsub_dao.add_guild_to_subscription(broadcaster_user_id, guild_id)

                                eventsub_dao.close()

                        stats['successful'] += 1
                        logger.info(f"Subscribed to {username} for {len(guild_ids)} guilds")
                    else:
                        stats['failed'] += 1
                        stats['errors'].append(f"{username}: {message}")
                        logger.error(f"Failed to subscribe to {username}: {message}")

                except Exception as e:
                    stats['failed'] += 1
                    error_msg = f"{username}: {str(e)}"
                    stats['errors'].append(error_msg)
                    logger.error(f"Error subscribing to {username}: {e}", exc_info=True)

            return stats

        except Exception as e:
            logger.error(f"Error in bulk subscription: {e}", exc_info=True)
            stats['errors'].append(f"Bulk subscription error: {str(e)}")
            return stats


# Synchronous wrapper for Flask routes
def run_async(coro):
    """Run async function synchronously"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
