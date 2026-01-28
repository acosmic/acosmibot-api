"""Guild management endpoints - config, permissions, stats"""
import sys
from pathlib import Path
from flask import Blueprint, jsonify, request, current_app
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import GuildDao, GuildUserDao, ReactionRoleDao
from api.services.discord_integration import check_admin_sync, get_channels_sync, http_client
from api.services.twitch_subscription_manager import TwitchSubscriptionManager
from api.services.youtube_subscription_manager import YouTubeSubscriptionManager
from api.services.kick_subscription_manager import KickSubscriptionManager
from api.services.redis_client import publish_cache_invalidation
from acosmibot.Services.youtube_service import YouTubeService
import aiohttp
import json
from datetime import datetime
import logging
import asyncio

from acosmibot.models.settings_manager import SettingsManager
from acosmibot.utils.premium_checker import PremiumChecker
from api import run_async_threadsafe

logger = logging.getLogger(__name__)
guilds_bp = Blueprint('guilds', __name__, url_prefix='/api')

def get_settings_manager():
    """
    Get settings manager singleton instance.
    """
    try:
        return SettingsManager.get_instance()
    except ValueError:
        with GuildDao() as guild_dao:
            return SettingsManager(guild_dao)

@guilds_bp.route('/user/guilds', methods=['GET'])
@require_auth
def get_user_guilds():
    """Get guilds from database with actual Discord permissions - OPTIMIZED"""
    def get_guilds_sync():
        async def get_guilds_async():
            # This is a complex async function that needs to run in the background thread
            # It fetches data from DB and then makes parallel Discord API calls.
            with GuildDao() as guild_dao:
                sql = "SELECT DISTINCT g.id, g.name, g.owner_id FROM Guilds g JOIN GuildUsers gu ON g.id = gu.guild_id WHERE gu.user_id = %s AND gu.is_active = TRUE"
                results = guild_dao.execute_query(sql, (int(request.user_id),))
                if not results:
                    return []

                guild_ids = [row[0] for row in results]
                member_counts = {}
                if guild_ids:
                    placeholders = ','.join(['%s'] * len(guild_ids))
                    member_count_sql = f"SELECT guild_id, COUNT(*) as count FROM GuildUsers WHERE guild_id IN ({placeholders}) AND is_active = TRUE GROUP BY guild_id"
                    counts_result = guild_dao.execute_query(member_count_sql, tuple(guild_ids))
                    member_counts = {row[0]: row[1] for row in counts_result}

            async def process_guild(guild_id, guild_name, owner_id):
                try:
                    guild_info = await http_client.get_guild_info(str(guild_id))
                    has_admin = await http_client.check_admin(request.user_id, str(guild_id), guild_info)
                    
                    fresh_owner_id = guild_info.get('owner_id') if guild_info else None
                    is_owner = str(fresh_owner_id) == request.user_id if fresh_owner_id else str(owner_id) == request.user_id

                    if fresh_owner_id and str(fresh_owner_id) != str(owner_id):
                        with GuildDao() as guild_dao_update:
                            guild_record = guild_dao_update.get_guild(guild_id)
                            if guild_record:
                                guild_record.owner_id = int(fresh_owner_id)
                                guild_dao_update.update_guild(guild_record)
                    
                    permissions = ["administrator"] if is_owner or has_admin else ["member"]

                    # Get premium tier for this guild
                    premium_tier = PremiumChecker.get_guild_tier(guild_id)

                    return {
                        "id": str(guild_id),
                        "name": guild_info.get('name', guild_name) if guild_info else guild_name,
                        "member_count": member_counts.get(guild_id, 0),
                        "owner": is_owner,
                        "permissions": permissions,
                        "icon": guild_info.get('icon') if guild_info else None,
                        "banner": guild_info.get('banner') if guild_info else None,
                        "premium_tier": premium_tier,
                    }
                except Exception as e:
                    logger.error(f"Error processing guild {guild_id}: {e}", exc_info=True)
                    return None

            tasks = [process_guild(row[0], row[1], row[2]) for row in results]
            processed_guilds = await asyncio.gather(*tasks)
            return [g for g in processed_guilds if g is not None]

        return run_async_threadsafe(get_guilds_async())

    try:
        return jsonify({"success": True, "guilds": get_guilds_sync()})
    except Exception as e:
        logger.error(f"Error getting guilds from database: {e}", exc_info=True)
        return jsonify({"success": False, "message": str(e)}), 500

@guilds_bp.route('/guilds/<guild_id>/permissions', methods=['GET'])
@require_auth
def get_guild_permissions(guild_id):
    """Check user's permissions for a guild"""
    try:
        # Check if user has admin permissions
        has_admin = run_async_threadsafe(http_client.check_admin(request.user_id, guild_id))

        # For now, has_admin and can_configure_bot are the same
        # You can add more granular permission checks here if needed
        return jsonify({
            "success": True,
            "data": {
                "has_admin": has_admin,
                "can_configure_bot": has_admin
            }
        })
    except Exception as e:
        logger.error(f"Error checking guild permissions: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Internal server error", "error": str(e)}), 500

@guilds_bp.route('/guilds/<guild_id>/channels', methods=['GET'])
@require_auth
def get_guild_channels(guild_id):
    """Get text channels for a guild"""
    try:
        # Check if user has admin permissions
        has_admin = run_async_threadsafe(http_client.check_admin(request.user_id, guild_id))
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to view this server's channels"
            }), 403

        async def fetch_channels():
            all_channels = await http_client.get_guild_channels(guild_id)
            # Filter to only text and announcement channels (type 0 and 5)
            channels = [
                ch for ch in all_channels
                if ch.get('type') in [0, 5]  # Text and announcement channels
            ]
            return channels

        channels = run_async_threadsafe(fetch_channels())
        return jsonify({
            "success": True,
            "channels": channels
        })
    except Exception as e:
        logger.error(f"Error fetching guild channels: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch channels",
            "error": str(e)
        }), 500

@guilds_bp.route('/guilds/<guild_id>/config-hybrid', methods=['GET', 'POST'])
@require_auth
def guild_config_hybrid(guild_id):
    """Get or update guild configuration using hybrid approach"""
    try:
        has_admin = run_async_threadsafe(http_client.check_admin(request.user_id, guild_id))
        if not has_admin:
            return jsonify({"success": False, "message": "You don't have permission to manage this server"}), 403

        settings_manager = get_settings_manager()

        # GET request - fetch current configuration
        if request.method == 'GET':
            async def fetch_guild_data():
                settings = settings_manager.get_settings_dict(int(guild_id))

                # Define default moderation settings
                default_moderation_settings = {
                    "enabled": False,
                    "mod_log_channel_id": None,
                    "member_activity_channel_id": None,
                    "events": {
                        "on_member_join": {"enabled": True, "color": "#00ff00", "message": "Welcome {user.mention} to the server!"},
                        "on_member_remove": {"enabled": True, "color": "#ff0000", "message": "{user.name} has left the server."},
                        "on_message_edit": {"enabled": True},
                        "on_message_delete": {"enabled": True},
                        "on_audit_log_entry": {
                            "ban": {"enabled": True},
                            "unban": {"enabled": True},
                            "kick": {"enabled": True},
                            "mute": {"enabled": True},
                            "role_change": {"enabled": True}
                        },
                        "on_member_update": {
                            "nickname_change": {"enabled": True}
                        }
                    }
                }

                # Merge with existing settings
                if "moderation" in settings:
                    # A simple dict update won't work for nested dicts, so we do it manually
                    for key, value in default_moderation_settings.items():
                        if key not in settings["moderation"]:
                            settings["moderation"][key] = value
                        elif isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if sub_key not in settings["moderation"][key]:
                                    settings["moderation"][key][sub_key] = sub_value
                else:
                    settings["moderation"] = default_moderation_settings

                # Fetch Discord metadata for dropdowns
                guild_info = await http_client.get_guild_info(guild_id)
                all_channels = await http_client.get_guild_channels(guild_id)
                roles = await http_client.get_guild_roles(guild_id)
                emojis = await http_client.get_guild_emojis(guild_id)

                # Filter to only text and announcement channels (type 0 and 5)
                channels = [
                    ch for ch in all_channels
                    if ch.get('type') in [0, 5]  # Text and announcement channels
                ]

                # Format emojis with URLs for frontend
                formatted_emojis = []
                for emoji in emojis:
                    ext = 'gif' if emoji.get('animated') else 'png'
                    formatted_emojis.append({
                        'id': emoji.get('id'),
                        'name': emoji.get('name'),
                        'animated': emoji.get('animated', False),
                        'url': f"https://cdn.discordapp.com/emojis/{emoji.get('id')}.{ext}"
                    })

                # Get guild icon hash (frontend will construct the URL)
                guild_icon = guild_info.get('icon') if guild_info else None

                # Get premium tier information
                premium_tier = PremiumChecker.get_guild_tier(int(guild_id))

                return {
                    "guild_id": guild_id,
                    "guild_name": guild_info.get('name') if guild_info else 'Guild Settings',
                    "guild_icon": guild_icon,
                    "premium_tier": premium_tier,
                    "settings": settings,
                    "available_channels": channels,
                    "available_roles": roles,
                    "available_emojis": formatted_emojis
                }

            guild_data = run_async_threadsafe(fetch_guild_data())
            return jsonify({
                "success": True,
                "data": guild_data
            })

        # POST request - update configuration
        data = request.get_json()
        if not data or 'settings' not in data:
            return jsonify({"success": False, "message": "Settings data is required"}), 400

        settings = data['settings']
        current_settings = settings_manager.get_settings_dict(int(guild_id))

        # Twitch subscription logic
        if 'twitch' in settings and settings['twitch'].get('enabled'):
            current_twitch_streamers = {s['username'].lower() for s in current_settings.get('twitch', {}).get('tracked_streamers', [])}
            new_twitch_streamers = {s['username'].lower() for s in settings['twitch'].get('tracked_streamers', [])}
            added_twitch = new_twitch_streamers - current_twitch_streamers
            removed_twitch = current_twitch_streamers - new_twitch_streamers

            if added_twitch or removed_twitch:
                twitch_manager = TwitchSubscriptionManager()
                for username in added_twitch:
                    run_async_threadsafe(twitch_manager.subscribe_to_streamer(username, int(guild_id)))
                for username in removed_twitch:
                    run_async_threadsafe(twitch_manager.unsubscribe_from_streamer(username, int(guild_id)))

        # YouTube subscription logic
        if 'youtube' in settings and settings['youtube'].get('enabled'):
            current_youtube_channels = {s['username'] for s in current_settings.get('youtube', {}).get('tracked_streamers', [])}
            new_youtube_channels = {s['username'] for s in settings['youtube'].get('tracked_streamers', [])}
            added_youtube = new_youtube_channels - current_youtube_channels
            removed_youtube = current_youtube_channels - new_youtube_channels

            if added_youtube or removed_youtube:
                youtube_webhook_callback_url = current_app.config.get("YOUTUBE_WEBHOOK_CALLBACK_URL")
                if not youtube_webhook_callback_url:
                    return jsonify({"success": False, "message": "YouTube webhook callback URL not configured on server."}), 500

                youtube_manager = YouTubeSubscriptionManager(youtube_webhook_callback_url)
                
                async def process_youtube_changes():
                    youtube_service = YouTubeService()
                    async with aiohttp.ClientSession() as session:
                        for username in added_youtube:
                            channel_id = await youtube_service.resolve_channel_id(session, username)
                            if channel_id:
                                # Fetch channel info to get the channel name
                                channel_info = await youtube_service.get_channel_info(session, channel_id)
                                channel_name = channel_info.get('title') if channel_info else None
                                await youtube_manager.add_subscription(int(guild_id), channel_id, channel_name)
                            else:
                                logger.error(f"Could not resolve YouTube channel ID for username: {username}")

                        for username in removed_youtube:
                            channel_id = await youtube_service.resolve_channel_id(session, username)
                            if channel_id:
                                await youtube_manager.remove_subscription(int(guild_id), channel_id)
                            else:
                                logger.error(f"Could not resolve YouTube channel ID for username to remove subscription: {username}")

                run_async_threadsafe(process_youtube_changes())

        # Kick subscription logic
        if 'kick' in settings and settings['kick'].get('enabled'):
            current_kick_streamers = {s['username'].lower() for s in current_settings.get('kick', {}).get('tracked_streamers', [])}
            new_kick_streamers = {s['username'].lower() for s in settings['kick'].get('tracked_streamers', [])}
            added_kick = new_kick_streamers - current_kick_streamers
            removed_kick = current_kick_streamers - new_kick_streamers

            if added_kick or removed_kick:
                kick_manager = KickSubscriptionManager()

                async def process_kick_changes():
                    for username in added_kick:
                        success, message = await kick_manager.subscribe_to_streamer(username, int(guild_id))
                        if not success:
                            logger.error(f"Failed to subscribe to Kick streamer {username}: {message}")
                        else:
                            logger.info(f"Successfully subscribed to Kick streamer {username} for guild {guild_id}")

                    for username in removed_kick:
                        success, message = await kick_manager.unsubscribe_from_streamer(username, int(guild_id))
                        if not success:
                            logger.error(f"Failed to unsubscribe from Kick streamer {username}: {message}")
                        else:
                            logger.info(f"Successfully unsubscribed from Kick streamer {username} for guild {guild_id}")

                run_async_threadsafe(process_kick_changes())

        success = settings_manager.update_settings_dict(guild_id, settings)

        if not success:
            return jsonify({"success": False, "message": "Failed to update settings in database"}), 500

        # ⚡ NEW: Publish cache invalidation to bot instances
        publish_cache_invalidation(int(guild_id))

        return jsonify({
            "success": True,
            "message": "Settings updated successfully",
            "data": {"guild_id": guild_id, "settings": settings, "updated_at": datetime.now().isoformat()}
        })

    except Exception as e:
        logger.error(f"Error updating guild config: {e}", exc_info=True)
        return jsonify({"success": False, "message": "Internal server error", "error": str(e)}), 500


@guilds_bp.route('/guilds/<guild_id>/stats-db', methods=['GET'])
@require_auth
def get_guild_stats_db(guild_id):
    """Get guild statistics from database"""
    try:
        with GuildUserDao() as guild_user_dao:
            # Verify user is member of this guild
            guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
            if not guild_user or not guild_user.is_active:
                return jsonify({
                    "success": False,
                    "message": "You are not a member of this server"
                }), 403

            # Get guild stats
            stats = guild_user_dao.get_guild_stats(int(guild_id))

        # Get guild name from GuildDao
        with GuildDao() as guild_dao:
            guild = guild_dao.get_guild(int(guild_id))
            guild_name = guild.name if guild else "Unknown"

        return jsonify({
            "success": True,
            "data": {
                "guild_name": guild_name,
                "member_count": stats.get('total_active_users', 0),
                "total_active_members": stats.get('total_active_users', 0),
                "total_messages": stats.get('total_messages', 0),
                "total_exp_distributed": stats.get('total_exp', 0),
                "total_currency": stats.get('total_currency', 0),
                "total_reactions": stats.get('total_reactions', 0)
            }
        })

    except Exception as e:
        logger.error(f"Error getting guild stats: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get guild stats",
            "error": str(e)
        }), 500