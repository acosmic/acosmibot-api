"""
Twitch EventSub subscription management service
Handles creating, deleting, and monitoring EventSub subscriptions
"""
import os
import aiohttp
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class TwitchEventSubService:
    """Manages Twitch EventSub subscriptions via API"""

    def __init__(self):
        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET")
        self.webhook_secret = os.getenv("TWITCH_WEBHOOK_SECRET")  # NEW ENV VAR
        self.callback_url = os.getenv("TWITCH_WEBHOOK_CALLBACK_URL",
                                       "https://api.acosmibot.com/api/webhooks/twitch")
        self.base_url = "https://api.twitch.tv/helix"
        self._access_token: Optional[str] = None

    async def _get_app_access_token(self, session: aiohttp.ClientSession) -> str:
        """Get app access token (required for EventSub)"""
        if self._access_token:
            return self._access_token

        try:
            auth_url = "https://id.twitch.tv/oauth2/token"
            async with session.post(auth_url, data={
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'grant_type': 'client_credentials'
            }) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._access_token = data['access_token']
                return self._access_token
        except Exception as e:
            logger.error(f"Failed to get app access token: {e}")
            raise

    async def _get_headers(self, session: aiohttp.ClientSession) -> Dict[str, str]:
        """Get headers for EventSub API requests"""
        token = await self._get_app_access_token(session)
        return {
            'Client-ID': self.client_id,
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }

    async def create_stream_online_subscription(
        self,
        session: aiohttp.ClientSession,
        broadcaster_user_id: str
    ) -> Optional[str]:
        """
        Create stream.online EventSub subscription

        Returns:
            Subscription ID on success, None on failure
        """
        url = f"{self.base_url}/eventsub/subscriptions"
        headers = await self._get_headers(session)

        payload = {
            "type": "stream.online",
            "version": "1",
            "condition": {
                "broadcaster_user_id": broadcaster_user_id
            },
            "transport": {
                "method": "webhook",
                "callback": self.callback_url,
                "secret": self.webhook_secret
            }
        }

        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 202:  # Accepted
                    data = await resp.json()
                    subscription_id = data['data'][0]['id']
                    logger.info(f"Created stream.online subscription {subscription_id} for {broadcaster_user_id}")
                    return subscription_id
                elif resp.status == 409:  # Conflict - subscription already exists
                    logger.warning(f"stream.online subscription already exists for {broadcaster_user_id}")
                    return None
                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to create stream.online subscription: {resp.status} - {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Error creating stream.online subscription: {e}")
            return None

    async def create_stream_offline_subscription(
        self,
        session: aiohttp.ClientSession,
        broadcaster_user_id: str
    ) -> Optional[str]:
        """
        Create stream.offline EventSub subscription

        Returns:
            Subscription ID on success, None on failure
        """
        url = f"{self.base_url}/eventsub/subscriptions"
        headers = await self._get_headers(session)

        payload = {
            "type": "stream.offline",
            "version": "1",
            "condition": {
                "broadcaster_user_id": broadcaster_user_id
            },
            "transport": {
                "method": "webhook",
                "callback": self.callback_url,
                "secret": self.webhook_secret
            }
        }

        try:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 202:
                    data = await resp.json()
                    subscription_id = data['data'][0]['id']
                    logger.info(f"Created stream.offline subscription {subscription_id} for {broadcaster_user_id}")
                    return subscription_id
                elif resp.status == 409:
                    logger.warning(f"stream.offline subscription already exists for {broadcaster_user_id}")
                    return None
                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to create stream.offline subscription: {resp.status} - {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Error creating stream.offline subscription: {e}")
            return None

    async def delete_subscription(
        self,
        session: aiohttp.ClientSession,
        subscription_id: str
    ) -> bool:
        """Delete an EventSub subscription"""
        url = f"{self.base_url}/eventsub/subscriptions"
        headers = await self._get_headers(session)

        try:
            async with session.delete(url, headers=headers, params={'id': subscription_id}) as resp:
                if resp.status == 204:  # No Content - success
                    logger.info(f"Deleted EventSub subscription {subscription_id}")
                    return True
                else:
                    error_text = await resp.text()
                    logger.error(f"Failed to delete subscription {subscription_id}: {resp.status} - {error_text}")
                    return False
        except Exception as e:
            logger.error(f"Error deleting subscription {subscription_id}: {e}")
            return False

    async def get_subscriptions(
        self,
        session: aiohttp.ClientSession,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all EventSub subscriptions

        Args:
            status: Filter by status (enabled, webhook_callback_verification_pending,
                    webhook_callback_verification_failed, etc.)
        """
        url = f"{self.base_url}/eventsub/subscriptions"
        headers = await self._get_headers(session)
        params = {}
        if status:
            params['status'] = status

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data.get('data', [])
        except Exception as e:
            logger.error(f"Error fetching EventSub subscriptions: {e}")
            return []

    def verify_webhook_signature(
        self,
        signature: str,
        message_id: str,
        timestamp: str,
        body: bytes
    ) -> bool:
        """
        Verify webhook signature from Twitch

        Args:
            signature: Twitch-Eventsub-Message-Signature header
            message_id: Twitch-Eventsub-Message-Id header
            timestamp: Twitch-Eventsub-Message-Timestamp header
            body: Raw request body
        """
        import hmac
        import hashlib

        # Construct HMAC message: message_id + timestamp + body
        message = message_id.encode() + timestamp.encode() + body

        # Compute HMAC-SHA256
        expected_signature = 'sha256=' + hmac.new(
            self.webhook_secret.encode(),
            message,
            hashlib.sha256
        ).hexdigest()

        # Constant-time comparison
        return hmac.compare_digest(expected_signature, signature)
