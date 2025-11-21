"""Admin panel endpoints - settings, guilds, audit logs, users"""
import json
import logging
import sys
from pathlib import Path
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.middleware.admin_auth import require_admin, require_super_admin, log_admin_action, check_is_admin
from api.services.dao_imports import (
    AdminUserDao, GlobalSettingsDao, AuditLogDao,
    GuildDao, UserDao
)

# Ensure bot path is in sys.path for models import
current_dir = Path(__file__).parent.parent.parent
bot_project_path = current_dir.parent / "acosmibot"
if str(bot_project_path) not in sys.path:
    sys.path.insert(0, str(bot_project_path))

from models.settings_manager import SettingsManager

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')
logger = logging.getLogger(__name__)


@admin_bp.route('/check', methods=['GET'])
@require_auth
def check_admin_status():
    """Check if the current user is an admin"""
    try:
        is_admin = check_is_admin(request.user_id)
        with AdminUserDao() as admin_dao:
            admin_info = admin_dao.get_admin_by_discord_id(request.user_id) if is_admin else None

        return jsonify({
            "success": True,
            "is_admin": is_admin,
            "admin_info": admin_info
        })
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/settings', methods=['GET'])
@require_auth
@require_admin
def get_global_settings():
    """Get all global bot settings"""
    try:
        with GlobalSettingsDao() as settings_dao:
            grouped_settings = settings_dao.get_all_settings_grouped()

        return jsonify({
            "success": True,
            "settings": grouped_settings
        })
    except Exception as e:
        logger.error(f"Error fetching global settings: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/settings', methods=['POST'])
@require_auth
@require_admin
def update_global_settings():
    """Update multiple global settings"""
    try:
        data = request.get_json()
        if not data or 'settings' not in data:
            return jsonify({
                "success": False,
                "message": "Settings data is required"
            }), 400

        settings_dao = GlobalSettingsDao()

        # Track changes for audit log
        changes = {}

        for setting_key, setting_value in data['settings'].items():
            old_setting = settings_dao.get_setting(setting_key)
            old_value = old_setting['setting_value'] if old_setting else None

            # Update setting
            success = settings_dao.update_setting_value(
                setting_key,
                setting_value,
                updated_by=request.admin_info['discord_id']
            )

            if success:
                changes[setting_key] = {
                    'old_value': old_value,
                    'new_value': setting_value
                }

        # Log the action
        log_admin_action(
            action_type='update_global_settings',
            target_type='settings',
            target_id='bulk_update',
            changes=changes
        )

        return jsonify({
            "success": True,
            "message": "Settings updated successfully",
            "updated_count": len(changes)
        })

    except Exception as e:
        logger.error(f"Error updating global settings: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/settings/<category>', methods=['GET'])
@require_auth
@require_admin
def get_settings_by_category(category):
    """Get settings for a specific category"""
    try:
        valid_categories = ['features', 'rate_limits', 'defaults', 'maintenance']
        if category not in valid_categories:
            return jsonify({
                "success": False,
                "message": f"Invalid category. Must be one of: {', '.join(valid_categories)}"
            }), 400

        settings_dao = GlobalSettingsDao()
        settings = settings_dao.get_settings_by_category(category)

        return jsonify({
            "success": True,
            "category": category,
            "settings": settings
        })
    except Exception as e:
        logger.error(f"Error fetching settings for category {category}: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/guilds', methods=['GET'])
@require_auth
@require_admin
def get_all_guilds_admin():
    """Get all guilds with detailed stats (admin only)"""
    try:
        guild_dao = GuildDao()

        # Get pagination parameters
        limit = min(int(request.args.get('limit', 50)), 100)
        offset = int(request.args.get('offset', 0))
        search = request.args.get('search', '')

        # Get all guilds
        all_guilds = guild_dao.get_all_guilds()

        guilds_data = []
        for guild in all_guilds:
            # Filter by search term if provided
            if search and search.lower() not in guild.name.lower():
                continue

            # Get member count
            member_count = guild_dao.get_active_member_count(guild.id)

            # Get settings from SettingsManager
            try:
                settings_manager = SettingsManager.get_instance()
                guild_settings = settings_manager.get_guild_settings(str(guild.id))
            except:
                # If settings manager not initialized, create one
                with GuildDao() as guild_dao_temp:
                    settings_manager = SettingsManager(guild_dao_temp)
                    guild_settings = settings_manager.get_guild_settings(str(guild.id))

            guilds_data.append({
                'id': str(guild.id),
                'name': guild.name,
                'owner_id': str(guild.owner_id),
                'member_count': member_count,
                'active': guild.active,
                'created_at': guild.created.isoformat() if guild.created else None,
                'last_active': guild.last_active.isoformat() if guild.last_active else None,
                'settings_enabled': {
                    'leveling': guild_settings.leveling.enabled,
                    'ai': guild_settings.ai.enabled,
                    'economy': guild_settings.games.enabled if hasattr(guild_settings.games, 'enabled') else False,
                    'portal': guild_settings.cross_server_portal.enabled
                }
            })

        # Apply pagination
        total_count = len(guilds_data)
        guilds_data = guilds_data[offset:offset + limit]

        return jsonify({
            "success": True,
            "guilds": guilds_data,
            "total": total_count,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"Error fetching all guilds: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/guilds/<guild_id>', methods=['GET'])
@require_auth
@require_admin
def get_guild_details_admin(guild_id):
    """Get detailed information about a specific guild (admin only)"""
    try:
        guild_dao = GuildDao()
        guild = guild_dao.find_by_id(int(guild_id))

        if not guild:
            return jsonify({
                "success": False,
                "message": "Guild not found"
            }), 404

        # Get detailed stats
        member_count = guild_dao.get_active_member_count(guild.id)

        # Get settings from SettingsManager
        try:
            settings_manager = SettingsManager.get_instance()
            guild_settings = settings_manager.get_guild_settings(str(guild.id))
        except:
            # If settings manager not initialized, create one
            with GuildDao() as guild_dao_temp:
                settings_manager = SettingsManager(guild_dao_temp)
            guild_settings = settings_manager.get_guild_settings(str(guild.id))

        guild_data = {
            'id': str(guild.id),
            'name': guild.name,
            'owner_id': str(guild.owner_id),
            'member_count': member_count,
            'active': guild.active,
            'created_at': guild.created.isoformat() if guild.created else None,
            'last_active': guild.last_active.isoformat() if guild.last_active else None,
            'settings': guild_settings.dict()
        }

        return jsonify({
            "success": True,
            "guild": guild_data
        })

    except Exception as e:
        logger.error(f"Error fetching guild details: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/stats/overview', methods=['GET'])
@require_auth
@require_admin
def get_admin_stats_overview():
    """Get overview statistics for admin dashboard"""
    try:

        guild_dao = GuildDao()
        user_dao = UserDao()

        # Get guild stats
        all_guilds = guild_dao.get_all_guilds()
        active_guilds = [g for g in all_guilds if g.active]

        # Get user stats
        total_users = user_dao.get_total_active_users()
        total_messages = user_dao.get_total_messages()
        total_currency = user_dao.get_total_currency()

        # Calculate today's activity (simplified - you may want to add date filtering)
        commands_today = 0  # You'll need to implement command tracking

        overview_stats = {
            'total_guilds': len(all_guilds),
            'active_guilds': len(active_guilds),
            'total_users': total_users,
            'total_messages': total_messages,
            'total_currency': total_currency,
            'commands_today': commands_today,
            'avg_members_per_guild': sum(guild_dao.get_active_member_count(g.id) for g in active_guilds) / len(active_guilds) if active_guilds else 0
        }

        return jsonify({
            "success": True,
            "stats": overview_stats
        })

    except Exception as e:
        logger.error(f"Error fetching admin stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/audit-log', methods=['GET'])
@require_auth
@require_admin
def get_audit_log():
    """Get audit log with pagination and filtering"""
    try:
        audit_dao = AuditLogDao()

        # Get query parameters
        limit = min(int(request.args.get('limit', 100)), 500)
        offset = int(request.args.get('offset', 0))
        action_type = request.args.get('action_type', None)
        admin_id = request.args.get('admin_id', None)
        search = request.args.get('search', None)

        # Fetch logs based on filters
        if action_type:
            logs = audit_dao.get_logs_by_action_type(action_type, limit, offset)
        elif admin_id:
            logs = audit_dao.get_logs_by_admin(admin_id, limit, offset)
        elif search:
            logs = audit_dao.search_logs(search, limit, offset)
        else:
            logs = audit_dao.get_recent_logs(limit, offset)

        total_count = audit_dao.get_log_count()

        return jsonify({
            "success": True,
            "logs": logs,
            "total": total_count,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"Error fetching audit log: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/users', methods=['GET'])
@require_super_admin
def get_admin_users():
    """Get all admin users (super admin only)"""
    try:
        admin_dao = AdminUserDao()
        admins = admin_dao.get_all_admins()

        return jsonify({
            "success": True,
            "admins": admins
        })
    except Exception as e:
        logger.error(f"Error fetching admin users: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@admin_bp.route('/users', methods=['POST'])
@require_super_admin
def create_admin_user():
    """Create a new admin user (super admin only)"""
    try:
        data = request.get_json()

        required_fields = ['discord_id', 'discord_username', 'role']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    "success": False,
                    "message": f"Missing required field: {field}"
                }), 400

        if data['role'] not in ['admin', 'super_admin']:
            return jsonify({
                "success": False,
                "message": "Role must be 'admin' or 'super_admin'"
            }), 400

        admin_dao = AdminUserDao()
        admin_id = admin_dao.create_admin(
            discord_id=data['discord_id'],
            discord_username=data['discord_username'],
            role=data['role'],
            created_by=request.admin_info['discord_id']
        )

        if admin_id:
            # Log the action
            log_admin_action(
                action_type='create_admin_user',
                target_type='admin',
                target_id=data['discord_id'],
                changes={'role': data['role'], 'username': data['discord_username']}
            )

            return jsonify({
                "success": True,
                "message": "Admin user created successfully",
                "admin_id": admin_id
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to create admin user"
            }), 500

    except Exception as e:
        logger.error(f"Error creating admin user: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
