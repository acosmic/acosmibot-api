"""User endpoints - stats, rankings, games"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import UserDao, GuildUserDao, GamesDao

users_bp = Blueprint('users', __name__, url_prefix='/api')


@users_bp.route('/user/<int:user_id>')
def get_user_info(user_id):
    try:
        with UserDao() as user_dao:
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


@users_bp.route('/user/<int:user_id>/rank/currency')
def get_user_currency_rank(user_id):
    try:
        with UserDao() as user_dao:
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


@users_bp.route('/user/<int:user_id>/rank/exp')
def get_user_exp_rank(user_id):
    try:
        with UserDao() as user_dao:
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


@users_bp.route('/user/<int:user_id>/games')
def get_user_game_stats(user_id):
    try:
        with GamesDao() as games_dao:
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


@users_bp.route('/stats/global')
def get_global_stats():
    try:
        with UserDao() as user_dao:
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


@users_bp.route('/user/<int:user_id>/guilds')
def get_user_guilds_simple(user_id):
    try:
        with GuildUserDao() as guild_user_dao:
            # Get all guilds for this user
            guild_users = guild_user_dao.get_user_guilds(user_id)
        
        if guild_users:
            guilds = []
            for gu in guild_users:
                if gu.is_active:
                    guilds.append({
                        'guild_id': gu.guild_id,
                        'level': gu.level,
                        'exp': gu.exp,
                        'currency': gu.currency,
                        'messages_sent': gu.messages_sent,
                        'joined_at': gu.joined_at.strftime('%Y-%m-%d') if gu.joined_at else None
                    })
            
            return jsonify({
                'user_id': user_id,
                'guilds': guilds,
                'total_guilds': len(guilds)
            })
        else:
            return jsonify({
                'user_id': user_id,
                'guilds': [],
                'total_guilds': 0
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@users_bp.route('/guilds/<guild_id>/user/<int:user_id>/stats', methods=['GET'])
@require_auth
def get_user_guild_stats(guild_id, user_id):
    """Get user statistics for a specific guild"""
    try:
        with GuildUserDao() as guild_user_dao:
            # Check if requesting user is a member of this guild
            requester_guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
            if not requester_guild_user or not requester_guild_user.is_active:
                return jsonify({
                    "success": False,
                    "message": "You are not a member of this server"
                }), 403

            # Get the target user's stats
            guild_user = guild_user_dao.get_guild_user(int(user_id), int(guild_id))
        
        if guild_user and guild_user.is_active:
            return jsonify({
                "success": True,
                "data": {
                    "user_id": guild_user.user_id,
                    "guild_id": guild_user.guild_id,
                    "level": guild_user.level,
                    "exp": guild_user.exp,
                    "currency": guild_user.currency,
                    "messages_sent": guild_user.messages_sent,
                    "last_message_at": guild_user.last_message_at.strftime('%Y-%m-%d %H:%M:%S') if guild_user.last_message_at else None,
                    "joined_at": guild_user.joined_at.strftime('%Y-%m-%d') if guild_user.joined_at else None,
                    "streak": guild_user.streak,
                    "last_daily_claim": guild_user.last_daily_claim.strftime('%Y-%m-%d') if guild_user.last_daily_claim else None
                }
            })
        else:
            return jsonify({
                "success": False,
                "message": "User not found in this server or is not active"
            }), 404
            
    except Exception as e:
        print(f"Error getting user guild stats: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get user stats",
            "error": str(e)
        }), 500
