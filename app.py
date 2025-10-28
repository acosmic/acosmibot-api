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


from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler

# Ensure Logs directory exists
os.makedirs('Logs', exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        RotatingFileHandler('Logs/api.log', maxBytes=10*1024*1024, backupCount=30),
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
    """Get guild statistics using database-only approach (accessible to all guild members)"""
    try:
        from Dao.GuildDao import GuildDao
        from Dao.GuildUserDao import GuildUserDao

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

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



@app.route('/api/guilds/<guild_id>/leaderboard/messages-db', methods=['GET'])
@require_auth
def get_guild_messages_leaderboard_db(guild_id):
    """Get messages leaderboard for a specific guild using database-only approach (accessible to all guild members)"""
    try:
        from Dao.GuildDao import GuildDao
        from Dao.GuildUserDao import GuildUserDao

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

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


@app.route('/api/user/guilds', methods=['GET'])
@require_auth
def get_user_guilds():
    """Get guilds from database with actual Discord permissions"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        from Dao.GuildDao import GuildDao

        guild_user_dao = GuildUserDao()
        guild_dao = GuildDao()

        # Get guilds where user is a member
        user_guilds = []

        # Query database for guilds this user is in
        sql = """
              SELECT DISTINCT g.id, g.name, g.owner_id
              FROM Guilds g
                       JOIN GuildUsers gu ON g.id = gu.guild_id
              WHERE gu.user_id = %s \
                AND gu.is_active = TRUE \
              """

        guild_dao = GuildDao()
        results = guild_dao.execute_query(sql, (int(request.user_id),))

        if results:
            for row in results:
                guild_id, guild_name, owner_id = row

                # Get real-time member count from GuildUsers table
                member_count = guild_dao.get_active_member_count(guild_id)

                # Get fresh owner info from Discord API to handle ownership transfers
                guild_icon = None
                guild_banner = None
                try:
                    guild_info = run_sync(http_client.get_guild_info(str(guild_id)))
                    fresh_owner_id = guild_info.get('owner_id') if guild_info else None
                    is_owner = str(fresh_owner_id) == request.user_id if fresh_owner_id else False

                    # Extract icon and banner from Discord API
                    guild_icon = guild_info.get('icon') if guild_info else None
                    guild_banner = guild_info.get('banner') if guild_info else None

                    # Update database if owner changed
                    if fresh_owner_id and str(fresh_owner_id) != str(owner_id):
                        logger.info(f"Owner changed for guild {guild_id}: {owner_id} -> {fresh_owner_id}, updating database")
                        guild_record = guild_dao.get_guild(guild_id)
                        if guild_record:
                            guild_record.owner_id = int(fresh_owner_id)
                            guild_dao.update_guild(guild_record)
                except Exception as e:
                    logger.error(f"Failed to get fresh owner for guild {guild_id}: {e}")
                    # Fallback to database owner_id if Discord API fails
                    is_owner = str(owner_id) == request.user_id

                logger.info(f"Processing guild {guild_id} ({guild_name}): is_owner={is_owner}, user={request.user_id}, owner={owner_id})")

                # Check actual Discord permissions for non-owners
                permissions = []
                if is_owner:
                    permissions = ["administrator"]
                    logger.info(f"  -> User is owner, granting administrator permissions")
                else:
                    # Check if user has admin/manage server permission via Discord API
                    logger.info(f"  -> User is NOT owner, checking Discord API permissions...")
                    try:
                        has_admin = check_admin_sync(request.user_id, str(guild_id))
                        logger.info(f"  -> Discord API returned has_admin={has_admin}")
                        if has_admin:
                            permissions = ["administrator"]
                            logger.info(f"  -> Granting administrator permissions")
                        else:
                            permissions = ["member"]
                            logger.info(f"  -> Granting member permissions only")
                    except Exception as e:
                        logger.error(f"  -> Error checking permissions for guild {guild_id}: {e}")
                        import traceback
                        traceback.print_exc()
                        permissions = ["member"]  # Default to member if check fails
                        logger.info(f"  -> Falling back to member permissions due to error")

                user_guilds.append({
                    "id": str(guild_id),
                    "name": guild_name,
                    "member_count": member_count,
                    "owner": is_owner,
                    "permissions": permissions,
                    "icon": guild_icon,
                    "banner": guild_banner
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
            # Use the pre-calculated 'total' stats from GamesDao
            if 'total' in game_stats:
                total_stats = game_stats['total']
                return jsonify({
                    'total_games': total_stats.get('total_games', 0),
                    'wins': total_stats.get('wins', 0),
                    'losses': total_stats.get('losses', 0),
                    'win_rate': round(float(total_stats.get('win_rate', 0)), 1),
                    'by_game_type': game_stats
                })
            else:
                # Fallback: manually sum stats (skipping 'total' key to avoid double counting)
                total_games = 0
                total_wins = 0
                total_losses = 0

                for game_type, stats in game_stats.items():
                    if isinstance(stats, dict) and game_type != 'total':
                        total_games += stats.get('total_games', 0)
                        total_wins += stats.get('wins', 0)
                        total_losses += stats.get('losses', 0)

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



@app.route('/api/guilds/<guild_id>/leaderboard/level', methods=['GET'])
@require_auth
def get_guild_level_leaderboard(guild_id):
    """Get level leaderboard for a specific guild (accessible to all guild members)"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

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
    """Get messages leaderboard for a specific guild (accessible to all guild members)"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

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
    """Get specific user's stats in a guild (accessible to all guild members)"""
    try:
        from Dao.GuildUserDao import GuildUserDao
        guild_user_dao = GuildUserDao()

        # Check if requesting user is a member of this guild
        requesting_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not requesting_user or not requesting_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

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
        from Dao.GuildDao import GuildDao
        from Dao.GuildUserDao import GuildUserDao

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

        # Get guild info to check ownership
        guild = guild_dao.find_by_id(int(guild_id))
        is_owner = guild and str(guild.owner_id) == request.user_id

        # Check if user has admin permissions via Discord API
        has_admin = False
        try:
            has_admin = check_admin_sync(request.user_id, guild_id)
        except Exception as e:
            # Log the actual error instead of silently catching
            logger.error(f"Error checking admin for user {request.user_id} in guild {guild_id}: {e}")
            import traceback
            traceback.print_exc()
            # If Discord API check fails, fall back to owner status
            has_admin = is_owner

        permissions = {
            "guild_id": guild_id,
            "user_id": request.user_id,
            "is_owner": is_owner,
            "has_admin": has_admin or is_owner,
            "can_configure_bot": has_admin or is_owner,
            "can_view_stats": True  # All guild members can view stats
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
            "guild_icon": guild_info.get("icon"),
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
        required_sections = ['leveling', 'roles', 'ai', 'games', 'cross_server_portal']
        for section in required_sections:
            if section not in settings:
                return jsonify({
                    "success": False,
                    "message": f"Missing required settings section: {section}"
                }), 400

        # Validate leveling settings
        leveling_required_fields = {
            'enabled': bool,
            'level_up_announcements': bool,
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

        # Validate roles mode
        if settings['roles']['mode'] not in ['progressive', 'single', 'cumulative']:
            return jsonify({
                "success": False,
                "message": "roles.mode must be one of: progressive, single, cumulative"
            }), 400

        if 'games' in settings and 'slots-config' in settings['games']:
            slots_config = settings['games']['slots-config']

            # Only validate required guild-specific fields: enabled and symbols
            # All other fields (multipliers, bets) are pulled from global defaults in Slots.py

            # Validate 'enabled' field
            if 'enabled' not in slots_config:
                return jsonify({
                    "success": False,
                    "message": "Missing required games.slots-config field: enabled"
                }), 400

            if not isinstance(slots_config['enabled'], bool):
                return jsonify({
                    "success": False,
                    "message": "Invalid type for games.slots-config.enabled, expected bool"
                }), 400

            # Validate 'symbols' field if provided
            if 'symbols' in slots_config:
                if not isinstance(slots_config['symbols'], list):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.symbols, expected list"
                    }), 400

                if len(slots_config['symbols']) != 12:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.symbols must contain exactly 12 emojis"
                    }), 400

            # Optional: Validate other fields IF they are provided (for future use by dev admins)
            if 'match_two_multiplier' in slots_config:
                if not isinstance(slots_config['match_two_multiplier'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.match_two_multiplier, expected int"
                    }), 400
                if slots_config['match_two_multiplier'] < 1 or slots_config['match_two_multiplier'] > 10:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.match_two_multiplier must be between 1 and 10"
                    }), 400

            if 'match_three_multiplier' in slots_config:
                if not isinstance(slots_config['match_three_multiplier'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.match_three_multiplier, expected int"
                    }), 400
                if slots_config['match_three_multiplier'] < 1 or slots_config['match_three_multiplier'] > 100:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.match_three_multiplier must be between 1 and 100"
                    }), 400

            if 'min_bet' in slots_config:
                if not isinstance(slots_config['min_bet'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.min_bet, expected int"
                    }), 400
                if slots_config['min_bet'] < 1 or slots_config['min_bet'] > 10000:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.min_bet must be between 1 and 10000"
                    }), 400

            if 'max_bet' in slots_config:
                if not isinstance(slots_config['max_bet'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.max_bet, expected int"
                    }), 400
                min_bet = slots_config.get('min_bet', 1)
                if slots_config['max_bet'] < min_bet or slots_config['max_bet'] > 1000000:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.max_bet must be between min_bet and 1000000"
                    }), 400

            if 'bet_options' in slots_config:
                if not isinstance(slots_config['bet_options'], list):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.bet_options, expected list"
                    }), 400
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

        # Validate cross-server portal settings
        if 'cross_server_portal' in settings:
            portal_config = settings['cross_server_portal']

            # Validate 'enabled' field
            if 'enabled' not in portal_config:
                return jsonify({
                    "success": False,
                    "message": "Missing required cross_server_portal field: enabled"
                }), 400

            if not isinstance(portal_config['enabled'], bool):
                return jsonify({
                    "success": False,
                    "message": "Invalid type for cross_server_portal.enabled, expected bool"
                }), 400

            # Validate optional fields if portal is enabled
            if portal_config.get('enabled'):
                # Validate portal_cost if provided
                if 'portal_cost' in portal_config:
                    if not isinstance(portal_config['portal_cost'], int):
                        return jsonify({
                            "success": False,
                            "message": "Invalid type for cross_server_portal.portal_cost, expected int"
                        }), 400
                    if portal_config['portal_cost'] < 100 or portal_config['portal_cost'] > 100000:
                        return jsonify({
                            "success": False,
                            "message": "cross_server_portal.portal_cost must be between 100 and 100000"
                        }), 400

                # Validate display_name if provided
                if 'display_name' in portal_config and portal_config['display_name']:
                    if not isinstance(portal_config['display_name'], str):
                        return jsonify({
                            "success": False,
                            "message": "Invalid type for cross_server_portal.display_name, expected string"
                        }), 400
                    if len(portal_config['display_name']) > 50:
                        return jsonify({
                            "success": False,
                            "message": "cross_server_portal.display_name must be 50 characters or less"
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




# ==================== Cross-Server Portal Endpoints ====================

@app.route('/api/guilds/<guild_id>/portal-config', methods=['GET'])
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


@app.route('/api/guilds/<guild_id>/portal-config', methods=['PATCH'])
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


@app.route('/api/guilds/search-portals', methods=['GET'])
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


# ==================== ADMIN PANEL ENDPOINTS ====================

from admin_auth import require_admin, require_super_admin, log_admin_action, check_is_admin
from Dao.AdminUserDao import AdminUserDao
from Dao.GlobalSettingsDao import GlobalSettingsDao
from Dao.AuditLogDao import AuditLogDao


@app.route('/api/admin/check', methods=['GET'])
@require_auth
def check_admin_status():
    """Check if the current user is an admin"""
    try:
        is_admin = check_is_admin(request.user_id)
        admin_dao = AdminUserDao()
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


# ==================== Global Settings Endpoints ====================

@app.route('/api/admin/settings', methods=['GET'])
@require_auth
@require_admin
def get_global_settings():
    """Get all global bot settings"""
    try:
        settings_dao = GlobalSettingsDao()
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


@app.route('/api/admin/settings', methods=['POST'])
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


@app.route('/api/admin/settings/<category>', methods=['GET'])
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


# ==================== Guild Management Endpoints ====================

@app.route('/api/admin/guilds', methods=['GET'])
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

            # Parse settings
            settings = {}
            if guild.settings:
                try:
                    settings = json.loads(guild.settings) if isinstance(guild.settings, str) else guild.settings
                except:
                    settings = {}

            guilds_data.append({
                'id': str(guild.id),
                'name': guild.name,
                'owner_id': str(guild.owner_id),
                'member_count': member_count,
                'active': guild.active,
                'created_at': guild.created.isoformat() if guild.created else None,
                'last_active': guild.last_active.isoformat() if guild.last_active else None,
                'settings_enabled': {
                    'leveling': settings.get('leveling', {}).get('enabled', False),
                    'ai': settings.get('ai', {}).get('enabled', False),
                    'economy': settings.get('economy', {}).get('enabled', False),
                    'portal': settings.get('cross_server_portal', {}).get('enabled', False)
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


@app.route('/api/admin/guilds/<guild_id>', methods=['GET'])
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

        # Parse settings
        settings = {}
        if guild.settings:
            try:
                settings = json.loads(guild.settings) if isinstance(guild.settings, str) else guild.settings
            except:
                settings = {}

        guild_data = {
            'id': str(guild.id),
            'name': guild.name,
            'owner_id': str(guild.owner_id),
            'member_count': member_count,
            'active': guild.active,
            'created_at': guild.created.isoformat() if guild.created else None,
            'last_active': guild.last_active.isoformat() if guild.last_active else None,
            'settings': settings
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


# ==================== Monitoring Dashboard Endpoints ====================

@app.route('/api/admin/stats/overview', methods=['GET'])
@require_auth
@require_admin
def get_admin_stats_overview():
    """Get overview statistics for admin dashboard"""
    try:
        from Dao.UserDao import UserDao

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


# ==================== Audit Log Endpoints ====================

@app.route('/api/admin/audit-log', methods=['GET'])
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


# ==================== Admin User Management Endpoints ====================

@app.route('/api/admin/users', methods=['GET'])
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


@app.route('/api/admin/users', methods=['POST'])
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


if __name__ == '__main__':
    print("Starting Flask app...")
    app.run(host='0.0.0.0', port=5000, debug=True)