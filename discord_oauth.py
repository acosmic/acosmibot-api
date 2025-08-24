import os
import requests
import jwt
from datetime import datetime, timedelta
from flask import session


class DiscordOAuthService:
    def __init__(self):
        self.client_id = os.getenv('DISCORD_CLIENT_ID')
        self.client_secret = os.getenv('DISCORD_CLIENT_SECRET')
        self.redirect_uri = os.getenv('DISCORD_REDIRECT_URI')
        self.jwt_secret = os.getenv('JWT_SECRET', 'your-secret-key')

    def get_auth_url(self):
        return (f"https://discord.com/api/oauth2/authorize"
                f"?client_id={self.client_id}"
                f"&redirect_uri={self.redirect_uri}"
                f"&response_type=code"
                f"&scope=identify%20guilds")

    def exchange_code(self, code):
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': self.redirect_uri
        }

        response = requests.post('https://discord.com/api/oauth2/token', data=data)
        return response.json() if response.status_code == 200 else None

    def get_user_info(self, access_token):
        headers = {'Authorization': f'Bearer {access_token}'}
        response = requests.get('https://discord.com/api/v10/users/@me', headers=headers)
        return response.json() if response.status_code == 200 else None

    def create_jwt(self, user_data):
        payload = {
            'user_id': user_data['id'],
            'username': user_data['username'],
            'exp': datetime.utcnow() + timedelta(hours=24)
        }
        return jwt.encode(payload, self.jwt_secret, algorithm='HS256')