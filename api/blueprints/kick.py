"""Kick integration endpoints"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
import sys
import os
import logging

kick_bp = Blueprint('kick', __name__, url_prefix='/api/kick')
logger = logging.getLogger(__name__)


@kick_bp.route('/validate-username', methods=['POST'])
@require_auth
def validate_kick_username():
    """Validate that a Kick username exists using Kick's official public API."""
    try:
        data = request.get_json()
        if not data or 'username' not in data:
            return jsonify({
                "success": False,
                "message": "Username is required"
            }), 400

        username = data['username'].strip()
        if not username:
            return jsonify({
                "success": False,
                "message": "Username cannot be empty"
            }), 400

        # Import KickService
        from pathlib import Path
        current_dir = Path(__file__).parent.parent.parent
        bot_project_path = current_dir.parent / "acosmibot"
        if str(bot_project_path) not in sys.path:
            sys.path.insert(0, str(bot_project_path))

        from Services.kick_service import KickService
        import asyncio
        import aiohttp

        async def check_username():
            kick = KickService()
            # Simple session - no special headers or SSL needed for official API
            async with aiohttp.ClientSession() as session:
                return await kick.validate_username(session, username)

        # Run async validation
        is_valid = asyncio.run(check_username())

        if is_valid:
            return jsonify({
                "success": True,
                "valid": True,
                "message": f"Username '{username}' is valid"
            })
        else:
            return jsonify({
                "success": True,
                "valid": False,
                "message": f"Username '{username}' not found on Kick"
            })

    except Exception as e:
        logger.error(f"Error validating Kick username: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Failed to validate username",
            "error": str(e)
        }), 500


@kick_bp.route('/channel/<username>', methods=['GET'])
@require_auth
def get_kick_channel(username):
    """Get Kick channel information"""
    try:
        from pathlib import Path
        current_dir = Path(__file__).parent.parent.parent
        bot_project_path = current_dir.parent / "acosmibot"
        if str(bot_project_path) not in sys.path:
            sys.path.insert(0, str(bot_project_path))

        from Services.kick_service import KickService
        import asyncio
        import aiohttp

        async def get_channel():
            kick = KickService()
            # Simple session - no special headers or SSL needed for official API
            async with aiohttp.ClientSession() as session:
                return await kick.get_channel_info(session, username)

        channel_info = asyncio.run(get_channel())

        if channel_info:
            return jsonify({
                "success": True,
                "channel": channel_info
            })
        else:
            return jsonify({
                "success": False,
                "message": f"Channel '{username}' not found"
            }), 404

    except Exception as e:
        logger.error(f"Error getting Kick channel: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get channel info",
            "error": str(e)
        }), 500
