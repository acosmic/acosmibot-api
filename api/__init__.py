"""Application factory for Flask API"""
import os
from pathlib import Path
from dotenv import load_dotenv

# --- Path Setup ---
# Load environment variables from the correct .env file
project_root = Path(__file__).parent.parent.parent
dotenv_path = project_root / 'acosmibot' / '.env'
load_dotenv(dotenv_path=dotenv_path)

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from contextlib import asynccontextmanager
from flask import Flask
from flask_cors import CORS
from config import config
import asyncio
import threading
import atexit

# --- Global Async Event Loop Setup ---
# This setup runs a single asyncio event loop in a background thread.
# Synchronous Flask routes can then safely submit async tasks to this
# persistent loop, avoiding "different loop" errors with shared async resources
# like the SQLAlchemy engine.

loop = asyncio.new_event_loop()

def run_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

background_thread = threading.Thread(target=run_background_loop, args=(loop,), daemon=True)
background_thread.start()

def run_async_threadsafe(coro):
    """Safely runs a coroutine on the background event loop from a sync thread."""
    return asyncio.run_coroutine_threadsafe(coro, loop).result()

def stop_background_loop():
    """Gracefully stop the background event loop."""
    loop.call_soon_threadsafe(loop.stop)

atexit.register(stop_background_loop)


# --- Database Setup ---
DB_USER = os.getenv("db_user")
DB_PASSWORD = os.getenv("db_password")
DB_HOST = os.getenv("db_host")
DB_NAME = os.getenv("db_name")

DATABASE_URL = f"mysql+aiomysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"

async_engine = create_async_engine(DATABASE_URL, pool_recycle=3600)
AsyncSessionLocal = async_sessionmaker(autocommit=False, autoflush=False, bind=async_engine)

@asynccontextmanager
async def get_db_session():
    """Provide a transactional scope around a series of operations."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except:
            await session.rollback()
            raise
        finally:
            await session.close()

# --- End Database Setup ---


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
        users_bp, portal_bp, admin_bp, twitch_bp, youtube_bp,
        reaction_roles_bp, subscriptions_bp, custom_commands_bp, ai_images_bp,
        kick_bp, kick_webhooks_bp, embeds_bp
    )
    from api.blueprints.twitch_webhooks import twitch_webhooks_bp
    from api.blueprints.youtube_webhooks import youtube_webhooks_bp

    app.register_blueprint(utilities_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(guilds_bp)
    app.register_blueprint(leaderboards_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(portal_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(twitch_bp)
    app.register_blueprint(youtube_bp)
    app.register_blueprint(kick_bp)
    app.register_blueprint(twitch_webhooks_bp)  # EventSub webhooks
    app.register_blueprint(youtube_webhooks_bp) # YouTube WebSub webhooks
    app.register_blueprint(kick_webhooks_bp)    # Kick webhooks
    app.register_blueprint(reaction_roles_bp)
    app.register_blueprint(subscriptions_bp)
    app.register_blueprint(custom_commands_bp)
    app.register_blueprint(ai_images_bp)
    app.register_blueprint(embeds_bp)

    return app
