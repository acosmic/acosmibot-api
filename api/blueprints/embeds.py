"""
Embeds API Blueprint

Provides REST API endpoints for managing custom Discord embeds.
Includes image upload functionality and Discord message integration.
"""
import sys
import os
import uuid
import time
import magic
from pathlib import Path
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from api.middleware.auth_decorators import require_auth
from api.services.discord_integration import check_admin_sync, http_client, run_sync
from acosmibot_core.dao import EmbedDao
import sys
from acosmibot_core.utils import PremiumChecker
from acosmibot_core.models import (
    validate_embed_config,
    validate_button_config,
    build_embed_from_config,
    build_button_components
)
import logging

logger = logging.getLogger(__name__)

embeds_bp = Blueprint('embeds', __name__, url_prefix='/api')

# Image upload configuration
UPLOAD_FOLDER = '/var/www/acosmibot-cdn/embed-images'
CDN_BASE_URL = 'https://cdn.acosmibot.com/embed-images'
ALLOWED_MIME_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB
MAX_IMAGE_DIMENSION = 4096

@embeds_bp.route('/guilds/<guild_id>/embeds', methods=['GET'])
@require_auth
def get_embeds(guild_id):
    """
    Get all embeds for a guild

    Query Parameters:
        enabled_only (bool): Filter to only enabled embeds (default: false)

    Returns:
        200: List of embeds
        403: User is not admin
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to view embeds"
            }), 403

        # Get query parameter
        enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'

        # Fetch embeds from database
        with EmbedDao() as dao:
            embeds_data = dao.get_guild_embeds(
                guild_id=str(guild_id),
                enabled_only=enabled_only
            )

        return jsonify({
            "success": True,
            "embeds": embeds_data,
            "count": len(embeds_data)
        }), 200

    except Exception as e:
        logger.error(f"Error fetching embeds for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch embeds"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/<int:embed_id>', methods=['GET'])
@require_auth
def get_embed(guild_id, embed_id):
    """
    Get a single embed by ID

    Returns:
        200: Embed data
        403: User is not admin
        404: Embed not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to view embeds"
            }), 403

        # Fetch embed from database
        with EmbedDao() as dao:
            embed_data = dao.get_embed(embed_id)

        if not embed_data:
            return jsonify({
                "success": False,
                "message": "Embed not found"
            }), 404

        # Verify embed belongs to this guild
        if embed_data.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Embed not found"
            }), 404

        return jsonify({
            "success": True,
            "embed": embed_data.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error fetching embed {embed_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch embed"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds', methods=['POST'])
@require_auth
def create_embed(guild_id):
    """
    Create a new embed (draft)

    Request Body:
        name (str): Internal name for organization
        message_text (str): Text content before embed (optional)
        embed_config (dict): Embed configuration
        channel_id (str): Target channel ID (optional)
        buttons (list): Button configuration (optional)

    Returns:
        201: Embed created successfully
        400: Invalid request data or premium limit reached
        403: User is not admin
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to create embeds"
            }), 403

        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Validate required fields
        if 'name' not in data or not data['name']:
            return jsonify({
                "success": False,
                "message": "Embed name is required"
            }), 400

        if 'embed_config' not in data or not data['embed_config']:
            return jsonify({
                "success": False,
                "message": "Embed configuration is required"
            }), 400

        # Check premium limit
        with EmbedDao() as dao:
            current_count = dao.count_guild_embeds(str(guild_id))

        can_create, error_msg = PremiumChecker.check_embed_limit(int(guild_id), current_count)
        if not can_create:
            return jsonify({
                "success": False,
                "message": error_msg
            }), 403

        # Validate embed config
        is_valid, error = validate_embed_config(data['embed_config'])
        if not is_valid:
            return jsonify({
                "success": False,
                "message": f"Invalid embed configuration: {error}"
            }), 400

        # Validate buttons if provided
        if data.get('buttons'):
            is_valid, error = validate_button_config(data['buttons'])
            if not is_valid:
                return jsonify({
                    "success": False,
                    "message": f"Invalid button configuration: {error}"
                }), 400

        # Create embed in database
        with EmbedDao() as dao:
            embed_id = dao.create_embed(
                guild_id=str(guild_id),
                name=data['name'],
                created_by=str(request.user_id),
                embed_config=data['embed_config'],
                message_text=data.get('message_text'),
                channel_id=data.get('channel_id'),
                buttons=data.get('buttons')
            )

        if not embed_id:
            return jsonify({
                "success": False,
                "message": "Failed to create embed"
            }), 500

        # Fetch created embed
        with EmbedDao() as dao:
            embed_data = dao.get_embed(embed_id)

        return jsonify({
            "success": True,
            "message": "Embed created successfully",
            "embed": embed_data.to_dict()
        }), 201

    except Exception as e:
        logger.error(f"Error creating embed for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to create embed"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/<int:embed_id>', methods=['PUT'])
@require_auth
def update_embed(guild_id, embed_id):
    """
    Update an existing embed

    Request Body:
        name (str): Internal name (optional)
        message_text (str): Text content (optional)
        embed_config (dict): Embed configuration (optional)
        channel_id (str): Target channel ID (optional)
        buttons (list): Button configuration (optional)
        is_enabled (bool): Enabled status (optional)

    Returns:
        200: Embed updated successfully
        400: Invalid request data
        403: User is not admin
        404: Embed not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to update embeds"
            }), 403

        # Verify embed exists and belongs to guild
        with EmbedDao() as dao:
            embed_data = dao.get_embed(embed_id)

        if not embed_data or embed_data.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Embed not found"
            }), 404

        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Validate embed config if provided
        if 'embed_config' in data and data['embed_config']:
            is_valid, error = validate_embed_config(data['embed_config'])
            if not is_valid:
                return jsonify({
                    "success": False,
                    "message": f"Invalid embed configuration: {error}"
                }), 400

        # Validate buttons if provided
        if 'buttons' in data and data['buttons']:
            is_valid, error = validate_button_config(data['buttons'])
            if not is_valid:
                return jsonify({
                    "success": False,
                    "message": f"Invalid button configuration: {error}"
                }), 400

        # Update embed in database
        with EmbedDao() as dao:
            success = dao.update_embed(
                embed_id=embed_id,
                guild_id=str(guild_id),
                name=data.get('name'),
                message_text=data.get('message_text'),
                embed_config=data.get('embed_config'),
                channel_id=data.get('channel_id'),
                buttons=data.get('buttons'),
                is_enabled=data.get('is_enabled')
            )

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to update embed"
            }), 500

        # Fetch updated embed
        with EmbedDao() as dao:
            updated_embed = dao.get_embed(embed_id)

        return jsonify({
            "success": True,
            "message": "Embed updated successfully",
            "embed": updated_embed.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error updating embed {embed_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to update embed"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/<int:embed_id>', methods=['DELETE'])
@require_auth
def delete_embed(guild_id, embed_id):
    """
    Delete an embed

    Returns:
        200: Embed deleted successfully
        403: User is not admin
        404: Embed not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to delete embeds"
            }), 403

        # Verify embed exists and belongs to guild
        with EmbedDao() as dao:
            embed_data = dao.get_embed(embed_id)

        if not embed_data or embed_data.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Embed not found"
            }), 404

        # Delete from Discord if it was sent
        if embed_data.has_message_id():
            try:
                run_sync(http_client.delete_message(
                    int(embed_data.channel_id),
                    int(embed_data.message_id)
                ))
            except Exception as e:
                logger.warning(f"Failed to delete Discord message for embed {embed_id}: {e}")

        # Delete from database
        with EmbedDao() as dao:
            success = dao.delete_embed(embed_id, str(guild_id))

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to delete embed"
            }), 500

        return jsonify({
            "success": True,
            "message": "Embed deleted successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error deleting embed {embed_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to delete embed"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/<int:embed_id>/send', methods=['POST'])
@require_auth
def send_embed(guild_id, embed_id):
    """
    Send embed to Discord channel

    Returns:
        200: Embed sent successfully
        400: Invalid data or missing channel
        403: User is not admin
        404: Embed not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to send embeds"
            }), 403

        # Verify embed exists and belongs to guild
        with EmbedDao() as dao:
            embed_data = dao.get_embed(embed_id)

        if not embed_data or embed_data.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Embed not found"
            }), 404

        # Verify channel ID is set
        if not embed_data.channel_id:
            return jsonify({
                "success": False,
                "message": "Channel ID is required to send embed"
            }), 400

        # Build Discord message payload
        message_content = {}
        if embed_data.message_text:
            message_content['content'] = embed_data.message_text

        # Build embed
        embed = build_embed_from_config(embed_data.embed_config)
        message_content['embeds'] = [embed]

        # Add buttons if configured
        if embed_data.has_buttons():
            components = build_button_components(embed_data.buttons)
            message_content['components'] = components

        # If message was already sent, edit it instead
        if embed_data.has_message_id():
            try:
                edited_message = run_sync(http_client.edit_message(
                    int(embed_data.channel_id),
                    int(embed_data.message_id),
                    message_content
                ))

                if not edited_message:
                    return jsonify({
                        "success": False,
                        "message": "Failed to edit message in Discord"
                    }), 500

                return jsonify({
                    "success": True,
                    "message": "Embed updated in Discord",
                    "message_id": embed_data.message_id
                }), 200

            except Exception as e:
                logger.error(f"Failed to edit Discord message: {e}")
                return jsonify({
                    "success": False,
                    "message": "Failed to edit message in Discord"
                }), 500

        # Post new message to Discord
        try:
            posted_message = run_sync(http_client.post_message(
                int(embed_data.channel_id),
                message_content
            ))

            if not posted_message or 'id' not in posted_message:
                return jsonify({
                    "success": False,
                    "message": "Failed to post message to Discord"
                }), 500

            message_id = posted_message['id']

            # Update database with message ID and mark as sent
            with EmbedDao() as dao:
                dao.update_message_id(embed_id, str(message_id))
                dao.mark_as_sent(embed_id)

            return jsonify({
                "success": True,
                "message": "Embed sent to Discord successfully",
                "message_id": message_id
            }), 200

        except Exception as e:
            logger.error(f"Failed to post embed to Discord: {e}")
            return jsonify({
                "success": False,
                "message": "Failed to send embed to Discord"
            }), 500

    except Exception as e:
        logger.error(f"Error sending embed {embed_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to send embed"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/<int:embed_id>/duplicate', methods=['POST'])
@require_auth
def duplicate_embed(guild_id, embed_id):
    """
    Duplicate an existing embed

    Returns:
        201: Embed duplicated successfully
        403: User is not admin or premium limit reached
        404: Embed not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to duplicate embeds"
            }), 403

        # Verify embed exists and belongs to guild
        with EmbedDao() as dao:
            source_embed = dao.get_embed(embed_id)

        if not source_embed or source_embed.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Embed not found"
            }), 404

        # Check premium limit
        with EmbedDao() as dao:
            current_count = dao.count_guild_embeds(str(guild_id))

        can_create, error_msg = PremiumChecker.check_embed_limit(int(guild_id), current_count)
        if not can_create:
            return jsonify({
                "success": False,
                "message": error_msg
            }), 403

        # Create duplicate
        new_name = f"{source_embed.name} (Copy)"
        with EmbedDao() as dao:
            new_embed_id = dao.create_embed(
                guild_id=str(guild_id),
                name=new_name,
                created_by=str(request.user_id),
                embed_config=source_embed.embed_config,
                message_text=source_embed.message_text,
                channel_id=source_embed.channel_id,
                buttons=source_embed.buttons
            )

        if not new_embed_id:
            return jsonify({
                "success": False,
                "message": "Failed to duplicate embed"
            }), 500

        # Fetch created embed
        with EmbedDao() as dao:
            new_embed = dao.get_embed(new_embed_id)

        return jsonify({
            "success": True,
            "message": "Embed duplicated successfully",
            "embed": new_embed.to_dict()
        }), 201

    except Exception as e:
        logger.error(f"Error duplicating embed {embed_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to duplicate embed"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/stats', methods=['GET'])
@require_auth
def get_embed_stats(guild_id):
    """
    Get embed statistics and limits for a guild

    Returns:
        200: Stats data
        403: User is not admin
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to view stats"
            }), 403

        # Get counts
        with EmbedDao() as dao:
            total_count = dao.count_guild_embeds(str(guild_id))
            sent_count = len(dao.get_sent_embeds(str(guild_id)))
            draft_count = len(dao.get_draft_embeds(str(guild_id)))

        # Get premium tier and limit
        tier_info = PremiumChecker.get_tier_info(int(guild_id))
        tier = tier_info.get('tier', 'free')
        limit = PremiumChecker.get_embed_limit(int(guild_id))

        return jsonify({
            "success": True,
            "stats": {
                "total": total_count,
                "sent": sent_count,
                "drafts": draft_count,
                "limit": limit,
                "tier": tier,
                "can_create_more": total_count < limit
            }
        }), 200

    except Exception as e:
        logger.error(f"Error fetching stats for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch stats"
        }), 500

@embeds_bp.route('/guilds/<guild_id>/embeds/upload-image', methods=['POST'])
@require_auth
def upload_embed_image(guild_id):
    """
    Upload an image for use in embeds

    Form Data:
        image (file): Image file (PNG/JPG/GIF/WEBP, max 8MB)
        image_type (str): Type of image (author_icon, footer_icon, thumbnail, image)

    Returns:
        200: Image uploaded successfully with CDN URL
        400: Invalid file or request
        403: User is not admin
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to upload images"
            }), 403

        # Validate file present
        if 'image' not in request.files:
            return jsonify({
                "success": False,
                "message": "No file provided"
            }), 400

        file = request.files['image']
        if file.filename == '':
            return jsonify({
                "success": False,
                "message": "No file selected"
            }), 400

        # Check file size
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)

        if size > MAX_FILE_SIZE:
            return jsonify({
                "success": False,
                "message": f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)"
            }), 400

        # Check magic bytes for file type
        file_header = file.read(2048)
        file.seek(0)
        file_type = magic.from_buffer(file_header, mime=True)

        if file_type not in ALLOWED_MIME_TYPES:
            return jsonify({
                "success": False,
                "message": f"Invalid file type. Allowed: PNG, JPG, GIF, WEBP"
            }), 400

        # Generate filename
        ext = file_type.split('/')[1]
        if ext == 'jpeg':
            ext = 'jpg'
        filename = f"{uuid.uuid4()}_{int(time.time())}.{ext}"

        # Create guild directory
        guild_dir = Path(UPLOAD_FOLDER) / guild_id
        guild_dir.mkdir(parents=True, exist_ok=True)

        # Save file
        filepath = guild_dir / filename
        file.save(str(filepath))

        # Set proper permissions
        os.chmod(str(filepath), 0o644)

        # Return CDN URL
        url = f"{CDN_BASE_URL}/{guild_id}/{filename}"

        logger.info(f"Image uploaded for guild {guild_id}: {filename}")

        return jsonify({
            "success": True,
            "url": url,
            "filename": filename
        }), 200

    except Exception as e:
        logger.error(f"Error uploading image for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to upload image"
        }), 500
