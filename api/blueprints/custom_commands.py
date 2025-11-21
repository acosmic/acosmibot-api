"""
Custom Commands API Blueprint

Provides REST API endpoints for managing custom bot commands.
Premium feature: Free tier = 1 command, Premium tier = 25 commands.
"""
import sys
import re
from pathlib import Path
from flask import Blueprint, request, jsonify
from api.middleware.auth_decorators import require_auth
from api.services.discord_integration import check_admin_sync

# Add bot directory to path to import DAOs
bot_dir = Path(__file__).parent.parent.parent.parent / 'acosmibot'
if str(bot_dir) not in sys.path:
    sys.path.insert(0, str(bot_dir))

from Dao.CustomCommandDao import CustomCommandDao
from utils.premium_checker import PremiumChecker
import logging

logger = logging.getLogger(__name__)

custom_commands_bp = Blueprint('custom_commands', __name__, url_prefix='/api')


# Validation functions (copied from manager to avoid discord.py dependency)
def validate_command_name(command: str) -> tuple:
    """Validate command name format"""
    if not command:
        return False, "Command name cannot be empty"
    if len(command) > 100:
        return False, "Command name must be 100 characters or less"
    if not re.match(r'^[a-zA-Z0-9_-]+$', command):
        return False, "Command name can only contain letters, numbers, hyphens, and underscores"

    reserved = {'help', 'info', 'ping', 'stats', 'settings', 'config', 'setup', 'admin', 'mod', 'moderator'}
    if command.lower() in reserved:
        return False, f"Command name '{command}' is reserved"

    return True, ""


def validate_embed_config(embed_config: dict) -> tuple:
    """Validate embed configuration"""
    if not embed_config:
        return False, "Embed configuration cannot be empty"
    if not embed_config.get('title') and not embed_config.get('description'):
        return False, "Embed must have at least a title or description"

    if 'color' in embed_config:
        color = embed_config['color']
        if isinstance(color, str):
            if not re.match(r'^#?[0-9A-Fa-f]{6}$', color):
                return False, "Color must be a valid hex code (e.g., #5865F2)"

    if 'fields' in embed_config:
        if not isinstance(embed_config['fields'], list):
            return False, "Fields must be a list"
        for i, field in enumerate(embed_config['fields']):
            if not isinstance(field, dict):
                return False, f"Field {i+1} must be an object"
            if 'name' not in field or 'value' not in field:
                return False, f"Field {i+1} must have 'name' and 'value'"

    return True, ""


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands', methods=['GET'])
@require_auth
def get_custom_commands(guild_id, current_user):
    """
    Get all custom commands for a guild

    Query Parameters:
        enabled_only (bool): Filter to only enabled commands (default: false)

    Returns:
        200: List of custom commands
        403: User is not admin
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to view custom commands"
            }), 403

        # Get query parameter
        enabled_only = request.args.get('enabled_only', 'false').lower() == 'true'

        # Fetch commands from database
        with CustomCommandDao() as dao:
            commands_data = dao.get_guild_commands(
                guild_id=str(guild_id),
                enabled_only=enabled_only
            )

        return jsonify({
            "success": True,
            "commands": commands_data,
            "count": len(commands_data)
        }), 200

    except Exception as e:
        logger.error(f"Error fetching custom commands for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch custom commands"
        }), 500


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands', methods=['POST'])
@require_auth
def create_custom_command(guild_id, current_user):
    """
    Create a new custom command

    Request Body:
        command (str): Command word (without prefix)
        prefix (str): Command prefix (default: '!')
        response_type (str): 'text' or 'embed'
        response_text (str): Text response (required if response_type='text')
        embed_config (dict): Embed configuration (required if response_type='embed')

    Returns:
        201: Command created successfully
        400: Invalid request data or premium limit reached
        403: User is not admin
        409: Command already exists
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator to create custom commands"
            }), 403

        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Validate required fields
        command = data.get('command', '').strip()
        prefix = data.get('prefix', '!').strip()
        response_type = data.get('response_type', 'text')
        response_text = data.get('response_text')
        embed_config = data.get('embed_config')

        if not command:
            return jsonify({
                "success": False,
                "message": "Command name is required"
            }), 400

        # Validate command name
        is_valid, error_msg = validate_command_name(command)
        if not is_valid:
            return jsonify({
                "success": False,
                "message": error_msg
            }), 400

        # Validate response type
        if response_type not in ['text', 'embed']:
            return jsonify({
                "success": False,
                "message": "response_type must be 'text' or 'embed'"
            }), 400

        # Validate response content
        if response_type == 'text' and not response_text:
            return jsonify({
                "success": False,
                "message": "response_text is required for text responses"
            }), 400

        if response_type == 'embed':
            if not embed_config:
                return jsonify({
                    "success": False,
                    "message": "embed_config is required for embed responses"
                }), 400

            # Validate embed config
            is_valid_embed, error_msg = validate_embed_config(embed_config)
            if not is_valid_embed:
                return jsonify({
                    "success": False,
                    "message": f"Invalid embed configuration: {error_msg}"
                }), 400

        # Check premium limit
        with CustomCommandDao() as dao:
            current_count = dao.count_guild_commands(str(guild_id))

        can_create, limit_msg = PremiumChecker.check_custom_command_limit(
            int(guild_id),
            current_count
        )
        if not can_create:
            return jsonify({
                "success": False,
                "message": limit_msg
            }), 400

        # Check if command already exists
        with CustomCommandDao() as dao:
            existing_command = dao.get_command_by_command(
                guild_id=str(guild_id),
                command=command,
                prefix=prefix
            )
        if existing_command:
            return jsonify({
                "success": False,
                "message": f"Command {prefix}{command} already exists in this server"
            }), 409

        # Create command
        with CustomCommandDao() as dao:
            command_id = dao.create_command(
                guild_id=str(guild_id),
                command=command,
                created_by=str(current_user['id']),
                prefix=prefix,
                response_type=response_type,
                response_text=response_text,
                embed_config=embed_config
            )

        if not command_id:
            return jsonify({
                "success": False,
                "message": "Failed to create command"
            }), 500

        # Get created command
        with CustomCommandDao() as dao:
            created_command = dao.get_by_id(command_id)

        return jsonify({
            "success": True,
            "message": f"Custom command {prefix}{command} created successfully",
            "command": created_command.to_dict() if created_command else None
        }), 201

    except Exception as e:
        logger.error(f"Error creating custom command for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to create custom command"
        }), 500


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands/<int:command_id>', methods=['GET'])
@require_auth
def get_custom_command(guild_id, command_id, current_user):
    """
    Get a specific custom command

    Returns:
        200: Command details
        403: User is not admin
        404: Command not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator"
            }), 403

        # Get command
        with CustomCommandDao() as dao:
            command = dao.get_by_id(command_id)

        if not command or command.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Command not found"
            }), 404

        return jsonify({
            "success": True,
            "command": command.to_dict()
        }), 200

    except Exception as e:
        logger.error(f"Error fetching command {command_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch command"
        }), 500


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands/<int:command_id>', methods=['PUT'])
@require_auth
def update_custom_command(guild_id, command_id, current_user):
    """
    Update a custom command

    Request Body:
        command (str): New command word
        prefix (str): New command prefix
        response_type (str): New response type
        response_text (str): New text response
        embed_config (dict): New embed configuration
        is_enabled (bool): Enable/disable command

    Returns:
        200: Command updated successfully
        400: Invalid request data
        403: User is not admin
        404: Command not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator"
            }), 403

        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Validate command exists and belongs to guild
        with CustomCommandDao() as dao:
            existing_command = dao.get_by_id(command_id)

        if not existing_command or existing_command.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Command not found"
            }), 404

        # Extract update fields
        update_fields = {}

        if 'command' in data:
            command_name = data['command'].strip()
            is_valid, error_msg = validate_command_name(command_name)
            if not is_valid:
                return jsonify({"success": False, "message": error_msg}), 400
            update_fields['command'] = command_name

        if 'prefix' in data:
            update_fields['prefix'] = data['prefix'].strip()

        if 'response_type' in data:
            if data['response_type'] not in ['text', 'embed']:
                return jsonify({
                    "success": False,
                    "message": "response_type must be 'text' or 'embed'"
                }), 400
            update_fields['response_type'] = data['response_type']

        if 'response_text' in data:
            update_fields['response_text'] = data['response_text']

        if 'embed_config' in data:
            embed_config = data['embed_config']
            is_valid_embed, error_msg = validate_embed_config(embed_config)
            if not is_valid_embed:
                return jsonify({
                    "success": False,
                    "message": f"Invalid embed configuration: {error_msg}"
                }), 400
            update_fields['embed_config'] = embed_config

        if 'is_enabled' in data:
            update_fields['is_enabled'] = bool(data['is_enabled'])

        # Update command
        with CustomCommandDao() as dao:
            success = dao.update_command(
                command_id=command_id,
                guild_id=str(guild_id),
                **update_fields
            )

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to update command"
            }), 500

        # Get updated command
        with CustomCommandDao() as dao:
            updated_command = dao.get_by_id(command_id)

        return jsonify({
            "success": True,
            "message": "Command updated successfully",
            "command": updated_command.to_dict() if updated_command else None
        }), 200

    except Exception as e:
        logger.error(f"Error updating command {command_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to update command"
        }), 500


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands/<int:command_id>', methods=['DELETE'])
@require_auth
def delete_custom_command(guild_id, command_id, current_user):
    """
    Delete a custom command

    Returns:
        200: Command deleted successfully
        403: User is not admin
        404: Command not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator"
            }), 403

        # Verify command exists and belongs to guild
        with CustomCommandDao() as dao:
            existing_command = dao.get_by_id(command_id)

        if not existing_command or existing_command.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Command not found"
            }), 404

        # Delete command
        with CustomCommandDao() as dao:
            success = dao.delete_command(command_id, str(guild_id))

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to delete command"
            }), 500

        return jsonify({
            "success": True,
            "message": f"Command {existing_command.get_full_command()} deleted successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error deleting command {command_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to delete command"
        }), 500


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands/<int:command_id>/toggle', methods=['POST'])
@require_auth
def toggle_custom_command(guild_id, command_id, current_user):
    """
    Enable or disable a custom command

    Returns:
        200: Command toggled successfully
        403: User is not admin
        404: Command not found
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator"
            }), 403

        # Get current command state
        with CustomCommandDao() as dao:
            command = dao.get_by_id(command_id)

        if not command or command.guild_id != str(guild_id):
            return jsonify({
                "success": False,
                "message": "Command not found"
            }), 404

        # Toggle enabled state
        new_state = not command.is_enabled

        with CustomCommandDao() as dao:
            if new_state:
                success = dao.enable_command(command_id, str(guild_id))
            else:
                success = dao.disable_command(command_id, str(guild_id))

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to toggle command"
            }), 500

        return jsonify({
            "success": True,
            "message": f"Command {command.get_full_command()} {'enabled' if new_state else 'disabled'}",
            "is_enabled": new_state
        }), 200

    except Exception as e:
        logger.error(f"Error toggling command {command_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to toggle command"
        }), 500


@custom_commands_bp.route('/guilds/<guild_id>/custom-commands/stats', methods=['GET'])
@require_auth
def get_custom_commands_stats(guild_id, current_user):
    """
    Get usage statistics for custom commands

    Query Parameters:
        limit (int): Number of top commands to return (default: 10)

    Returns:
        200: Command statistics
        403: User is not admin
        500: Server error
    """
    try:
        # Check if user is admin
        is_admin = check_admin_sync(guild_id, current_user['id'])
        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You must be a server administrator"
            }), 403

        # Get limit from query params
        limit = request.args.get('limit', 10, type=int)
        limit = min(max(limit, 1), 50)  # Clamp between 1 and 50

        # Get most used commands
        with CustomCommandDao() as dao:
            top_commands = dao.get_most_used_commands(str(guild_id), limit=limit)
            total_count = dao.count_guild_commands(str(guild_id))

        # Get tier info
        tier = PremiumChecker.get_guild_tier(int(guild_id))
        max_commands = PremiumChecker.get_limit(int(guild_id), 'custom_commands')

        return jsonify({
            "success": True,
            "stats": {
                "total_commands": total_count,
                "max_commands": max_commands,
                "tier": tier,
                "top_commands": top_commands
            }
        }), 200

    except Exception as e:
        logger.error(f"Error fetching command stats for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": "Failed to fetch command statistics"
        }), 500
