"""Utility endpoints - health checks, testing, debug tools"""
from flask import Blueprint, jsonify
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import UserDao
from api.services.discord_integration import list_guilds_sync, check_admin_sync, get_channels_sync
import jwt
import os

utilities_bp = Blueprint('utilities', __name__)


@utilities_bp.route('/')
def hello():
    """Basic health check endpoint"""
    return jsonify({'message': 'Acosmibot API is working!'})


@utilities_bp.route('/test-import')
def test_import():
    """Test that bot module imports work"""
    try:
        # Test imports
        with UserDao() as user_dao:
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


@utilities_bp.route('/test-db')
def test_db():
    """Test database connection"""
    try:
        with UserDao() as user_dao:
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


@utilities_bp.route('/api/endpoints')
def list_endpoints():
    """List all available API endpoints"""
    from flask import current_app

    endpoints = []
    for rule in current_app.url_map.iter_rules():
        if rule.endpoint != 'static':
            endpoints.append({
                'endpoint': rule.rule,
                'methods': list(rule.methods - {'HEAD', 'OPTIONS'})
            })
    return jsonify(endpoints)


@utilities_bp.route('/bot/invite')
def get_bot_invite():
    """Bot invite coming soon message"""
    return jsonify({
        'message': 'Bot invite feature coming soon!',
        'status': 'coming_soon',
        'eta': 'We are actively developing the bot. Public invites will be available soon.',
        'contact': 'Contact Acosmic on Discord for more info!'
    })


@utilities_bp.route('/api/debug/guilds', methods=['GET'])
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


@utilities_bp.route('/test/create-token/<int:user_id>')
def create_test_token(user_id):
    """Create a test JWT token for a specific user ID (REMOVE IN PRODUCTION!)"""
    try:
        with UserDao() as user_dao:
            user = user_dao.get_user(user_id)

            if not user:
                return jsonify({'error': 'User not found in database'}), 404

            # Create test user data for JWT
            test_user_data = {
                'id': str(user.id),
                'username': user.discord_username,
                'global_name': user.global_name
            }

        # Create JWT
        from api.services.discord_oauth import DiscordOAuthService
        oauth_service = DiscordOAuthService()
        jwt_token = oauth_service.create_jwt(test_user_data)

        return jsonify({
            'user_id': user.id,
            'username': user.discord_username,
            'test_token': jwt_token,
            'note': 'Use this token for testing - REMOVE THIS ENDPOINT IN PRODUCTION!'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@utilities_bp.route('/test/validate-token')
def validate_test_token():
    """Test token validation"""
    from flask import request

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


@utilities_bp.route('/api/simple-test/<guild_id>', methods=['GET'])
@require_auth
def simple_test(guild_id):
    """Super simple test endpoint"""
    try:
        from flask import request
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
