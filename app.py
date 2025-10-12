import json
import sys
from pathlib import Path
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps
import jwt
import os
import atexit
import asyncio



# Set up path to bot project
current_dir = Path(__file__).parent
bot_project_path = current_dir.parent / "acosmibot"
sys.path.insert(0, str(bot_project_path))

from Dao.GuildDao import GuildDao
# Import services and models
from discord_oauth import DiscordOAuthService
from models.settings_manager import SettingsManager
from models.api_models import (
    UpdateLevelingSettingsRequest,
    UpdateRoleSettingsRequest,
    # UpdateEconomySettingsRequest,
    UpdateAISettingsRequest,
    RoleMappingRequest,
    BulkRoleMappingRequest,
    FullGuildSettingsRequest
)
from models.base_models import RoleCacheEntry
from discord_integration import check_admin_sync, get_channels_sync, list_guilds_sync, http_client, run_sync

# # Add this helper function for the new REST client
# def run_async_rest(coro):
#     """Run async function for REST client"""
#     return run_async(coro)

from datetime import datetime
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.FileHandler('api.log'),
        logging.StreamHandler()  # This adds console output
    ]
)

logger = logging.getLogger(__name__)
load_dotenv()

app = Flask(__name__)
CORS(app, origins=['https://acosmibot.com', 'https://api.acosmibot.com'], supports_credentials=True)
app.secret_key = os.getenv('JWT_SECRET')

oauth_service = DiscordOAuthService()

# # # Cleanup on shutdown
# atexit.register(lambda: run_async(cleanup_discord_client()))
# #

def get_settings_manager():
    """Get settings manager instance"""
    from Dao.GuildDao import GuildDao
    guild_dao = GuildDao()
    return SettingsManager(guild_dao)


def require_auth(f):
    """JWT authentication decorator"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing authorization header'}), 401

        token = auth_header.split(' ')[1]

        try:
            payload = jwt.decode(token, os.getenv('JWT_SECRET'), algorithms=['HS256'])
            request.user_id = payload['user_id']
            return f(*args, **kwargs)
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401

    return decorated_function


# Basic endpoints
@app.route('/')
def hello():
    return jsonify({'message': 'Acosmibot API is working!'})


@app.route('/test-import')
def test_import():
    try:
        from Dao.UserDao import UserDao
        from logger import AppLogger

        return jsonify({
            'status': 'success',
            'message': 'Successfully imported bot modules!',
            'imports': ['UserDao', 'AppLogger']
        })
    except ImportError as e:
        return jsonify({
            'status': 'error',
            'message': f'Import failed: {str(e)}'
        }), 500


@app.route('/test-db')
def test_db():
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        return jsonify({
            'status': 'success',
            'message': 'Database connection working!',
            'dao_initialized': True
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Database error: {str(e)}'
        }), 500


# Auth endpoints
@app.route('/auth/login')
def login():
    auth_url = oauth_service.get_auth_url()
    return redirect(auth_url)


@app.route('/auth/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'No authorization code received'}), 400

    # Exchange code for token
    token_data = oauth_service.exchange_code(code)
    if not token_data:
        return jsonify({'error': 'Failed to exchange code'}), 400

    # Get user info
    user_info = oauth_service.get_user_info(token_data['access_token'])
    if not user_info:
        return jsonify({'error': 'Failed to get user info'}), 400

    # Create JWT
    jwt_token = oauth_service.create_jwt(user_info)

    # Redirect to user dashboard on main website with token
    user_dashboard_url = f"https://acosmibot.com/user-dashboard.html?token={jwt_token}"
    return redirect(user_dashboard_url)


@app.route('/auth/me')
def get_current_user():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Missing authorization header'}), 401

    token = auth_header.split(' ')[1]

    try:
        payload = jwt.decode(token, os.getenv('JWT_SECRET'), algorithms=['HS256'])

        # Get fresh user data from your database
        from Dao.UserDao import UserDao
        user_dao = UserDao()
        user = user_dao.get_user(int(payload['user_id']))

        if user:
            # Safely format dates - handle both datetime objects and strings
            def safe_date_format(date_field, format_str='%Y-%m-%d'):
                if not date_field:
                    return None
                if isinstance(date_field, str):
                    return date_field  # Already a string
                return date_field.strftime(format_str)

            def safe_datetime_format(date_field, format_str='%Y-%m-%d %H:%M:%S'):
                if not date_field:
                    return None
                if isinstance(date_field, str):
                    return date_field  # Already a string
                return date_field.strftime(format_str)

            # Get user's Discord avatar
            avatar_url = user.avatar_url or f"https://cdn.discordapp.com/embed/avatars/{int(payload['user_id']) % 5}.png"

            return jsonify({
                'id': user.id,
                'username': user.discord_username,
                'global_name': user.global_name,
                'avatar': avatar_url,
                'level': user.global_level,
                'currency': user.total_currency,
                'total_messages': user.total_messages,
                'total_reactions': user.total_reactions,
                'global_exp': user.global_exp,
                'account_created': safe_date_format(user.account_created),
                'first_seen': safe_date_format(user.first_seen),
                'last_seen': safe_datetime_format(user.last_seen)
            })
        else:
            return jsonify({'error': 'User not found in database'}), 404

    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/guilds/<guild_id>/stats-db', methods=['GET'])
@require_auth
def get_guild_stats_db_only(guild_id):
    """Get guild statistics using database-only approach"""
    try:
        from Dao.GuildDao import GuildDao
        from Dao.GuildUserDao import GuildUserDao

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Get guild record
        guild_record = guild_dao.find_by_id(int(guild_id))
        if not guild_record:
            return jsonify({
                "success": False,
                "message": "Guild not found"
            }), 404

        # Get basic stats using direct SQL queries
        try:
            # Count active members
            active_members_sql = "SELECT COUNT(*) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            active_members_result = guild_dao.execute_query(active_members_sql, (int(guild_id),))
            active_members = active_members_result[0][0] if active_members_result else 0

            # Total messages in guild
            total_messages_sql = "SELECT SUM(messages_sent) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            total_messages_result = guild_dao.execute_query(total_messages_sql, (int(guild_id),))
            total_messages = total_messages_result[0][0] if total_messages_result and total_messages_result[0][0] else 0

            # Total exp in guild
            total_exp_sql = "SELECT SUM(exp) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            total_exp_result = guild_dao.execute_query(total_exp_sql, (int(guild_id),))
            total_exp = total_exp_result[0][0] if total_exp_result and total_exp_result[0][0] else 0

            # Highest level
            highest_level_sql = "SELECT MAX(level) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            highest_level_result = guild_dao.execute_query(highest_level_sql, (int(guild_id),))
            highest_level = highest_level_result[0][0] if highest_level_result and highest_level_result[0][0] else 0

            # Average level
            avg_level_sql = "SELECT AVG(level) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            avg_level_result = guild_dao.execute_query(avg_level_sql, (int(guild_id),))
            avg_level = round(avg_level_result[0][0], 1) if avg_level_result and avg_level_result[0][0] else 0

        except Exception as e:
            # Fallback values if queries fail
            active_members = guild_record.member_count or 0
            total_messages = 0
            total_exp = 0
            highest_level = 0
            avg_level = 0

        guild_stats = {
            "guild_id": guild_id,
            "guild_name": guild_record.name,
            "member_count": guild_record.member_count or 0,
            "total_active_members": active_members,
            "total_messages": total_messages,
            "total_exp_distributed": total_exp,
            "highest_level": highest_level,
            "avg_level": avg_level,
            "last_activity": guild_record.last_active,
            "method": "database_only"
        }

        return jsonify({
            "success": True,
            "data": guild_stats
        })

    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "message": "Failed to get guild statistics",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# @app.route('/api/guilds/<guild_id>/leaderboard/level-db', methods=['GET'])
# @require_auth
# def get_guild_level_leaderboard_db(guild_id):
#     """Get level leaderboard for a specific guild using database-only approach"""
#     try:
#         from Dao.GuildDao import GuildDao
#
#         guild_dao = GuildDao()
#         limit = min(int(request.args.get('limit', 10)), 50)
#
#         # Direct SQL query for leaderboard
#         sql = """
#               SELECT gu.user_id, u.discord_username, gu.level, gu.exp, gu.messages_sent
#               FROM GuildUsers gu
#                        LEFT JOIN Users u ON gu.user_id = u.id
#               WHERE gu.guild_id = %s \
#                 AND gu.is_active = TRUE
#               ORDER BY gu.level DESC, gu.exp DESC
#                   LIMIT %s \
#               """
#
#         results = guild_dao.execute_query(sql, (int(guild_id), limit))
#
#         leaderboard = []
#         for i, row in enumerate(results):
#             user_id, username, level, exp, messages = row
#             leaderboard.append({
#                 "rank": i + 1,
#                 "user_id": user_id,
#                 "username": username or f"User {user_id}",
#                 "level": level or 0,
#                 "exp": exp or 0,
#                 "messages": messages or 0
#             })
#
#         return jsonify({
#             "success": True,
#             "data": leaderboard
#         })
#
#     except Exception as e:
#         import traceback
#         return jsonify({
#             "success": False,
#             "message": "Failed to get leaderboard",
#             "error": str(e),
#             "traceback": traceback.format_exc()
#         }), 500


@app.route('/api/guilds/<guild_id>/leaderboard/messages-db', methods=['GET'])
@require_auth
def get_guild_messages_leaderboard_db(guild_id):
    """Get messages leaderboard for a specific guild using database-only approach"""
    try:
        from Dao.GuildDao import GuildDao

        guild_dao = GuildDao()
        limit = min(int(request.args.get('limit', 10)), 50)

        # Direct SQL query for messages leaderboard
        sql = """
              SELECT gu.user_id, u.discord_username, gu.messages_sent, gu.level, gu.exp
              FROM GuildUsers gu
                       LEFT JOIN Users u ON gu.user_id = u.id
              WHERE gu.guild_id = %s \
                AND gu.is_active = TRUE
              ORDER BY gu.messages_sent DESC
                  LIMIT %s \
              """

        results = guild_dao.execute_query(sql, (int(guild_id), limit))

        leaderboard = []
        for i, row in enumerate(results):
            user_id, username, messages, level, exp = row
            leaderboard.append({
                "rank": i + 1,
                "user_id": user_id,
                "username": username or f"User {user_id}",
                "messages": messages or 0,
                "level": level or 0,
                "exp": exp or 0
            })

        return jsonify({
            "success": True,
            "data": leaderboard
        })

    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "message": "Failed to get leaderboard",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

# Guild Management Endpoints
# @app.route('/api/user/guilds', methods=['GET'])
# @require_auth
# def get_user_guilds():
#     logger.info(f"User {request.user_id} requesting guilds")
#     try:
#         logger.info("Calling get_user_manageable_guilds_sync...")
#         user_guilds = get_user_manageable_guilds_sync(request.user_id)
#         logger.info(f"Returned {len(user_guilds)} guilds: {user_guilds}")
#
#         return jsonify({
#             "success": True,
#             "guilds": user_guilds
#         })
#     except Exception as e:
#         logger.error(f"Error getting user guilds: {e}")
#         import traceback
#         traceback.print_exc()
#         return jsonify({
#             "success": False,
#             "message": str(e),
#             "error": str(e)
#         }), 500

@app.route('/api/user/guilds', methods=['GET'])
@require_auth
def get_user_guilds():
    """Get guilds from database instead of Discord API"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        from Dao.GuildDao import GuildDao

        guild_user_dao = GuildUserDao()
        guild_dao = GuildDao()

        # Get guilds where user is a member
        user_guilds = []

        # Query database for guilds this user is in
        sql = """
              SELECT DISTINCT g.id, g.name, g.member_count, g.owner_id
              FROM Guilds g
                       JOIN GuildUsers gu ON g.id = gu.guild_id
              WHERE gu.user_id = %s \
                AND gu.is_active = TRUE \
              """

        guild_dao = GuildDao()
        results = guild_dao.execute_query(sql, (int(request.user_id),))

        if results:
            for row in results:
                guild_id, guild_name, member_count, owner_id = row
                is_owner = str(owner_id) == request.user_id

                user_guilds.append({
                    "id": str(guild_id),
                    "name": guild_name,
                    "member_count": member_count,
                    "owner": is_owner,
                    "permissions": ["administrator"] if is_owner else ["member"]
                })

        logger.info(f"Found {len(user_guilds)} guilds for user {request.user_id}")
        return jsonify({
            "success": True,
            "guilds": user_guilds
        })

    except Exception as e:
        logger.error(f"Error getting guilds from database: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500


#
# @app.route('/api/guilds/<guild_id>/config-db', methods=['GET'])
# @require_auth
# def get_guild_config_database_only(guild_id):
#     """Get guild config using database-only approach (bypass Discord API issues)"""
#     try:
#         from Dao.GuildDao import GuildDao
#         from Dao.GuildUserDao import GuildUserDao
#
#         guild_dao = GuildDao()
#         guild_user_dao = GuildUserDao()
#
#         # Get guild record
#         guild_record = guild_dao.find_by_id(int(guild_id))
#         if not guild_record or not guild_record.active:
#             return jsonify({
#                 "success": False,
#                 "message": "Guild not found or inactive"
#             }), 404
#
#         # Check permissions
#         is_owner = str(guild_record.owner_id) == request.user_id
#         guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
#         is_active_member = guild_user is not None and guild_user.is_active
#
#         if not (is_owner or is_active_member):
#             return jsonify({
#                 "success": False,
#                 "message": "Access denied"
#             }), 403
#
#         # Get settings
#         settings_manager = get_settings_manager()
#         settings = settings_manager.get_guild_settings(guild_id)
#
#         # Build config using ONLY attributes that exist
#         config_data = {
#             "guild_id": guild_id,
#             "guild_name": guild_record.name,
#             "member_count": guild_record.member_count or 0,
#             "owner_id": str(guild_record.owner_id),
#             "settings": {
#                 "leveling": {
#                     "enabled": settings.leveling.enabled,
#                     "exp_per_message": settings.leveling.exp_per_message,
#                     "base_exp": settings.leveling.base_exp,
#                     "exp_multiplier": settings.leveling.exp_multiplier,
#                     "exp_growth_factor": settings.leveling.exp_growth_factor,
#                     "exp_cooldown_seconds": settings.leveling.exp_cooldown_seconds,
#                     "max_level": settings.leveling.max_level,
#                     "level_up_announcements": settings.leveling.level_up_announcements,
#                     "announcement_channel_id": settings.leveling.announcement_channel_id
#                 },
#                 "roles": {
#                     # Only use attributes that were shown to exist
#                     "enabled": settings.roles.enabled,
#                     "mode": settings.roles.mode,
#                     "remove_previous_roles": settings.roles.remove_previous_roles,
#                     "max_level_tracked": settings.roles.max_level_tracked,
#                     "role_announcement": settings.roles.role_announcement,
#                     "role_announcement_message": settings.roles.role_announcement_message,
#                     "role_mappings": dict(settings.roles.role_mappings),
#                     "role_cache": {role_id: role.dict() for role_id, role in settings.roles.role_cache.items()}
#                 }
#             },
#             "available_roles": [],  # Discord API not available
#             "available_channels": [],  # Discord API not available
#             "permissions": {
#                 "is_owner": is_owner,
#                 "can_configure": is_owner or is_active_member,
#                 "can_view_stats": True,
#                 "method": "database_only"
#             }
#         }
#
#         return jsonify({
#             "success": True,
#             "data": config_data
#         })
#
#     except Exception as e:
#         import traceback
#         return jsonify({
#             "success": False,
#             "message": "Internal server error",
#             "error": str(e),
#             "traceback": traceback.format_exc()
#         }), 500

# @app.route('/api/guilds/<guild_id>/config', methods=['GET'])
# @require_auth
# def get_guild_config(guild_id):
#     """Get complete guild configuration"""
#     try:
#         # Check permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to manage this server",
#                 "error_code": "insufficient_permissions"
#             }), 403
#
#         # Get guild info
#         guild_info = get_guild_info_sync(guild_id)
#         if not guild_info:
#             return jsonify({
#                 "success": False,
#                 "message": "Guild not found",
#                 "error_code": "guild_not_found"
#             }), 404
#
#         # Get settings
#         settings_manager = get_settings_manager()
#         settings = settings_manager.get_guild_settings(guild_id)
#
#         # Get Discord data
#         available_roles, available_channels = get_discord_guild_data_sync(guild_id)
#
#         # Build role mappings response
#         role_mappings = []
#         for level, role_ids in settings.roles.role_mappings.items():
#             roles = []
#             for role_id in role_ids:
#                 # Find role in available roles
#                 role_info = next((r for r in available_roles if r.id == role_id), None)
#                 if role_info:
#                     roles.append(role_info.dict())
#                 else:
#                     # Use cached role info
#                     cached_role = settings.roles.role_cache.get(role_id)
#                     if cached_role:
#                         roles.append({
#                             "id": role_id,
#                             "name": cached_role.name,
#                             "color": cached_role.color,
#                             "position": cached_role.position,
#                             "managed": cached_role.managed,
#                             "mentionable": True,
#                             "hoist": False
#                         })
#
#             if roles:  # Only include levels that have valid roles
#                 role_mappings.append({
#                     "level": int(level),
#                     "roles": roles
#                 })
#
#         response_data = {
#             "guild_id": guild_id,
#             "guild_name": guild_info["name"],
#             "settings": settings.dict(),
#             "available_roles": [role.dict() for role in available_roles],
#             "available_channels": [channel.dict() for channel in available_channels],
#             "role_mappings": role_mappings
#         }
#
#         return jsonify({
#             "success": True,
#             "data": response_data
#         })
#
#     except Exception as e:
#         print(f"Error getting guild config: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Failed to get guild configuration",
#             "error": str(e)
#         }), 500

@app.route('/api/guilds/<guild_id>/leveling', methods=['PUT'])
@require_auth
def update_leveling_settings(guild_id):
    """Update leveling settings"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Validate request data
        try:
            updates = UpdateLevelingSettingsRequest(**request.json)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Invalid request data",
                "error": str(e)
            }), 400

        # Get current settings
        settings_manager = get_settings_manager()
        settings = settings_manager.get_guild_settings(guild_id)
        settings_dict = settings.dict()

        # Update leveling settings
        if 'leveling' not in settings_dict:
            settings_dict['leveling'] = {}

        settings_dict['leveling'].update({
            'enabled': updates.enabled,
            'exp_per_message': updates.exp_per_message,
            'exp_cooldown_seconds': updates.exp_cooldown_seconds,
            'level_up_announcements': updates.level_up_announcements,
            'announcement_channel_id': updates.announcement_channel_id
        })

        # Save updated settings
        success = settings_manager.guild_dao.update_guild_settings(int(guild_id), settings_dict)

        if success:
            return jsonify({
                "success": True,
                "message": "Leveling settings updated successfully",
                "data": settings_dict['leveling']
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update leveling settings"
            }), 500

    except Exception as e:
        print(f"Error updating leveling settings: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500

#
# @app.route('/api/guilds/<guild_id>/roles', methods=['PUT'])
# @require_auth
# def update_role_settings(guild_id):
#     """Update role settings"""
#     try:
#         # Check permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to manage this server"
#             }), 403
#
#         # Validate request data
#         try:
#             updates = UpdateRoleSettingsRequest(**request.json)
#         except Exception as e:
#             return jsonify({
#                 "success": False,
#                 "message": "Invalid request data",
#                 "error": str(e)
#             }), 400
#
#         # Update settings
#         settings_manager = get_settings_manager()
#         success = settings_manager.update_role_settings(guild_id, updates)
#
#         if success:
#             return jsonify({
#                 "success": True,
#                 "message": "Role settings updated successfully"
#             })
#         else:
#             return jsonify({
#                 "success": False,
#                 "message": "Failed to update role settings"
#             }), 500
#
#     except Exception as e:
#         print(f"Error updating role settings: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Internal server error",
#             "error": str(e)
#         }), 500

#
# @app.route('/api/guilds/<guild_id>/role-mappings', methods=['POST'])
# @require_auth
# def update_role_mapping(guild_id):
#     """Add or update role mapping for a specific level"""
#     try:
#         # Check permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to manage this server"
#             }), 403
#
#         # Validate request data
#         try:
#             mapping_request = RoleMappingRequest(**request.json)
#         except Exception as e:
#             return jsonify({
#                 "success": False,
#                 "message": "Invalid request data",
#                 "error": str(e)
#             }), 400
#
#         # Get available roles to validate and cache role info
#         available_roles, _ = get_discord_guild_data_sync(guild_id)
#         role_cache = {}
#
#         for role_id in mapping_request.role_ids:
#             # Find role in available roles
#             role_info = next((r for r in available_roles if r.id == role_id), None)
#             if role_info:
#                 # Check if role can be assigned by bot
#                 if role_info.managed:
#                     return jsonify({
#                         "success": False,
#                         "message": f"Role '{role_info.name}' is managed by Discord and cannot be assigned"
#                     }), 400
#
#                 role_cache[role_id] = RoleCacheEntry(
#                     name=role_info.name,
#                     color=role_info.color,
#                     position=role_info.position,
#                     last_verified=datetime.now(),
#                     exists=True,
#                     managed=role_info.managed
#                 )
#             else:
#                 return jsonify({
#                     "success": False,
#                     "message": f"Role {role_id} not found in server"
#                 }), 400
#
#         # Update role mapping
#         settings_manager = get_settings_manager()
#         success = settings_manager.update_role_mapping(
#             guild_id,
#             mapping_request.level,
#             mapping_request.role_ids,
#             role_cache
#         )
#
#         if success:
#             return jsonify({
#                 "success": True,
#                 "message": f"Role mapping updated for level {mapping_request.level}"
#             })
#         else:
#             return jsonify({
#                 "success": False,
#                 "message": "Failed to update role mapping"
#             }), 500
#
#     except Exception as e:
#         print(f"Error updating role mapping: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Internal server error",
#             "error": str(e)
#         }), 500

#
# @app.route('/api/guilds/<guild_id>/role-mappings/<int:level>', methods=['DELETE'])
# @require_auth
# def delete_role_mapping(guild_id, level):
#     """Delete role mapping for a specific level"""
#     try:
#         # Check permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to manage this server"
#             }), 403
#
#         # Delete role mapping
#         settings_manager = get_settings_manager()
#         success = settings_manager.delete_role_mapping(guild_id, level)
#
#         if success:
#             return jsonify({
#                 "success": True,
#                 "message": f"Role mapping deleted for level {level}"
#             })
#         else:
#             return jsonify({
#                 "success": False,
#                 "message": "Failed to delete role mapping"
#             }), 500
#
#     except Exception as e:
#         print(f"Error deleting role mapping: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Internal server error",
#             "error": str(e)
#         }), 500

#
# @app.route('/api/guilds/<guild_id>/roles', methods=['GET'])
# @require_auth
# def get_guild_roles(guild_id):
#     """Get available roles for the guild"""
#     try:
#         # Check permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to manage this server"
#             }), 403
#
#         # Get Discord roles
#         available_roles, _ = get_discord_guild_data_sync(guild_id)
#
#         # Filter out @everyone and managed roles for assignment
#         assignable_roles = [
#             role for role in available_roles
#             if role.name != "@everyone" and not role.managed
#         ]
#
#         return jsonify({
#             "success": True,
#             "roles": [role.dict() for role in assignable_roles]
#         })
#
#     except Exception as e:
#         print(f"Error getting guild roles: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Internal server error",
#             "error": str(e)
#         }), 500

#
# @app.route('/api/guilds/<guild_id>/channels', methods=['GET'])
# @require_auth
# def get_guild_channels(guild_id):
#     """Get available text channels for the guild"""
#     try:
#         # Check permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to manage this server"
#             }), 403
#
#         # Get Discord channels
#         _, available_channels = get_discord_guild_data_sync(guild_id)
#
#         # Filter to only text-based channels
#         text_channels = [
#             channel for channel in available_channels
#             if channel.is_text_based()
#         ]
#
#         return jsonify({
#             "success": True,
#             "channels": [channel.dict() for channel in text_channels]
#         })
#
#     except Exception as e:
#         print(f"Error getting guild channels: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Internal server error",
#             "error": str(e)
#         }), 500


# User and statistics endpoints
@app.route('/api/user/<int:user_id>')
def get_user_info(user_id):
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()
        user = user_dao.get_user(user_id)

        if user:
            # Safe date formatting function
            def safe_date_format(date_field, format_str='%Y-%m-%d'):
                if not date_field:
                    return None
                if isinstance(date_field, str):
                    return date_field
                return date_field.strftime(format_str)

            def safe_datetime_format(date_field, format_str='%Y-%m-%d %H:%M:%S'):
                if not date_field:
                    return None
                if isinstance(date_field, str):
                    return date_field
                return date_field.strftime(format_str)

            return jsonify({
                'id': user.id,
                'username': user.discord_username,
                'global_name': user.global_name,
                'avatar_url': user.avatar_url,
                'level': user.global_level,
                'currency': user.total_currency,
                'total_messages': user.total_messages,
                'total_reactions': user.total_reactions,
                'global_exp': user.global_exp,
                'account_created': safe_date_format(user.account_created),
                'first_seen': safe_date_format(user.first_seen),
                'last_seen': safe_datetime_format(user.last_seen)
            })
        else:
            return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Global leaderboards
@app.route('/api/leaderboard/currency')
def get_currency_leaderboard():
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        limit = min(int(request.args.get('limit', 10)), 50)  # Default 10, max 50
        top_users = user_dao.get_top_users_by_currency(limit)

        return jsonify(top_users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/leaderboard/messages')
def get_messages_leaderboard():
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = user_dao.get_top_users_by_messages(limit)

        return jsonify(top_users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/leaderboard/level')
def get_level_leaderboard():
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = user_dao.get_top_users_by_global_level(limit)

        return jsonify(top_users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# @app.route('/api/leaderboard/guild/level')
# def get_guild_level_leaderboard():
#     try:
#         from Dao.GuildUserDao import GuildUserDao
#         guild_user_dao = GuildUserDao()
#
#         limit = min(int(request.args.get('limit', 10)), 50)
#         top_users = guild_user_dao.get_top_users_by_level(limit)
#
#         return jsonify(top_users)
#     except Exception as e:
#         return jsonify({'error': str(e)}), 500

# User rankings
@app.route('/api/user/<int:user_id>/rank/currency')
def get_user_currency_rank(user_id):
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        user_rank = user_dao.get_user_rank_by_currency(user_id)
        if user_rank:
            return jsonify({
                'rank': user_rank[-1],  # Last column is the rank
                'currency': user_rank[7]  # total_currency column
            })
        else:
            return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/user/<int:user_id>/rank/exp')
def get_user_exp_rank(user_id):
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        user_rank = user_dao.get_user_rank_by_global_exp(user_id)
        if user_rank:
            return jsonify({
                'rank': user_rank[-1],  # Last column is the rank
                'exp': user_rank[5],  # global_exp column
                'level': user_rank[6]  # global_level column
            })
        else:
            return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Game statistics
@app.route('/api/user/<int:user_id>/games')
def get_user_game_stats(user_id):
    try:
        from Dao.GamesDao import GamesDao
        games_dao = GamesDao()

        # Get user's game statistics
        game_stats = games_dao.get_user_game_stats(user_id)

        if game_stats:
            total_games = 0
            total_wins = 0
            total_losses = 0

            for game_type, stats in game_stats.items():
                if isinstance(stats, dict):
                    total_games += stats.get('total_games', 0)

            win_rate = (total_wins / total_games * 100) if total_games > 0 else 0

            return jsonify({
                'total_games': total_games,
                'wins': total_wins,
                'losses': total_losses,
                'win_rate': round(win_rate, 1),
                'by_game_type': game_stats
            })
        else:
            return jsonify({
                'total_games': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'by_game_type': {}
            })

    except ImportError:
        # GamesDao not available
        return jsonify({
            'total_games': 0,
            'wins': 0,
            'losses': 0,
            'win_rate': 0,
            'by_game_type': {}
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Global statistics
@app.route('/api/stats/global')
def get_global_stats():
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()

        stats = {
            'total_users': user_dao.get_total_active_users(),
            'total_messages': user_dao.get_total_messages(),
            'total_reactions': user_dao.get_total_reactions(),
            'total_currency': user_dao.get_total_currency(),
            'total_exp': user_dao.get_total_global_exp()
        }

        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# User's guild information
@app.route('/api/user/<int:user_id>/guilds')
def get_user_guilds_simple(user_id):
    try:
        from Dao.GuildUserDao import GuildUserDao
        from Dao.GuildDao import GuildDao

        guild_user_dao = GuildUserDao()
        guild_dao = GuildDao()

        # Get user's guild memberships
        guild_users = guild_user_dao.get_user_guilds(user_id) if hasattr(guild_user_dao, 'get_user_guilds') else []

        guilds_info = []
        for guild_user in guild_users:
            if guild_user.is_active:
                guild = guild_dao.get_guild(guild_user.guild_id)
                if guild:
                    guilds_info.append({
                        'id': guild.id,
                        'name': guild.name,
                        'member_count': guild.member_count,
                        'user_level': guild_user.level,
                        'user_currency': guild_user.currency,
                        'user_messages': guild_user.messages_sent,
                        'user_reactions': guild_user.reactions_sent
                    })

        return jsonify(guilds_info)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Utility endpoints
@app.route('/api/endpoints')
def list_endpoints():
    """List all available API endpoints"""
    endpoints = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint != 'static':
            endpoints.append({
                'endpoint': rule.rule,
                'methods': list(rule.methods - {'HEAD', 'OPTIONS'})
            })
    return jsonify(endpoints)


@app.route('/bot/invite')
def get_bot_invite():
    """Bot invite coming soon message"""
    return jsonify({
        'message': 'Bot invite feature coming soon!',
        'status': 'coming_soon',
        'eta': 'We are actively developing the bot. Public invites will be available soon.',
        'contact': 'Contact Acosmic on Discord for more info!'
    })

#
# @app.route('/api/guilds/<guild_id>/stats', methods=['GET'])
# @require_auth
# def get_guild_stats(guild_id):
#     """Get guild statistics and overview"""
#     try:
#         # Check if user is in the guild (don't need admin for stats viewing)
#         user_guilds = get_user_manageable_guilds_sync(request.user_id)
#         user_in_guild = any(guild['id'] == guild_id for guild in user_guilds)
#
#         if not user_in_guild:
#             # Also check if user is just a member (you'll need to implement this)
#             # For now, we'll allow anyone with a valid token
#             pass
#
#         from Dao.GuildDao import GuildDao
#         from Dao.GuildUserDao import GuildUserDao
#
#         guild_dao = GuildDao()
#         guild_user_dao = GuildUserDao()
#
#         # Get basic guild info
#         guild = guild_dao.find_by_id(int(guild_id))
#         if not guild:
#             return jsonify({
#                 "success": False,
#                 "message": "Guild not found"
#             }), 404
#
#         # Get guild statistics
#         guild_stats = {
#             "guild_id": guild_id,
#             "guild_name": guild.name,
#             "member_count": guild.member_count,
#             "total_active_members": guild_user_dao.get_active_member_count(int(guild_id)),
#             "total_messages": guild_user_dao.get_total_messages_in_guild(int(guild_id)),
#             "total_exp_distributed": guild_user_dao.get_total_exp_in_guild(int(guild_id)),
#             "highest_level": guild_user_dao.get_highest_level_in_guild(int(guild_id)),
#             "avg_level": guild_user_dao.get_average_level_in_guild(int(guild_id)),
#             "last_activity": guild.last_active
#         }
#
#         return jsonify({
#             "success": True,
#             "data": guild_stats
#         })
#
#     except Exception as e:
#         print(f"Error getting guild stats: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Failed to get guild statistics",
#             "error": str(e)
#         }), 500


@app.route('/api/guilds/<guild_id>/leaderboard/level', methods=['GET'])
@require_auth
def get_guild_level_leaderboard(guild_id):
    """Get level leaderboard for a specific guild"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = guild_user_dao.get_top_users_by_guild_level(int(guild_id), limit)

        return jsonify({
            "success": True,
            "data": top_users
        })

    except Exception as e:
        print(f"Error getting guild level leaderboard: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get leaderboard",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/leaderboard/messages', methods=['GET'])
@require_auth
def get_guild_messages_leaderboard(guild_id):
    """Get messages leaderboard for a specific guild"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = guild_user_dao.get_top_users_by_messages_in_guild(int(guild_id), limit)

        return jsonify({
            "success": True,
            "data": top_users
        })

    except Exception as e:
        print(f"Error getting guild messages leaderboard: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get leaderboard",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/user/<int:user_id>/stats', methods=['GET'])
@require_auth
def get_user_guild_stats(guild_id, user_id):
    """Get specific user's stats in a guild"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        # Get user's stats in this guild
        guild_user = guild_user_dao.get_guild_user(user_id, int(guild_id))
        if not guild_user:
            return jsonify({
                "success": False,
                "message": "User not found in this guild"
            }), 404

        # Get user's rank in guild
        user_rank = guild_user_dao.get_user_rank_in_guild(user_id, int(guild_id))

        user_stats = {
            "user_id": user_id,
            "guild_id": guild_id,
            "level": guild_user.level,
            "exp": guild_user.exp,
            "messages": guild_user.messages_sent,
            "reactions": guild_user.reactions_sent,
            "currency": guild_user.currency,
            "rank": user_rank,
            "joined_at": guild_user.joined_at,
            "last_active": guild_user.last_active
        }

        return jsonify({
            "success": True,
            "data": user_stats
        })

    except Exception as e:
        print(f"Error getting user guild stats: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get user stats",
            "error": str(e)
        }), 500

#
# @app.route('/api/guilds/<guild_id>/permissions', methods=['GET'])
# @require_auth
# def get_user_guild_permissions(guild_id):
#     """Get user's permissions in a specific guild"""
#     try:
#         # Check what permissions the user has
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#
#         # Get guild info to check ownership
#         guild_info = get_guild_info_sync(guild_id)
#         is_owner = guild_info and guild_info.get('owner_id') == request.user_id
#
#         permissions = {
#             "guild_id": guild_id,
#             "user_id": request.user_id,
#             "is_owner": is_owner,
#             "has_admin": has_admin,
#             "can_configure_bot": has_admin,
#             "can_view_stats": True  # Everyone can view stats if they're in the guild
#         }
#
#         return jsonify({
#             "success": True,
#             "data": permissions
#         })
#
#     except Exception as e:
#         print(f"Error getting user permissions: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Failed to get permissions",
#             "error": str(e)
#         }), 500

#
# @app.route('/api/guilds/<guild_id>/bot-status', methods=['GET'])
# @require_auth
# def get_bot_status_in_guild(guild_id):
#     """Get bot's status and permissions in the guild"""
#     try:
#         # Check user has admin permissions
#         has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
#         if not has_admin:
#             return jsonify({
#                 "success": False,
#                 "message": "You don't have permission to view bot status"
#             }), 403
#
#         # Get bot's permissions and status
#         guild_info = get_guild_info_sync(guild_id)
#         if not guild_info:
#             return jsonify({
#                 "success": False,
#                 "message": "Guild not found or bot not in guild"
#             }), 404
#
#         # You'll need to implement getting bot permissions
#         # This is a placeholder for bot status
#         bot_status = {
#             "guild_id": guild_id,
#             "bot_in_guild": True,
#             "can_manage_roles": True,  # Check actual permissions
#             "can_send_messages": True,
#             "can_embed_links": True,
#             "highest_role_position": 10,  # Get actual position
#             "missing_permissions": [],  # List any missing permissions
#             "features_available": {
#                 "leveling": True,
#                 "role_assignment": True,
#                 "economy": True,
#                 "announcements": True
#             }
#         }
#
#         return jsonify({
#             "success": True,
#             "data": bot_status
#         })
#
#     except Exception as e:
#         print(f"Error getting bot status: {e}")
#         return jsonify({
#             "success": False,
#             "message": "Failed to get bot status",
#             "error": str(e)
#         }), 500


# Test endpoints (remove in production)
@app.route('/test/create-token/<int:user_id>')
def create_test_token(user_id):
    """Create a test JWT token for a specific user ID (REMOVE IN PRODUCTION!)"""
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()
        user = user_dao.get_user(user_id)

        if not user:
            return jsonify({'error': 'User not found in database'}), 404

        # Create test user data for JWT
        test_user_data = {
            'id': str(user.id),
            'username': user.discord_username,
            'global_name': user.global_name
        }

        # Create JWT using your oauth service
        jwt_token = oauth_service.create_jwt(test_user_data)

        return jsonify({
            'user_id': user.id,
            'username': user.discord_username,
            'test_token': jwt_token,
            'note': 'Use this token for testing - REMOVE THIS ENDPOINT IN PRODUCTION!'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/test/validate-token')
def validate_test_token():
    """Test token validation"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Missing authorization header'}), 401

    token = auth_header.split(' ')[1]

    try:
        payload = jwt.decode(token, os.getenv('JWT_SECRET'), algorithms=['HS256'])
        return jsonify({
            'valid': True,
            'payload': payload,
            'expires': payload.get('exp')
        })
    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expired', 'valid': False}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token', 'valid': False}), 401




@app.route('/api/simple-test/<guild_id>', methods=['GET'])
@require_auth
def simple_test(guild_id):
    """Super simple test endpoint"""
    try:
        print(f"Testing for user {request.user_id} in guild {guild_id}")

        # Check if user is admin
        is_admin = check_admin_sync(request.user_id, guild_id)
        print(f"Is admin: {is_admin}")

        if not is_admin:
            return jsonify({
                "success": False,
                "message": "You are not an admin in this server"
            }), 403

        # Get channels
        channels = get_channels_sync(guild_id)
        print(f"Found {len(channels)} channels")

        return jsonify({
            "success": True,
            "is_admin": is_admin,
            "channels": channels,
            "channel_count": len(channels)
        })

    except Exception as e:
        print(f"Error in simple test: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/config-hybrid', methods=['GET'])
@require_auth
def get_guild_config_hybrid(guild_id):
    """Get guild configuration using hybrid approach (database + live Discord data)"""
    try:
        print(f"Hybrid config request for user {request.user_id} in guild {guild_id}")

        # Check permissions using our working HTTP client
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        print("Permission check passed")

        # Get guild info from Discord HTTP API
        guild_info = run_sync(http_client.get_guild_info(guild_id))
        if not guild_info:
            return jsonify({
                "success": False,
                "message": "Guild not found"
            }), 404

        # Get guild from database
        guild_dao = GuildDao()
        guild = guild_dao.find_by_id(int(guild_id))

        # Parse settings JSON from database
        settings_dict = {}
        if guild and guild.settings:
            try:
                if isinstance(guild.settings, str):
                    settings_dict = json.loads(guild.settings)
                else:
                    settings_dict = guild.settings
            except json.JSONDecodeError as e:
                print(f"Error parsing guild settings JSON: {e}")
                # Return empty dict if parsing fails
                settings_dict = {}

        # Get live Discord data using our working HTTP client
        available_roles = []
        available_channels = get_channels_sync(guild_id)

        # Get roles via HTTP API
        try:
            roles_data = run_sync(http_client.get_guild_roles(guild_id))
            for role in roles_data:
                if role['name'] != '@everyone':
                    available_roles.append({
                        'id': str(role['id']),
                        'name': role['name'],
                        'color': f"#{role['color']:06x}" if role['color'] != 0 else "#99AAB5",
                        'position': role['position'],
                        'managed': role['managed'],
                        'mentionable': role['mentionable'],
                        'hoist': role['hoist']
                    })
        except Exception as role_error:
            print(f"Error getting roles: {role_error}")
            # Continue without roles if there's an error

        # Build role mappings response - only from actual database data
        role_mappings = []
        if 'roles' in settings_dict and 'role_mappings' in settings_dict['roles']:
            for level, role_ids in settings_dict['roles']['role_mappings'].items():
                roles = []
                for role_id in role_ids:
                    # Find role in available roles
                    role_info = next((r for r in available_roles if r['id'] == str(role_id)), None)
                    if role_info:
                        roles.append(role_info)

                if roles:  # Only include levels that have valid roles
                    role_mappings.append({
                        "level": int(level),
                        "roles": roles
                    })

        available_emojis = []
        try:
            emojis_data = run_sync(http_client.get_guild_emojis(guild_id))
            for emoji in emojis_data:
                available_emojis.append({
                    'id': str(emoji['id']),
                    'name': emoji['name'],
                    'roles': emoji.get('roles', []),
                    'require_colons': emoji.get('require_colons', True),
                    'managed': emoji.get('managed', False),
                    'animated': emoji.get('animated', False),
                    'available': emoji.get('available', True),
                    'url': f"https://cdn.discordapp.com/emojis/{emoji['id']}.{'gif' if emoji.get('animated') else 'png'}"
                })
        except Exception as emoji_error:
            print(f"Error getting emojis: {emoji_error}")

        if "games" not in settings_dict:
            settings_dict["games"] = {
                "slots-config": {
                    "enabled": True,
                    "symbols": ["🍒", "🍋", "🍊", "🍇", "🍎", "🍌", "⭐", "🔔", "💎", "🎰", "🍀", "❤️"],
                    "match_two_multiplier": 2,
                    "match_three_multiplier": 10,
                    "min_bet": 100,
                    "max_bet": 25000,
                    "bet_options": [100, 1000, 5000, 10000, 25000]
                }
            }
        elif "slots-config" not in settings_dict["games"]:
            settings_dict["games"]["slots-config"] = {
                "enabled": True,
                "symbols": ["🍒", "🍋", "🍊", "🍇", "🍎", "🍌", "⭐", "🔔", "💎", "🎰", "🍀", "❤️"],
                "match_two_multiplier": 2,
                "match_three_multiplier": 10,
                "min_bet": 100,
                "max_bet": 25000,
                "bet_options": [100, 1000, 5000, 10000, 25000]
            }

        # Build response with ONLY real data from database
        response_data = {
            "guild_id": guild_id,
            "guild_name": guild_info["name"],
            "settings": settings_dict,  # Return exactly what's in the database
            "available_roles": available_roles,
            "available_channels": available_channels,
            "available_emojis": available_emojis,
            "role_mappings": role_mappings,
            "permissions": {
                "method": "hybrid_http_api",
                "has_admin": True
            }
        }

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        print(f"Error in hybrid config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/guilds/<guild_id>/config-hybrid', methods=['POST'])
@require_auth
def update_guild_config_hybrid(guild_id):
    """Update guild configuration using hybrid approach"""
    try:
        print(f"Update config request for user {request.user_id} in guild {guild_id}")

        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get request data
        data = request.get_json()
        if not data or 'settings' not in data:
            return jsonify({
                "success": False,
                "message": "Settings data is required"
            }), 400

        settings = data['settings']

        # Validate settings structure
        required_sections = ['leveling', 'roles', 'ai', 'games']
        for section in required_sections:
            if section not in settings:
                return jsonify({
                    "success": False,
                    "message": f"Missing required settings section: {section}"
                }), 400

        # Validate leveling settings
        leveling_required_fields = {
            'enabled': bool,
            'exp_per_message': int,
            'exp_cooldown_seconds': int,
            'level_up_announcements': bool,
            'streak_multiplier': float,
            'max_streak_bonus': int,
            'daily_bonus': int,
            'daily_announcements_enabled': bool
        }

        for field, field_type in leveling_required_fields.items():
            if field not in settings['leveling']:
                return jsonify({
                    "success": False,
                    "message": f"Missing required leveling field: {field}"
                }), 400

            if not isinstance(settings['leveling'][field], field_type):
                return jsonify({
                    "success": False,
                    "message": f"Invalid type for leveling.{field}, expected {field_type.__name__}"
                }), 400

            # Validate leveling numeric ranges
        if settings['leveling']['exp_per_message'] < 1 or settings['leveling']['exp_per_message'] > 100:
            return jsonify({
                "success": False,
                "message": "exp_per_message must be between 1 and 100"
            }), 400

        if settings['leveling']['exp_cooldown_seconds'] < 1 or settings['leveling']['exp_cooldown_seconds'] > 3600:
            return jsonify({
                "success": False,
                "message": "exp_cooldown_seconds must be between 1 and 3600"
            }), 400

        if settings['leveling']['streak_multiplier'] < 0 or settings['leveling']['streak_multiplier'] > 1.0:
            return jsonify({
                "success": False,
                "message": "streak_multiplier must be between 0 and 1.0"
            }), 400

        if settings['leveling']['max_streak_bonus'] < 1 or settings['leveling']['max_streak_bonus'] > 50:
            return jsonify({
                "success": False,
                "message": "max_streak_bonus must be between 1 and 50"
            }), 400

        if settings['leveling']['daily_bonus'] < 100 or settings['leveling']['daily_bonus'] > 10000:
            return jsonify({
                "success": False,
                "message": "daily_bonus must be between 100 and 10000"
            }), 400

        # Validate roles mode
        if settings['roles']['mode'] not in ['progressive', 'single', 'cumulative']:
            return jsonify({
                "success": False,
                "message": "roles.mode must be one of: progressive, single, cumulative"
            }), 400

        if 'games' in settings and 'slots-config' in settings['games']:
            slots_config = settings['games']['slots-config']

            slots_required_fields = {
                'enabled': bool,
                'symbols': list,
                'match_two_multiplier': int,
                'match_three_multiplier': int,
                'min_bet': int,
                'max_bet': int,
                'bet_options': list
            }

            for field, field_type in slots_required_fields.items():
                if field not in slots_config:
                    return jsonify({
                        "success": False,
                        "message": f"Missing required games.slots-config field: {field}"
                    }), 400

                if not isinstance(slots_config[field], field_type):
                    return jsonify({
                        "success": False,
                        "message": f"Invalid type for games.slots-config.{field}, expected {field_type.__name__}"
                    }), 400

            # Validate symbols list
            if len(slots_config['symbols']) != 12:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.symbols must contain exactly 12 emojis"
                }), 400

            # Validate multipliers
            if slots_config['match_two_multiplier'] < 1 or slots_config['match_two_multiplier'] > 10:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.match_two_multiplier must be between 1 and 10"
                }), 400

            if slots_config['match_three_multiplier'] < 1 or slots_config['match_three_multiplier'] > 100:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.match_three_multiplier must be between 1 and 100"
                }), 400

            # Validate bet amounts
            if slots_config['min_bet'] < 1 or slots_config['min_bet'] > 10000:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.min_bet must be between 1 and 10000"
                }), 400

            if slots_config['max_bet'] < slots_config['min_bet'] or slots_config['max_bet'] > 1000000:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.max_bet must be between min_bet and 1000000"
                }), 400

            # Validate bet options
            if not slots_config['bet_options'] or len(slots_config['bet_options']) == 0:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.bet_options cannot be empty"
                }), 400

            for bet_option in slots_config['bet_options']:
                if not isinstance(bet_option, int) or bet_option < 1:
                    return jsonify({
                        "success": False,
                        "message": "All bet_options must be positive integers"
                    }), 400

                # Validate multipliers
                if slots_config['match_two_multiplier'] < 1 or slots_config['match_two_multiplier'] > 10:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.match_two_multiplier must be between 1 and 10"
                    }), 400

            if slots_config['match_three_multiplier'] < 1 or slots_config['match_three_multiplier'] > 100:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.match_three_multiplier must be between 1 and 100"
                }), 400

            # Validate bet amounts
            if slots_config['min_bet'] < 1 or slots_config['min_bet'] > 10000:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.min_bet must be between 1 and 10000"
                }), 400

            if slots_config['max_bet'] < slots_config['min_bet'] or slots_config['max_bet'] > 1000000:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.max_bet must be between min_bet and 1000000"
                }), 400

            # Validate bet options
            if not slots_config['bet_options'] or len(slots_config['bet_options']) == 0:
                return jsonify({
                    "success": False,
                    "message": "games.slots-config.bet_options cannot be empty"
                }), 400

            for bet_option in slots_config['bet_options']:
                if not isinstance(bet_option, int) or bet_option < 1:
                    return jsonify({
                        "success": False,
                        "message": "All bet_options must be positive integers"
                    }), 400

        # Update settings in database
        settings_manager = get_settings_manager()
        success = settings_manager.guild_dao.update_guild_settings(int(guild_id), settings)

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to update settings in database"
            }), 500

        print(f"Successfully updated settings for guild {guild_id}")

        return jsonify({
            "success": True,
            "message": "Settings updated successfully",
            "data": {
                "guild_id": guild_id,
                "settings": settings,
                "updated_at": datetime.now().isoformat()
            }
        })

    except Exception as e:
        print(f"Error updating guild config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500

@app.route('/api/guilds/<guild_id>/ai', methods=['PUT'])
@require_auth
def update_ai_settings(guild_id):
    """Update AI settings"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Validate request data
        try:
            updates = UpdateAISettingsRequest(**request.json)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Invalid request data",
                "error": str(e)
            }), 400

        # Get current settings
        settings_manager = get_settings_manager()
        settings = settings_manager.get_guild_settings(guild_id)
        settings_dict = settings.dict()

        # Update AI settings
        if 'ai' not in settings_dict:
            settings_dict['ai'] = {}

        settings_dict['ai'].update({
            'enabled': updates.enabled,
            'instructions': updates.instructions,
            'model': updates.model,
            'daily_limit': updates.daily_limit
        })

        # Save updated settings
        success = settings_manager.guild_dao.update_guild_settings(int(guild_id), settings_dict)

        if success:
            return jsonify({
                "success": True,
                "message": "AI settings updated successfully",
                "data": settings_dict['ai']
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update AI settings"
            }), 500

    except Exception as e:
        print(f"Error updating AI settings: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500




@app.route('/api/debug/guilds', methods=['GET'])
def debug_guilds():
    """Debug endpoint to see what guilds the bot is in"""
    try:
        guilds = list_guilds_sync()
        return jsonify({
            "success": True,
            "guilds": guilds,
            "count": len(guilds)
        })
    except Exception as e:
        print(f"Error listing guilds: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


if __name__ == '__main__':
    print("Starting Flask app...")
    app.run(host='0.0.0.0', port=5000, debug=True)