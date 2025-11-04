"""Cross-server portal endpoints"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import GuildDao
from api.services.discord_integration import check_admin_sync

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

        # Get guild settings
        guild_dao = GuildDao()
        settings = guild_dao.get_guild_settings(int(guild_id))

        # Extract portal settings or use defaults
        portal_config = settings.get('cross_server_portal', {
            'enabled': False,
            'channel_id': None,
            'public_listing': True,
            'display_name': None,
            'portal_cost': 1000
        }) if settings else {
            'enabled': False,
            'channel_id': None,
            'public_listing': True,
            'display_name': None,
            'portal_cost': 1000
        }

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
        guild_dao = GuildDao()
        settings = guild_dao.get_guild_settings(int(guild_id)) or {}

        # Initialize portal config if it doesn't exist
        if 'cross_server_portal' not in settings:
            settings['cross_server_portal'] = {
                'enabled': False,
                'channel_id': None,
                'public_listing': True,
                'display_name': None,
                'portal_cost': 1000
            }

        # Update portal settings
        portal_config = settings['cross_server_portal']
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

        # Save updated settings
        success = guild_dao.update_guild_settings(int(guild_id), settings)

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
        guild_dao = GuildDao()
        all_guilds = guild_dao.get_all_guilds()

        results = []
        for guild_entity in all_guilds:
            # Get portal settings
            settings = guild_dao.get_guild_settings(guild_entity.id)
            if not settings:
                continue

            portal_config = settings.get('cross_server_portal', {})

            # Check if portals enabled and publicly listed
            if not portal_config.get('enabled', False):
                continue
            if not portal_config.get('public_listing', True):
                continue
            if not portal_config.get('channel_id'):
                continue

            # Get display name
            display_name = portal_config.get('display_name') or guild_entity.name

            # Check if query matches (case insensitive)
            if query.lower() in display_name.lower():
                results.append({
                    'id': str(guild_entity.id),
                    'name': display_name,
                    'member_count': guild_entity.member_count,
                    'portal_cost': portal_config.get('portal_cost', 1000)
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
