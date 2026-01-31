"""
Reaction Roles API Blueprint

Provides REST API endpoints for managing Discord reaction role messages.
Supports draft/sent workflow, emoji/button/dropdown interactions, and role mention suppression.
"""
import json
import logging
from typing import Dict, List, Optional
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.discord_integration import check_admin_sync, http_client, run_sync
from acosmibot_core.dao import ReactionRoleDao
from acosmibot_core.entities import ReactionRole
from acosmibot_core.models import ReactionRoleManager
from acosmibot_core.utils import PremiumChecker

logger = logging.getLogger(__name__)
reaction_roles_bp = Blueprint('reaction_roles', __name__, url_prefix='/api')


def get_reaction_role_dao():
    """Get reaction role DAO instance"""
    return ReactionRoleDao()


def get_reaction_role_manager():
    """Get reaction role manager instance"""
    with ReactionRoleDao() as dao:
        return ReactionRoleManager(dao)


def suppress_role_pings(text: str, guild_id: str) -> str:
    """
    Replace role mentions (<@&role_id>) with plain text (@Role Name).

    Args:
        text: Text content with potential role mentions
        guild_id: Discord guild ID

    Returns:
        Text with role mentions replaced
    """
    if not text or '<@&' not in text:
        return text

    try:
        import re
        roles = run_sync(http_client.get_guild_roles(guild_id))
        role_map = {str(r['id']): r['name'] for r in roles}

        def replace_mention(match):
            role_id = match.group(1)
            role_name = role_map.get(role_id, f"Unknown Role")
            return f"@{role_name}"

        return re.sub(r'<@&(\d+)>', replace_mention, text)
    except Exception as e:
        logger.error(f"Error suppressing role pings: {e}")
        return text


def apply_role_mention_suppression(embed_config: Optional[Dict], guild_id: str) -> Optional[Dict]:
    """
    Apply role mention suppression to embed fields.

    Args:
        embed_config: Embed configuration dict
        guild_id: Discord guild ID

    Returns:
        Embed config with suppressed role mentions
    """
    if not embed_config:
        return embed_config

    config = embed_config.copy()

    # Suppress in title
    if config.get('title'):
        config['title'] = suppress_role_pings(config['title'], guild_id)

    # Suppress in description
    if config.get('description'):
        config['description'] = suppress_role_pings(config['description'], guild_id)

    # Suppress in author name
    if config.get('author_name'):
        config['author_name'] = suppress_role_pings(config['author_name'], guild_id)

    # Suppress in footer
    if config.get('footer'):
        config['footer'] = suppress_role_pings(config['footer'], guild_id)

    # Suppress in fields
    if config.get('fields'):
        for field in config['fields']:
            if field.get('name'):
                field['name'] = suppress_role_pings(field['name'], guild_id)
            if field.get('value'):
                field['value'] = suppress_role_pings(field['value'], guild_id)

    return config


def build_discord_embed(embed_config: Dict) -> Dict:
    """
    Convert embed config to Discord API format.

    Args:
        embed_config: Internal embed configuration

    Returns:
        Discord-formatted embed
    """
    embed = {}

    if embed_config.get('title'):
        embed['title'] = embed_config['title']

    if embed_config.get('description'):
        embed['description'] = embed_config['description']

    if embed_config.get('color'):
        color_hex = embed_config['color'].lstrip('#')
        embed['color'] = int(color_hex, 16)

    if embed_config.get('author_name'):
        embed['author'] = {'name': embed_config['author_name']}
        if embed_config.get('author_icon'):
            embed['author']['icon_url'] = embed_config['author_icon']
        if embed_config.get('author_url'):
            embed['author']['url'] = embed_config['author_url']

    if embed_config.get('thumbnail'):
        embed['thumbnail'] = {'url': embed_config['thumbnail']}

    if embed_config.get('image'):
        embed['image'] = {'url': embed_config['image']}

    if embed_config.get('footer'):
        embed['footer'] = {'text': embed_config['footer']}
        if embed_config.get('footer_icon'):
            embed['footer']['icon_url'] = embed_config['footer_icon']

    if embed_config.get('fields'):
        embed['fields'] = [
            {
                'name': f.get('name', 'Field'),
                'value': f.get('value', ''),
                'inline': f.get('inline', False)
            }
            for f in embed_config['fields']
        ]

    return embed


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles', methods=['GET'])
@require_auth
def get_reaction_roles(guild_id):
    """
    Get all reaction roles for a guild (both drafts and sent).

    Returns:
        200: List of reaction roles
        403: User is not admin
        500: Server error
    """
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        with ReactionRoleDao() as dao:
            reaction_roles = dao.get_all_by_guild(int(guild_id))

            # Convert to dict format with parsed JSON
            roles_data = []
            for rr in reaction_roles:
                data = {
                    "id": rr.id,
                    "guild_id": rr.guild_id,
                    "name": rr.name,
                    "message_id": rr.message_id,
                    "channel_id": rr.channel_id,
                    "interaction_type": rr.interaction_type,
                    "text_content": rr.text_content,
                    "embed_config": json.loads(rr.embed_config) if rr.embed_config else None,
                    "allow_removal": rr.allow_removal,
                    "emoji_role_mappings": json.loads(rr.emoji_role_mappings) if rr.emoji_role_mappings else None,
                    "button_configs": json.loads(rr.button_configs) if rr.button_configs else None,
                    "dropdown_config": json.loads(rr.dropdown_config) if rr.dropdown_config else None,
                    "enabled": rr.enabled,
                    "is_sent": rr.is_sent,
                    "created_at": str(rr.created_at) if rr.created_at else None,
                    "updated_at": str(rr.updated_at) if rr.updated_at else None
                }
                roles_data.append(data)

        return jsonify({
            "success": True,
            "data": roles_data,
            "count": len(roles_data)
        }), 200

    except Exception as e:
        logger.error(f"Error getting reaction roles for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:rr_id>', methods=['GET'])
@require_auth
def get_reaction_role(guild_id, rr_id):
    """
    Get a single reaction role by ID.

    Returns:
        200: Reaction role data
        403: User is not admin
        404: Not found
        500: Server error
    """
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        with ReactionRoleDao() as dao:
            rr = dao.get_by_id(rr_id)

            if not rr or rr.guild_id != int(guild_id):
                return jsonify({
                    "success": False,
                    "message": "Reaction role not found"
                }), 404

            data = {
                "id": rr.id,
                "guild_id": rr.guild_id,
                "name": rr.name,
                "message_id": rr.message_id,
                "channel_id": rr.channel_id,
                "interaction_type": rr.interaction_type,
                "text_content": rr.text_content,
                "embed_config": json.loads(rr.embed_config) if rr.embed_config else None,
                "allow_removal": rr.allow_removal,
                "emoji_role_mappings": json.loads(rr.emoji_role_mappings) if rr.emoji_role_mappings else None,
                "button_configs": json.loads(rr.button_configs) if rr.button_configs else None,
                "dropdown_config": json.loads(rr.dropdown_config) if rr.dropdown_config else None,
                "enabled": rr.enabled,
                "is_sent": rr.is_sent,
                "created_at": str(rr.created_at) if rr.created_at else None,
                "updated_at": str(rr.updated_at) if rr.updated_at else None
            }

        return jsonify({
            "success": True,
            "data": data
        }), 200

    except Exception as e:
        logger.error(f"Error getting reaction role {rr_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles', methods=['POST'])
@require_auth
def create_reaction_role(guild_id):
    """
    Create a new reaction role (draft - not sent to Discord).

    Request Body:
        name: Internal name (required)
        channel_id: Target channel ID (required)
        interaction_type: emoji/button/dropdown (required)
        text_content: Message text (optional)
        embed_config: Embed configuration (optional)
        allow_removal: Allow role removal (default: true)
        emoji_role_mappings: Emoji mappings (for emoji type)
        button_configs: Button configs (for button type)
        dropdown_config: Dropdown config (for dropdown type)

    Returns:
        201: Created draft reaction role
        400: Validation error
        403: Permission denied or limit reached
        500: Server error
    """
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

        # Check premium limits
        with ReactionRoleDao() as dao:
            counts = dao.count_guild_reaction_roles(int(guild_id))

        can_create, error_msg = PremiumChecker.check_reaction_role_limit(
            int(guild_id),
            counts['total']
        )
        if not can_create:
            return jsonify({
                "success": False,
                "message": error_msg
            }), 403

        # Validate required fields
        if not data.get('name'):
            return jsonify({
                "success": False,
                "message": "Name is required"
            }), 400

        if not data.get('channel_id'):
            return jsonify({
                "success": False,
                "message": "Channel ID is required"
            }), 400

        if data.get('interaction_type') not in ['emoji', 'button', 'dropdown']:
            return jsonify({
                "success": False,
                "message": "interaction_type must be emoji, button, or dropdown"
            }), 400

        # Create reaction role entity (draft state)
        reaction_role = ReactionRole(
            guild_id=int(guild_id),
            name=data['name'],
            message_id=None,  # Draft - no message ID yet
            channel_id=int(data['channel_id']),
            interaction_type=data['interaction_type'],
            text_content=data.get('text_content'),
            embed_config=json.dumps(data['embed_config']) if data.get('embed_config') else None,
            allow_removal=data.get('allow_removal', True),
            emoji_role_mappings=json.dumps(data.get('emoji_role_mappings')) if data.get('emoji_role_mappings') else None,
            button_configs=json.dumps(data.get('button_configs')) if data.get('button_configs') else None,
            dropdown_config=json.dumps(data.get('dropdown_config')) if data.get('dropdown_config') else None,
            enabled=True,
            is_sent=False  # Draft
        )

        # Save to database
        with ReactionRoleDao() as dao:
            created_id = dao.create_reaction_role(reaction_role)

            if not created_id:
                return jsonify({
                    "success": False,
                    "message": "Failed to create reaction role"
                }), 500

            # Fetch the created reaction role
            created_rr = dao.get_by_id(created_id)

        result = {
            "id": created_rr.id,
            "guild_id": created_rr.guild_id,
            "name": created_rr.name,
            "message_id": created_rr.message_id,
            "channel_id": created_rr.channel_id,
            "interaction_type": created_rr.interaction_type,
            "text_content": created_rr.text_content,
            "embed_config": json.loads(created_rr.embed_config) if created_rr.embed_config else None,
            "allow_removal": created_rr.allow_removal,
            "emoji_role_mappings": json.loads(created_rr.emoji_role_mappings) if created_rr.emoji_role_mappings else None,
            "button_configs": json.loads(created_rr.button_configs) if created_rr.button_configs else None,
            "dropdown_config": json.loads(created_rr.dropdown_config) if created_rr.dropdown_config else None,
            "enabled": created_rr.enabled,
            "is_sent": created_rr.is_sent,
            "created_at": str(created_rr.created_at) if created_rr.created_at else None,
            "updated_at": str(created_rr.updated_at) if created_rr.updated_at else None
        }

        return jsonify({
            "success": True,
            "message": "Reaction role draft created successfully",
            "data": result
        }), 201

    except Exception as e:
        logger.error(f"Error creating reaction role: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:rr_id>', methods=['PUT'])
@require_auth
def update_reaction_role(guild_id, rr_id):
    """
    Update an existing reaction role.
    If sent, also updates the Discord message.

    Returns:
        200: Updated successfully
        403: User is not admin
        404: Not found
        500: Server error
    """
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

        with ReactionRoleDao() as dao:
            # Get existing reaction role
            existing = dao.get_by_id(rr_id)

            if not existing or existing.guild_id != int(guild_id):
                return jsonify({
                    "success": False,
                    "message": "Reaction role not found"
                }), 404

            # Update fields
            existing.name = data.get('name', existing.name)
            existing.channel_id = int(data.get('channel_id', existing.channel_id))
            existing.text_content = data.get('text_content', existing.text_content)
            existing.embed_config = json.dumps(data['embed_config']) if 'embed_config' in data else existing.embed_config
            existing.allow_removal = data.get('allow_removal', existing.allow_removal)

            # Update interaction-type-specific configs
            if 'emoji_role_mappings' in data:
                existing.emoji_role_mappings = json.dumps(data['emoji_role_mappings']) if data['emoji_role_mappings'] else None
            if 'button_configs' in data:
                existing.button_configs = json.dumps(data['button_configs']) if data['button_configs'] else None
            if 'dropdown_config' in data:
                existing.dropdown_config = json.dumps(data['dropdown_config']) if data['dropdown_config'] else None

            # Save to database
            success = dao.update_reaction_role(existing)

            if not success:
                return jsonify({
                    "success": False,
                    "message": "Failed to update reaction role"
                }), 500

            # If sent, update Discord message
            if existing.is_sent and existing.message_id:
                try:
                    message_content = {}

                    # Build text content (with role mention suppression if enabled)
                    text = existing.text_content
                    if data.get('suppress_role_pings') and text:
                        text = suppress_role_pings(text, guild_id)
                    if text:
                        message_content['content'] = text

                    # Build embed
                    if existing.embed_config:
                        embed_config = json.loads(existing.embed_config)
                        if data.get('suppress_role_pings'):
                            embed_config = apply_role_mention_suppression(embed_config, guild_id)
                        message_content['embeds'] = [build_discord_embed(embed_config)]

                    # Update message on Discord
                    updated_msg = run_sync(http_client.edit_message(
                        int(existing.channel_id),
                        int(existing.message_id),
                        message_content
                    ))

                    if not updated_msg:
                        logger.warning(f"Failed to update Discord message {existing.message_id}")

                except Exception as e:
                    logger.error(f"Error updating Discord message: {e}")
                    # Continue even if Discord update fails

            # Fetch updated record
            updated_rr = dao.get_by_id(rr_id)

        result = {
            "id": updated_rr.id,
            "guild_id": updated_rr.guild_id,
            "name": updated_rr.name,
            "message_id": updated_rr.message_id,
            "channel_id": updated_rr.channel_id,
            "interaction_type": updated_rr.interaction_type,
            "text_content": updated_rr.text_content,
            "embed_config": json.loads(updated_rr.embed_config) if updated_rr.embed_config else None,
            "allow_removal": updated_rr.allow_removal,
            "emoji_role_mappings": json.loads(updated_rr.emoji_role_mappings) if updated_rr.emoji_role_mappings else None,
            "button_configs": json.loads(updated_rr.button_configs) if updated_rr.button_configs else None,
            "dropdown_config": json.loads(updated_rr.dropdown_config) if updated_rr.dropdown_config else None,
            "enabled": updated_rr.enabled,
            "is_sent": updated_rr.is_sent,
            "created_at": str(updated_rr.created_at) if updated_rr.created_at else None,
            "updated_at": str(updated_rr.updated_at) if updated_rr.updated_at else None
        }

        return jsonify({
            "success": True,
            "message": "Reaction role updated successfully",
            "data": result
        }), 200

    except Exception as e:
        logger.error(f"Error updating reaction role {rr_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:rr_id>/send', methods=['POST'])
@require_auth
def send_reaction_role(guild_id, rr_id):
    """
    Send a draft reaction role to Discord.
    Posts the message and sets up reactions/buttons/dropdown.

    Request Body:
        suppress_role_pings: (optional bool) Replace role mentions with plain text

    Returns:
        200: Sent successfully
        400: Validation error or already sent
        403: User is not admin
        404: Not found
        500: Server error
    """
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        data = request.get_json() or {}

        with ReactionRoleDao() as dao:
            # Get reaction role
            rr = dao.get_by_id(rr_id)

            if not rr or rr.guild_id != int(guild_id):
                return jsonify({
                    "success": False,
                    "message": "Reaction role not found"
                }), 404

            if rr.is_sent:
                return jsonify({
                    "success": False,
                    "message": "Reaction role already sent to Discord"
                }), 400

            # Build message content
            message_content = {}

            # Build text content (with role mention suppression if enabled)
            text = rr.text_content
            if data.get('suppress_role_pings') and text:
                text = suppress_role_pings(text, guild_id)
            if text:
                message_content['content'] = text

            # Build embed
            if rr.embed_config:
                embed_config = json.loads(rr.embed_config)
                if data.get('suppress_role_pings'):
                    embed_config = apply_role_mention_suppression(embed_config, guild_id)
                message_content['embeds'] = [build_discord_embed(embed_config)]

            # Build buttons (for button type)
            if rr.interaction_type == 'button' and rr.button_configs:
                button_configs = json.loads(rr.button_configs)
                components = []
                rows = []
                for idx, btn in enumerate(button_configs):
                    button = {
                        'type': 2,  # Button
                        'custom_id': f"rr_{rr_id}_btn_{idx}",
                        'label': btn.get('label', 'Button'),
                        'style': btn.get('style', 1)
                    }
                    if btn.get('emoji'):
                        button['emoji'] = {'name': btn['emoji']}
                    rows.append(button)
                    # Max 5 buttons per row
                    if len(rows) == 5:
                        components.append({'type': 1, 'components': rows})
                        rows = []
                if rows:
                    components.append({'type': 1, 'components': rows})
                if components:
                    message_content['components'] = components

            # Build dropdown (for dropdown type)
            if rr.interaction_type == 'dropdown' and rr.dropdown_config:
                dropdown_config = json.loads(rr.dropdown_config)
                options = []
                for idx, opt in enumerate(dropdown_config.get('options', [])):
                    option = {
                        'label': opt.get('label', f'Option {idx+1}'),
                        'value': f"rr_{rr_id}_opt_{idx}"
                    }
                    if opt.get('description'):
                        option['description'] = opt['description']
                    if opt.get('emoji'):
                        option['emoji'] = {'name': opt['emoji']}
                    options.append(option)

                if options:
                    select_menu = {
                        'type': 3,  # String select
                        'custom_id': f"rr_{rr_id}_dropdown",
                        'placeholder': dropdown_config.get('placeholder', 'Select roles...'),
                        'options': options
                    }
                    message_content['components'] = [{'type': 1, 'components': [select_menu]}]

            # Post message to Discord
            posted_message = run_sync(http_client.post_message(
                int(rr.channel_id),
                message_content
            ))

            if not posted_message or not posted_message.get('id'):
                return jsonify({
                    "success": False,
                    "message": "Failed to post message to Discord"
                }), 500

            message_id = int(posted_message['id'])

            # Add emoji reactions (for emoji type)
            if rr.interaction_type == 'emoji' and rr.emoji_role_mappings:
                emoji_mappings = json.loads(rr.emoji_role_mappings)
                for emoji in emoji_mappings.keys():
                    try:
                        run_sync(http_client.add_reaction(
                            int(rr.channel_id),
                            message_id,
                            emoji
                        ))
                    except Exception as e:
                        logger.error(f"Error adding reaction {emoji}: {e}")

            # Mark as sent
            dao.mark_as_sent(rr_id, message_id)

            # Fetch updated record
            sent_rr = dao.get_by_id(rr_id)

        result = {
            "id": sent_rr.id,
            "guild_id": sent_rr.guild_id,
            "name": sent_rr.name,
            "message_id": sent_rr.message_id,
            "channel_id": sent_rr.channel_id,
            "interaction_type": sent_rr.interaction_type,
            "text_content": sent_rr.text_content,
            "embed_config": json.loads(sent_rr.embed_config) if sent_rr.embed_config else None,
            "allow_removal": sent_rr.allow_removal,
            "emoji_role_mappings": json.loads(sent_rr.emoji_role_mappings) if sent_rr.emoji_role_mappings else None,
            "button_configs": json.loads(sent_rr.button_configs) if sent_rr.button_configs else None,
            "dropdown_config": json.loads(sent_rr.dropdown_config) if sent_rr.dropdown_config else None,
            "enabled": sent_rr.enabled,
            "is_sent": sent_rr.is_sent,
            "created_at": str(sent_rr.created_at) if sent_rr.created_at else None,
            "updated_at": str(sent_rr.updated_at) if sent_rr.updated_at else None
        }

        return jsonify({
            "success": True,
            "message": "Reaction role sent to Discord successfully",
            "data": result
        }), 200

    except Exception as e:
        logger.error(f"Error sending reaction role {rr_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:rr_id>/duplicate', methods=['POST'])
@require_auth
def duplicate_reaction_role(guild_id, rr_id):
    """
    Duplicate an existing reaction role (creates a new draft).

    Returns:
        201: Duplicated successfully
        403: User is not admin or limit reached
        404: Not found
        500: Server error
    """
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        with ReactionRoleDao() as dao:
            # Check premium limits
            counts = dao.count_guild_reaction_roles(int(guild_id))
            can_create, error_msg = PremiumChecker.check_reaction_role_limit(
                int(guild_id),
                counts['total']
            )
            if not can_create:
                return jsonify({
                    "success": False,
                    "message": error_msg
                }), 403

            # Get original reaction role
            original = dao.get_by_id(rr_id)

            if not original or original.guild_id != int(guild_id):
                return jsonify({
                    "success": False,
                    "message": "Reaction role not found"
                }), 404

            # Create duplicate (as draft)
            duplicate = ReactionRole(
                guild_id=original.guild_id,
                name=f"{original.name} (Copy)",
                message_id=None,  # Draft
                channel_id=original.channel_id,
                interaction_type=original.interaction_type,
                text_content=original.text_content,
                embed_config=original.embed_config,
                allow_removal=original.allow_removal,
                emoji_role_mappings=original.emoji_role_mappings,
                button_configs=original.button_configs,
                dropdown_config=original.dropdown_config,
                enabled=True,
                is_sent=False  # Draft
            )

            # Save to database
            created_id = dao.create_reaction_role(duplicate)

            if not created_id:
                return jsonify({
                    "success": False,
                    "message": "Failed to duplicate reaction role"
                }), 500

            # Fetch created record
            created_rr = dao.get_by_id(created_id)

        result = {
            "id": created_rr.id,
            "guild_id": created_rr.guild_id,
            "name": created_rr.name,
            "message_id": created_rr.message_id,
            "channel_id": created_rr.channel_id,
            "interaction_type": created_rr.interaction_type,
            "text_content": created_rr.text_content,
            "embed_config": json.loads(created_rr.embed_config) if created_rr.embed_config else None,
            "allow_removal": created_rr.allow_removal,
            "emoji_role_mappings": json.loads(created_rr.emoji_role_mappings) if created_rr.emoji_role_mappings else None,
            "button_configs": json.loads(created_rr.button_configs) if created_rr.button_configs else None,
            "dropdown_config": json.loads(created_rr.dropdown_config) if created_rr.dropdown_config else None,
            "enabled": created_rr.enabled,
            "is_sent": created_rr.is_sent,
            "created_at": str(created_rr.created_at) if created_rr.created_at else None,
            "updated_at": str(created_rr.updated_at) if created_rr.updated_at else None
        }

        return jsonify({
            "success": True,
            "message": "Reaction role duplicated successfully",
            "data": result
        }), 201

    except Exception as e:
        logger.error(f"Error duplicating reaction role {rr_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/<int:rr_id>', methods=['DELETE'])
@require_auth
def delete_reaction_role(guild_id, rr_id):
    """
    Delete a reaction role.
    If sent, also deletes the Discord message.

    Returns:
        200: Deleted successfully
        403: User is not admin
        404: Not found
        500: Server error
    """
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        with ReactionRoleDao() as dao:
            # Get reaction role
            rr = dao.get_by_id(rr_id)

            if not rr or rr.guild_id != int(guild_id):
                return jsonify({
                    "success": False,
                    "message": "Reaction role not found"
                }), 404

            # If sent, delete from Discord
            if rr.is_sent and rr.message_id:
                try:
                    run_sync(http_client.delete_message(
                        int(rr.channel_id),
                        int(rr.message_id)
                    ))
                except Exception as e:
                    logger.warning(f"Failed to delete Discord message {rr.message_id}: {e}")
                    # Continue with database deletion even if Discord deletion fails

            # Delete from database
            success = dao.delete_reaction_role(rr_id)

            if not success:
                return jsonify({
                    "success": False,
                    "message": "Failed to delete reaction role"
                }), 500

        return jsonify({
            "success": True,
            "message": "Reaction role deleted successfully"
        }), 200

    except Exception as e:
        logger.error(f"Error deleting reaction role {rr_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


@reaction_roles_bp.route('/guilds/<guild_id>/reaction-roles/stats', methods=['GET'])
@require_auth
def get_reaction_role_stats(guild_id):
    """
    Get reaction role statistics and limits for a guild.

    Returns:
        200: Stats including total, sent, draft counts and premium limits
        403: User is not admin
        500: Server error
    """
    try:
        # Check admin permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        with ReactionRoleDao() as dao:
            counts = dao.count_guild_reaction_roles(int(guild_id))

        # Get premium tier limits
        max_reaction_roles = PremiumChecker.get_limit(int(guild_id), 'reaction_roles')

        return jsonify({
            "success": True,
            "stats": {
                "total": counts['total'],
                "sent": counts['sent'],
                "draft": counts['draft'],
                "max": max_reaction_roles,
                "remaining": max(0, max_reaction_roles - counts['total'])
            }
        }), 200

    except Exception as e:
        logger.error(f"Error getting reaction role stats for guild {guild_id}: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500
