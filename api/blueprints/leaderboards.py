"""Leaderboard endpoints - guild and global rankings"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import UserDao, GuildUserDao, GuildDao

leaderboards_bp = Blueprint('leaderboards', __name__, url_prefix='/api')


# ============================================================================
# GLOBAL LEADERBOARDS
# ============================================================================

@leaderboards_bp.route('/leaderboard/currency')
def get_currency_leaderboard():
    """Get global currency leaderboard"""
    try:
        user_dao = UserDao()

        limit = min(int(request.args.get('limit', 10)), 50)  # Default 10, max 50
        top_users = user_dao.get_top_users_by_currency(limit)

        return jsonify(top_users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@leaderboards_bp.route('/leaderboard/messages')
def get_messages_leaderboard():
    """Get global messages leaderboard"""
    try:
        user_dao = UserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = user_dao.get_top_users_by_messages(limit)

        return jsonify(top_users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@leaderboards_bp.route('/leaderboard/level')
def get_level_leaderboard():
    """Get global level leaderboard"""
    try:
        user_dao = UserDao()

        limit = min(int(request.args.get('limit', 10)), 50)
        top_users = user_dao.get_top_users_by_global_level(limit)

        return jsonify(top_users)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# GUILD-SPECIFIC LEADERBOARDS
# ============================================================================

@leaderboards_bp.route('/guilds/<guild_id>/leaderboard/level', methods=['GET'])
@require_auth
def get_guild_level_leaderboard(guild_id):
    """Get level leaderboard for a specific guild (accessible to all guild members)"""
    try:
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


@leaderboards_bp.route('/guilds/<guild_id>/leaderboard/messages', methods=['GET'])
@require_auth
def get_guild_messages_leaderboard(guild_id):
    """Get messages leaderboard for a specific guild (accessible to all guild members)"""
    try:
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


@leaderboards_bp.route('/guilds/<guild_id>/leaderboard/messages-db', methods=['GET'])
@require_auth
def get_guild_messages_leaderboard_db(guild_id):
    """Get messages leaderboard for a specific guild using database-only approach (accessible to all guild members)"""
    try:
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
              WHERE gu.guild_id = %s               AND gu.is_active = TRUE
              ORDER BY gu.messages_sent DESC
                  LIMIT %s               """

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
