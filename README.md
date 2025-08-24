# Acosmibot API

Flask-based REST API for Acosmibot Discord bot web interface.

## Features
- Discord OAuth authentication
- User management endpoints
- Integration with bot database
- JWT token-based auth

## Setup
1. Clone repository
2. Create virtual environment: `python3 -m venv .venv`
3. Activate: `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Configure `.env` file (see .env.example)
6. Run: `python app.py`

## API Endpoints
- `GET /` - Health check
- `GET /auth/login` - Discord OAuth login
- `GET /auth/callback` - OAuth callback
- `GET /auth/me` - Get current user
- `GET /api/user/{id}` - Get user info

## Related Projects
- [Acosmibot](https://github.com/yourusername/acosmibot) - Main Discord bot
