import sys
import os
from pathlib import Path

current_dir = Path(__file__).parent
bot_project_path = current_dir.parent / "acosmibot"
sys.path.insert(0, str(bot_project_path))

from flask import Flask, jsonify, request, redirect
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv('JWT_SECRET')

# Import your OAuth service
from discord_oauth import DiscordOAuthService

oauth_service = DiscordOAuthService()


@app.route('/')
def hello():
    return jsonify({'message': 'Flask is working!'})


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

    return jsonify({
        'success': True,
        'user': user_info,
        'token': jwt_token
    })


@app.route('/auth/me')
def get_current_user():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Missing authorization header'}), 401

    token = auth_header.split(' ')[1]

    try:
        import jwt
        payload = jwt.decode(token, os.getenv('JWT_SECRET'), algorithms=['HS256'])

        # Get fresh user data from your database
        from Dao.UserDao import UserDao
        user_dao = UserDao()
        user = user_dao.get_user(int(payload['user_id']))

        if user:
            return jsonify({
                'id': user.id,
                'username': user.discord_username,
                'level': user.global_level,
                'currency': user.total_currency
            })
        else:
            return jsonify({'error': 'User not found in database'}), 404

    except jwt.ExpiredSignatureError:
        return jsonify({'error': 'Token expired'}), 401
    except jwt.InvalidTokenError:
        return jsonify({'error': 'Invalid token'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/user/<int:user_id>')
def get_user_info(user_id):
    try:
        from Dao.UserDao import UserDao
        user_dao = UserDao()
        user = user_dao.get_user(user_id)

        if user:
            return jsonify({
                'id': user.id,
                'username': user.discord_username,
                'level': user.global_level,
                'currency': user.total_currency,
                'total_messages': user.total_messages
            })
        else:
            return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)