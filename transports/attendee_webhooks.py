import asyncio
import os
from loguru import logger
from fastapi import FastAPI, Request, BackgroundTasks, Response
from fastapi.responses import JSONResponse
import uvicorn
import hashlib
import hmac

class WebhookServer:
    def __init__(self, port: int, zoom_secret: str, meeting_id_to_watch: str = None):
        self.port = port
        self.zoom_secret = zoom_secret
        self.meeting_id_to_watch = meeting_id_to_watch.replace("-", "") if meeting_id_to_watch else None
        self.app = FastAPI(title="Attendee & Zoom Webhooks")
        self.server = None
        self.task = None
        
        # Callbacks
        self.on_participant_joined = None
        self.on_bot_ended = None
        
        self.setup_routes()

    def setup_routes(self):
        @self.app.get("/ping")
        async def ping():
            return {"status": "ok", "message": "Webhook server is live"}

        @self.app.post("/webhooks/zoom")
        async def zoom_webhook(request: Request, background_tasks: BackgroundTasks):
            body = await request.body()
            logger.debug(f"Received request on /webhooks/zoom: {body.decode('utf-8')}")
            
            payload = await request.json()
            event = payload.get("event")
            
            # 1. Handle Endpoint Validation Challenge
            if event == "endpoint.url_validation":
                plain_token = payload.get("payload", {}).get("plainToken")
                encrypted_token = hmac.new(
                    self.zoom_secret.encode('utf-8'),
                    plain_token.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()
                
                return JSONResponse(content={
                    "plainToken": plain_token,
                    "encryptedToken": encrypted_token
                })

            # 2. Handle Participant Joined
            if event == "meeting.participant_joined":
                obj = payload.get("payload", {}).get("object", {})
                meeting_id = str(obj.get("id")).replace("-", "")
                
                logger.info(f"Zoom webhook: Participant joined meeting {meeting_id}")
                
                if self.meeting_id_to_watch is None or meeting_id == self.meeting_id_to_watch:
                    participant_name = obj.get("participant", {}).get("user_name", "Unknown")
                    logger.info(f"Join event for meeting {meeting_id} from {participant_name}! Triggering callback.")
                    if self.on_participant_joined:
                        background_tasks.add_task(self.on_participant_joined)
                else:
                    logger.debug(f"Ignoring join event for meeting {meeting_id} (watching {self.meeting_id_to_watch})")
            
            return Response(status_code=200)

        @self.app.post("/webhooks/attendee")
        async def attendee_webhook(request: Request, background_tasks: BackgroundTasks):
            body = await request.body()
            logger.debug(f"Received request on /webhooks/attendee: {body.decode('utf-8')}")
            
            payload = await request.json()
            event = payload.get("event")
            
            if event == "bot.state_change":
                data = payload.get("data", {})
                bot_id = data.get("bot_id")
                state = data.get("state")
                
                logger.info(f"Attendee bot {bot_id} state changed to: {state}")
                
                if state == "ended":
                    logger.info(f"Bot {bot_id} session ended. Triggering callback.")
                    if self.on_bot_ended:
                        background_tasks.add_task(self.on_bot_ended)
            
            return Response(status_code=200)

    async def start(self):
        config = uvicorn.Config(self.app, host="0.0.0.0", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        logger.info(f"Webhook server listening on port {self.port}")

    async def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.task:
                await self.task
