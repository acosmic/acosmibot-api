import asyncio
import aiohttp
import os
from dotenv import load_dotenv
import logging

load_dotenv()

logger = logging.getLogger(__name__)


class SimpleDiscordHTTPClient:
    def __init__(self):
        self.bot_token = os.getenv('DISCORD_BOT_TOKEN')
        self.base_url = "https://discord.com/api/v10"
        self.headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json"
        }

    async def get_guild_info(self, guild_id: str):
        """Get guild info via HTTP API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/guilds/{guild_id}"
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Failed to get guild {guild_id}: {response.status}")
                        return None
            except Exception as e:
                print(f"Error getting guild info: {e}")
                return None

    async def get_guild_member(self, guild_id: str, user_id: str):
        """Get guild member info via HTTP API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/guilds/{guild_id}/members/{user_id}"
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Failed to get member {user_id} in guild {guild_id}: {response.status}")
                        return None
            except Exception as e:
                print(f"Error getting member info: {e}")
                return None

    async def get_guild_channels(self, guild_id: str):
        """Get guild channels via HTTP API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/guilds/{guild_id}/channels"
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Failed to get channels for guild {guild_id}: {response.status}")
                        return []
            except Exception as e:
                print(f"Error getting channels: {e}")
                return []

    async def get_guild_roles(self, guild_id: str):
        """Get guild roles via HTTP API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/guilds/{guild_id}/roles"
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Failed to get roles for guild {guild_id}: {response.status}")
                        return []
            except Exception as e:
                print(f"Error getting roles: {e}")
                return []

    async def get_guild_emojis(self, guild_id: str):
        """Get guild emojis from Discord API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/guilds/{guild_id}/emojis"
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Error getting emojis: {response.status}")
                        return []
            except Exception as e:
                print(f"Error getting emojis: {e}")
                return []

    async def list_bot_guilds(self):
        """List all guilds the bot is in via HTTP API"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/users/@me/guilds"
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Failed to get bot guilds: {response.status}")
                        return []
            except Exception as e:
                print(f"Error listing guilds: {e}")
                return []

    async def check_admin(self, user_id: str, guild_id: str):
        """Check if user has admin permissions"""
        try:
            # Get guild info
            guild = await self.get_guild_info(guild_id)
            if not guild:
                logger.warning(f"[check_admin] Guild {guild_id} not found or bot not in guild")
                return False

            logger.info(f"[check_admin] Guild found: {guild['name']}, Owner: {guild['owner_id']}")

            # Check if user is guild owner
            if str(guild['owner_id']) == str(user_id):
                logger.info(f"[check_admin] User {user_id} is owner of guild {guild_id}")
                return True

            # Get member info
            member = await self.get_guild_member(guild_id, user_id)
            if not member:
                logger.warning(f"[check_admin] User {user_id} not found in guild {guild_id}")
                return False

            logger.info(f"[check_admin] Member has roles: {member.get('roles', [])}")

            # Get guild roles to calculate permissions
            guild_roles = await self.get_guild_roles(guild_id)
            if not guild_roles:
                logger.warning(f"[check_admin] Could not fetch guild roles for {guild_id}")
                return False

            # Calculate permissions from user's roles
            user_role_ids = member.get('roles', [])
            combined_permissions = 0

            for role in guild_roles:
                if str(role['id']) in user_role_ids or role['id'] in user_role_ids:
                    role_perms = int(role.get('permissions', '0'))
                    combined_permissions |= role_perms
                    logger.info(f"[check_admin] Role '{role['name']}' (id: {role['id']}) has permissions: {role_perms} (binary: {bin(role_perms)})")

            logger.info(f"[check_admin] Combined permissions: {combined_permissions} (binary: {bin(combined_permissions)})")

            # Administrator permission bit is 0x8 (bit 3)
            has_admin = bool(combined_permissions & 0x8)
            # Manage guild permission bit is 0x20 (bit 5)
            has_manage_guild = bool(combined_permissions & 0x20)

            logger.info(f"[check_admin] Has admin (0x8): {has_admin}, Has manage guild (0x20): {has_manage_guild}")

            return has_admin or has_manage_guild

        except Exception as e:
            logger.error(f"[check_admin] Error checking admin: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def post_message(self, channel_id: int, message_data: dict):
        """Post a message to a Discord channel"""
        async with aiohttp.ClientSession() as session:
            try:
                url = f"{self.base_url}/channels/{channel_id}/messages"
                async with session.post(url, headers=self.headers, json=message_data) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"Failed to post message to channel {channel_id}: {response.status}")
                        error_text = await response.text()
                        logger.error(f"Response: {error_text}")
                        return None
            except Exception as e:
                logger.error(f"Error posting message: {e}")
                return None

    async def add_reaction(self, channel_id: int, message_id: int, emoji: str):
        """Add a reaction to a Discord message"""
        async with aiohttp.ClientSession() as session:
            try:
                # URL encode the emoji (custom emojis need special handling)
                if ':' in emoji:
                    # Custom emoji format: <:name:id> or <a:name:id>
                    # Extract just the id part for the API
                    emoji_for_url = emoji.split(':')[-1].rstrip('>')
                else:
                    # Standard emoji - URL encode it
                    import urllib.parse
                    emoji_for_url = urllib.parse.quote(emoji)

                url = f"{self.base_url}/channels/{channel_id}/messages/{message_id}/reactions/{emoji_for_url}/@me"
                async with session.put(url, headers=self.headers) as response:
                    if response.status == 204:
                        return True
                    else:
                        logger.error(f"Failed to add reaction to message {message_id}: {response.status}")
                        error_text = await response.text()
                        logger.error(f"Response: {error_text}")
                        return False
            except Exception as e:
                logger.error(f"Error adding reaction: {e}")
                return False

    async def get_channels(self, guild_id: str):
        """Get text and announcement channels for guild"""
        try:
            channels_data = await self.get_guild_channels(guild_id)

            channels = []
            for channel in channels_data:
                # Type 0 = text channel, Type 5 = announcement/news channel
                if channel.get('type') in [0, 5]:
                    channel_type = 'announcement' if channel.get('type') == 5 else 'text'
                    channels.append({
                        'id': str(channel['id']),
                        'name': channel['name'],
                        'type': channel_type
                    })

            return channels

        except Exception as e:
            print(f"Error getting channels: {e}")
            return []

    async def list_all_guilds(self):
        """List all guilds bot is in"""
        try:
            guilds_data = await self.list_bot_guilds()

            guilds = []
            print(f"Bot is in {len(guilds_data)} guilds:")
            for guild in guilds_data:
                guild_info = {
                    'id': str(guild['id']),
                    'name': guild['name'],
                    'owner_id': str(guild.get('owner_id', 'unknown')),
                    'permissions': guild.get('permissions', '0')
                }
                guilds.append(guild_info)
                print(f"  - {guild['name']} (ID: {guild['id']}, Owner: {guild.get('owner_id', 'unknown')})")

            return guilds

        except Exception as e:
            print(f"Error listing guilds: {e}")
            import traceback
            traceback.print_exc()
            return []


# Global client instance
http_client = SimpleDiscordHTTPClient()


def run_sync(coro):
    """Run async function synchronously"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def check_admin_sync(user_id: str, guild_id: str):
    return run_sync(http_client.check_admin(user_id, guild_id))


def get_channels_sync(guild_id: str):
    return run_sync(http_client.get_channels(guild_id))


def list_guilds_sync():
    return run_sync(http_client.list_all_guilds())