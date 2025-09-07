import sys
from pathlib import Path
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps
import jwt
import os
import atexit

# Set up path to bot project
current_dir = Path(__file__).parent
bot_project_path = current_dir.parent / "acosmibot"
sys.path.insert(0, str(bot_project_path))

# Import services and models
from discord_oauth import DiscordOAuthService
from models.settings_manager import SettingsManager
from models.api_models import (
    UpdateLevelingSettingsRequest,
    UpdateRoleSettingsRequest,
    RoleMappingRequest
)
from models.base_models import RoleCacheEntry
from models.discord_models import DiscordRole, GuildChannelInfo
from discord_integration import (
    check_guild_admin_permissions_sync,
    get_discord_guild_data_sync,
    get_guild_info_sync,
    get_user_manageable_guilds_sync,
    initialize_discord_client,
    cleanup_discord_client,
    run_async
)
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
CORS(app, origins=['https://acosmibot.com'])
app.secret_key = os.getenv('JWT_SECRET')

oauth_service = DiscordOAuthService()

# Cleanup on shutdown
atexit.register(lambda: run_async(cleanup_discord_client()))


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


@app.route('/api/guilds/<guild_id>/config', methods=['GET'])
@require_auth
def get_guild_config(guild_id):
    """Get complete guild configuration"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server",
                "error_code": "insufficient_permissions"
            }), 403

        # Get guild info
        guild_info = get_guild_info_sync(guild_id)
        if not guild_info:
            return jsonify({
                "success": False,
                "message": "Guild not found",
                "error_code": "guild_not_found"
            }), 404

        # Get settings
        settings_manager = get_settings_manager()
        settings = settings_manager.get_guild_settings(guild_id)

        # Get Discord data
        available_roles, available_channels = get_discord_guild_data_sync(guild_id)

        # Build role mappings response
        role_mappings = []
        for level, role_ids in settings.roles.role_mappings.items():
            roles = []
            for role_id in role_ids:
                # Find role in available roles
                role_info = next((r for r in available_roles if r.id == role_id), None)
                if role_info:
                    roles.append(role_info.dict())
                else:
                    # Use cached role info
                    cached_role = settings.roles.role_cache.get(role_id)
                    if cached_role:
                        roles.append({
                            "id": role_id,
                            "name": cached_role.name,
                            "color": cached_role.color,
                            "position": cached_role.position,
                            "managed": cached_role.managed,
                            "mentionable": True,
                            "hoist": False
                        })

            if roles:  # Only include levels that have valid roles
                role_mappings.append({
                    "level": int(level),
                    "roles": roles
                })

        response_data = {
            "guild_id": guild_id,
            "guild_name": guild_info["name"],
            "settings": settings.dict(),
            "available_roles": [role.dict() for role in available_roles],
            "available_channels": [channel.dict() for channel in available_channels],
            "role_mappings": role_mappings
        }

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        print(f"Error getting guild config: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get guild configuration",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/leveling', methods=['PUT'])
@require_auth
def update_leveling_settings(guild_id):
    """Update leveling settings"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
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

        # Update settings
        settings_manager = get_settings_manager()
        success = settings_manager.update_leveling_settings(guild_id, updates)

        if success:
            return jsonify({
                "success": True,
                "message": "Leveling settings updated successfully"
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


@app.route('/api/guilds/<guild_id>/roles', methods=['PUT'])
@require_auth
def update_role_settings(guild_id):
    """Update role settings"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Validate request data
        try:
            updates = UpdateRoleSettingsRequest(**request.json)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Invalid request data",
                "error": str(e)
            }), 400

        # Update settings
        settings_manager = get_settings_manager()
        success = settings_manager.update_role_settings(guild_id, updates)

        if success:
            return jsonify({
                "success": True,
                "message": "Role settings updated successfully"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update role settings"
            }), 500

    except Exception as e:
        print(f"Error updating role settings: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/role-mappings', methods=['POST'])
@require_auth
def update_role_mapping(guild_id):
    """Add or update role mapping for a specific level"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Validate request data
        try:
            mapping_request = RoleMappingRequest(**request.json)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Invalid request data",
                "error": str(e)
            }), 400

        # Get available roles to validate and cache role info
        available_roles, _ = get_discord_guild_data_sync(guild_id)
        role_cache = {}

        for role_id in mapping_request.role_ids:
            # Find role in available roles
            role_info = next((r for r in available_roles if r.id == role_id), None)
            if role_info:
                # Check if role can be assigned by bot
                if role_info.managed:
                    return jsonify({
                        "success": False,
                        "message": f"Role '{role_info.name}' is managed by Discord and cannot be assigned"
                    }), 400

                role_cache[role_id] = RoleCacheEntry(
                    name=role_info.name,
                    color=role_info.color,
                    position=role_info.position,
                    last_verified=datetime.now(),
                    exists=True,
                    managed=role_info.managed
                )
            else:
                return jsonify({
                    "success": False,
                    "message": f"Role {role_id} not found in server"
                }), 400

        # Update role mapping
        settings_manager = get_settings_manager()
        success = settings_manager.update_role_mapping(
            guild_id,
            mapping_request.level,
            mapping_request.role_ids,
            role_cache
        )

        if success:
            return jsonify({
                "success": True,
                "message": f"Role mapping updated for level {mapping_request.level}"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update role mapping"
            }), 500

    except Exception as e:
        print(f"Error updating role mapping: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/role-mappings/<int:level>', methods=['DELETE'])
@require_auth
def delete_role_mapping(guild_id, level):
    """Delete role mapping for a specific level"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Delete role mapping
        settings_manager = get_settings_manager()
        success = settings_manager.delete_role_mapping(guild_id, level)

        if success:
            return jsonify({
                "success": True,
                "message": f"Role mapping deleted for level {level}"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to delete role mapping"
            }), 500

    except Exception as e:
        print(f"Error deleting role mapping: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/roles', methods=['GET'])
@require_auth
def get_guild_roles(guild_id):
    """Get available roles for the guild"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get Discord roles
        available_roles, _ = get_discord_guild_data_sync(guild_id)

        # Filter out @everyone and managed roles for assignment
        assignable_roles = [
            role for role in available_roles
            if role.name != "@everyone" and not role.managed
        ]

        return jsonify({
            "success": True,
            "roles": [role.dict() for role in assignable_roles]
        })

    except Exception as e:
        print(f"Error getting guild roles: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/channels', methods=['GET'])
@require_auth
def get_guild_channels(guild_id):
    """Get available text channels for the guild"""
    try:
        # Check permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get Discord channels
        _, available_channels = get_discord_guild_data_sync(guild_id)

        # Filter to only text-based channels
        text_channels = [
            channel for channel in available_channels
            if channel.is_text_based()
        ]

        return jsonify({
            "success": True,
            "channels": [channel.dict() for channel in text_channels]
        })

    except Exception as e:
        print(f"Error getting guild channels: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


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


# Add these endpoints to your app.py

@app.route('/api/guilds/<guild_id>/stats', methods=['GET'])
@require_auth
def get_guild_stats(guild_id):
    """Get guild statistics and overview"""
    try:
        # Check if user is in the guild (don't need admin for stats viewing)
        user_guilds = get_user_manageable_guilds_sync(request.user_id)
        user_in_guild = any(guild['id'] == guild_id for guild in user_guilds)

        if not user_in_guild:
            # Also check if user is just a member (you'll need to implement this)
            # For now, we'll allow anyone with a valid token
            pass

        from Dao.GuildDao import GuildDao
        from Dao.GuildUserDao import GuildUserDao

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Get basic guild info
        guild = guild_dao.find_by_id(int(guild_id))
        if not guild:
            return jsonify({
                "success": False,
                "message": "Guild not found"
            }), 404

        # Get guild statistics
        guild_stats = {
            "guild_id": guild_id,
            "guild_name": guild.name,
            "member_count": guild.member_count,
            "total_active_members": guild_user_dao.get_active_member_count(int(guild_id)),
            "total_messages": guild_user_dao.get_total_messages_in_guild(int(guild_id)),
            "total_exp_distributed": guild_user_dao.get_total_exp_in_guild(int(guild_id)),
            "highest_level": guild_user_dao.get_highest_level_in_guild(int(guild_id)),
            "avg_level": guild_user_dao.get_average_level_in_guild(int(guild_id)),
            "last_activity": guild.last_active
        }

        return jsonify({
            "success": True,
            "data": guild_stats
        })

    except Exception as e:
        print(f"Error getting guild stats: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get guild statistics",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/leaderboard/level', methods=['GET'])
@require_auth
def get_guild_level_leaderboard(guild_id):
    """Get level leaderboard for a specific guild"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = guild_user_dao.get_top_users_by_level_in_guild(int(guild_id), limit)

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


@app.route('/api/guilds/<guild_id>/permissions', methods=['GET'])
@require_auth
def get_user_guild_permissions(guild_id):
    """Get user's permissions in a specific guild"""
    try:
        # Check what permissions the user has
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)

        # Get guild info to check ownership
        guild_info = get_guild_info_sync(guild_id)
        is_owner = guild_info and guild_info.get('owner_id') == request.user_id

        permissions = {
            "guild_id": guild_id,
            "user_id": request.user_id,
            "is_owner": is_owner,
            "has_admin": has_admin,
            "can_configure_bot": has_admin,
            "can_view_stats": True  # Everyone can view stats if they're in the guild
        }

        return jsonify({
            "success": True,
            "data": permissions
        })

    except Exception as e:
        print(f"Error getting user permissions: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get permissions",
            "error": str(e)
        }), 500


@app.route('/api/guilds/<guild_id>/bot-status', methods=['GET'])
@require_auth
def get_bot_status_in_guild(guild_id):
    """Get bot's status and permissions in the guild"""
    try:
        # Check user has admin permissions
        has_admin = check_guild_admin_permissions_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to view bot status"
            }), 403

        # Get bot's permissions and status
        guild_info = get_guild_info_sync(guild_id)
        if not guild_info:
            return jsonify({
                "success": False,
                "message": "Guild not found or bot not in guild"
            }), 404

        # You'll need to implement getting bot permissions
        # This is a placeholder for bot status
        bot_status = {
            "guild_id": guild_id,
            "bot_in_guild": True,
            "can_manage_roles": True,  # Check actual permissions
            "can_send_messages": True,
            "can_embed_links": True,
            "highest_role_position": 10,  # Get actual position
            "missing_permissions": [],  # List any missing permissions
            "features_available": {
                "leveling": True,
                "role_assignment": True,
                "economy": True,
                "announcements": True
            }
        }

        return jsonify({
            "success": True,
            "data": bot_status
        })

    except Exception as e:
        print(f"Error getting bot status: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get bot status",
            "error": str(e)
        }), 500


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


if __name__ == '__main__':
    # Initialize Discord client on startup
    try:
        run_async(initialize_discord_client())
        print("Discord API client initialized")
    except Exception as e:
        print(f"Failed to initialize Discord client: {e}")

    app.run(host='0.0.0.0', port=5000, debug=True)