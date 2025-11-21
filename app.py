"""
Acosmibot API - Production Entry Point

All routes are now in blueprints. This is the minimal entry point.
Blueprints:
- utilities: Health checks, test endpoints
- auth: OAuth authentication
- twitch: Twitch integration
- leaderboards: Global and guild leaderboards
- users: User stats, rankings, games
- portal: Cross-server portal configuration
- guilds: Guild management, config, permissions
- admin: Admin panel endpoints
"""

import os
from api import create_app

# Determine environment (default to production)
env = os.getenv('FLASK_ENV', 'production')
app = create_app(env)

if __name__ == '__main__':
    print(f"🚀 Starting Acosmibot API in {env} mode...")
    print("✅ All 12 blueprints loaded:")
    print("   - utilities, auth, twitch, leaderboards")
    print("   - users, portal, guilds, admin")
    print("   - reaction_roles, subscriptions, custom_commands, ai_images")
    app.run(host='0.0.0.0', port=5000)
