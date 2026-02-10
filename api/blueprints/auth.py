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
    # Handle denied access
    if 'error' in request.args:
        error = request.args.get('error')
        error_description = request.args.get('error_description')
        return redirect(f"https://acosmibot.com/?error={error}&error_description={error_description}")

    code = request.args.get('code')
    if not code:
        # Redirect with a generic error if no code and no specific error from Discord
        return redirect("https://acosmibot.com/?error=missing_code&error_description=No_authorization_code_received")

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

    # Redirect to dashboard page on main website with token
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
            avatar_url = user.avatar_url or f"https://cdn.discordapp.com/embed/avatars/{(int(payload['user_id']) >> 22) % 6}.png"

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
            # User not in database (new user with no servers) - return basic info from JWT
            # This allows new users to access the dashboard and see the onboarding flow
            user_id = payload['user_id']
            avatar_hash = payload.get('avatar')

            # Build avatar URL - use Discord avatar if available, otherwise default
            if avatar_hash:
                avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png?size=256"
            else:
                avatar_url = f"https://cdn.discordapp.com/embed/avatars/{(int(user_id) >> 22) % 6}.png"

            return jsonify({
                'id': str(user_id),
                'username': payload.get('username', 'User'),
                'global_name': payload.get('global_name', payload.get('username', 'User')),
                'avatar': avatar_url,
                'level': 1,
                'currency': 0,
                'total_messages': 0,
                'total_reactions': 0,
                'global_exp': 0,
                'account_created': None,
                'first_seen': None,
                'last_seen': None,
                'is_new_user': True  # Flag to indicate this is a new user
            })

    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500
