"""Application factory for Flask API"""
from flask import Flask
from flask_cors import CORS
from config import config


def create_app(config_name='default'):
    """Create and configure the Flask application"""
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    # Setup CORS
    CORS(app, origins=app.config['CORS_ORIGINS'], supports_credentials=True)

    # Setup logging
    from api.utils.logging_config import setup_logging
    logger = setup_logging(app)
    app.logger = logger

    # Register blueprints
    from api.blueprints import (
        utilities_bp, auth_bp, guilds_bp, leaderboards_bp,
        users_bp, portal_bp, admin_bp, twitch_bp, reaction_roles_bp,
        subscriptions_bp, custom_commands_bp, ai_images_bp
    )

    app.register_blueprint(utilities_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(guilds_bp)
    app.register_blueprint(leaderboards_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(twitch_bp)
    app.register_blueprint(reaction_roles_bp)
    app.register_blueprint(subscriptions_bp)
    app.register_blueprint(custom_commands_bp)
    app.register_blueprint(ai_images_bp)

    return app
