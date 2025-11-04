import os
import logging
from logging.handlers import RotatingFileHandler


def setup_logging(app):
    """Configure logging for the application"""
    os.makedirs(app.config['LOG_DIR'], exist_ok=True)

    logging.basicConfig(
        level=logging.DEBUG if app.config['DEBUG'] else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            RotatingFileHandler(
                f'{app.config["LOG_DIR"]}/api.log',
                maxBytes=app.config['LOG_MAX_BYTES'],
                backupCount=app.config['LOG_BACKUP_COUNT']
            ),
            logging.StreamHandler()
        ]
    )

    return logging.getLogger(__name__)
