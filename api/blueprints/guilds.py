"""Guild management endpoints - config, permissions, stats"""
from flask import Blueprint, jsonify, request
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import GuildDao, GuildUserDao
from api.services.discord_integration import check_admin_sync, get_channels_sync, http_client, run_sync
from models.settings_manager import SettingsManager
import json
from datetime import datetime
import logging

logger = logging.getLogger(__name__)
guilds_bp = Blueprint('guilds', __name__, url_prefix='/api')


def get_settings_manager():
    """Get settings manager instance"""
    guild_dao = GuildDao()
    return SettingsManager(guild_dao)


@guilds_bp.route('/user/guilds', methods=['GET'])
@require_auth
def get_user_guilds():
    """Get guilds from database with actual Discord permissions"""
    try:

        guild_user_dao = GuildUserDao()
        guild_dao = GuildDao()

        # Get guilds where user is a member
        user_guilds = []

        # Query database for guilds this user is in
        sql = """
              SELECT DISTINCT g.id, g.name, g.owner_id
              FROM Guilds g
                       JOIN GuildUsers gu ON g.id = gu.guild_id
              WHERE gu.user_id = %s \
                AND gu.is_active = TRUE \
              """

        guild_dao = GuildDao()
        results = guild_dao.execute_query(sql, (int(request.user_id),))

        if results:
            for row in results:
                guild_id, guild_name, owner_id = row

                # Get real-time member count from GuildUsers table
                member_count = guild_dao.get_active_member_count(guild_id)

                # Get fresh owner info from Discord API to handle ownership transfers
                guild_icon = None
                guild_banner = None
                try:
                    guild_info = run_sync(http_client.get_guild_info(str(guild_id)))
                    fresh_owner_id = guild_info.get('owner_id') if guild_info else None
                    is_owner = str(fresh_owner_id) == request.user_id if fresh_owner_id else False

                    # Extract icon and banner from Discord API
                    guild_icon = guild_info.get('icon') if guild_info else None
                    guild_banner = guild_info.get('banner') if guild_info else None

                    # Update database if owner changed
                    if fresh_owner_id and str(fresh_owner_id) != str(owner_id):
                        logger.info(f"Owner changed for guild {guild_id}: {owner_id} -> {fresh_owner_id}, updating database")
                        guild_record = guild_dao.get_guild(guild_id)
                        if guild_record:
                            guild_record.owner_id = int(fresh_owner_id)
                            guild_dao.update_guild(guild_record)
                except Exception as e:
                    logger.error(f"Failed to get fresh owner for guild {guild_id}: {e}")
                    # Fallback to database owner_id if Discord API fails
                    is_owner = str(owner_id) == request.user_id

                logger.info(f"Processing guild {guild_id} ({guild_name}): is_owner={is_owner}, user={request.user_id}, owner={owner_id})")

                # Check actual Discord permissions for non-owners
                permissions = []
                if is_owner:
                    permissions = ["administrator"]
                    logger.info(f"  -> User is owner, granting administrator permissions")
                else:
                    # Check if user has admin/manage server permission via Discord API
                    logger.info(f"  -> User is NOT owner, checking Discord API permissions...")
                    try:
                        has_admin = check_admin_sync(request.user_id, str(guild_id))
                        logger.info(f"  -> Discord API returned has_admin={has_admin}")
                        if has_admin:
                            permissions = ["administrator"]
                            logger.info(f"  -> Granting administrator permissions")
                        else:
                            permissions = ["member"]
                            logger.info(f"  -> Granting member permissions only")
                    except Exception as e:
                        logger.error(f"  -> Error checking permissions for guild {guild_id}: {e}")
                        import traceback
                        traceback.print_exc()
                        permissions = ["member"]  # Default to member if check fails
                        logger.info(f"  -> Falling back to member permissions due to error")

                user_guilds.append({
                    "id": str(guild_id),
                    "name": guild_name,
                    "member_count": member_count,
                    "owner": is_owner,
                    "permissions": permissions,
                    "icon": guild_icon,
                    "banner": guild_banner
                })

        logger.info(f"Found {len(user_guilds)} guilds for user {request.user_id}")
        return jsonify({
            "success": True,
            "guilds": user_guilds
        })

    except Exception as e:
        logger.error(f"Error getting guilds from database: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

@guilds_bp.route('/guilds/<guild_id>/permissions', methods=['GET'])
@require_auth
def get_user_guild_permissions(guild_id):
    """Get user's permissions in a specific guild"""
    try:

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

        # Get guild info to check ownership
        guild = guild_dao.find_by_id(int(guild_id))
        is_owner = guild and str(guild.owner_id) == request.user_id

        # Check if user has admin permissions via Discord API
        has_admin = False
        try:
            has_admin = check_admin_sync(request.user_id, guild_id)
        except Exception as e:
            # Log the actual error instead of silently catching
            logger.error(f"Error checking admin for user {request.user_id} in guild {guild_id}: {e}")
            import traceback
            traceback.print_exc()
            # If Discord API check fails, fall back to owner status
            has_admin = is_owner

        permissions = {
            "guild_id": guild_id,
            "user_id": request.user_id,
            "is_owner": is_owner,
            "has_admin": has_admin or is_owner,
            "can_configure_bot": has_admin or is_owner,
            "can_view_stats": True  # All guild members can view stats
        }

        return jsonify({
            "success": True,
            "data": permissions
        })

    except Exception as e:
        print(f"Error getting user permissions: {e}")
        return jsonify({
            "success": False,
            "message": "Failed to get permissions",
            "error": str(e)
        }), 500

@guilds_bp.route('/guilds/<guild_id>/stats-db', methods=['GET'])
@require_auth
def get_guild_stats_db_only(guild_id):
    """Get guild statistics using database-only approach (accessible to all guild members)"""
    try:

        guild_dao = GuildDao()
        guild_user_dao = GuildUserDao()

        # Check if user is a member of this guild
        guild_user = guild_user_dao.get_guild_user(int(request.user_id), int(guild_id))
        if not guild_user or not guild_user.is_active:
            return jsonify({
                "success": False,
                "message": "You are not a member of this server"
            }), 403

        # Get guild record
        guild_record = guild_dao.find_by_id(int(guild_id))
        if not guild_record:
            return jsonify({
                "success": False,
                "message": "Guild not found"
            }), 404

        # Get basic stats using direct SQL queries
        try:
            # Count active members
            active_members_sql = "SELECT COUNT(*) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            active_members_result = guild_dao.execute_query(active_members_sql, (int(guild_id),))
            active_members = active_members_result[0][0] if active_members_result else 0

            # Total messages in guild
            total_messages_sql = "SELECT SUM(messages_sent) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            total_messages_result = guild_dao.execute_query(total_messages_sql, (int(guild_id),))
            total_messages = total_messages_result[0][0] if total_messages_result and total_messages_result[0][0] else 0

            # Total exp in guild
            total_exp_sql = "SELECT SUM(exp) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            total_exp_result = guild_dao.execute_query(total_exp_sql, (int(guild_id),))
            total_exp = total_exp_result[0][0] if total_exp_result and total_exp_result[0][0] else 0

            # Highest level
            highest_level_sql = "SELECT MAX(level) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            highest_level_result = guild_dao.execute_query(highest_level_sql, (int(guild_id),))
            highest_level = highest_level_result[0][0] if highest_level_result and highest_level_result[0][0] else 0

            # Average level
            avg_level_sql = "SELECT AVG(level) FROM GuildUsers WHERE guild_id = %s AND is_active = TRUE"
            avg_level_result = guild_dao.execute_query(avg_level_sql, (int(guild_id),))
            avg_level = round(avg_level_result[0][0], 1) if avg_level_result and avg_level_result[0][0] else 0

        except Exception as e:
            # Fallback values if queries fail
            active_members = guild_record.member_count or 0
            total_messages = 0
            total_exp = 0
            highest_level = 0
            avg_level = 0

        guild_stats = {
            "guild_id": guild_id,
            "guild_name": guild_record.name,
            "member_count": guild_record.member_count or 0,
            "total_active_members": active_members,
            "total_messages": total_messages,
            "total_exp_distributed": total_exp,
            "highest_level": highest_level,
            "avg_level": avg_level,
            "last_activity": guild_record.last_active,
            "method": "database_only"
        }

        return jsonify({
            "success": True,
            "data": guild_stats
        })

    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "message": "Failed to get guild statistics",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@guilds_bp.route('/guilds/<guild_id>/config-hybrid', methods=['GET'])
@require_auth
def get_guild_config_hybrid(guild_id):
    """Get guild configuration using hybrid approach (database + live Discord data)"""
    try:
        print(f"Hybrid config request for user {request.user_id} in guild {guild_id}")

        # Check permissions using our working HTTP client
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        print("Permission check passed")

        # Get guild info from Discord HTTP API
        guild_info = run_sync(http_client.get_guild_info(guild_id))
        if not guild_info:
            return jsonify({
                "success": False,
                "message": "Guild not found"
            }), 404

        # Get guild from database
        guild_dao = GuildDao()
        guild = guild_dao.find_by_id(int(guild_id))

        # Parse settings JSON from database
        settings_dict = {}
        if guild and guild.settings:
            try:
                if isinstance(guild.settings, str):
                    settings_dict = json.loads(guild.settings)
                else:
                    settings_dict = guild.settings
            except json.JSONDecodeError as e:
                print(f"Error parsing guild settings JSON: {e}")
                # Return empty dict if parsing fails
                settings_dict = {}

        # Get live Discord data using our working HTTP client
        available_roles = []
        available_channels = get_channels_sync(guild_id)

        # Get roles via HTTP API
        try:
            roles_data = run_sync(http_client.get_guild_roles(guild_id))
            for role in roles_data:
                if role['name'] != '@everyone':
                    available_roles.append({
                        'id': str(role['id']),
                        'name': role['name'],
                        'color': f"#{role['color']:06x}" if role['color'] != 0 else "#99AAB5",
                        'position': role['position'],
                        'managed': role['managed'],
                        'mentionable': role['mentionable'],
                        'hoist': role['hoist']
                    })
        except Exception as role_error:
            print(f"Error getting roles: {role_error}")
            # Continue without roles if there's an error

        # Build role mappings response - only from actual database data
        role_mappings = []
        if 'roles' in settings_dict and 'role_mappings' in settings_dict['roles']:
            for level, role_ids in settings_dict['roles']['role_mappings'].items():
                roles = []
                for role_id in role_ids:
                    # Find role in available roles
                    role_info = next((r for r in available_roles if r['id'] == str(role_id)), None)
                    if role_info:
                        roles.append(role_info)

                if roles:  # Only include levels that have valid roles
                    role_mappings.append({
                        "level": int(level),
                        "roles": roles
                    })

        available_emojis = []
        try:
            emojis_data = run_sync(http_client.get_guild_emojis(guild_id))
            for emoji in emojis_data:
                available_emojis.append({
                    'id': str(emoji['id']),
                    'name': emoji['name'],
                    'roles': emoji.get('roles', []),
                    'require_colons': emoji.get('require_colons', True),
                    'managed': emoji.get('managed', False),
                    'animated': emoji.get('animated', False),
                    'available': emoji.get('available', True),
                    'url': f"https://cdn.discordapp.com/emojis/{emoji['id']}.{'gif' if emoji.get('animated') else 'png'}"
                })
        except Exception as emoji_error:
            print(f"Error getting emojis: {emoji_error}")

        if "games" not in settings_dict:
            settings_dict["games"] = {
                "slots-config": {
                    "enabled": True,
                    "symbols": ["🍒", "🍋", "🍊", "🍇", "🍎", "🍌", "⭐", "🔔", "💎", "🎰", "🍀", "❤️"],
                    "match_two_multiplier": 2,
                    "match_three_multiplier": 10,
                    "min_bet": 100,
                    "max_bet": 25000,
                    "bet_options": [100, 1000, 5000, 10000, 25000]
                }
            }
        elif "slots-config" not in settings_dict["games"]:
            settings_dict["games"]["slots-config"] = {
                "enabled": True,
                "symbols": ["🍒", "🍋", "🍊", "🍇", "🍎", "🍌", "⭐", "🔔", "💎", "🎰", "🍀", "❤️"],
                "match_two_multiplier": 2,
                "match_three_multiplier": 10,
                "min_bet": 100,
                "max_bet": 25000,
                "bet_options": [100, 1000, 5000, 10000, 25000]
            }

        # Add default Twitch settings if not present
        if "twitch" not in settings_dict:
            settings_dict["twitch"] = {
                "enabled": False,
                "announcement_channel_id": None,
                "tracked_streamers": [],
                "announcement_settings": {
                    "include_thumbnail": True,
                    "include_game": True,
                    "include_viewer_count": True,
                    "include_start_time": True,
                    "color": "0x6441A4"
                },
                "vod_settings": {
                    "enabled": False,
                    "edit_message_when_vod_available": True,
                    "vod_check_interval_seconds": 300,
                    "vod_message_suffix": "\n\n📺 **VOD Available:** [Watch Recording]({vod_url})"
                },
                "notification_method": "polling"
            }

        # Build response with ONLY real data from database
        response_data = {
            "guild_id": guild_id,
            "guild_name": guild_info["name"],
            "guild_icon": guild_info.get("icon"),
            "settings": settings_dict,  # Return exactly what's in the database
            "available_roles": available_roles,
            "available_channels": available_channels,
            "available_emojis": available_emojis,
            "role_mappings": role_mappings,
            "permissions": {
                "method": "hybrid_http_api",
                "has_admin": True
            }
        }

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        print(f"Error in hybrid config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@guilds_bp.route('/guilds/<guild_id>/config-hybrid', methods=['POST'])
@require_auth
def update_guild_config_hybrid(guild_id):
    """Update guild configuration using hybrid approach"""
    try:
        print(f"Update config request for user {request.user_id} in guild {guild_id}")

        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Get request data
        data = request.get_json()
        if not data or 'settings' not in data:
            return jsonify({
                "success": False,
                "message": "Settings data is required"
            }), 400

        settings = data['settings']

        # Validate settings structure
        required_sections = ['leveling', 'roles', 'ai', 'games', 'cross_server_portal', 'twitch']
        for section in required_sections:
            if section not in settings:
                return jsonify({
                    "success": False,
                    "message": f"Missing required settings section: {section}"
                }), 400

        # Validate Twitch settings if present
        if 'twitch' in settings and settings['twitch'].get('enabled'):
            # Validate tracked_streamers limit (2 max for non-premium)
            tracked_streamers = settings['twitch'].get('tracked_streamers', [])
            if len(tracked_streamers) > 2:
                return jsonify({
                    "success": False,
                    "message": "Maximum 2 streamers allowed (upgrade to premium for more)"
                }), 400

            # Validate each streamer has required fields
            for streamer in tracked_streamers:
                if 'twitch_username' not in streamer or not streamer['twitch_username']:
                    return jsonify({
                        "success": False,
                        "message": "Each streamer must have a twitch_username"
                    }), 400

        # Validate leveling settings
        leveling_required_fields = {
            'enabled': bool,
            'level_up_announcements': bool,
            'daily_announcements_enabled': bool
        }

        for field, field_type in leveling_required_fields.items():
            if field not in settings['leveling']:
                return jsonify({
                    "success": False,
                    "message": f"Missing required leveling field: {field}"
                }), 400

            if not isinstance(settings['leveling'][field], field_type):
                return jsonify({
                    "success": False,
                    "message": f"Invalid type for leveling.{field}, expected {field_type.__name__}"
                }), 400

        # Validate roles mode
        if settings['roles']['mode'] not in ['progressive', 'single', 'cumulative']:
            return jsonify({
                "success": False,
                "message": "roles.mode must be one of: progressive, single, cumulative"
            }), 400

        if 'games' in settings and 'slots-config' in settings['games']:
            slots_config = settings['games']['slots-config']

            # Only validate required guild-specific fields: enabled and symbols
            # All other fields (multipliers, bets) are pulled from global defaults in Slots.py

            # Validate 'enabled' field
            if 'enabled' not in slots_config:
                return jsonify({
                    "success": False,
                    "message": "Missing required games.slots-config field: enabled"
                }), 400

            if not isinstance(slots_config['enabled'], bool):
                return jsonify({
                    "success": False,
                    "message": "Invalid type for games.slots-config.enabled, expected bool"
                }), 400

            # Validate 'symbols' field if provided
            if 'symbols' in slots_config:
                if not isinstance(slots_config['symbols'], list):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.symbols, expected list"
                    }), 400

                if len(slots_config['symbols']) != 12:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.symbols must contain exactly 12 emojis"
                    }), 400

            # Optional: Validate other fields IF they are provided (for future use by dev admins)
            if 'match_two_multiplier' in slots_config:
                if not isinstance(slots_config['match_two_multiplier'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.match_two_multiplier, expected int"
                    }), 400
                if slots_config['match_two_multiplier'] < 1 or slots_config['match_two_multiplier'] > 10:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.match_two_multiplier must be between 1 and 10"
                    }), 400

            if 'match_three_multiplier' in slots_config:
                if not isinstance(slots_config['match_three_multiplier'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.match_three_multiplier, expected int"
                    }), 400
                if slots_config['match_three_multiplier'] < 1 or slots_config['match_three_multiplier'] > 100:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.match_three_multiplier must be between 1 and 100"
                    }), 400

            if 'min_bet' in slots_config:
                if not isinstance(slots_config['min_bet'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.min_bet, expected int"
                    }), 400
                if slots_config['min_bet'] < 1 or slots_config['min_bet'] > 10000:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.min_bet must be between 1 and 10000"
                    }), 400

            if 'max_bet' in slots_config:
                if not isinstance(slots_config['max_bet'], int):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.max_bet, expected int"
                    }), 400
                min_bet = slots_config.get('min_bet', 1)
                if slots_config['max_bet'] < min_bet or slots_config['max_bet'] > 1000000:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.max_bet must be between min_bet and 1000000"
                    }), 400

            if 'bet_options' in slots_config:
                if not isinstance(slots_config['bet_options'], list):
                    return jsonify({
                        "success": False,
                        "message": "Invalid type for games.slots-config.bet_options, expected list"
                    }), 400
                if not slots_config['bet_options'] or len(slots_config['bet_options']) == 0:
                    return jsonify({
                        "success": False,
                        "message": "games.slots-config.bet_options cannot be empty"
                    }), 400
                for bet_option in slots_config['bet_options']:
                    if not isinstance(bet_option, int) or bet_option < 1:
                        return jsonify({
                            "success": False,
                            "message": "All bet_options must be positive integers"
                        }), 400

        # Validate cross-server portal settings
        if 'cross_server_portal' in settings:
            portal_config = settings['cross_server_portal']

            # Validate 'enabled' field
            if 'enabled' not in portal_config:
                return jsonify({
                    "success": False,
                    "message": "Missing required cross_server_portal field: enabled"
                }), 400

            if not isinstance(portal_config['enabled'], bool):
                return jsonify({
                    "success": False,
                    "message": "Invalid type for cross_server_portal.enabled, expected bool"
                }), 400

            # Validate optional fields if portal is enabled
            if portal_config.get('enabled'):
                # Validate portal_cost if provided
                if 'portal_cost' in portal_config:
                    if not isinstance(portal_config['portal_cost'], int):
                        return jsonify({
                            "success": False,
                            "message": "Invalid type for cross_server_portal.portal_cost, expected int"
                        }), 400
                    if portal_config['portal_cost'] < 100 or portal_config['portal_cost'] > 100000:
                        return jsonify({
                            "success": False,
                            "message": "cross_server_portal.portal_cost must be between 100 and 100000"
                        }), 400

                # Validate display_name if provided
                if 'display_name' in portal_config and portal_config['display_name']:
                    if not isinstance(portal_config['display_name'], str):
                        return jsonify({
                            "success": False,
                            "message": "Invalid type for cross_server_portal.display_name, expected string"
                        }), 400
                    if len(portal_config['display_name']) > 50:
                        return jsonify({
                            "success": False,
                            "message": "cross_server_portal.display_name must be 50 characters or less"
                        }), 400

        # Update settings in database
        settings_manager = get_settings_manager()
        success = settings_manager.guild_dao.update_guild_settings(int(guild_id), settings)

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to update settings in database"
            }), 500

        print(f"Successfully updated settings for guild {guild_id}")

        return jsonify({
            "success": True,
            "message": "Settings updated successfully",
            "data": {
                "guild_id": guild_id,
                "settings": settings,
                "updated_at": datetime.now().isoformat()
            }
        })

    except Exception as e:
        print(f"Error updating guild config: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500

@guilds_bp.route('/guilds/<guild_id>/leveling', methods=['PUT'])
@require_auth
def update_leveling_settings(guild_id):
    """Update leveling settings"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Validate request data
        try:
            updates = UpdateLevelingSettingsRequest(**request.json)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Invalid request data",
                "error": str(e)
            }), 400

        # Get current settings
        settings_manager = get_settings_manager()
        settings = settings_manager.get_guild_settings(guild_id)
        settings_dict = settings.dict()

        # Update leveling settings
        if 'leveling' not in settings_dict:
            settings_dict['leveling'] = {}

        settings_dict['leveling'].update({
            'enabled': updates.enabled,
            'exp_per_message': updates.exp_per_message,
            'exp_cooldown_seconds': updates.exp_cooldown_seconds,
            'level_up_announcements': updates.level_up_announcements,
            'announcement_channel_id': updates.announcement_channel_id
        })

        # Save updated settings
        success = settings_manager.guild_dao.update_guild_settings(int(guild_id), settings_dict)

        if success:
            return jsonify({
                "success": True,
                "message": "Leveling settings updated successfully",
                "data": settings_dict['leveling']
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update leveling settings"
            }), 500

    except Exception as e:
        print(f"Error updating leveling settings: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500

@guilds_bp.route('/guilds/<guild_id>/ai', methods=['PUT'])
@require_auth
def update_ai_settings(guild_id):
    """Update AI settings"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server"
            }), 403

        # Validate request data
        try:
            updates = UpdateAISettingsRequest(**request.json)
        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Invalid request data",
                "error": str(e)
            }), 400

        # Get current settings
        settings_manager = get_settings_manager()
        settings = settings_manager.get_guild_settings(guild_id)
        settings_dict = settings.dict()

        # Update AI settings
        if 'ai' not in settings_dict:
            settings_dict['ai'] = {}

        settings_dict['ai'].update({
            'enabled': updates.enabled,
            'instructions': updates.instructions,
            'model': updates.model,
            'daily_limit': updates.daily_limit
        })

        # Save updated settings
        success = settings_manager.guild_dao.update_guild_settings(int(guild_id), settings_dict)

        if success:
            return jsonify({
                "success": True,
                "message": "AI settings updated successfully",
                "data": settings_dict['ai']
            })
        else:
            return jsonify({
                "success": False,
                "message": "Failed to update AI settings"
            }), 500

    except Exception as e:
        print(f"Error updating AI settings: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500
