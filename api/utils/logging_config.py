import os
import logging
from logging.handlers import RotatingFileHandler
from flask import has_request_context, request


class RequestFormatter(logging.Formatter):
    """Custom formatter that includes client IP from request context"""
    def format(self, record):
        if has_request_context():
            # Get real client IP (works with ProxyFix middleware)
            record.client_ip = request.remote_addr
            # Also available: request.headers.get('CF-Connecting-IP') for Cloudflare-specific
        else:
            record.client_ip = '-'
        return super().format(record)


def setup_logging(app):
    """Configure logging for the application

    Log level can be controlled via LOG_LEVEL_API environment variable.
    Valid values: DEBUG, INFO, WARNING, ERROR, CRITICAL
    Defaults to DEBUG if app.config['DEBUG'] is True, otherwise INFO.
    """
    os.makedirs(app.config['LOG_DIR'], exist_ok=True)

    # Create formatter with client IP
    formatter = RequestFormatter('%(asctime)s %(levelname)s [%(client_ip)s] %(message)s')

    # File handler
    file_handler = RotatingFileHandler(
        f'{app.config["LOG_DIR"]}/api.log',
        maxBytes=app.config['LOG_MAX_BYTES'],
        backupCount=app.config['LOG_BACKUP_COUNT']
    )
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Determine log level from environment variable or app config
    log_level_str = os.getenv('LOG_LEVEL_API', 'DEBUG' if app.config['DEBUG'] else 'INFO')
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        handlers=[file_handler, console_handler]
    )

    return logging.getLogger(__name__)
