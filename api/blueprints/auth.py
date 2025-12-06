"""Authentication endpoints - OAuth, login, token management"""
from flask import Blueprint, jsonify, request, redirect
from api.services.discord_oauth import DiscordOAuthService
from api.services.dao_imports import UserDao
import jwt
import os

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')
oauth_service = DiscordOAuthService()


@auth_bp.route('/login')
def login():
    """Initiate Discord OAuth flow"""
    auth_url = oauth_service.get_auth_url()
    return redirect(auth_url)


@auth_bp.route('/callback')
def callback():
    """Handle Discord OAuth callback"""
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
    user_dashboard_url = f"https://acosmibot.com/dashboard?token={jwt_token}"
    return redirect(user_dashboard_url)


@auth_bp.route('/me')
def get_current_user():
    """Get current user info from JWT token"""
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Missing authorization header'}), 401

    token = auth_header.split(' ')[1]

    try:
        payload = jwt.decode(token, os.getenv('JWT_SECRET'), algorithms=['HS256'])

        # Get fresh user data from database
        with UserDao() as user_dao:
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
                'id': str(user.id),
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
