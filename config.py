import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Base configuration"""
    SECRET_KEY = os.getenv('JWT_SECRET')
    CORS_ORIGINS = ['https://acosmibot.com', 'https://api.acosmibot.com']
    LOG_DIR = 'Logs'
    LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT = 30
    DISCORD_CLIENT_ID = os.getenv('DISCORD_CLIENT_ID')
    DISCORD_CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
    REDIRECT_URI = os.getenv('REDIRECT_URI')

    # YouTube Webhook Configuration
    YOUTUBE_WEBHOOK_CALLBACK_URL = os.getenv('YOUTUBE_WEBHOOK_CALLBACK_URL')
    YOUTUBE_WEBHOOK_SECRET = os.getenv('YOUTUBE_WEBHOOK_SECRET')


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    """Testing configuration"""
    DEBUG = True
    TESTING = True


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': ProductionConfig
}
