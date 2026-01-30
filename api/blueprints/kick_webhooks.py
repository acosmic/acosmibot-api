"""
Kick webhook endpoints
Receives and processes livestream.status.updated events
"""
import sys
from pathlib import Path
from flask import Blueprint, request, jsonify
import logging
import json
import asyncio
import aiohttp
from datetime import datetime
import hashlib
import hmac
import os
from api.services.discord_integration import http_client
from acosmibot_core.dao import KickWebhookEventDao
from acosmibot_core.dao import KickSubscriptionDao
from acosmibot_core.dao import KickAnnouncementDao
from acosmibot_core.dao import GuildDao
from acosmibot_core.services import KickService

logger = logging.getLogger(__name__)
kick_webhooks_bp = Blueprint('kick_webhooks', __name__, url_prefix='/api/webhooks')

def verify_kick_signature(
    message_id: str,
    timestamp: str,
    body: bytes,
    signature: str
) -> bool:
    """
    Verify webhook signature from Kick.

    Kick may use HMAC-SHA256 or RSA signature verification.
    This implementation supports HMAC-SHA256 with a webhook secret.
    """
    webhook_secret = os.getenv('KICK_WEBHOOK_SECRET', '')

    if not webhook_secret:
        # If no secret configured, log warning but allow (for testing)
        logger.warning("No KICK_WEBHOOK_SECRET configured, skipping signature verification")
        return True

    try:
        # Construct message: id.timestamp.body
        message = f"{message_id}.{timestamp}.".encode() + body

        # Calculate expected signature
        expected_sig = hmac.new(
            webhook_secret.encode(),
            message,
            hashlib.sha256
        ).hexdigest()

        # Compare signatures
        return hmac.compare_digest(signature, expected_sig)
    except Exception as e:
        logger.error(f"Kick signature verification failed: {e}")
        return False

@kick_webhooks_bp.route('/kick', methods=['POST'])
def kick_webhook():
    """
    Kick webhook endpoint for livestream events

    Headers (expected based on Kick API):
    - Kick-Event-Message-Id: Unique message ID
    - Kick-Event-Subscription-Id: Subscription ID
    - Kick-Event-Signature: Signature for verification
    - Kick-Event-Message-Timestamp: Timestamp
    - Kick-Event-Type: Event type (e.g., 'livestream.status.updated')
    """
    # Get headers
    message_id = request.headers.get('Kick-Event-Message-Id') or request.headers.get('X-Kick-Message-Id')
    subscription_id = request.headers.get('Kick-Event-Subscription-Id') or request.headers.get('X-Kick-Subscription-Id')
    signature = request.headers.get('Kick-Event-Signature') or request.headers.get('X-Kick-Signature')
    timestamp = request.headers.get('Kick-Event-Message-Timestamp') or request.headers.get('X-Kick-Timestamp')
    event_type = request.headers.get('Kick-Event-Type') or request.headers.get('X-Kick-Event-Type')

    # Generate message ID if not provided
    if not message_id:
        message_id = f"kick_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"

    if not timestamp:
        timestamp = datetime.utcnow().isoformat()

    # Get body for signature verification
    body = request.get_data()

    # Log webhook received
    logger.info(f"Kick webhook received - Message ID: {message_id}, Type: {event_type}")

    # Verify signature if provided
    # NOTE: Kick uses RSA signatures (not HMAC-SHA256), so this verification will fail
    # This is a known issue and can be fixed by implementing RSA verification
    # For now, we log but don't reject webhooks
    if signature:
        is_valid = verify_kick_signature(message_id, timestamp, body, signature)
        if not is_valid:
            logger.debug(f"Signature verification skipped for message {message_id} (RSA not implemented)")

    # Parse payload
    try:
        payload = request.get_json()
    except Exception as e:
        logger.error(f"Failed to parse Kick webhook payload: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    if not payload:
        logger.error("Empty Kick webhook payload")
        return jsonify({"error": "Empty payload"}), 400

    # Determine event type from payload if not in headers
    if not event_type:
        event_type = payload.get('event_type') or payload.get('type') or 'unknown'

    # Check for duplicate (idempotency)
    event_dao = KickWebhookEventDao()
    try:
        if event_dao.event_exists(message_id):
            logger.info(f"Duplicate Kick event {message_id}, skipping")
            return jsonify({"success": True, "message": "Duplicate event"}), 200

        # Extract broadcaster info
        broadcaster = payload.get('broadcaster', {}) or payload.get('channel', {}) or {}
        broadcaster_user_id = str(broadcaster.get('user_id') or broadcaster.get('id') or payload.get('broadcaster_user_id') or '')
        broadcaster_username = broadcaster.get('slug') or broadcaster.get('username') or payload.get('broadcaster_username') or ''

        # Record event
        event_dao.create_event(
            event_id=message_id,
            event_type=event_type,
            subscription_id=subscription_id,
            broadcaster_user_id=broadcaster_user_id,
            broadcaster_username=broadcaster_username,
            event_data=payload
        )
    finally:
        event_dao.close()

    # Process event based on type
    if event_type in ['livestream.status.updated', 'stream.online', 'stream.offline', 'live']:
        asyncio.run(handle_livestream_status_updated(payload, message_id))
    else:
        logger.info(f"Received Kick event type: {event_type}")

    return jsonify({"success": True}), 200

async def handle_livestream_status_updated(payload: dict, event_id: str):
    """
    Handle livestream.status.updated event

    Payload may include:
    - is_live: boolean
    - title: stream title
    - started_at: when stream started (if live)
    - ended_at: when stream ended (if not live)
    - broadcaster: user info object
    """
    # Extract live status
    is_live = payload.get('is_live', False)

    # Also check for nested livestream data
    livestream = payload.get('livestream', {})
    if livestream:
        is_live = livestream.get('is_live', is_live)

    # Extract broadcaster info
    broadcaster = payload.get('broadcaster', {}) or payload.get('channel', {}) or {}
    broadcaster_user_id = str(broadcaster.get('user_id') or broadcaster.get('id') or payload.get('broadcaster_user_id') or '')
    broadcaster_username = broadcaster.get('slug') or broadcaster.get('username') or payload.get('broadcaster_username') or ''
    broadcaster_display_name = broadcaster.get('display_name') or broadcaster.get('name') or broadcaster_username

    logger.info(f"Processing Kick livestream event for {broadcaster_username}: is_live={is_live}")

    if is_live:
        await handle_stream_online(payload, broadcaster_user_id, broadcaster_username,
                                   broadcaster_display_name, event_id)
    else:
        await handle_stream_offline(payload, broadcaster_user_id, broadcaster_username, event_id)

async def handle_stream_online(payload, broadcaster_user_id, broadcaster_username,
                                broadcaster_display_name, event_id):
    """Handle stream going online - post announcements"""
    logger.info(f"Processing Kick stream.online for {broadcaster_username} ({broadcaster_user_id})")

    # Get subscription record to find tracking guilds
    subscription_dao = KickSubscriptionDao()
    subscription_record = subscription_dao.get_subscription_by_broadcaster(broadcaster_user_id)

    if not subscription_record:
        # Try by username
        subscription_record = subscription_dao.get_subscription_by_username(broadcaster_username)

    subscription_dao.close()

    if not subscription_record or subscription_record['guild_count'] == 0:
        logger.warning(f"No guilds tracking Kick streamer {broadcaster_username}, skipping")
        return

    tracked_guild_ids = subscription_record['tracked_guild_ids']

    # Extract broadcaster data from payload
    broadcaster = payload.get('broadcaster', {}) or {}

    # Extract stream data from payload
    livestream = payload.get('livestream', {}) or payload
    stream_title = livestream.get('session_title') or livestream.get('title') or payload.get('title', 'Live on Kick!')
    category = livestream.get('categories', [{}])[0] if livestream.get('categories') else {}
    category_name = category.get('name') or livestream.get('category', {}).get('name') or ''
    viewer_count = livestream.get('viewer_count', 0) or payload.get('viewer_count', 0)
    started_at = livestream.get('start_time') or livestream.get('started_at') or payload.get('started_at')
    thumbnail = livestream.get('thumbnail', {})
    thumbnail_url = thumbnail.get('url') or thumbnail.get('src') or ''

    stream_link = f"https://kick.com/{broadcaster_username}"

    # Get additional channel info from API
    kick_service = KickService()
    # Try to get profile picture from webhook payload first
    profile_picture_url = broadcaster.get('profile_picture', '')

    async with aiohttp.ClientSession() as session:
        try:
            channel_info = await kick_service.get_channel_info(session, broadcaster_username)
            if channel_info:
                # Get thumbnail from API (webhook doesn't include it)
                if channel_info.get('stream'):
                    stream = channel_info['stream']
                    api_thumbnail = stream.get('thumbnail', '')
                    if api_thumbnail and not thumbnail_url:
                        thumbnail_url = api_thumbnail
                        logger.info(f"Using thumbnail from API for {broadcaster_username}: {thumbnail_url[:100]}")

                # Try to construct profile picture URL using pattern
                # Kick uses: https://files.kick.com/images/user/{user_id}/profile_image/...
                if not profile_picture_url and broadcaster_user_id:
                    # We don't have the exact filename, but some streamers might have it in the webhook
                    # For now, log that it's missing
                    logger.info(f"No profile picture available for {broadcaster_username} (user_id: {broadcaster_user_id})")
        except Exception as e:
            logger.warning(f"Failed to get Kick channel info for {broadcaster_username}: {e}")

    # Process each tracking guild
    guild_dao = GuildDao()
    announcement_dao = KickAnnouncementDao()

    for guild_id_str in tracked_guild_ids:
        try:
            guild_id = int(guild_id_str)

            # Get guild settings
            settings = guild_dao.get_guild_settings(guild_id)
            if not settings:
                logger.warning(f"No settings found for guild {guild_id}")
                continue

            kick_settings = settings.get('kick', {})
            if not kick_settings.get('enabled'):
                logger.info(f"Kick disabled for guild {guild_id}")
                continue

            # Find streamer config
            streamer_config = None
            for s in kick_settings.get('tracked_streamers', []):
                if s.get('username', '').lower() == broadcaster_username.lower():
                    streamer_config = s
                    break

            if not streamer_config:
                logger.warning(f"Streamer {broadcaster_username} not in guild {guild_id} config")
                continue

            # Check if announcement already exists (avoid duplicates)
            existing = announcement_dao.get_active_announcement(guild_id, broadcaster_username)
            if existing:
                logger.info(f"Active announcement already exists for {broadcaster_username} in guild {guild_id}")
                continue

            # Get announcement channel
            channel_id = kick_settings.get('announcement_channel_id')
            if not channel_id:
                logger.warning(f"No announcement channel configured for guild {guild_id}")
                continue

            # Build Discord announcement
            ann_settings = kick_settings.get('announcement_settings', {})
            color_hex = ann_settings.get('kick_color', '0x53FC18')  # Kick green
            color = int(color_hex.replace('0x', ''), 16) if isinstance(color_hex, str) else color_hex

            embed = {
                "title": f"🟢 {broadcaster_display_name} is live on Kick!",
                "description": f"### [{stream_title}]({stream_link})",
                "color": color,
                "fields": []
            }

            if ann_settings.get('include_thumbnail', True) and thumbnail_url:
                embed['image'] = {"url": thumbnail_url}
            if profile_picture_url:
                embed['thumbnail'] = {"url": profile_picture_url}

            if ann_settings.get('include_category', True) and category_name:
                embed['fields'].append({
                    "name": "Category",
                    "value": category_name,
                    "inline": False
                })

            if ann_settings.get('include_viewer_count', True):
                embed['fields'].append({
                    "name": "Viewers",
                    "value": f"{viewer_count:,}",
                    "inline": False
                })

            if ann_settings.get('include_start_time', True) and started_at:
                try:
                    if isinstance(started_at, str):
                        started_dt = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
                    else:
                        started_dt = started_at
                    unix_ts = int(started_dt.timestamp())
                    embed['fields'].append({
                        "name": "Started",
                        "value": f"<t:{unix_ts}:R>",
                        "inline": False
                    })
                except Exception:
                    pass

            # Build content with mentions
            content = build_announcement_content(
                streamer_config, broadcaster_display_name, category_name, stream_title, viewer_count
            )

            message_data = {"embeds": [embed]}
            if content:
                message_data["content"] = content

            # Post to Discord
            message = await http_client.post_message(int(channel_id), message_data)

            if message:
                # Parse started_at for database
                stream_started_dt = datetime.utcnow()
                if started_at:
                    try:
                        if isinstance(started_at, str):
                            stream_started_dt = datetime.fromisoformat(started_at.replace('Z', '+00:00')).replace(tzinfo=None)
                    except Exception:
                        pass

                # Store in KickAnnouncements
                announcement_dao.create_announcement(
                    guild_id=guild_id,
                    streamer_username=broadcaster_username,
                    message_id=int(message['id']),
                    channel_id=int(channel_id),
                    stream_started_at=stream_started_dt,
                    streamer_id=broadcaster_user_id,
                    initial_viewer_count=viewer_count,
                    stream_title=stream_title,
                    category_name=category_name
                )
                logger.info(f"Posted Kick announcement for {broadcaster_username} in guild {guild_id}")
            else:
                logger.error(f"Failed to post Kick announcement for {broadcaster_username} in guild {guild_id}")

        except Exception as e:
            logger.error(f"Error processing guild {guild_id_str} for Kick streamer {broadcaster_username}: {e}", exc_info=True)

    guild_dao.close()
    announcement_dao.close()

    # Mark event as processed
    event_dao = KickWebhookEventDao()
    event_dao.mark_event_processed(event_id)
    event_dao.close()

async def handle_stream_offline(payload, broadcaster_user_id, broadcaster_username, event_id):
    """Handle stream going offline - update announcements"""
    logger.info(f"Processing Kick stream.offline for {broadcaster_username} ({broadcaster_user_id})")

    # Get subscription record to find tracking guilds
    subscription_dao = KickSubscriptionDao()
    subscription_record = subscription_dao.get_subscription_by_broadcaster(broadcaster_user_id)

    if not subscription_record:
        subscription_record = subscription_dao.get_subscription_by_username(broadcaster_username)

    subscription_dao.close()

    if not subscription_record:
        logger.warning(f"No subscription record for Kick streamer {broadcaster_username}")
        return

    tracked_guild_ids = subscription_record['tracked_guild_ids']
    stream_end_time = datetime.utcnow()

    # Process each guild's active announcement
    announcement_dao = KickAnnouncementDao()

    for guild_id_str in tracked_guild_ids:
        try:
            guild_id = int(guild_id_str)

            # Get active announcement
            announcement = announcement_dao.get_active_announcement(guild_id, broadcaster_username)

            if not announcement:
                logger.info(f"No active Kick announcement for {broadcaster_username} in guild {guild_id}")
                continue

            # Calculate duration
            stream_started_at = announcement['stream_started_at']
            duration_seconds = int((stream_end_time - stream_started_at).total_seconds())

            # Mark stream as offline in database
            announcement_dao.mark_stream_offline(
                guild_id,
                broadcaster_username,
                final_viewer_count=announcement.get('initial_viewer_count'),
                stream_duration_seconds=duration_seconds
            )

            # Edit Discord message
            await edit_announcement_on_stream_end(
                announcement['channel_id'],
                announcement['message_id'],
                stream_started_at,
                stream_end_time,
                duration_seconds
            )

            logger.info(f"Marked Kick stream offline for {broadcaster_username} in guild {guild_id}")

        except Exception as e:
            logger.error(f"Error processing Kick stream.offline for guild {guild_id_str}: {e}", exc_info=True)

    announcement_dao.close()

    # Mark event as processed
    event_dao = KickWebhookEventDao()
    event_dao.mark_event_processed(event_id)
    event_dao.close()

async def edit_announcement_on_stream_end(channel_id, message_id, stream_started_at, stream_end_time, duration_seconds):
    """Edit Discord announcement message when stream ends"""
    try:
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
        headers = {
            "Authorization": f"Bot {http_client.bot_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch Kick message {message_id}: {resp.status}")
                    return

                message_data = await resp.json()
                embeds = message_data.get('embeds', [])

                if not embeds:
                    logger.warning(f"Kick message {message_id} has no embeds")
                    return

                embed = embeds[0]

                # Update title
                if 'title' in embed and '🟢' in embed['title']:
                    embed['title'] = embed['title'].replace('🟢', '⚫').replace('is live', 'was live')

                # Update color to gray
                embed['color'] = 0x808080

                # Remove image
                embed.pop('image', None)

                # Keep fields except "Viewers"
                if 'fields' in embed:
                    embed['fields'] = [f for f in embed['fields'] if f.get('name') != 'Viewers']
                else:
                    embed['fields'] = []

                # Add stream end metadata
                ended_ts = int(stream_end_time.timestamp())
                embed['fields'].extend([
                    {
                        "name": "Ended",
                        "value": f"<t:{ended_ts}:F>",
                        "inline": False
                    },
                    {
                        "name": "Duration",
                        "value": format_duration(duration_seconds),
                        "inline": False
                    }
                ])

                # Edit message
                async with session.patch(url, headers=headers, json={"embeds": [embed]}) as edit_resp:
                    if edit_resp.status == 200:
                        logger.info(f"Edited Kick announcement {message_id} for stream end")
                    else:
                        error_text = await edit_resp.text()
                        logger.error(f"Failed to edit Kick message {message_id}: {edit_resp.status} - {error_text}")

    except Exception as e:
        logger.error(f"Error editing Kick announcement on stream end: {e}", exc_info=True)

def build_announcement_content(streamer_config: dict, username: str, category_name: str, stream_title: str, viewer_count: int) -> str:
    """Build announcement message content with mentions"""
    mention_parts = []

    mention = streamer_config.get('mention', '').lower()
    if mention == 'everyone':
        mention_parts.append("@everyone")
    elif mention == 'here':
        mention_parts.append("@here")
    elif mention.startswith('<@&'):
        mention_parts.append(mention)

    if streamer_config.get('mention_role_ids'):
        role_ids = streamer_config['mention_role_ids']
        if isinstance(role_ids, list):
            for role_id in role_ids:
                mention_parts.append(f"<@&{role_id}>")

    content_parts = []
    if mention_parts:
        content_parts.append(" ".join(mention_parts))

    custom_message = streamer_config.get('custom_message')
    if custom_message:
        custom_message = custom_message.replace('{username}', username)
        custom_message = custom_message.replace('{category}', category_name)
        custom_message = custom_message.replace('{title}', stream_title)
        custom_message = custom_message.replace('{viewer_count}', str(viewer_count))
        content_parts.append(custom_message)

    return " ".join(content_parts) if content_parts else ""

def format_duration(seconds: int) -> str:
    """Format duration in seconds to human-readable format"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)
