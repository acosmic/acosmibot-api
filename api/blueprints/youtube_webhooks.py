import os
import xml.etree.ElementTree as ET
from datetime import datetime
from flask import Blueprint, request, Response, current_app
from sqlalchemy.exc import IntegrityError
import asyncio
import aiohttp

from api import get_db_session, run_async_threadsafe
from acosmibot_core.dao import YoutubeDao
from api.services.youtube_websub_service import YouTubeWebSubService
from acosmibot_core.services import YouTubeService
import logging
logger = logging.getLogger(__name__)

youtube_webhooks_bp = Blueprint('youtube_webhooks', __name__, url_prefix='/api/webhooks/youtube')

@youtube_webhooks_bp.route('/', methods=['GET'])
def handle_challenge():
    """
    Handles the PubSubHubbub subscription challenge from YouTube.
    """
    mode = request.args.get('hub.mode')
    challenge = request.args.get('hub.challenge')
    topic = request.args.get('hub.topic')
    
    if not challenge:
        logger.error("Missing hub.challenge in WebSub verification request.")
        return Response("Missing hub.challenge", status=400)

    if mode in ['subscribe', 'unsubscribe']:
        logger.info(f"WebSub {mode} challenge received for topic: {topic}. Responding with challenge.")
        return Response(challenge, status=200, mimetype='text/plain')
    else:
        logger.warning(f"Unknown hub.mode received: {mode}")
        return Response("Unknown mode", status=400)

@youtube_webhooks_bp.route('/', methods=['POST'])
def handle_webhook_notification():
    """
    Handles incoming PubSubHubbub notifications from YouTube.
    """
    async def do_processing_async():
        signature = request.headers.get('X-Hub-Signature')
        if signature:
            websub_service = YouTubeWebSubService(current_app.config.get("YOUTUBE_WEBHOOK_CALLBACK_URL"))
            if not websub_service.verify_signature(signature, request.data):
                logger.warning("YouTube webhook signature verification failed.")
                return Response("Signature verification failed", status=403)
            logger.info("YouTube webhook signature verified successfully.")
        elif os.getenv("YOUTUBE_WEBHOOK_SECRET"):
            logger.warning("YOUTUBE_WEBHOOK_SECRET is set, but X-Hub-Signature header is missing.")
            return Response("Missing signature", status=403)
        
        if not request.data:
            logger.warning("Received empty POST request to YouTube webhook endpoint.")
            return Response("Empty request", status=204)

        try:
            root = ET.fromstring(request.data)
            atom_ns = '{http://www.w3.org/2005/Atom}'
            yt_ns = '{http://www.youtube.com/xml/schemas/2015}'

            entries = root.findall(f'.//{atom_ns}entry')
            if not entries:
                logger.warning(f"No entries found in YouTube webhook payload: {request.data.decode()}")
                return Response("No entries", status=200)

            # Initialize YouTube service to check if video is live
            youtube_service = YouTubeService()

            async with aiohttp.ClientSession() as http_session:
                async with get_db_session() as session:
                    youtube_dao = YoutubeDao(session)
                    for entry in entries:
                        video_id_el = entry.find(f'.//{yt_ns}videoId')
                        channel_id_el = entry.find(f'.//{yt_ns}channelId')

                        if video_id_el is None or channel_id_el is None:
                            continue

                        video_id = video_id_el.text
                        channel_id = channel_id_el.text

                        # Check if the video is actually live using YouTube Data API
                        logger.info(f"Received webhook for video {video_id} on channel {channel_id}. Checking if live...")
                        video_details = await youtube_service.get_video_details(http_session, video_id)

                        if not video_details:
                            logger.warning(f"Could not fetch details for video {video_id}. Skipping.")
                            continue

                        # Only process if the video is actually live
                        if not video_details.get('is_live'):
                            logger.info(f"Video {video_id} is not live (is_upcoming={video_details.get('is_upcoming')}). Skipping.")
                            continue

                        logger.info(f"Video {video_id} is LIVE! Title: {video_details.get('title')}, Viewers: {video_details.get('viewer_count')}")

                        # NOTE: event_id must NOT include timestamp to prevent duplicates
                        # The UNIQUE constraint on event_id will catch duplicate webhook events
                        event_id = f"{channel_id}-{video_id}-live_start"

                        # Store comprehensive payload with live stream details
                        payload_data = {
                            "title": video_details.get('title', 'N/A'),
                            "link": video_details.get('url', f"https://www.youtube.com/watch?v={video_id}"),
                            "thumbnail_url": video_details.get('thumbnail_url'),
                            "started_at": video_details.get('started_at'),
                            "viewer_count": video_details.get('viewer_count', 0),
                            "channel_title": video_details.get('channel_title'),
                            "source": "websub"  # Mark source as WebSub webhook
                        }

                        try:
                            await youtube_dao.add_youtube_webhook_event(event_id, channel_id, video_id, 'live_start', payload_data)
                            logger.info(f"Stored live stream event for channel {channel_id}, video {video_id}.")
                        except IntegrityError:
                            logger.warning(f"Duplicate YouTube webhook event received for {event_id}. Skipping.")
                            await session.rollback()
                        except Exception as e:
                            logger.error(f"Failed to store YouTube webhook event for channel {channel_id}, video {video_id}: {e}", exc_info=True)
                            await session.rollback()

            return Response("Acknowledged", status=200)

        except ET.ParseError as e:
            logger.error(f"Failed to parse XML payload: {e}", exc_info=True)
            return Response("Invalid XML", status=400)
        except Exception as e:
            logger.error(f"Unhandled error in webhook notification handler: {e}", exc_info=True)
            return Response("Internal Server Error", status=500)

    return run_async_threadsafe(do_processing_async())