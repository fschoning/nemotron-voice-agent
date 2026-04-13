import asyncio
import base64
import json
from loguru import logger
import httpx

class AttendeeClient:
    """Client for the Attendee REST API."""
    
    BASE_URL = "https://app.attendee.dev/api/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json"
        }
    
    async def create_bot(self, meeting_url: str, bot_name: str, ws_url: str, webhook_url: str = None) -> dict:
        """Creates a new Attendee bot to join a meeting."""
        payload = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "websocket_settings": {
                "audio": {
                    "url": ws_url,
                    "sample_rate": 16000
                }
            },
            "automatic_leave_settings": {
                "silence_timeout_seconds": 600,
                "waiting_room_timeout_seconds": 900,
                "only_participant_in_meeting_timeout_seconds": 60
            }
        }
        
        if webhook_url:
            payload["webhooks"] = [{
                "url": webhook_url,
                "triggers": ["bot.state_change"]
            }]

        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"Creating Attendee bot for meeting: {meeting_url}")
                response = await client.post(f"{self.BASE_URL}/bots", headers=self.headers, json=payload, timeout=30.0)
                response.raise_for_status()
                data = response.json()
                logger.info(f"Bot created successfully with ID: {data.get('id')}")
                return data
            except httpx.HTTPError as e:
                logger.error(f"Failed to create Attendee bot: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Response: {e.response.text}")
                raise

    async def get_bot_status(self, bot_id: str) -> dict:
        """Gets the status of an existing Attendee bot."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(f"{self.BASE_URL}/bots/{bot_id}", headers=self.headers, timeout=10.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as e:
                logger.error(f"Failed to get bot status for {bot_id}: {e}")
                raise

    async def delete_bot(self, bot_id: str) -> bool:
        """Removes the bot from the meeting."""
        async with httpx.AsyncClient() as client:
            try:
                logger.info(f"Attempting to remove bot {bot_id}")
                # Attendee API might just use DELETE /bots/{id} or we might need to rely on automatic leave.
                # Documentation says GET is supported. DELETE is commonly used for removal.
                response = await client.delete(f"{self.BASE_URL}/bots/{bot_id}", headers=self.headers, timeout=10.0)
                response.raise_for_status()
                logger.info(f"Successfully requested removal for bot {bot_id}")
                return True
            except httpx.HTTPError as e:
                logger.error(f"Failed to delete bot {bot_id}: {e}")
                return False
