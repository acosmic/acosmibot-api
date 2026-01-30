"""
Kick webhook subscription management service
Handles creating, deleting, and monitoring webhook subscriptions via Kick API
"""
import os
import aiohttp
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class KickWebhookService:
    """Manages Kick webhook subscriptions via API"""

    def __init__(self):
        self.client_id = os.getenv("KICK_CLIENT_ID")
        self.client_secret = os.getenv("KICK_CLIENT_SECRET")
        self.webhook_secret = os.getenv("KICK_WEBHOOK_SECRET")
        self.callback_url = os.getenv("KICK_WEBHOOK_CALLBACK_URL",
                                       "https://api.acosmibot.com/api/webhooks/kick")
        # Kick uses their public API v1
        self.base_url = "https://api.kick.com/public/v1"
        self.auth_url = "https://id.kick.com/oauth/token"
        self._access_token: Optional[str] = None
        self._token_expires_at: Optional[datetime] = None

    async def _get_app_access_token(self, session: aiohttp.ClientSession) -> str:
        """Get app access token using client credentials flow"""
        # Check if token is still valid
        if self._access_token and self._token_expires_at:
            if datetime.now() < self._token_expires_at:
                return self._access_token

        try:
            async with session.post(self.auth_url, data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials'
            }, timeout=10) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._access_token = data['access_token']

                # Store expiration if provided
                if 'expires_in' in data:
                    self._token_expires_at = datetime.now() + timedelta(seconds=data['expires_in'] - 60)

                logger.info("Successfully obtained Kick access token for webhook subscriptions")
                return self._access_token
        except Exception as e:
            logger.error(f"Failed to get Kick app access token: {e}")
            raise

    async def _get_headers(self, session: aiohttp.ClientSession) -> Dict[str, str]:
        """Get headers for Kick API requests"""
        token = await self._get_app_access_token(session)
        return {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    async def create_livestream_subscription(
        self,
        session: aiohttp.ClientSession,
        broadcaster_user_id: str
    ) -> Optional[str]:
        """
        Create webhook subscription for livestream status updates

        Subscribes to the livestream.status.updated event which fires when:
        - A stream starts (is_live: true)
        - A stream ends (is_live: false)

        Args:
            session: aiohttp session
            broadcaster_user_id: Kick broadcaster user ID (as string or int)

        Returns:
            Subscription ID on success, None on failure
        """
        # Kick's event subscriptions endpoint (per official API docs)
        url = f"{self.base_url}/events/subscriptions"
        headers = await self._get_headers(session)

        # Payload format per Kick API documentation
        # livestream.status.updated covers both stream starting and ending
        payload = {
            "broadcaster_user_id": int(broadcaster_user_id),
            "events": [
                {
                    "name": "livestream.status.updated",
                    "version": 1
                }
            ],
            "method": "webhook"
        }

        try:
            async with session.post(url, headers=headers, json=payload, timeout=15) as resp:
                if resp.status in [200, 201, 202]:  # Success or Accepted
                    data = await resp.json()
                    logger.info(f"Kick subscription response: {data}")

                    # Extract subscription IDs from response
                    # Response format: {"data": [{"subscription_id": "...", "name": "stream.online", ...}, ...]}
                    if 'data' in data and isinstance(data['data'], list) and len(data['data']) > 0:
                        # Get the first subscription ID (both events should have same subscription flow)
                        first_sub = data['data'][0]
                        subscription_id = first_sub.get('subscription_id')

                        if subscription_id:
                            logger.info(f"Created Kick subscriptions for broadcaster {broadcaster_user_id}: {subscription_id}")
                            return str(subscription_id)
                        else:
                            # Check if there was an error
                            error = first_sub.get('error')
                            if error:
                                logger.error(f"Kick API returned error: {error}")
                            else:
                                logger.error(f"Kick API returned success but no subscription ID: {data}")
                            return None
                    else:
                        logger.error(f"Unexpected Kick API response format: {data}")
                        return None

                elif resp.status == 409:  # Conflict - subscription already exists
                    logger.warning(f"Kick subscription already exists for broadcaster {broadcaster_user_id}")
                    data = await resp.json()
                    # Try to extract existing subscription ID
                    if 'data' in data and isinstance(data['data'], list) and len(data['data']) > 0:
                        existing_id = data['data'][0].get('subscription_id')
                        return str(existing_id) if existing_id else None
                    return None

                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to create Kick subscription (status {resp.status}): {error_text}")
                    return None

        except aiohttp.ClientError as e:
            logger.error(f"HTTP error creating Kick subscription: {e}")
            return None
        except Exception as e:
            logger.error(f"Error creating Kick subscription: {e}", exc_info=True)
            return None

    async def delete_subscription(
        self,
        session: aiohttp.ClientSession,
        subscription_id: str
    ) -> bool:
        """
        Delete a webhook subscription

        Args:
            session: aiohttp session
            subscription_id: Subscription ID to delete

        Returns:
            True on success, False on failure
        """
        # Kick's event subscriptions endpoint with ID as query parameter
        url = f"{self.base_url}/events/subscriptions"
        headers = await self._get_headers(session)
        params = {"id": subscription_id}

        try:
            async with session.delete(url, headers=headers, params=params, timeout=15) as resp:
                if resp.status in [200, 204]:  # Success or No Content
                    logger.info(f"Deleted Kick subscription {subscription_id}")
                    return True
                elif resp.status == 404:
                    logger.warning(f"Kick subscription {subscription_id} not found (may already be deleted)")
                    return True  # Consider it successful if already gone
                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to delete Kick subscription {subscription_id} (status {resp.status}): {error_text}")
                    return False

        except aiohttp.ClientError as e:
            logger.error(f"HTTP error deleting Kick subscription {subscription_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error deleting Kick subscription {subscription_id}: {e}")
            return False

    async def get_subscriptions(
        self,
        session: aiohttp.ClientSession,
        broadcaster_user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get webhook subscriptions

        Args:
            session: aiohttp session
            broadcaster_user_id: Optional filter by broadcaster

        Returns:
            List of subscription objects
        """
        # Kick's event subscriptions endpoint
        url = f"{self.base_url}/events/subscriptions"
        headers = await self._get_headers(session)

        params = {}
        if broadcaster_user_id:
            params['broadcaster_user_id'] = int(broadcaster_user_id)

        try:
            async with session.get(url, headers=headers, params=params, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Handle response structure per Kick API docs
                    if isinstance(data, dict) and 'data' in data:
                        return data['data']
                    elif isinstance(data, list):
                        return data
                    else:
                        return []
                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to get Kick subscriptions (status {resp.status}): {error_text}")
                    return []

        except Exception as e:
            logger.error(f"Error fetching Kick subscriptions: {e}")
            return []

    def verify_webhook_signature(
        self,
        signature: str,
        message_id: str,
        timestamp: str,
        body: bytes
    ) -> bool:
        """
        Verify webhook signature from Kick

        Args:
            signature: Signature header from Kick
            message_id: Message ID header
            timestamp: Timestamp header
            body: Raw request body

        Returns:
            True if signature is valid, False otherwise
        """
        if not self.webhook_secret:
            logger.warning("No webhook secret configured, skipping signature verification")
            return True

        import hmac
        import hashlib

        try:
            # Construct message: message_id.timestamp.body
            # This is a common pattern, adjust if Kick uses different format
            message = f"{message_id}.{timestamp}.".encode() + body

            # Compute HMAC-SHA256
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                message,
                hashlib.sha256
            ).hexdigest()

            # Constant-time comparison
            return hmac.compare_digest(signature, expected_signature)

        except Exception as e:
            logger.error(f"Error verifying Kick webhook signature: {e}")
            return False
