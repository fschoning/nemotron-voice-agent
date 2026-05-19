import asyncio
import os
import sys
import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from loguru import logger

# Import the bot components from our updated astrology_call_line.py
from apps.astrology_call_line import bot
from pipecat.runner.types import RunnerArguments

# Configure logging once at daemon startup
logging.getLogger("aiortc").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("pipecat").setLevel(logging.INFO)

logger.remove()
logger.add(sys.stderr, level="INFO", filter=lambda record: 
           "pipecat.transports.smallwebrtc" not in record["name"] and 
           "pipecat.runner" not in record["name"] and
           "pipecat.services" not in record["name"])

app = FastAPI(title="Nemotron Voice Agent Webhook Server")

# We only support a single concurrent bot task in this V1 architecture
active_bot_task = None
active_room_uid = None

class JoinRoomRequest(BaseModel):
    roomUrl: str
    apptUid: str
    tenant: str
    sessionData: dict

def _on_bot_task_done(task):
    """Callback fired when the bot asyncio.Task finishes (normally or via exception).
    Clears the global state so the next /join-room call can proceed."""
    global active_bot_task, active_room_uid
    uid = active_room_uid
    active_bot_task = None
    active_room_uid = None
    
    exc = task.exception() if not task.cancelled() else None
    if exc:
        logger.error(f"Bot task for {uid} ended with error: {exc}")
    else:
        logger.info(f"Bot task for {uid} completed naturally.")

@app.post("/join-room")
async def join_room(req: JoinRoomRequest):
    global active_bot_task, active_room_uid
    
    if active_bot_task and not active_bot_task.done():
        if active_room_uid == req.apptUid:
            logger.info(f"Bot is already running for room {req.apptUid}. Ignoring duplicate request.")
            return {"status": "already_running"}
        else:
            logger.error(f"Cannot join {req.apptUid}. Bot is busy with {active_room_uid}.")
            raise HTTPException(status_code=409, detail="Bot is busy with another session")

    active_room_uid = req.apptUid
    logger.info(f"Spawning Pipecat bot for {req.roomUrl}...")
    
    runner_args = RunnerArguments()
    active_bot_task = asyncio.create_task(
        bot(runner_args, req.roomUrl, req.sessionData, req.tenant, req.apptUid)
    )
    active_bot_task.add_done_callback(_on_bot_task_done)
    
    return {"status": "started"}

@app.post("/leave-room")
async def leave_room(apptUid: str):
    global active_bot_task, active_room_uid
    
    if active_room_uid != apptUid:
        return {"status": "not_running"}
        
    if active_bot_task and not active_bot_task.done():
        logger.info(f"Instructed to leave room {apptUid}. Cancelling bot task...")
        active_bot_task.cancel()
        # done_callback will clear the globals
        return {"status": "stopped"}
        
    return {"status": "not_running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8090)
