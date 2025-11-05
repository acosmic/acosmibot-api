"""
Centralized DAO imports from the bot project.

This module sets up the path to the acosmibot project and imports all DAOs
in one place for easy access across the API blueprints.
"""
import sys
from pathlib import Path

# Set up path to bot project
current_dir = Path(__file__).parent.parent.parent
bot_project_path = current_dir.parent / "acosmibot"
if str(bot_project_path) not in sys.path:
    sys.path.insert(0, str(bot_project_path))

# Import all DAOs
from Dao.GuildDao import GuildDao
from Dao.UserDao import UserDao
from Dao.GuildUserDao import GuildUserDao
from Dao.AdminUserDao import AdminUserDao
from Dao.GlobalSettingsDao import GlobalSettingsDao
from Dao.AuditLogDao import AuditLogDao
from Dao.GamesDao import GamesDao
from Dao.ReactionRoleDao import ReactionRoleDao

__all__ = [
    'GuildDao',
    'UserDao',
    'GuildUserDao',
    'AdminUserDao',
    'GlobalSettingsDao',
    'AuditLogDao',
    'GamesDao',
    'ReactionRoleDao'
]
