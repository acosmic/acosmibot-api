import os
import aiohttp
import hashlib
import hmac
from datetime import datetime, timedelta

import logging
logger = logging.getLogger(__name__)

class YouTubeWebSubService:
    HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
    VERIFY_TOKEN_LENGTH = 64 # Recommended length for hub.verify_token

    def __init__(self, callback_url: str):
        if not callback_url:
            raise ValueError("YOUTUBE_WEBHOOK_CALLBACK_URL environment variable is not set.")
        self.callback_url = callback_url
        self.secret = os.getenv("YOUTUBE_WEBHOOK_SECRET", None) # Optional, but highly recommended for authenticity

    async def _send_hub_request(self, mode: str, topic_url: str, lease_seconds: int = 432000) -> bool:
        """
        Sends a request to the PubSubHubbub hub to subscribe or unsubscribe.
        lease_seconds is 5 days (5 * 24 * 60 * 60) by default, max 7 days.
        """
        data = {
            "hub.mode": mode,
            "hub.callback": self.callback_url,
            "hub.topic": topic_url,
            "hub.lease_seconds": lease_seconds,
            "hub.secret": self.secret if self.secret else None, # Include secret if set
            "hub.verify": "async" # Use async verification
        }
        
        # Remove hub.secret if it's None to avoid sending 'null'
        if data["hub.secret"] is None:
            del data["hub.secret"]

        logger.info(f"Sending WebSub request to hub. URL: {self.HUB_URL}, Mode: {mode}, Callback: {self.callback_url}, Topic: {topic_url}")
        logger.debug(f"Full WebSub request data: {data}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.HUB_URL, data=data) as response:
                    response_text = await response.text()
                    logger.info(f"Hub response status: {response.status}, Body: {response_text}")
                    response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
                    logger.info(f"WebSub {mode} request for {topic_url} successful.")
                    return True
        except aiohttp.ClientError as e:
            logger.error(f"WebSub {mode} request for {topic_url} failed: {e}", exc_info=True)
            return False

    async def subscribe(self, channel_id: str, lease_seconds: int = 432000) -> bool:
        """
        Subscribes to a YouTube channel's updates.
        Topic URL format: https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID
        """
        topic_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        return await self._send_hub_request("subscribe", topic_url, lease_seconds)

    async def unsubscribe(self, channel_id: str) -> bool:
        """
        Unsubscribes from a YouTube channel's updates.
        """
        topic_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        return await self._send_hub_request("unsubscribe", topic_url)
    
    @staticmethod
    def generate_verify_token() -> str:
        """Generates a random string to use as hub.verify_token."""
        return os.urandom(YouTubeWebSubService.VERIFY_TOKEN_LENGTH).hex()

    def verify_signature(self, signature_header: str, payload: bytes) -> bool:
        """
        Verifies the X-Hub-Signature header from a WebSub notification.
        The signature is a SHA1 HMAC of the payload using the hub.secret.
        Format: sha1=SIGNATURE_HEX
        """
        if not self.secret:
            logger.warning("YOUTUBE_WEBHOOK_SECRET is not set. Cannot verify webhook signature.")
            return False

        try:
            algorithm, signature = signature_header.split('=', 1)
            if algorithm != 'sha1':
                logger.warning(f"Unsupported signature algorithm: {algorithm}. Expected 'sha1'.")
                return False
            
            # Calculate HMAC SHA1 hash of the payload using the secret
            hmac_obj = hmac.new(self.secret.encode('utf-8'), payload, hashlib.sha1)
            calculated_signature = hmac_obj.hexdigest()

            if hmac.compare_digest(calculated_signature, signature):
                logger.debug("Webhook signature verified successfully.")
                return True
            else:
                logger.warning("Webhook signature verification failed: Signatures do not match.")
                return False
        except Exception as e:
            logger.error(f"Error during webhook signature verification: {e}", exc_info=True)
            return False
