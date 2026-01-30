"""Stripe payment service wrapper"""
import stripe
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Initialize Stripe with secret key
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

class StripeService:
    """Service for handling Stripe payment operations"""

    def __init__(self):
        self.premium_price_id = os.getenv('STRIPE_PREMIUM_PRICE_ID')
        self.premium_plus_ai_price_id = os.getenv('STRIPE_PREMIUM_PLUS_AI_PRICE_ID')

        if not stripe.api_key:
            logger.error("STRIPE_SECRET_KEY not set in environment variables")
            raise ValueError("Stripe API key not configured")

        if not self.premium_price_id:
            logger.error("STRIPE_PREMIUM_PRICE_ID not set in environment variables")
            raise ValueError("Stripe premium price ID not configured")

        if not self.premium_plus_ai_price_id:
            logger.error("STRIPE_PREMIUM_PLUS_AI_PRICE_ID not set in environment variables")
            raise ValueError("Stripe premium plus AI price ID not configured")

    def create_checkout_session(
        self,
        guild_id: str,
        guild_name: str,
        user_id: str,
        success_url: str,
        cancel_url: str,
        tier: str = 'premium'
    ) -> Optional[Dict[str, Any]]:
        """
        Create a Stripe Checkout session for premium subscription

        Args:
            guild_id: Discord guild ID
            guild_name: Discord guild name (for display)
            user_id: Discord user ID (who initiated checkout)
            success_url: URL to redirect after successful payment
            cancel_url: URL to redirect if payment is canceled
            tier: Subscription tier ('premium' or 'premium_plus_ai', defaults to 'premium')

        Returns:
            Dictionary with checkout session details or None on error
        """
        try:
            # Select the correct price ID based on tier
            if tier == 'premium_plus_ai':
                price_id = self.premium_plus_ai_price_id
            else:
                price_id = self.premium_price_id

            session = stripe.checkout.Session.create(
                mode='subscription',
                line_items=[{
                    'price': price_id,
                    'quantity': 1
                }],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    'guild_id': str(guild_id),
                    'user_id': str(user_id),
                    'guild_name': guild_name,
                    'tier': tier
                },
                subscription_data={
                    'metadata': {
                        'guild_id': str(guild_id),
                        'guild_name': guild_name,
                        'tier': tier
                    }
                },
                allow_promotion_codes=True,  # Allow promo codes
                billing_address_collection='auto'
            )

            logger.info(f"Created checkout session for guild {guild_id} (tier: {tier}): {session.id}")

            return {
                'session_id': session.id,
                'checkout_url': session.url
            }

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error creating checkout session: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating checkout session: {e}")
            return None

    def create_customer(
        self,
        guild_id: str,
        guild_name: str,
        email: Optional[str] = None
    ) -> Optional[str]:
        """
        Create a Stripe customer

        Args:
            guild_id: Discord guild ID
            guild_name: Discord guild name
            email: Contact email (optional)

        Returns:
            Customer ID or None on error
        """
        try:
            customer = stripe.Customer.create(
                metadata={
                    'guild_id': str(guild_id),
                    'guild_name': guild_name
                },
                email=email,
                description=f"Acosmibot Premium - {guild_name}"
            )

            logger.info(f"Created Stripe customer for guild {guild_id}: {customer.id}")
            return customer.id

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error creating customer: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating customer: {e}")
            return None

    def get_subscription(self, subscription_id: str) -> Optional[Dict[str, Any]]:
        """
        Get subscription details from Stripe

        Args:
            subscription_id: Stripe subscription ID

        Returns:
            Subscription data or None on error
        """
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)

            # Get period dates from the subscription items
            current_period_start = subscription.get('billing_cycle_anchor') or subscription.get('created')
            current_period_end = current_period_start

            # Try to get from items if available
            if subscription.get('items') and subscription['items'].get('data'):
                item = subscription['items']['data'][0]
                current_period_start = item.get('current_period_start', current_period_start)
                current_period_end = item.get('current_period_end', current_period_end)

            return {
                'id': subscription.id,
                'status': subscription.status,
                'current_period_start': current_period_start,
                'current_period_end': current_period_end,
                'cancel_at_period_end': subscription.get('cancel_at_period_end', False),
                'customer_id': subscription.customer
            }

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error retrieving subscription: {e}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving subscription: {e}")
            return None

    def cancel_subscription(
        self,
        subscription_id: str,
        immediately: bool = False
    ) -> bool:
        """
        Cancel a subscription

        Args:
            subscription_id: Stripe subscription ID
            immediately: If True, cancel immediately. If False, cancel at period end.

        Returns:
            True if successful, False otherwise
        """
        try:
            if immediately:
                stripe.Subscription.delete(subscription_id)
                logger.info(f"Canceled subscription immediately: {subscription_id}")
            else:
                stripe.Subscription.modify(
                    subscription_id,
                    cancel_at_period_end=True
                )
                logger.info(f"Scheduled subscription cancellation: {subscription_id}")

            return True

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error canceling subscription: {e}")
            return False
        except Exception as e:
            logger.error(f"Error canceling subscription: {e}")
            return False

    def create_customer_portal_session(
        self,
        customer_id: str,
        return_url: str
    ) -> Optional[str]:
        """
        Create a customer portal session for managing subscription

        Args:
            customer_id: Stripe customer ID
            return_url: URL to return to after portal session

        Returns:
            Portal URL or None on error
        """
        try:
            session = stripe.billing_portal.Session.create(
                customer=customer_id,
                return_url=return_url
            )

            logger.info(f"Created portal session for customer {customer_id}")
            return session.url

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error creating portal session: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating portal session: {e}")
            return None

    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
        webhook_secret: str
    ) -> Optional[Any]:
        """
        Verify Stripe webhook signature and construct event

        Args:
            payload: Raw request body
            signature: Stripe-Signature header value
            webhook_secret: Webhook signing secret

        Returns:
            Stripe Event object or None if verification fails
        """
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, webhook_secret
            )
            return event

        except ValueError as e:
            logger.error(f"Invalid webhook payload: {e}")
            return None
        except stripe._error.SignatureVerificationError as e:
            logger.error(f"Invalid webhook signature: {e}")
            return None
        except Exception as e:
            logger.error(f"Error verifying webhook: {e}")
            return None

    def get_upcoming_invoice(self, customer_id: str) -> Optional[Dict[str, Any]]:
        """
        Get upcoming invoice for a customer

        Args:
            customer_id: Stripe customer ID

        Returns:
            Invoice data or None
        """
        try:
            invoice = stripe.Invoice.upcoming(customer=customer_id)

            return {
                'amount_due': invoice.amount_due,
                'currency': invoice.currency,
                'period_start': invoice.period_start,
                'period_end': invoice.period_end,
                'next_payment_attempt': invoice.next_payment_attempt
            }

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error retrieving upcoming invoice: {e}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving upcoming invoice: {e}")
            return None

    def list_customer_subscriptions(self, customer_id: str) -> list:
        """
        List all subscriptions for a customer

        Args:
            customer_id: Stripe customer ID

        Returns:
            List of subscriptions
        """
        try:
            subscriptions = stripe.Subscription.list(customer=customer_id)
            return subscriptions.data

        except stripe._error.StripeError as e:
            logger.error(f"Stripe error listing subscriptions: {e}")
            return []
        except Exception as e:
            logger.error(f"Error listing subscriptions: {e}")
            return []
