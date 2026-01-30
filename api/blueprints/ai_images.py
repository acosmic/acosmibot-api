#! /usr/bin/python3
"""
AI Images API Blueprint

Provides REST API endpoints for AI image generation and analysis usage statistics.
"""

from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.discord_integration import check_admin_sync
from acosmibot_core.dao import AIImageDao
from acosmibot_core.utils import PremiumChecker, AppLogger

logger = AppLogger(__name__).get_logger()
ai_images_bp = Blueprint('ai_images', __name__, url_prefix='/api')
@ai_images_bp.route('/guilds/<guild_id>/ai-images/stats', methods=['GET'])
@require_auth
def get_image_stats(guild_id):
    """
    Get AI image usage statistics for a guild.
    Query params:
        - None
    Returns:
        JSON with image generation and analysis statistics
    """
    try:
        # Check if user is admin in this guild
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to view image statistics"
            }), 403
        # Get statistics
        with AIImageDao() as image_dao:
            stats = image_dao.get_usage_stats(guild_id)
        # Get tier limits
        tier = PremiumChecker.get_guild_tier(int(guild_id))
        monthly_limit = PremiumChecker.get_image_monthly_limit(int(guild_id))
        analysis_monthly_limit = PremiumChecker.get_limit(int(guild_id), 'image_analysis_monthly_limit')
        can_generate = PremiumChecker.has_feature(int(guild_id), 'image_generation')
        can_analyze = PremiumChecker.has_feature(int(guild_id), 'image_analysis')
        return jsonify({
            "success": True,
            "stats": stats,
            "limits": {
                "tier": tier,
                "monthly_image_limit": monthly_limit,
                "image_analysis_monthly_limit": analysis_monthly_limit,
                "can_generate": can_generate,
                "can_analyze": can_analyze
            }
        }), 200
    except Exception as e:
        logger.error(f"Error getting image stats for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to retrieve image statistics"
        }), 500
@ai_images_bp.route('/guilds/<guild_id>/ai-images', methods=['GET'])
@require_auth
def get_guild_images(guild_id):
    """
    Get AI images for a guild.
    Query params:
        - type: Filter by type ('generation' or 'analysis'), optional
        - limit: Number of images to return (default: 50, max: 100)
    Returns:
        JSON with list of AI images
    """
    try:
        # Check if user is admin in this guild
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to view images"
            }), 403
        # Get query params
        image_type = request.args.get('type', None)
        limit = min(int(request.args.get('limit', 50)), 100)
        # Validate type if provided
        if image_type and image_type not in ['generation', 'analysis']:
            return jsonify({
                "success": False,
                "message": "Invalid type. Must be 'generation' or 'analysis'"
            }), 400
        # Get images
        with AIImageDao() as image_dao:
            images = image_dao.get_guild_images(guild_id, type=image_type, limit=limit)
        return jsonify({
            "success": True,
            "count": len(images),
            "images": images
        }), 200
    except ValueError:
        return jsonify({
            "success": False,
            "message": "Invalid limit parameter"
        }), 400
    except Exception as e:
        logger.error(f"Error getting images for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to retrieve images"
        }), 500
@ai_images_bp.route('/users/<user_id>/ai-images', methods=['GET'])
@require_auth
def get_user_images(user_id):
    """
    Get AI images for a user.
    Query params:
        - guild_id: Filter by guild ID (optional)
        - type: Filter by type ('generation' or 'analysis'), optional
        - limit: Number of images to return (default: 50, max: 100)
    Returns:
        JSON with list of AI images
    """
    try:
        # Users can only view their own images unless admin
        if user_id != str(request.user_id):
            return jsonify({
                "success": False,
                "message": "You can only view your own images"
            }), 403
        # Get query params
        guild_id = request.args.get('guild_id', None)
        image_type = request.args.get('type', None)
        limit = min(int(request.args.get('limit', 50)), 100)
        # Validate type if provided
        if image_type and image_type not in ['generation', 'analysis']:
            return jsonify({
                "success": False,
                "message": "Invalid type. Must be 'generation' or 'analysis'"
            }), 400
        # Get images
        with AIImageDao() as image_dao:
            images = image_dao.get_user_images(
                user_id,
                guild_id=guild_id,
                type=image_type,
                limit=limit
            )
        return jsonify({
            "success": True,
            "count": len(images),
            "images": images
        }), 200
    except ValueError:
        return jsonify({
            "success": False,
            "message": "Invalid limit parameter"
        }), 400
    except Exception as e:
        logger.error(f"Error getting images for user {user_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to retrieve images"
        }), 500