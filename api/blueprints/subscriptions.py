"""Subscription management and Stripe webhook endpoints"""
import sys
import os
import logging
from pathlib import Path
from flask import Blueprint, jsonify, request
from datetime import datetime
from api.middleware.auth_decorators import require_auth
from api.services.dao_imports import GuildDao
from api.services.discord_integration import check_admin_sync
from api.services.stripe_service import StripeService

# Ensure bot path is in sys.path for DAO imports
current_dir = Path(__file__).parent.parent.parent
bot_project_path = current_dir.parent / "acosmibot"
if str(bot_project_path) not in sys.path:
    sys.path.insert(0, str(bot_project_path))

from Dao.SubscriptionDao import SubscriptionDao

logger = logging.getLogger(__name__)
subscriptions_bp = Blueprint('subscriptions', __name__, url_prefix='/api')

# Initialize Stripe service
stripe_service = StripeService()
WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')


@subscriptions_bp.route('/guilds/<guild_id>/subscription', methods=['GET'])
@require_auth
def get_subscription(guild_id):
    """Get current subscription status for a guild"""
    try:
        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to view this server's subscription"
            }), 403

        # Get guild tier from Guilds table (primary source of truth)
        with GuildDao() as guild_dao:
            guild = guild_dao.find_by_id(int(guild_id))
            tier = guild.subscription_tier if guild else 'free'
            status = guild.subscription_status if guild else 'active'

        # Get subscription from Subscriptions table (Stripe billing info)
        with SubscriptionDao() as sub_dao:
            subscription = sub_dao.get_by_guild_id(str(guild_id))

        # Always return the tier from Guilds table
        return jsonify({
            "success": True,
            "subscription": subscription.to_dict() if subscription else None,
            "tier": tier,
            "status": status
        })

    except Exception as e:
        logger.error(f"Error getting subscription for guild {guild_id}: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@subscriptions_bp.route('/subscriptions/create-checkout', methods=['POST'])
@require_auth
def create_checkout():
    """Create a Stripe checkout session"""
    try:
        data = request.get_json()
        if not data or 'guild_id' not in data:
            return jsonify({
                "success": False,
                "message": "guild_id is required"
            }), 400

        guild_id = data['guild_id']
        tier = data.get('tier', 'premium')  # Default to 'premium' if not specified

        # Validate tier
        if tier not in ['premium', 'premium_plus_ai']:
            return jsonify({
                "success": False,
                "message": "Invalid tier. Must be 'premium' or 'premium_plus_ai'"
            }), 400

        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server's subscription"
            }), 403

        # Check if guild already has active subscription
        with SubscriptionDao() as sub_dao:
            existing = sub_dao.get_by_guild_id(str(guild_id))

        if existing and existing.status == 'active':
            # Allow upgrade from premium to premium_plus_ai
            if existing.tier == 'premium' and tier == 'premium':
                return jsonify({
                    "success": False,
                    "message": "Guild already has an active premium subscription"
                }), 400
            elif existing.tier == 'premium_plus_ai':
                return jsonify({
                    "success": False,
                    "message": "Guild already has an active premium plus AI subscription"
                }), 400

        # Get guild info
        with GuildDao() as guild_dao:
            guild = guild_dao.find_by_id(int(guild_id))
            if not guild:
                return jsonify({
                    "success": False,
                    "message": "Guild not found"
                }), 404

        # Create checkout session
        success_url = data.get('success_url', 'https://acosmibot.com/subscription-success')
        cancel_url = data.get('cancel_url', f'https://acosmibot.com/guild-dashboard?guild={guild_id}')

        session = stripe_service.create_checkout_session(
            guild_id=str(guild_id),
            guild_name=guild.name,
            user_id=str(request.user_id),
            success_url=success_url,
            cancel_url=cancel_url,
            tier=tier
        )

        if not session:
            return jsonify({
                "success": False,
                "message": "Failed to create checkout session"
            }), 500

        return jsonify({
            "success": True,
            "checkout_url": session['checkout_url'],
            "session_id": session['session_id']
        })

    except Exception as e:
        logger.error(f"Error creating checkout session: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@subscriptions_bp.route('/subscriptions/cancel', methods=['POST'])
@require_auth
def cancel_subscription():
    """Cancel a subscription"""
    try:
        data = request.get_json()
        if not data or 'guild_id' not in data:
            return jsonify({
                "success": False,
                "message": "guild_id is required"
            }), 400

        guild_id = data['guild_id']

        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server's subscription"
            }), 403

        # Get subscription
        with SubscriptionDao() as sub_dao:
            subscription = sub_dao.get_by_guild_id(str(guild_id))

        if not subscription or not subscription.stripe_subscription_id:
            return jsonify({
                "success": False,
                "message": "No active subscription found"
            }), 404

        # Cancel in Stripe (at period end by default)
        immediately = data.get('immediately', False)
        success = stripe_service.cancel_subscription(
            subscription.stripe_subscription_id,
            immediately=immediately
        )

        if not success:
            return jsonify({
                "success": False,
                "message": "Failed to cancel subscription with Stripe"
            }), 500

        # Update database
        with SubscriptionDao() as sub_dao:
            sub_dao.update_subscription(
                guild_id=str(guild_id),
                status='canceled' if immediately else 'active',
                cancel_at_period_end=True
            )

        message = "Subscription canceled immediately" if immediately else "Subscription will cancel at end of billing period"

        return jsonify({
            "success": True,
            "message": message
        })

    except Exception as e:
        logger.error(f"Error canceling subscription: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@subscriptions_bp.route('/subscriptions/portal', methods=['POST'])
@require_auth
def customer_portal():
    """Get Stripe customer portal URL"""
    try:
        data = request.get_json()
        if not data or 'guild_id' not in data:
            return jsonify({
                "success": False,
                "message": "guild_id is required"
            }), 400

        guild_id = data['guild_id']

        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server's subscription"
            }), 403

        # Get subscription
        with SubscriptionDao() as sub_dao:
            subscription = sub_dao.get_by_guild_id(str(guild_id))

        # If no Stripe subscription exists, redirect to premium page to start subscription
        if not subscription or not subscription.stripe_customer_id:
            return jsonify({
                "success": False,
                "message": "No active Stripe subscription. Please upgrade through the Premium page.",
                "redirect_to_premium": True
            }), 404

        return_url = data.get('return_url', f'https://acosmibot.com/guild-dashboard?guild={guild_id}')

        # Create portal session
        portal_url = stripe_service.create_customer_portal_session(
            customer_id=subscription.stripe_customer_id,
            return_url=return_url
        )

        if not portal_url:
            return jsonify({
                "success": False,
                "message": "Failed to create portal session"
            }), 500

        return jsonify({
            "success": True,
            "portal_url": portal_url
        })

    except Exception as e:
        logger.error(f"Error creating portal session: {e}")
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@subscriptions_bp.route('/subscriptions/test-upgrade', methods=['POST'])
@require_auth
def test_upgrade_guild():
    """TEST ONLY: Manually upgrade a guild to any tier for testing"""
    try:
        # Disable in production - only allow when using test Stripe keys
        stripe_key = os.getenv('STRIPE_SECRET_KEY', '')
        if stripe_key.startswith('sk_live_'):
            return jsonify({
                "success": False,
                "message": "Test endpoint disabled when using live Stripe keys"
            }), 403

        data = request.get_json()
        if not data or 'guild_id' not in data:
            return jsonify({
                "success": False,
                "message": "guild_id is required"
            }), 400

        guild_id = data['guild_id']
        tier = data.get('tier', 'premium')  # Default to 'premium' if not specified

        # Validate tier
        if tier not in ['free', 'premium', 'premium_plus_ai']:
            return jsonify({
                "success": False,
                "message": "Invalid tier. Must be 'free', 'premium', or 'premium_plus_ai'"
            }), 400

        # Check permissions
        has_admin = check_admin_sync(request.user_id, guild_id)
        if not has_admin:
            return jsonify({
                "success": False,
                "message": "You don't have permission to manage this server's subscription"
            }), 403

        # Update Guilds table to set tier
        with GuildDao() as guild_dao:
            guild_dao.execute_query(
                "UPDATE Guilds SET subscription_tier = %s, subscription_status = 'active' WHERE id = %s",
                (tier, int(guild_id)),
                commit=True
            )

        logger.info(f"Test upgrade: Guild {guild_id} upgraded to {tier} by user {request.user_id}")

        return jsonify({
            "success": True,
            "message": f"Guild upgraded to {tier} (test mode)"
        })

    except Exception as e:
        logger.error(f"Error in test upgrade for guild {guild_id}: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "message": "Internal server error",
            "error": str(e)
        }), 500


@subscriptions_bp.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """
    Stripe webhook handler - THIS IS CRITICAL FOR SUBSCRIPTION MANAGEMENT

    This endpoint handles all Stripe events and updates the database accordingly.
    It must be publicly accessible and configured in Stripe Dashboard.
    """
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    if not sig_header:
        logger.error("No Stripe signature in webhook request")
        return jsonify({"error": "No signature"}), 400

    if not WEBHOOK_SECRET:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        return jsonify({"error": "Webhook secret not configured"}), 500

    # Verify webhook signature
    event = stripe_service.verify_webhook_signature(payload, sig_header, WEBHOOK_SECRET)

    if not event:
        logger.error("Invalid webhook signature")
        return jsonify({"error": "Invalid signature"}), 400

    event_type = event['type']
    logger.info(f"Received Stripe webhook: {event_type}")

    try:
        # Handle different event types
        if event_type == 'checkout.session.completed':
            handle_checkout_completed(event['data']['object'])

        elif event_type == 'customer.subscription.updated':
            handle_subscription_updated(event['data']['object'])

        elif event_type == 'customer.subscription.deleted':
            handle_subscription_deleted(event['data']['object'])

        elif event_type == 'invoice.payment_failed':
            handle_payment_failed(event['data']['object'])

        elif event_type == 'invoice.payment_succeeded':
            handle_payment_succeeded(event['data']['object'])

        else:
            logger.info(f"Unhandled webhook event type: {event_type}")

        return jsonify({"success": True}), 200

    except Exception as e:
        logger.error(f"Error handling webhook {event_type}: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def handle_checkout_completed(session):
    """Handle successful checkout completion"""
    logger.info(f"Checkout completed: {session['id']}")

    guild_id = session['metadata'].get('guild_id')
    if not guild_id:
        logger.error("No guild_id in checkout session metadata")
        return

    # Extract tier from metadata (default to 'premium' for backwards compatibility)
    tier = session['metadata'].get('tier', 'premium')

    subscription_id = session.get('subscription')
    customer_id = session.get('customer')

    if not subscription_id:
        logger.error("No subscription ID in checkout session")
        return

    # Get subscription details from Stripe
    subscription_data = stripe_service.get_subscription(subscription_id)
    if not subscription_data:
        logger.error(f"Failed to get subscription details for {subscription_id}")
        return

    # Create subscription record
    with SubscriptionDao() as sub_dao:
        sub_dao.create_subscription(
            guild_id=guild_id,
            tier=tier,
            stripe_subscription_id=subscription_id,
            stripe_customer_id=customer_id,
            current_period_start=datetime.fromtimestamp(subscription_data['current_period_start']),
            current_period_end=datetime.fromtimestamp(subscription_data['current_period_end'])
        )

    # Update Guild tier
    with GuildDao() as guild_dao:
        guild_dao.execute_query(
            "UPDATE Guilds SET subscription_tier = %s, subscription_status = 'active' WHERE id = %s",
            (tier, int(guild_id)),
            commit=True
        )

    logger.info(f"Subscription created for guild {guild_id} with tier {tier}")


def handle_subscription_updated(subscription):
    """Handle subscription update events"""
    logger.info(f"Subscription updated: {subscription['id']}")

    subscription_id = subscription['id']
    status = subscription['status']

    # Get period dates from subscription items
    current_period_start = subscription.get('billing_cycle_anchor') or subscription.get('created')
    current_period_end = current_period_start

    if subscription.get('items') and subscription['items'].get('data'):
        item = subscription['items']['data'][0]
        current_period_start = item.get('current_period_start', current_period_start)
        current_period_end = item.get('current_period_end', current_period_end)

    # Get cancel_at_period_end value
    # Stripe now uses 'cancel_at' timestamp instead of cancel_at_period_end boolean
    cancel_at = subscription.get('cancel_at')
    cancel_at_period_end = subscription.get('cancel_at_period_end', False) or (cancel_at is not None)

    logger.info(f"Updating subscription {subscription_id}: status={status}, cancel_at={cancel_at}, cancel_at_period_end={cancel_at_period_end}")

    # Update subscription in database
    with SubscriptionDao() as sub_dao:
        sub_dao.update_by_stripe_subscription_id(
            stripe_subscription_id=subscription_id,
            status=status,
            current_period_start=datetime.fromtimestamp(current_period_start),
            current_period_end=datetime.fromtimestamp(current_period_end),
            cancel_at_period_end=cancel_at_period_end,
            cancel_at=datetime.fromtimestamp(cancel_at) if cancel_at else None
        )

        # Get guild_id
        subscription_record = sub_dao.get_by_stripe_subscription_id(subscription_id)

    if subscription_record:
        # Update Guild status
        with GuildDao() as guild_dao:
            guild_dao.execute_query(
                "UPDATE Guilds SET subscription_status = %s WHERE id = %s",
                (status, int(subscription_record.guild_id)),
                commit=True
            )

    logger.info(f"Subscription {subscription_id} updated to status: {status}")


def handle_subscription_deleted(subscription):
    """Handle subscription deletion/cancellation"""
    logger.info(f"Subscription deleted: {subscription['id']}")

    subscription_id = subscription['id']

    with SubscriptionDao() as sub_dao:
        # Get subscription record
        subscription_record = sub_dao.get_by_stripe_subscription_id(subscription_id)

        if subscription_record:
            # Update subscription status
            sub_dao.update_by_stripe_subscription_id(
                stripe_subscription_id=subscription_id,
                status='canceled'
            )

            # Downgrade guild to free tier
            with GuildDao() as guild_dao:
                guild_dao.execute_query(
                    "UPDATE Guilds SET subscription_tier = 'free', subscription_status = 'canceled' WHERE id = %s",
                    (int(subscription_record.guild_id),),
                    commit=True
                )

            logger.info(f"Guild {subscription_record.guild_id} downgraded to free tier")


def handle_payment_failed(invoice):
    """Handle failed payment"""
    logger.warning(f"Payment failed for invoice: {invoice['id']}")

    subscription_id = invoice.get('subscription')
    if not subscription_id:
        return

    with SubscriptionDao() as sub_dao:
        # Mark subscription as past_due
        sub_dao.update_by_stripe_subscription_id(
            stripe_subscription_id=subscription_id,
            status='past_due'
        )

        # Get subscription record
        subscription_record = sub_dao.get_by_stripe_subscription_id(subscription_id)

        if subscription_record:
            # Update guild status
            with GuildDao() as guild_dao:
                guild_dao.execute_query(
                    "UPDATE Guilds SET subscription_status = 'past_due' WHERE id = %s",
                    (int(subscription_record.guild_id),),
                    commit=True
                )

    logger.info(f"Subscription {subscription_id} marked as past_due")


def handle_payment_succeeded(invoice):
    """Handle successful payment"""
    logger.info(f"Payment succeeded for invoice: {invoice['id']}")

    subscription_id = invoice.get('subscription')
    if not subscription_id:
        return

    with SubscriptionDao() as sub_dao:
        # Mark subscription as active
        sub_dao.update_by_stripe_subscription_id(
            stripe_subscription_id=subscription_id,
            status='active'
        )

        # Get subscription record
        subscription_record = sub_dao.get_by_stripe_subscription_id(subscription_id)

        if subscription_record:
            # Preserve existing tier when reactivating subscription
            tier = subscription_record.tier if subscription_record.tier else 'premium'

            # Ensure guild has correct tier and active status
            with GuildDao() as guild_dao:
                guild_dao.execute_query(
                    "UPDATE Guilds SET subscription_tier = %s, subscription_status = 'active' WHERE id = %s",
                    (tier, int(subscription_record.guild_id)),
                    commit=True
                )

    logger.info(f"Subscription {subscription_id} marked as active")
