"""
YouTube API Blueprint

Provides endpoints for YouTube channel validation and streaming features.
"""
import sys
import os
import asyncio
import aiohttp
from flask import Blueprint, request, jsonify
from functools import wraps
import logging

# Import bot services
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from acosmibot.Services.youtube_service import YouTubeService

logger = logging.getLogger(__name__)

youtube_bp = Blueprint('youtube', __name__, url_prefix='/api/youtube')


def require_auth(f):
    """Decorator to require authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # TODO: Implement proper JWT authentication if needed
        # For now, allow all requests (can be secured later)
        return f(*args, **kwargs)
    return decorated_function


@youtube_bp.route('/validate-channel', methods=['POST'])
@require_auth
def validate_channel():
    """
    Validate a YouTube channel by username, @handle, or channel ID.

    Request body:
    {
        "identifier": "@mkbhd" | "mkbhd" | "UCBJycsmduvYEL83R_U4JriQ"
    }

    Response:
    {
        "success": true,
        "valid": true,
        "channel_id": "UCBJycsmduvYEL83R_U4JriQ",
        "channel_info": {
            "title": "Marques Brownlee",
            "thumbnail_url": "https://...",
            "subscriber_count": 18000000,
            "custom_url": "@mkbhd",
            "description": "..."
        }
    }
    """
    try:
        data = request.get_json()

        if not data or 'identifier' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing required field: identifier'
            }), 400

        identifier = data['identifier'].strip()

        if not identifier:
            return jsonify({
                'success': False,
                'error': 'Identifier cannot be empty'
            }), 400

        # Run async validation
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(_validate_channel_async(identifier))
            return jsonify(result), 200 if result['success'] else 400
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error validating YouTube channel: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500


async def _validate_channel_async(identifier):
    """Async helper to validate YouTube channel"""
    yt = YouTubeService()

    async with aiohttp.ClientSession() as session:
        # Resolve channel ID
        channel_id = await yt.resolve_channel_id(session, identifier)

        if not channel_id:
            return {
                'success': True,
                'valid': False,
                'message': f'Could not find YouTube channel: {identifier}'
            }

        # Get channel info
        channel_info = await yt.get_channel_info(session, channel_id)

        if not channel_info:
            return {
                'success': True,
                'valid': True,
                'channel_id': channel_id,
                'message': 'Channel found but could not fetch details'
            }

        return {
            'success': True,
            'valid': True,
            'channel_id': channel_id,
            'channel_info': {
                'title': channel_info['title'],
                'description': channel_info['description'],
                'custom_url': channel_info['custom_url'],
                'thumbnail_url': channel_info['thumbnail_url'],
                'subscriber_count': channel_info['subscriber_count'],
                'view_count': channel_info['view_count'],
                'video_count': channel_info['video_count']
            }
        }


@youtube_bp.route('/check-live', methods=['POST'])
@require_auth
def check_live():
    """
    Check if a YouTube channel is currently live.

    Request body:
    {
        "channel_id": "UCBJycsmduvYEL83R_U4JriQ"
    }

    Response:
    {
        "success": true,
        "is_live": true,
        "stream_info": {
            "video_id": "...",
            "title": "...",
            "viewer_count": 1234,
            "started_at": "2025-12-05T10:00:00Z",
            "url": "https://www.youtube.com/watch?v=..."
        }
    }
    """
    try:
        data = request.get_json()

        if not data or 'channel_id' not in data:
            return jsonify({
                'success': False,
                'error': 'Missing required field: channel_id'
            }), 400

        channel_id = data['channel_id'].strip()

        if not channel_id:
            return jsonify({
                'success': False,
                'error': 'channel_id cannot be empty'
            }), 400

        # Run async check
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            result = loop.run_until_complete(_check_live_async(channel_id))
            return jsonify(result), 200
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"Error checking YouTube live status: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Internal server error'
        }), 500


async def _check_live_async(channel_id):
    """Async helper to check if channel is live"""
    yt = YouTubeService()

    async with aiohttp.ClientSession() as session:
        stream_info = await yt.get_live_stream_info(session, channel_id)

        if not stream_info:
            return {
                'success': True,
                'is_live': False
            }

        return {
            'success': True,
            'is_live': True,
            'stream_info': {
                'video_id': stream_info['video_id'],
                'title': stream_info['title'],
                'viewer_count': stream_info['viewer_count'],
                'started_at': stream_info['started_at'],
                'thumbnail_url': stream_info['thumbnail_url'],
                'url': stream_info['url']
            }
        }
