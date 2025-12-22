"""
Twitch EventSub webhook endpoints
Receives and processes stream.online and stream.offline events
"""
import sys
from pathlib import Path
from flask import Blueprint, request, jsonify
import logging
import json
import asyncio
import aiohttp
from datetime import datetime

# Ensure bot path is in sys.path
current_dir = Path(__file__).parent.parent.parent
bot_project_path = current_dir.parent / "acosmibot"
if str(bot_project_path) not in sys.path:
    sys.path.insert(0, str(bot_project_path))

from api.services.twitch_eventsub_service import TwitchEventSubService
from api.services.discord_integration import http_client
from Dao.TwitchWebhookEventDao import TwitchWebhookEventDao
from Dao.TwitchEventSubDao import TwitchEventSubDao
from Dao.StreamingAnnouncementDao import StreamingAnnouncementDao
from Dao.GuildDao import GuildDao
from Services.twitch_service import TwitchService

logger = logging.getLogger(__name__)
twitch_webhooks_bp = Blueprint('twitch_webhooks', __name__, url_prefix='/api/webhooks')

eventsub_service = TwitchEventSubService()


@twitch_webhooks_bp.route('/twitch', methods=['POST'])
def twitch_eventsub_webhook():
    """
    Twitch EventSub webhook endpoint

    Handles:
    - Challenge-response verification
    - stream.online events
    - stream.offline events
    """
    # Get headers
    message_id = request.headers.get('Twitch-Eventsub-Message-Id')
    message_timestamp = request.headers.get('Twitch-Eventsub-Message-Timestamp')
    message_signature = request.headers.get('Twitch-Eventsub-Message-Signature')
    message_type = request.headers.get('Twitch-Eventsub-Message-Type')

    if not all([message_id, message_timestamp, message_signature, message_type]):
        logger.error("Missing required Twitch EventSub headers")
        return jsonify({"error": "Missing headers"}), 400

    # Verify signature
    body = request.get_data()
    if not eventsub_service.verify_webhook_signature(
        message_signature,
        message_id,
        message_timestamp,
        body
    ):
        logger.error(f"Invalid webhook signature for message {message_id}")
        return jsonify({"error": "Invalid signature"}), 403

    # Parse payload
    try:
        payload = request.get_json()
    except Exception as e:
        logger.error(f"Failed to parse webhook payload: {e}")
        return jsonify({"error": "Invalid JSON"}), 400

    # Handle challenge-response (subscription verification)
    if message_type == 'webhook_callback_verification':
        challenge = payload.get('challenge')
        if not challenge:
            return jsonify({"error": "Missing challenge"}), 400

        logger.info(f"Responding to EventSub challenge for subscription {payload.get('subscription', {}).get('id')}")
        return challenge, 200, {'Content-Type': 'text/plain'}

    # Handle notification
    elif message_type == 'notification':
        try:
            # Extract event data
            subscription = payload.get('subscription', {})
            event = payload.get('event', {})

            subscription_type = subscription.get('type')
            subscription_id = subscription.get('id')

            # Check for duplicate (idempotency)
            event_dao = TwitchWebhookEventDao()
            if event_dao.event_exists(message_id):
                logger.info(f"Duplicate event {message_id}, skipping")
                event_dao.close()
                return jsonify({"success": True, "message": "Duplicate event"}), 200

            # Record event
            broadcaster_user_id = event.get('broadcaster_user_id')
            broadcaster_username = event.get('broadcaster_user_login') or event.get('broadcaster_user_name')

            event_dao.create_event(
                event_id=message_id,
                event_type=subscription_type,
                subscription_id=subscription_id,
                broadcaster_user_id=broadcaster_user_id,
                broadcaster_username=broadcaster_username,
                event_data=payload
            )
            event_dao.close()

            # Process event based on type
            if subscription_type == 'stream.online':
                asyncio.run(handle_stream_online(event, message_id))
            elif subscription_type == 'stream.offline':
                asyncio.run(handle_stream_offline(event, message_id))
            else:
                logger.warning(f"Unhandled subscription type: {subscription_type}")

            return jsonify({"success": True}), 200

        except Exception as e:
            logger.error(f"Error processing webhook notification: {e}", exc_info=True)
            return jsonify({"error": "Processing failed"}), 500

    # Handle revocation
    elif message_type == 'revocation':
        subscription = payload.get('subscription', {})
        subscription_id = subscription.get('id')
        reason = subscription.get('status')

        logger.warning(f"EventSub subscription {subscription_id} revoked: {reason}")
        # Mark subscription as failed in database
        # This should trigger re-subscription logic

        return jsonify({"success": True}), 200

    else:
        logger.warning(f"Unknown message type: {message_type}")
        return jsonify({"error": "Unknown message type"}), 400


async def handle_stream_online(event: dict, event_id: str):
    """
    Handle stream.online event

    Flow:
    1. Get broadcaster info from event
    2. Query TwitchEventSubDao to find which guilds track this streamer
    3. For each guild:
       - Get guild settings (announcement channel, mention settings, etc.)
       - Fetch additional stream data (title, game, viewer count, thumbnail)
       - Build Discord embed
       - Post announcement via discord_integration.py
       - Create StreamingAnnouncement record
    """
    broadcaster_user_id = event.get('broadcaster_user_id')
    broadcaster_username = event.get('broadcaster_user_login')
    broadcaster_display_name = event.get('broadcaster_user_name')
    stream_started_at = event.get('started_at')  # ISO 8601 format

    logger.info(f"Processing stream.online for {broadcaster_username} ({broadcaster_user_id})")

    # Get subscription record to find tracking guilds
    eventsub_dao = TwitchEventSubDao()
    subscription_record = eventsub_dao.get_subscription_by_broadcaster(broadcaster_user_id)
    eventsub_dao.close()

    if not subscription_record or subscription_record['guild_count'] == 0:
        logger.warning(f"No guilds tracking {broadcaster_username}, skipping")
        return

    tracked_guild_ids = subscription_record['tracked_guild_ids']

    # Fetch full stream data from Twitch API
    twitch_service = TwitchService()
    async with aiohttp.ClientSession() as session:
        stream_data = await twitch_service.get_live_streams_batch(session, [broadcaster_username])

        if broadcaster_username not in stream_data:
            logger.error(f"Failed to fetch stream data for {broadcaster_username}")
            # Mark event as processed with error
            event_dao = TwitchWebhookEventDao()
            event_dao.mark_event_processed(event_id, "Failed to fetch stream data")
            event_dao.close()
            return

        stream_info = stream_data[broadcaster_username]['data'][0]

        # Get profile picture
        user_info = await twitch_service.get_user_info(session, broadcaster_username)
        profile_picture_url = user_info.get('profile_image_url', '') if user_info else ''

        # Extract stream metadata
        stream_title = stream_info['title']
        game_name = stream_info['game_name']
        viewer_count = stream_info['viewer_count']
        stream_id = stream_info['id']
        thumbnail_url = stream_info['thumbnail_url'].replace('{width}', '1920').replace('{height}', '1080')
        stream_link = f"https://www.twitch.tv/{broadcaster_display_name}"

        # Process each tracking guild
        guild_dao = GuildDao()
        announcement_dao = StreamingAnnouncementDao()

        for guild_id_str in tracked_guild_ids:
            try:
                guild_id = int(guild_id_str)

                # Get guild settings
                settings = guild_dao.get_guild_settings(guild_id)
                if not settings:
                    logger.warning(f"No settings found for guild {guild_id}")
                    continue

                streaming_settings = settings.get('streaming', {})
                if not streaming_settings.get('enabled'):
                    logger.info(f"Streaming disabled for guild {guild_id}")
                    continue

                # Find streamer config
                streamer_config = None
                for s in streaming_settings.get('tracked_streamers', []):
                    if s.get('platform') == 'twitch' and s.get('username').lower() == broadcaster_username.lower():
                        streamer_config = s
                        break

                if not streamer_config:
                    logger.warning(f"Streamer {broadcaster_username} not in guild {guild_id} config")
                    continue

                # Get announcement channel
                channel_id = streaming_settings.get('announcement_channel_id')
                if not channel_id:
                    logger.warning(f"No announcement channel configured for guild {guild_id}")
                    continue

                # Build Discord announcement
                ann_settings = streaming_settings.get('announcement_settings', {})
                color_hex = ann_settings.get('twitch_color', '0x6441A4')
                color = int(color_hex.replace('0x', ''), 16) if isinstance(color_hex, str) else color_hex

                embed = {
                    "title": f"🔴 {broadcaster_display_name} is live on Twitch!",
                    "description": f"### [{stream_title}]({stream_link})",
                    "color": color,
                    "fields": []
                }

                if ann_settings.get('include_thumbnail', True):
                    embed['image'] = {"url": thumbnail_url}
                    embed['thumbnail'] = {"url": profile_picture_url}

                if ann_settings.get('include_game', True):
                    embed['fields'].append({
                        "name": "Category",
                        "value": game_name,
                        "inline": False
                    })

                if ann_settings.get('include_viewer_count', True):
                    embed['fields'].append({
                        "name": "Viewers",
                        "value": f"{viewer_count:,}",
                        "inline": False
                    })

                if ann_settings.get('include_start_time', True):
                    # Convert to Discord timestamp
                    started_dt = datetime.strptime(stream_started_at, "%Y-%m-%dT%H:%M:%SZ")
                    unix_ts = int(started_dt.timestamp())
                    embed['fields'].append({
                        "name": "Started",
                        "value": f"<t:{unix_ts}:R>",
                        "inline": False
                    })

                # Build content with mentions
                content = build_announcement_content(streamer_config, broadcaster_display_name, game_name, stream_title, viewer_count)

                message_data = {
                    "embeds": [embed]
                }
                if content:
                    message_data["content"] = content

                # Post to Discord
                message = await http_client.post_message(int(channel_id), message_data)

                if message:
                    # Store in StreamingAnnouncements
                    started_dt = datetime.strptime(stream_started_at, "%Y-%m-%dT%H:%M:%SZ")
                    announcement_dao.create_announcement(
                        platform='twitch',
                        guild_id=guild_id,
                        channel_id=int(channel_id),
                        message_id=message['id'],
                        streamer_username=broadcaster_username,
                        streamer_id=broadcaster_user_id,
                        stream_id=stream_id,
                        stream_title=stream_title,
                        game_name=game_name,
                        stream_started_at=started_dt,
                        initial_viewer_count=viewer_count
                    )
                    logger.info(f"Posted announcement for {broadcaster_username} in guild {guild_id}")
                else:
                    logger.error(f"Failed to post announcement for {broadcaster_username} in guild {guild_id}")

            except Exception as e:
                logger.error(f"Error processing guild {guild_id_str} for {broadcaster_username}: {e}", exc_info=True)

        guild_dao.close()
        announcement_dao.close()

    # Mark event as processed
    event_dao = TwitchWebhookEventDao()
    event_dao.mark_event_processed(event_id)
    event_dao.close()


async def handle_stream_offline(event: dict, event_id: str):
    """
    Handle stream.offline event

    Flow:
    1. Get broadcaster info
    2. Query StreamingAnnouncements for active streams (stream_ended_at IS NULL)
    3. For each active announcement:
       - Mark stream as offline
       - Calculate duration
       - Edit Discord message to show stream ended
    4. VOD checking will be handled by unified_streaming_vod_checker.py
    """
    broadcaster_user_id = event.get('broadcaster_user_id')
    broadcaster_username = event.get('broadcaster_user_login')

    logger.info(f"Processing stream.offline for {broadcaster_username} ({broadcaster_user_id})")

    # Get subscription record to find tracking guilds
    eventsub_dao = TwitchEventSubDao()
    subscription_record = eventsub_dao.get_subscription_by_broadcaster(broadcaster_user_id)
    eventsub_dao.close()

    if not subscription_record:
        logger.warning(f"No subscription record for {broadcaster_username}")
        return

    tracked_guild_ids = subscription_record['tracked_guild_ids']
    stream_end_time = datetime.utcnow()

    # Process each guild's active announcement
    announcement_dao = StreamingAnnouncementDao()

    for guild_id_str in tracked_guild_ids:
        try:
            guild_id = int(guild_id_str)

            # Get active announcement
            announcement = announcement_dao.get_active_stream_for_streamer(
                'twitch',
                guild_id,
                broadcaster_username
            )

            if not announcement:
                logger.info(f"No active announcement for {broadcaster_username} in guild {guild_id}")
                continue

            # Calculate duration
            duration_seconds = int((stream_end_time - announcement.stream_started_at).total_seconds())

            # Mark stream as offline
            announcement_dao.mark_stream_offline(
                'twitch',
                guild_id,
                broadcaster_username,
                stream_end_time,
                announcement.final_viewer_count or announcement.initial_viewer_count
            )

            # Edit Discord message
            await edit_announcement_on_stream_end(announcement, stream_end_time, duration_seconds)

            logger.info(f"Marked stream offline for {broadcaster_username} in guild {guild_id}")

        except Exception as e:
            logger.error(f"Error processing stream.offline for guild {guild_id_str}: {e}", exc_info=True)

    announcement_dao.close()

    # Mark event as processed
    event_dao = TwitchWebhookEventDao()
    event_dao.mark_event_processed(event_id)
    event_dao.close()


async def edit_announcement_on_stream_end(announcement, stream_end_time: datetime, duration_seconds: int):
    """Edit Discord announcement message when stream ends"""
    try:
        # Fetch the message via Discord API
        channel_id = announcement.channel_id
        message_id = announcement.message_id

        # Get message content
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
        headers = {
            "Authorization": f"Bot {http_client.bot_token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch message {message_id}: {resp.status}")
                    return

                message_data = await resp.json()
                embeds = message_data.get('embeds', [])

                if not embeds:
                    logger.warning(f"Message {message_id} has no embeds")
                    return

                embed = embeds[0]

                # Update title
                if 'title' in embed and '🔴' in embed['title']:
                    embed['title'] = embed['title'].replace('🔴', '⚫').replace('is live', 'was live')

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

                if announcement.final_viewer_count is not None:
                    embed['fields'].append({
                        "name": "Final Viewers",
                        "value": f"{announcement.final_viewer_count:,}",
                        "inline": False
                    })

                # Edit message
                async with session.patch(url, headers=headers, json={"embeds": [embed]}) as edit_resp:
                    if edit_resp.status == 200:
                        logger.info(f"Edited announcement {message_id} for stream end")
                    else:
                        error_text = await edit_resp.text()
                        logger.error(f"Failed to edit message {message_id}: {edit_resp.status} - {error_text}")

    except Exception as e:
        logger.error(f"Error editing announcement {announcement.id}: {e}", exc_info=True)


def build_announcement_content(streamer_config: dict, username: str, game_name: str, stream_title: str, viewer_count: int) -> str:
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
        custom_message = custom_message.replace('{game}', game_name)
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
