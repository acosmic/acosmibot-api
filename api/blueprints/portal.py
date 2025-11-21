"""Cross-server portal endpoints"""
import sys
from pathlib import Path
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import GuildDao
from api.services.discord_integration import check_admin_sync

# Ensure bot path is in sys.path for models import
current_dir = Path(__file__).parent.parent.parent
bot_project_path = current_dir.parent / "acosmibot"
if str(bot_project_path) not in sys.path:
    sys.path.insert(0, str(bot_project_path))

from models.settings_manager import SettingsManager

portal_bp = Blueprint('portal', __name__, url_prefix='/api')


@portal_bp.route('/guilds/<guild_id>/portal-config', methods=['GET'])
@require_auth
def get_portal_config(guild_id):
    """Get portal configuration for a guild"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get guild settings from SettingsManager
        try:
            settings_manager = SettingsManager.get_instance()
        except:
            # If settings manager not initialized, create one
            with GuildDao() as guild_dao:
                settings_manager = SettingsManager(guild_dao)

        guild_settings = settings_manager.get_guild_settings(guild_id)
        portal_config = guild_settings.cross_server_portal.dict()

        return jsonify({
            "success": True,
            "config": portal_config
        })

    except Exception as e:
        print(f"Error getting portal config: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@portal_bp.route('/guilds/<guild_id>/portal-config', methods=['PATCH'])
@require_auth
def update_portal_config(guild_id):
    """Update portal configuration for a guild"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get request data
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "message": "Request body is required"
            }), 400

        # Get current settings
        try:
            settings_manager = SettingsManager.get_instance()
        except:
            # If settings manager not initialized, create one
            with GuildDao() as guild_dao:
                settings_manager = SettingsManager(guild_dao)

        guild_settings = settings_manager.get_guild_settings(guild_id)

        # Update portal settings from request
        portal_config = guild_settings.cross_server_portal.dict()
        if 'enabled' in data:
            portal_config['enabled'] = bool(data['enabled'])
        if 'channel_id' in data:
            portal_config['channel_id'] = data['channel_id']
        if 'public_listing' in data:
            portal_config['public_listing'] = bool(data['public_listing'])
        if 'display_name' in data:
            portal_config['display_name'] = data['display_name']
        if 'portal_cost' in data:
            portal_config['portal_cost'] = int(data['portal_cost'])

        # Update the settings dict with the new portal config
        settings_dict = guild_settings.dict()
        settings_dict['cross_server_portal'] = portal_config

        # Save updated settings
        success = settings_manager.update_settings_dict(guild_id, settings_dict)

        if success:
            return jsonify({
                "success": True,
                "message": "Portal configuration updated successfully",
                "config": portal_config
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update portal configuration"
            }), 500

    except Exception as e:
        print(f"Error updating portal config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@portal_bp.route('/guilds/search-portals', methods=['GET'])
@require_auth
def search_portals():
    """Search for guilds with portals enabled"""
    try:
        query = request.args.get('q', '')

        # Get all guilds
        with GuildDao() as guild_dao:
            all_guilds = guild_dao.get_all_guilds()

        # Initialize settings manager
        try:
            settings_manager = SettingsManager.get_instance()
        except:
            settings_manager = SettingsManager(guild_dao)

        results = []
        for guild_entity in all_guilds:
            # Get portal settings from SettingsManager
            guild_settings = settings_manager.get_guild_settings(str(guild_entity.id))
            portal_config = guild_settings.cross_server_portal

            # Check if portals enabled and publicly listed
            if not portal_config.enabled:
                continue
            if not portal_config.public_listing:
                continue
            if not portal_config.channel_id:
                continue

            # Get display name
            display_name = portal_config.display_name or guild_entity.name

            # Check if query matches (case insensitive)
            if query.lower() in display_name.lower():
                results.append({
                    'id': str(guild_entity.id),
                    'name': display_name,
                    'member_count': guild_entity.member_count,
                    'portal_cost': portal_config.portal_cost
                })

        return jsonify({
            "success": True,
            "guilds": results,
            "count": len(results)
        })

    except Exception as e:
        print(f"Error searching portals: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500
