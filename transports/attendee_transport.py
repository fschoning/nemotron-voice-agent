import asyncio
import base64
import json
from loguru import logger
import websockets
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

class AttendeeTransportParams(TransportParams):
    ws_port: int = 8765
    ws_host: str = "0.0.0.0"
    sample_rate: int = 16000

class AttendeeInputProcessor(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._push_frame_task = None

class AttendeeOutputProcessor(FrameProcessor):
    def __init__(self, transport):
        super().__init__()
        self.transport = transport

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, OutputAudioRawFrame):
            # Send audio out to all connected websocket clients (usually just 1 Attendee bot)
            try:
                base64_audio = base64.b64encode(frame.audio).decode("utf-8")
                payload = {
                    "trigger": "realtime_audio.bot_output",
                    "data": {
                        "chunk": base64_audio,
                        "sample_rate": 16000
                    }
                }
                msg = json.dumps(payload)
                
                # Broadcast audio to all connected Attendee websockets
                for bot_id, ws_conn in list(self.transport.connections.items()):
                    if ws_conn.open:
                        await ws_conn.send(msg)
            except Exception as e:
                logger.error(f"Error sending audio to Attendee: {e}")

class AttendeeTransport(BaseTransport):
    """Transport for bridging Pipecat with Attendee's WebSocket audio protocol."""

    def __init__(self, params: AttendeeTransportParams):
        super().__init__(params)
        self.params = params
        self.connections: dict[str, websockets.WebSocketServerProtocol] = {}
        self._ws_server = None
        self._input_processor = AttendeeInputProcessor()
        self._output_processor = AttendeeOutputProcessor(self)
        self._server_task = None
        
        logger.info(f"Initialized AttendeeTransport (port: {self.params.ws_port})")

    def input(self) -> FrameProcessor:
        return self._input_processor

    def output(self) -> FrameProcessor:
        return self._output_processor

    async def _ws_handler(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connections from Attendee."""
        logger.info(f"New WebSocket connection from {websocket.remote_address} on path {path}")
        
        bot_id = None
        
        try:
            async for message in websocket:
                data = json.loads(message)
                
                # Keep track of the bot ID for the connection
                if not bot_id and "bot_id" in data:
                    bot_id = data["bot_id"]
                    self.connections[bot_id] = websocket
                    logger.info(f"Registered connection for bot_id: {bot_id}")
                
                trigger = data.get("trigger")
                if trigger == "realtime_audio.mixed":
                    # Extract audio and push to Pipecat input processor
                    chunk_b64 = data["data"]["chunk"]
                    audio_bytes = base64.b64decode(chunk_b64)
                    
                    frame = InputAudioRawFrame(
                        audio=audio_bytes,
                        sample_rate=data["data"].get("sample_rate", 16000),
                        num_channels=1
                    )
                    await self._input_processor.push_frame(frame)
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket closed for bot_id: {bot_id}")
        except Exception as e:
            logger.error(f"WebSocket error for bot_id {bot_id}: {e}")
        finally:
            if bot_id and bot_id in self.connections:
                del self.connections[bot_id]

    async def start(self):
        """Start the WebSocket server."""
        logger.info(f"Starting Attendee WebSocket server on {self.params.ws_host}:{self.params.ws_port}...")
        self._ws_server = await websockets.serve(
            self._ws_handler, 
            self.params.ws_host, 
            self.params.ws_port
        )

    async def stop(self):
        """Stop the WebSocket server."""
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None
        
        # Close all active connections
        for bot_id, ws_conn in list(self.connections.items()):
            await ws_conn.close()
        self.connections.clear()
