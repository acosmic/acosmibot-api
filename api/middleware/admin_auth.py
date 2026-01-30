#! /usr/bin/python3.10
"""
Admin Authentication & Authorization Middleware
Decorators for protecting admin-only API routes
"""

import os
from functools import wraps
from flask import request, jsonify
from acosmibot_core.dao import AdminUserDao, AuditLogDao
from acosmibot_core.utils import AppLogger

logger = AppLogger(__name__).get_logger()

def require_admin(f):
    """
    Decorator to require admin authentication for a route
    Checks if the authenticated user is in the AdminUsers table

    Usage:
        @app.route('/api/admin/settings')
        @require_admin
        def get_admin_settings():
            # user_id is available via request.user_id
            # admin_info is available via request.admin_info
            return jsonify({"status": "success"})
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated (set by main auth middleware)
        if not hasattr(request, 'user_id'):
            logger.warning("Admin route accessed without authentication")
            return jsonify({
                "error": "Authentication required",
                "message": "You must be logged in to access this resource"
            }), 401

        user_id = request.user_id

        # Check if user is an admin
        admin_dao = AdminUserDao()
        admin_info = admin_dao.get_admin_by_discord_id(user_id)

        if not admin_info:
            logger.warning(f"Non-admin user {user_id} attempted to access admin route")
            return jsonify({
                "error": "Forbidden",
                "message": "You do not have permission to access this resource"
            }), 403

        # Attach admin info to request for use in route
        request.admin_info = admin_info
        logger.info(f"Admin {admin_info['discord_username']} ({user_id}) accessing admin route: {request.path}")

        return f(*args, **kwargs)

    return decorated_function

def require_super_admin(f):
    """
    Decorator to require super admin authentication for a route
    More restrictive than require_admin - only for critical operations

    Usage:
        @app.route('/api/admin/users/delete')
        @require_super_admin
        def delete_admin_user():
            return jsonify({"status": "success"})
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated
        if not hasattr(request, 'user_id'):
            logger.warning("Super admin route accessed without authentication")
            return jsonify({
                "error": "Authentication required",
                "message": "You must be logged in to access this resource"
            }), 401

        user_id = request.user_id

        # Check if user is a super admin
        admin_dao = AdminUserDao()
        admin_info = admin_dao.get_admin_by_discord_id(user_id)

        if not admin_info or admin_info['role'] != 'super_admin':
            logger.warning(f"Non-super-admin user {user_id} attempted to access super admin route")
            return jsonify({
                "error": "Forbidden",
                "message": "This action requires super administrator privileges"
            }), 403

        # Attach admin info to request
        request.admin_info = admin_info
        logger.info(f"Super admin {admin_info['discord_username']} ({user_id}) accessing route: {request.path}")

        return f(*args, **kwargs)

    return decorated_function

def log_admin_action(action_type: str, target_type: str = None, target_id: str = None, changes: dict = None):
    """
    Helper function to log admin actions to audit log
    Should be called within routes decorated with @require_admin or @require_super_admin

    Args:
        action_type: Type of action performed
        target_type: Type of entity affected (optional)
        target_id: ID of entity affected (optional)
        changes: Dict of changes made (optional)

    Usage:
        @app.route('/api/admin/settings', methods=['POST'])
        @require_admin
        def update_settings():
            # ... update settings logic ...
            log_admin_action(
                action_type='update_global_settings',
                target_type='setting',
                target_id='features.ai_enabled',
                changes={'old_value': False, 'new_value': True}
            )
            return jsonify({"status": "success"})
    """
    try:
        if not hasattr(request, 'admin_info'):
            logger.error("log_admin_action called without admin authentication")
            return

        admin_info = request.admin_info
        ip_address = request.remote_addr

        audit_dao = AuditLogDao()
        audit_dao.log_action(
            admin_id=admin_info['discord_id'],
            admin_username=admin_info['discord_username'],
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            changes=changes,
            ip_address=ip_address
        )

    except Exception as e:
        logger.error(f"Error logging admin action: {e}")

def check_is_admin(discord_id: str) -> bool:
    """
    Helper function to check if a Discord user is an admin
    Can be used outside of route decorators

    Args:
        discord_id: Discord user ID

    Returns:
        True if user is an admin, False otherwise
    """
    try:
        admin_dao = AdminUserDao()
        return admin_dao.is_admin(discord_id)
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

def check_is_super_admin(discord_id: str) -> bool:
    """
    Helper function to check if a Discord user is a super admin

    Args:
        discord_id: Discord user ID

    Returns:
        True if user is a super admin, False otherwise
    """
    try:
        admin_dao = AdminUserDao()
        return admin_dao.is_super_admin(discord_id)
    except Exception as e:
        logger.error(f"Error checking super admin status: {e}")
        return False
