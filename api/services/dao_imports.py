"""
Centralized DAO imports from the acosmibot-core package.

This module imports all DAOs in one place for easy access across the API blueprints.
"""

# Import all DAOs from acosmibot-core
from acosmibot_core.dao import (
    GuildDao,
    UserDao,
    GuildUserDao,
    AdminUserDao,
    GlobalSettingsDao,
    AuditLogDao,
    GamesDao,
    ReactionRoleDao
)

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
