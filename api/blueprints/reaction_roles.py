"""Reaction roles management endpoints"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import GuildDao
from api.services.discord_integration import check_admin_sync, http_client, run_sync
import sys
import os
import json
import logging

logger = logging.getLogger(__name__)
reaction_roles_bp = Blueprint('reaction_roles', __name__, url_prefix='/api')

# Import bot models - add path if needed
try:
    from models.reaction_role_manager import ReactionRoleManager
    from Dao.ReactionRoleDao import ReactionRoleDao
    from utils.premium_checker import PremiumChecker
except ImportError:
    # Try adding bot path to sys.path
    bot_path = os.path.join(os.path.dirname(__file__), '../../acosmibot')
    if bot_path not in sys.path:
        sys.path.insert(0, bot_path)

    from models.reaction_role_manager import ReactionRoleManager
    from Dao.ReactionRoleDao import ReactionRoleDao
    from utils.premium_checker import PremiumChecker


def get_reaction_role_manager():
    """Get reaction role manager instance"""
    with ReactionRoleDao() as dao:
        return ReactionRoleManager(dao)


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles', methods=['GET'])
@require_auth
def get_reaction_roles(guild_id):
    """Get all reaction role configurations for a guild"""
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get all reaction roles for guild
        manager = get_reaction_role_manager()
        configs = manager.get_all_for_guild(int(guild_id))

        return jsonify({
            "success": True,
            "data": configs
        }), 200

    except Exception as e:
        logger.error(f"Error getting reaction roles for guild {guild_id}: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles', methods=['POST'])
@require_auth
def create_reaction_role(guild_id):
    """Create a new reaction role configuration"""
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Check premium tier and reaction role limit
        manager = get_reaction_role_manager()
        existing_count = len(manager.get_all_for_guild(int(guild_id)))

        can_create, error_msg = PremiumChecker.check_reaction_role_limit(int(guild_id), existing_count)
        if not can_create:
            return jsonify({
                "success": False,
                "message": error_msg
            }), 403

        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Validate required fields (message_id is NOT required - we'll post it)
        required_fields = ['channel_id', 'interaction_type']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    "success": False,
                    "message": f"Missing required field: {field}"
                }), 400

        # Post message to Discord to get message ID
        try:
            guild_info = run_sync(http_client.get_guild_info(guild_id))
            if not guild_info:
                return jsonify({
                    "success": False,
                    "message": "Guild not found"
                }), 404

            # Post the message to Discord
            message_content = {}
            if data.get('text_content'):
                message_content['content'] = data['text_content']

            # Convert embed_config to Discord embed format if present
            if data.get('embed_config'):
                embed_config = data['embed_config']
                color_hex = embed_config.get('color', '#5865F2')
                color_int = int(color_hex.lstrip('#'), 16)
                embed = {
                    'title': embed_config.get('title'),
                    'description': embed_config.get('description'),
                    'color': color_int
                }
                if embed_config.get('thumbnail'):
                    embed['thumbnail'] = {'url': embed_config['thumbnail']}
                if embed_config.get('image'):
                    embed['image'] = {'url': embed_config['image']}
                if embed_config.get('author_name'):
                    embed['author'] = {'name': embed_config['author_name']}
                if embed_config.get('footer'):
                    embed['footer'] = {'text': embed_config['footer']}
                if embed_config.get('fields'):
                    embed['fields'] = [
                        {'name': f.get('name', ''), 'value': f.get('value', ''), 'inline': f.get('inline', False)}
                        for f in embed_config['fields']
                    ]
                message_content['embeds'] = [embed]

            # Post message to Discord
            posted_message = run_sync(http_client.post_message(
                int(data['channel_id']),
                message_content
            ))

            if not posted_message:
                return jsonify({
                    "success": False,
                    "message": "Failed to post message to Discord"
                }), 400

            message_id = posted_message.get('id')
            if not message_id:
                return jsonify({
                    "success": False,
                    "message": "Failed to get message ID from Discord"
                }), 400

            data['message_id'] = int(message_id)

            # Convert emoji_role_mappings from array format to dict format if needed
            if data.get('interaction_type') == 'emoji' and data.get('emoji_role_mappings'):
                emoji_mappings = data['emoji_role_mappings']
                # Check if it's an array of {emoji, role_ids} objects
                if isinstance(emoji_mappings, list):
                    # Convert to dict format: {emoji_str: [role_ids]}
                    emoji_dict = {}
                    for mapping in emoji_mappings:
                        if mapping.get('emoji') and mapping.get('role_ids'):
                            emoji_dict[mapping['emoji']] = mapping['role_ids']
                    data['emoji_role_mappings'] = emoji_dict

            # Verify all role IDs exist
            available_roles = run_sync(http_client.get_guild_roles(guild_id))
            available_role_ids = {str(r['id']) for r in available_roles}
            role_ids = set()

            if data.get('interaction_type') == 'emoji' and data.get('emoji_role_mappings'):
                for role_list in data['emoji_role_mappings'].values():
                    role_ids.update(role_list)
            elif data.get('interaction_type') == 'button' and data.get('button_configs'):
                for button in data['button_configs']:
                    role_ids.update(button.get('role_ids', []))
            elif data.get('interaction_type') == 'dropdown' and data.get('dropdown_config'):
                for option in data['dropdown_config'].get('options', []):
                    role_ids.update(option.get('role_ids', []))

            # Check all role IDs are valid
            for role_id in role_ids:
                if str(role_id) not in available_role_ids:
                    return jsonify({
                        "success": False,
                        "message": f"Role {role_id} not found in guild"
                    }), 400

        except Exception as e:
            logger.error(f"Error verifying Discord entities: {e}")
            return jsonify({
                "success": False,
                "message": f"Error verifying guild/roles: {str(e)}"
            }), 400

        # Validate configuration
        manager = get_reaction_role_manager()
        is_valid, error_msg = manager.validate_config({
            'guild_id': int(guild_id),
            'message_id': int(data['message_id']),
            'channel_id': int(data['channel_id']),
            'interaction_type': data['interaction_type'],
            'text_content': data.get('text_content'),
            'embed_config': data.get('embed_config'),
            'emoji_role_mappings': data.get('emoji_role_mappings'),
            'button_configs': data.get('button_configs'),
            'dropdown_config': data.get('dropdown_config')
        })

        if not is_valid:
            return jsonify({
                "success": False,
                "message": error_msg
            }), 400

        # Create reaction role
        success = manager.create_reaction_role(
            guild_id=int(guild_id),
            message_id=int(data['message_id']),
            channel_id=int(data['channel_id']),
            interaction_type=data['interaction_type'],
            text_content=data.get('text_content'),
            embed_config=data.get('embed_config'),
            allow_removal=data.get('allow_removal', True),
            emoji_role_mappings=data.get('emoji_role_mappings'),
            button_configs=data.get('button_configs'),
            dropdown_config=data.get('dropdown_config')
        )

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to create reaction role configuration"
            }), 500

        # If emoji type, add reactions to the message
        if data.get('interaction_type') == 'emoji' and data.get('emoji_role_mappings'):
            emojis = data['emoji_role_mappings'].keys() if isinstance(data['emoji_role_mappings'], dict) else [m['emoji'] for m in data['emoji_role_mappings'] if m.get('emoji')]

            for emoji in emojis:
                try:
                    logger.info(f"Adding reaction {emoji} to message {data['message_id']}")
                    reaction_added = run_sync(http_client.add_reaction(
                        int(data['channel_id']),
                        int(data['message_id']),
                        emoji
                    ))
                    if not reaction_added:
                        logger.warning(f"Failed to add reaction {emoji} to message {data['message_id']} - HTTP request returned False")
                    else:
                        logger.info(f"Successfully added reaction {emoji}")
                except Exception as e:
                    logger.error(f"Error adding reaction {emoji}: {e}")
                    import traceback
                    traceback.print_exc()

        # Return created config
        config = manager.get_reaction_config(int(data['message_id']))
        return jsonify({
            "success": True,
            "message": "Reaction role created successfully",
            "data": config
        }), 201

    except Exception as e:
        logger.error(f"Error creating reaction role: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:message_id>', methods=['PUT'])
@require_auth
def update_reaction_role(guild_id, message_id):
    """Update an existing reaction role configuration"""
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        manager = get_reaction_role_manager()

        # Verify config exists
        existing = manager.get_reaction_config(message_id)
        if not existing:
            return jsonify({
                "success": False,
                "message": "Reaction role not found"
            }), 404

        # Verify guild matches
        if existing['guild_id'] != int(guild_id):
            return jsonify({
                "success": False,
                "message": "Guild ID mismatch"
            }), 400

        # Verify roles if provided
        if data.get('interaction_type') == 'emoji' and data.get('emoji_role_mappings'):
            try:
                available_roles = run_sync(http_client.get_guild_roles(guild_id))
                role_ids = set()
                for role_list in data['emoji_role_mappings'].values():
                    role_ids.update(role_list)

                available_role_ids = {str(r['id']) for r in available_roles}
                for role_id in role_ids:
                    if str(role_id) not in available_role_ids:
                        return jsonify({
                            "success": False,
                            "message": f"Role {role_id} not found in guild"
                        }), 400
            except Exception as e:
                logger.error(f"Error verifying roles: {e}")
                return jsonify({
                    "success": False,
                    "message": f"Error verifying roles: {str(e)}"
                }), 400

        # Update reaction role
        success = manager.update_reaction_role(
            message_id=message_id,
            guild_id=int(guild_id) if 'guild_id' in data else None,
            channel_id=int(data['channel_id']) if 'channel_id' in data else None,
            interaction_type=data.get('interaction_type'),
            text_content=data.get('text_content'),
            embed_config=data.get('embed_config'),
            allow_removal=data.get('allow_removal'),
            emoji_role_mappings=data.get('emoji_role_mappings'),
            button_configs=data.get('button_configs'),
            dropdown_config=data.get('dropdown_config'),
            enabled=data.get('enabled')
        )

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to update reaction role configuration"
            }), 500

        # Return updated config
        config = manager.get_reaction_config(message_id)
        return jsonify({
            "success": True,
            "message": "Reaction role updated successfully",
            "data": config
        }), 200

    except Exception as e:
        logger.error(f"Error updating reaction role: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:message_id>', methods=['DELETE'])
@require_auth
def delete_reaction_role(guild_id, message_id):
    """Delete a reaction role configuration"""
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        manager = get_reaction_role_manager()

        # Verify config exists and belongs to guild
        existing = manager.get_reaction_config(message_id)
        if not existing:
            return jsonify({
                "success": False,
                "message": "Reaction role not found"
            }), 404

        if existing['guild_id'] != int(guild_id):
            return jsonify({
                "success": False,
                "message": "Guild ID mismatch"
            }), 400

        # Delete reaction role
        success = manager.delete_reaction_role(message_id)

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to delete reaction role configuration"
            }), 500

        return jsonify({
            "success": True,
            "message": "Reaction role deleted successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error deleting reaction role: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/preview', methods=['POST'])
@require_auth
def preview_reaction_role(guild_id):
    """Preview how a reaction role message will look"""
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Build preview data
        preview = {
            "text_content": data.get('text_content', ''),
            "embed": data.get('embed_config'),
            "interaction_type": data.get('interaction_type'),
            "message": ""
        }

        # Build message description based on type
        if data.get('interaction_type') == 'emoji':
            emoji_count = len(data.get('emoji_role_mappings', {}))
            preview['message'] = f"This message will have {emoji_count} emoji reactions"
        elif data.get('interaction_type') == 'button':
            button_count = len(data.get('button_configs', []))
            preview['message'] = f"This message will have {button_count} buttons"
        elif data.get('interaction_type') == 'dropdown':
            option_count = len(data.get('dropdown_config', {}).get('options', []))
            preview['message'] = f"This message will have a dropdown with {option_count} options"

        return jsonify({
            "success": True,
            "data": preview
        }), 200

    except Exception as e:
        logger.error(f"Error previewing reaction role: {e}")
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500
