"""Twitch integration endpoints"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
import logging
import asyncio
import aiohttp
from acosmibot_core.services import TwitchService

twitch_bp = Blueprint('twitch', __name__, url_prefix='/api/twitch')
logger = logging.getLogger(__name__)

@twitch_bp.route('/validate-username', methods=['POST'])
@require_auth
def validate_twitch_username():
    """Validate that a Twitch username exists"""
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

        async def check_username():
            tw = TwitchService()
            async with aiohttp.ClientSession() as session:
                return await tw.validate_username(session, username)

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
                "message": f"Username '{username}' not found on Twitch"
            })

    except Exception as e:
        logger.error(f"Error validating Twitch username: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Failed to validate username",
            "error": str(e)
        }), 500
