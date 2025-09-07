import discord
import asyncio
import os
from typing import List, Tuple, Optional
from models.discord_models import DiscordRole, GuildChannelInfo, ChannelType
from dotenv import load_dotenv

load_dotenv()


class DiscordAPIClient:
    """Discord API client for the web API"""

    def __init__(self):
        self.bot_token = os.getenv('DISCORD_BOT_TOKEN')
        self.client = None
        self._loop = None

    async def initialize(self):
        """Initialize the Discord client"""
        if self.client is None:
            intents = discord.Intents.default()
            intents.guilds = True
            intents.members = True  # Needed for permission checking

            self.client = discord.Client(intents=intents)

            @self.client.event
            async def on_ready():
                print(f'Discord API client ready as {self.client.user}')

            # Start the client
            await self.client.login(self.bot_token)
            # Don't call connect() as we're using this for API calls only

    async def get_guild_data(self, guild_id: str) -> Tuple[List[DiscordRole], List[GuildChannelInfo]]:
        """Get guild roles and channels"""
        try:
            if self.client is None:
                await self.initialize()

            guild = self.client.get_guild(int(guild_id))
            if not guild:
                print(f"Guild {guild_id} not found")
                return [], []

            # Get roles (excluding @everyone and managed roles for assignment)
            roles = []
            for role in guild.roles:
                if role.name != "@everyone":  # Include all roles for display
                    roles.append(DiscordRole(
                        id=str(role.id),
                        name=role.name,
                        color=f"#{role.color.value:06x}" if role.color.value != 0 else "#99AAB5",
                        position=role.position,
                        permissions=str(role.permissions.value),
                        managed=role.managed,
                        mentionable=role.mentionable,
                        hoist=role.hoist
                    ))

            # Get text channels
            channels = []
            for channel in guild.channels:
                if isinstance(channel, discord.TextChannel):
                    channel_type = ChannelType.TEXT
                elif isinstance(channel, discord.VoiceChannel):
                    channel_type = ChannelType.VOICE
                elif isinstance(channel, discord.CategoryChannel):
                    channel_type = ChannelType.CATEGORY
                elif hasattr(discord, 'ForumChannel') and isinstance(channel, discord.ForumChannel):
                    channel_type = ChannelType.FORUM
                elif hasattr(discord, 'StageChannel') and isinstance(channel, discord.StageChannel):
                    channel_type = ChannelType.STAGE
                else:
                    continue  # Skip unknown channel types

                # Get category info
                category_name = None
                category_id = None
                if hasattr(channel, 'category') and channel.category:
                    category_name = channel.category.name
                    category_id = str(channel.category.id)

                channels.append(GuildChannelInfo(
                    id=str(channel.id),
                    name=channel.name,
                    type=channel_type,
                    position=channel.position,
                    category_id=category_id,
                    category_name=category_name,
                    nsfw=getattr(channel, 'nsfw', False)
                ))

            return roles, channels

        except Exception as e:
            print(f"Error getting guild data: {e}")
            return [], []

    async def check_user_permissions(self, user_id: str, guild_id: str) -> bool:
        """Check if user has admin permissions in guild"""
        try:
            if self.client is None:
                await self.initialize()

            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return False

            member = guild.get_member(int(user_id))
            if not member:
                return False

            # Check if user is guild owner
            if member.id == guild.owner_id:
                return True

            # Check if user has administrator permission
            if member.guild_permissions.administrator:
                return True

            # Check if user has manage_guild permission (also sufficient)
            if member.guild_permissions.manage_guild:
                return True

            return False

        except Exception as e:
            print(f"Error checking user permissions: {e}")
            return False

    async def get_guild_info(self, guild_id: str) -> Optional[dict]:
        """Get basic guild information"""
        try:
            if self.client is None:
                await self.initialize()

            guild = self.client.get_guild(int(guild_id))
            if not guild:
                return None

            return {
                "id": str(guild.id),
                "name": guild.name,
                "icon": guild.icon.key if guild.icon else None,
                "owner_id": str(guild.owner_id),
                "member_count": guild.member_count,
                "premium_tier": guild.premium_tier,
                "features": guild.features
            }

        except Exception as e:
            print(f"Error getting guild info: {e}")
            return None

    async def get_user_guilds(self, user_id: str) -> List[dict]:
        """Get guilds where user has admin permissions"""
        try:
            if self.client is None:
                await self.initialize()

            print(f"Discord client user: {self.client.user}")
            print(f"Bot is in {len(self.client.guilds)} guilds")

            user_guilds = []

            # Check all guilds the bot is in
            for guild in self.client.guilds:
                print(f"Checking guild: {guild.name} (ID: {guild.id})")
                member = guild.get_member(int(user_id))
                if member:
                    print(f"  User found in {guild.name}")
                    print(f"  User permissions: {member.guild_permissions}")
                    print(f"  Is owner: {member.id == guild.owner_id}")
                    print(f"  Has admin: {member.guild_permissions.administrator}")
                    print(f"  Has manage_guild: {member.guild_permissions.manage_guild}")

                    # Check if user has admin permissions
                    if (member.id == guild.owner_id or
                            member.guild_permissions.administrator or
                            member.guild_permissions.manage_guild):

                        print(f"  ✓ User has admin permissions in {guild.name}")
                        user_guilds.append({
                            "id": str(guild.id),
                            "name": guild.name,
                            "icon": guild.icon.key if guild.icon else None,
                            "owner": member.id == guild.owner_id,
                            "permissions": ["administrator"] if member.guild_permissions.administrator else [
                                "manage_guild"]
                        })
                    else:
                        print(f"  ✗ User lacks admin permissions in {guild.name}")
                else:
                    print(f"  User not found in {guild.name}")

            print(f"Final result: {len(user_guilds)} guilds with admin access")
            return user_guilds

        except Exception as e:
            print(f"Error getting user guilds: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def close(self):
        """Close the Discord client"""
        if self.client:
            await self.client.close()


# Global instance
discord_api = DiscordAPIClient()


# Async wrapper functions for use in your Flask app
def run_async(coro):
    """Run async function in Flask app"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # If loop is already running, use asyncio.create_task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result()
    else:
        return loop.run_until_complete(coro)


# Updated functions for your Flask app
async def check_guild_admin_permissions(user_id: str, guild_id: str) -> bool:
    """Check if user has admin permissions in the guild"""
    return await discord_api.check_user_permissions(user_id, guild_id)


async def get_discord_guild_data(guild_id: str) -> Tuple[List[DiscordRole], List[GuildChannelInfo]]:
    """Get Discord guild roles and channels"""
    return await discord_api.get_guild_data(guild_id)


async def get_guild_info(guild_id: str) -> Optional[dict]:
    """Get guild information"""
    return await discord_api.get_guild_info(guild_id)


async def get_user_manageable_guilds(user_id: str) -> List[dict]:
    """Get guilds the user can manage"""
    return await discord_api.get_user_guilds(user_id)


# Sync wrapper functions for Flask (since Flask doesn't handle async well)
def check_guild_admin_permissions_sync(user_id: str, guild_id: str) -> bool:
    """Sync wrapper for guild admin check"""
    return run_async(check_guild_admin_permissions(user_id, guild_id))


def get_discord_guild_data_sync(guild_id: str) -> Tuple[List[DiscordRole], List[GuildChannelInfo]]:
    """Sync wrapper for getting guild data"""
    return run_async(get_discord_guild_data(guild_id))


def get_guild_info_sync(guild_id: str) -> Optional[dict]:
    """Sync wrapper for getting guild info"""
    return run_async(get_guild_info(guild_id))


def get_user_manageable_guilds_sync(user_id: str) -> List[dict]:
    """Sync wrapper for getting user guilds"""
    return run_async(get_user_manageable_guilds(user_id))


# Initialize on app startup
async def initialize_discord_client():
    """Initialize the Discord client on app startup"""
    await discord_api.initialize()


# Cleanup on app shutdown
async def cleanup_discord_client():
    """Cleanup Discord client on app shutdown"""
    await discord_api.close()