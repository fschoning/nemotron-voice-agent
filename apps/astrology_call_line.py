#!/usr/bin/env python3
import asyncio
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path to allow importing from pipecat_bots
sys.path.append(str(Path(__file__).parent.parent))

import wave
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv
from loguru import logger
import logging

from mcp import ClientSession
from mcp.client.sse import sse_client

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import Frame, InputAudioRawFrame, LLMRunFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.aggregators.sentence import SentenceAggregator
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport

import base64
import httpx
from typing import AsyncGenerator

from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.tts_service import TTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import (
    AudioRawFrame, 
    ErrorFrame, 
    Frame, 
    TTSAudioRawFrame, 
    TTSStartedFrame, 
    TTSStoppedFrame
)

# Use a dedicated name for the standard logger to avoid shadowing loguru.logger
pipelog = logging.getLogger("pipecat")

# A dedicated Mistral Cloud TTS service using direct API calls (SDK-less).
class MistralCloudTTSService(TTSService):
    def __init__(self, api_key: str, model: str = "voxtral-mini-tts-2603", voice: str = "c69964a6-ab8b-4f8a-9465-ec0925096ec8", **kwargs):
        super().__init__(**kwargs)
        self._api_key = api_key
        self._model = model
        self._requested_voice = voice
        self._active_voice = None # Discovered in real-time
        self._url_speech = "https://api.mistral.ai/v1/audio/speech"
        self._url_voices = "https://api.mistral.ai/v1/audio/voices"
        self._client = None
        self._semaphore = asyncio.Semaphore(1) # Prevent concurrent API calls (fixes 503 overflow)

    def _get_client(self):
        if not self._client:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _discover_voice(self):
        """Discovers the available voices and picks the requested one or the first available."""
        if self._active_voice:
            return
            
        pipelog.info("🔍 Discovering available Mistral voices...")
        try:
            client = self._get_client()
            # We fetch the list of voices to see what IDs are actually valid for this account
            response = await client.get(
                self._url_voices,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                voices = data.get("items", [])
                if voices:
                    pipelog.info(f"✅ Found {len(voices)} voices.")
                    # 1. Try exact ID match
                    for v in voices:
                        if v.get("id") == self._requested_voice:
                            self._active_voice = v.get("id")
                            pipelog.info(f"🎤 Using voice: {v.get('name')} ({self._active_voice})")
                            return

                    # 2. Try exact Name match
                    for v in voices:
                        if v.get("name") == self._requested_voice:
                            self._active_voice = v.get("id")
                            pipelog.info(f"🎤 Using voice: {v.get('name')} ({self._active_voice})")
                            return

                    # 3. Try partial Name match
                    for v in voices:
                        if self._requested_voice.lower() in v.get("name", "").lower():
                            self._active_voice = v.get("id")
                            pipelog.info(f"🎤 Using voice: {v.get('name')} ({self._active_voice})")
                            return
                    
                    # 4. Fallback to first
                    self._active_voice = voices[0].get("id")
                    pipelog.warning(f"Voice '{self._requested_voice}' not found. Falling back to: {voices[0].get('name')} ({self._active_voice})")
                else:
                    pipelog.error("No voices found in Mistral API response.")
            else:
                pipelog.error(f"Failed to fetch Mistral voices: {response.status_code} {response.text}")
        except Exception as e:
            pipelog.error(f"Error during Mistral voice discovery: {e}")
            
        if not self._active_voice:
            self._active_voice = "c69964a6-ab8b-4f8a-9465-ec0925096ec8" # Hard fallback
            pipelog.warning(f"Using default Paul voice ID: {self._active_voice}")

    async def run_tts(self, text: str) -> AsyncGenerator[Frame, None]:
        # Ensure we have a valid voice ID before calling speech API
        await self._discover_voice()
        
        yield TTSStartedFrame()
        
        async with self._semaphore: # Concurrency control to prevent 503 overflow
            pipelog.debug(f"Calling Mistral Voxtral API for: {text[:40]}...")
            
            max_retries = 5
            client = self._get_client()
            
            for attempt in range(max_retries):
                try:
                    response = await client.post(
                        self._url_speech,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json={
                            "model": self._model,
                            "input": text,
                            "voice_id": self._active_voice,
                            "response_format": "wav" # Request WAV to accurately strip headers and get sample_rate
                        },
                        timeout=30.0
                    )
                    
                    if response.status_code in [503, 429, 502, 504]:
                        pipelog.warning(f"Mistral API overloaded ({response.status_code}). Retry {attempt+1}/{max_retries}...")
                        if attempt < max_retries - 1:
                            await asyncio.sleep(2.0 * (1.5 ** attempt)) # Exponential backoff
                            continue
                        else:
                            pipelog.error(f"Mistral API Error ({response.status_code}): Max retries exceeded.")
                            yield ErrorFrame(f"Mistral Error: {response.text}")
                            break
                    
                    if response.status_code != 200:
                        pipelog.error(f"Mistral API Error ({response.status_code}): {response.text}")
                        yield ErrorFrame(f"Mistral Error: {response.text}")
                        break

                    data = response.json()
                    if "audio_data" in data and data["audio_data"]:
                        audio_bytes = base64.b64decode(data["audio_data"])
                        
                        # Fix "machine industrial noise": Extract raw 16-bit PCM from WAV container.
                        # This strips out the WAV header bytes which sound glitchy/metallic as raw PCM
                        # and guarantees we read the EXACT sample rate the endpoint actually returned.
                        import wave
                        import io
                        try:
                            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                                sample_rate = wf.getframerate()
                                num_channels = wf.getnchannels()
                                raw_pcm = wf.readframes(wf.getnframes())
                                
                                # In 0.0.98 we must ensure we use the explicit native rate we discovered, 
                                # so Pipecat handles any resampling needed dynamically for the WebRTC transport
                                yield TTSAudioRawFrame(audio=raw_pcm, sample_rate=sample_rate, num_channels=num_channels)
                        except Exception as wav_error:
                            pipelog.error(f"Failed to parse audio as WAV: {wav_error}")
                            # Fallback if Mistral lies and gives us pure PCM anyway
                            yield TTSAudioRawFrame(audio=audio_bytes, sample_rate=24000, num_channels=1)

                        break # Success, exit retry loop
                    else:
                        pipelog.error("Mistral Voxtral API returned no audio data.")
                        break # Permanent error, exit retry loop
                        
                except Exception as e:
                    if attempt < max_retries - 1:
                        pipelog.warning(f"Mistral API connection error: {e}. Retrying {attempt+1}/{max_retries}...")
                        await asyncio.sleep(2.0 * (1.5 ** attempt))
                    else:
                        pipelog.error(f"Mistral Voxtral HTTP Error: {e}")
                        yield ErrorFrame(f"Mistral Error: {e}")
                        break
                
        yield TTSStoppedFrame()

from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

from pipecat_bots.nvidia_stt import NVidiaWebSocketSTTService
from pipecat_bots.magpie_websocket_tts import MagpieWebSocketTTSService
from pipecat_bots.v2v_metrics import V2VMetricsProcessor
from apps.prompts.vedic_astrologer import VEDIC_ASTROLOGER_AUDIO_PROMPT
from transports.attendee_transport import AttendeeTransportParams, AttendeeTransport
from transports.attendee_client import AttendeeClient
from transports.attendee_webhooks import WebhookServer
import urllib.parse

load_dotenv(override=True)

NVIDIA_ASR_URL = os.getenv("NVIDIA_ASR_URL", "ws://127.0.0.1:8080")
NVIDIA_TTS_URL = os.getenv("NVIDIA_TTS_URL", "http://127.0.0.1:8001")
MCP_SERVER_URL = "http://192.168.1.121:16080/mcp/sse"
ENABLE_RECORDING = os.getenv("ENABLE_RECORDING", "false").lower() == "true"
RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"
VAD_STOP_SECS = 0.2


# --- Global State ---
mcp_session = None
mcp_ready_event = asyncio.Event()
mcp_tools_cache = []
tool_call_queue = None

def ensure_recordings_dir() -> Path:
    RECORDINGS_DIR.mkdir(exist_ok=True)
    return RECORDINGS_DIR

async def save_audio_file(audio: bytes, sample_rate: int, num_channels: int, filepath: Path):
    def _write_wav():
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(num_channels)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)
        with open(filepath, "wb") as f:
            f.write(buffer.getvalue())
    try:
        await asyncio.to_thread(_write_wav)
        logger.info(f"Saved recording: {filepath}")
    except Exception as e:
        logger.error(f"Failed to save recording: {e}")

# --- Isolated MCP Event Loop ---
async def manage_mcp_connection():
    global mcp_session
    try:
        # Set timeout=None to prevent SSE session from timing out during standby
        async with sse_client(url=MCP_SERVER_URL, timeout=None) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                mcp_session = session
                
                tools_res = await session.list_tools()
                mcp_tools_cache.clear()
                mcp_tools_cache.extend(tools_res.tools)
                
                logger.info(f"✅ Connected to MCP! Discovered {len(tools_res.tools)} tools.")
                mcp_ready_event.set()
                
                while True:
                    payload = await tool_call_queue.get()
                    if payload is None: break
                    t_name, t_args, future = payload
                    try:
                        result = await session.call_tool(t_name, arguments=t_args)
                        
                        if not future.done():
                            if hasattr(result, 'content') and isinstance(result.content, list) and len(result.content) > 0:
                                text_val = result.content[0].text
                                try:
                                    parsed_json = json.loads(text_val)
                                    if isinstance(parsed_json, list):
                                        future.set_result({"data": parsed_json})
                                    else:
                                        future.set_result(parsed_json)
                                except json.JSONDecodeError:
                                    future.set_result({"result": text_val})
                            else:
                                future.set_result({"result": str(result)})
                    except Exception as e:
                        if not future.done():
                            future.set_exception(e)
                    finally:
                        tool_call_queue.task_done()
                        
    except asyncio.CancelledError:
        logger.info("MCP connection closed cleanly.")
    except Exception as e:
        logger.error(f"❌ MCP background task crashed: {e}")
        mcp_ready_event.set()

async def call_mcp_tool(tool_name: str, args: dict):
    if not mcp_session or not tool_call_queue:
        return {"error": "MCP session not initialized"}
    try:
        logger.info(f"⚙️ Queuing LLM Tool Call: {tool_name} with args: {args}")
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        tool_call_queue.put_nowait((tool_name, args, future))
        result = await future
        try:
            pretty_result = json.dumps(result, indent=2)
            logger.info(f"✅ Tool {tool_name} returned:\n{pretty_result}")
        except:
            logger.info(f"✅ Tool {tool_name} returned: {result}")
        return result
    except asyncio.CancelledError:
        logger.warning(f"⚠️ Tool call {tool_name} was interrupted.")
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"❌ MCP Tool Error ({tool_name}): {e}\n{error_details}")
        return {"error": f"Error executing tool: {str(e)}"}

transport_params = {
    "daily": lambda: DailyParams(
        audio_in_enabled=True, audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
    "twilio": lambda: FastAPIWebsocketParams(
        audio_in_enabled=True, audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
    "webrtc": lambda: TransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
    "attendee": lambda: AttendeeTransportParams(
        audio_in_enabled=True, audio_out_enabled=True,
        ws_port=int(os.environ.get("ATTENDEE_WS_PORT", "8765")),
        ws_host="0.0.0.0",
        sample_rate=16000,
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
        turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams()),
    ),
}

async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info("Starting Vedic Astrologer interleaved streaming bot")

    # Create a mapping for sanitized names (Gemini requires underscores only)
    mcp_name_map = {}
    pipecat_tools_list = []
    for tool in mcp_tools_cache:
        original_name = tool.name
        sanitized_name = original_name.replace("-", "_")
        mcp_name_map[sanitized_name] = original_name
        props = tool.inputSchema.get("properties", {})
        pipecat_tools_list.append(
            FunctionSchema(
                name=sanitized_name,
                description=tool.description,
                properties=props if props else None,
                required=tool.inputSchema.get("required", [])
            )
        )
    pipecat_tools = ToolsSchema(standard_tools=pipecat_tools_list)

    stt = NVidiaWebSocketSTTService(url=NVIDIA_ASR_URL, sample_rate=16000)

    # Select TTS provider based on environment variable (set via CLI in main)
    # Default is Mistral Cloud (Voxtral), swappable to Magpie with --magpie flag
    if os.getenv("USE_MAGPIE") == "true":
        logger.info("🎙️ Using Magpie WebSocket TTS (Local)")
        tts = MagpieWebSocketTTSService(server_url=NVIDIA_TTS_URL)
    else:
        pipelog.info("☁️ Using Mistral Cloud TTS (Voxtral)")
        tts = MistralCloudTTSService(
            api_key=os.environ.get("MISTRAL_API_KEY"),
            model="voxtral-mini-tts-2603",
            voice=os.environ.get("MISTRAL_VOICE_ID", "paul")
        )

    v2v_metrics = V2VMetricsProcessor(vad_stop_secs=VAD_STOP_SECS)
    audiobuffer = AudioBufferProcessor(num_channels=2) if ENABLE_RECORDING else None

    if audiobuffer:
        @audiobuffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio: bytes, sample_rate: int, num_channels: int):
            if len(audio) == 0: return
            ensure_recordings_dir()
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
            await save_audio_file(audio, sample_rate, num_channels, RECORDINGS_DIR / f"{timestamp}.wav")

    current_date = datetime.now().strftime("%A, %B %d, %Y")
    current_time = datetime.now().strftime("%H:%M")
    full_prompt = f"{VEDIC_ASTROLOGER_AUDIO_PROMPT}\n\n**IMPORTANT SESSION CONTEXT:** Today is {current_date}, and the current time is {current_time}. Use this to determine current transits and dasha periods accurately."

    messages = [
        {"role": "system", "content": full_prompt},
        {"role": "user", "content": "A new caller has connected to the line. Please greet them warmly, state your identity as the Vedic Astrologer, and ask for their birth details to begin."},
    ]

    context = LLMContext(messages, tools=pipecat_tools)
    context_aggregator = LLMContextAggregatorPair(context)

    llm = GoogleLLMService(
        api_key=os.environ.get("GEMINI_API_KEY"),
        model="gemini-2.5-flash", 
        run_in_parallel=False
    )

    for sanitized_name, original_name in mcp_name_map.items():
        def create_handler(o_name):
            async def handler(params: FunctionCallParams):
                result = await call_mcp_tool(o_name, params.arguments)
                await params.result_callback(result)
            return handler
        llm.register_function(sanitized_name, create_handler(original_name))

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    pipeline_processors = [
        transport.input(), rtvi, stt, context_aggregator.user(), llm,
        SentenceAggregator(), tts, v2v_metrics, transport.output(),
    ]
    if audiobuffer: pipeline_processors.append(audiobuffer)
    pipeline_processors.append(context_aggregator.assistant())
    
    pipeline = Pipeline(pipeline_processors)
    task = PipelineTask(
        pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[RTVIObserver(rtvi)], idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
    )

    @rtvi.event_handler("on_client_ready")
    async def on_client_ready(rtvi):
        logger.info("RTVI client ready")
        if audiobuffer: await audiobuffer.start_recording()
        await rtvi.set_bot_ready()
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    await runner.run(task)

async def zoom_mode(runner_args: RunnerArguments, webhook_server: WebhookServer, transport: AttendeeTransport):
    zoom_url = os.environ.get("ZOOM_SESSION_URL", "").strip().rstrip(".")
    bot_name = os.environ.get("ZOOM_BOT_NAME", "Vedic Pathway Astrologer")
    
    # Extract meeting ID from URL
    parsed_url = urllib.parse.urlparse(zoom_url)
    meeting_id = parsed_url.path.split("/")[-1].replace("-", "")
    
    logger.info(f"Setting up zoom standby mode for meeting {meeting_id} (URL: {zoom_url})")
    
    # Update webhook server to watch this specific meeting
    webhook_server.meeting_id_to_watch = meeting_id
    
    # Setup Attendee client
    attendee_api_key = os.environ.get("ATTENDEE_API_KEY", "")
    public_url = os.environ.get("PUBLIC_URL", "https://attendee.vedicpathway.com")
    attendee_client = AttendeeClient(attendee_api_key)
    
    transport_param_obj = transport_params["attendee"]()
    transport = AttendeeTransport(transport_param_obj)
    
    bot_ready_event = asyncio.Event()
    meeting_ended_event = asyncio.Event()
    
    async def on_join():
        logger.info("Triggered on_join, setting up Attendee bot...")
        try:
            bot_data = await attendee_client.create_bot(
                meeting_url=zoom_url,
                bot_name=bot_name,
                ws_url=f"wss://{public_url.replace('https://', '')}/attendee-audio",
                webhook_url=f"{public_url}/webhooks/attendee"
            )
            bot_id = bot_data.get("id")
            
            # Save bot ID for stop script
            os.makedirs("logs", exist_ok=True)
            import json
            with open("logs/attendee_bot.json", "w") as f:
                json.dump({"bot_id": bot_id}, f)
                
            logger.info(f"Attendee bot created: {bot_id}")
            bot_ready_event.set()
        except Exception as e:
            logger.error(f"Failed to create bot: {e}")
            meeting_ended_event.set()
            
    async def on_end():
        logger.info("Triggered on_end, stopping bot...")
        meeting_ended_event.set()
        
    webhook_server.on_participant_joined = on_join
    webhook_server.on_bot_ended = on_end
    
    await transport.start()
    
    logger.info("🟡 STANDBY: Waiting for guest to join Zoom meeting...")
    
    # Wait for either join or immediate failure
    done, pending = await asyncio.wait(
        [asyncio.create_task(bot_ready_event.wait()), 
         asyncio.create_task(meeting_ended_event.wait())],
        return_when=asyncio.FIRST_COMPLETED
    )
    
    # If meeting ended before we even joined, it was a failure
    if meeting_ended_event.is_set() and not bot_ready_event.is_set():
        logger.error("🔴 Bot failed to initialize. Exiting.")
        await transport.stop()
        return
    
    logger.info("🟢 Guest joined! Starting Pipecat pipeline...")
    
    run_task = asyncio.create_task(run_bot(transport, runner_args))
    
    # Wait for end
    await meeting_ended_event.wait()
    logger.info("🔴 Zoom meeting ended. Cleaning up and exiting.")
    
    # Teardown
    run_task.cancel()
    await transport.stop()
    
    sys.exit(0)

async def bot(runner_args: RunnerArguments, webhook_server: WebhookServer = None):
    # FOR GOD'S SAKE, SILENCE THE LOGS.
    # We do this here, INSIDE the bot function, because pipecat.runner.run.main() 
    # clobbers any logging setup done at the module level.
    logging.getLogger("aiortc").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("pipecat").setLevel(logging.INFO)
    logger.remove()
    logger.add(sys.stderr, level="INFO", filter=lambda record: 
               "pipecat.transports.smallwebrtc" not in record["name"] and 
               "pipecat.runner" not in record["name"] and
               "pipecat.services" not in record["name"])

    # Idiomatic Pipecat Init: Dependencies first, Transport second, Task third.
    global tool_call_queue
    tool_call_queue = asyncio.Queue()
    mcp_task = asyncio.create_task(manage_mcp_connection())
    await mcp_ready_event.wait()

    if not mcp_session:
        logger.error("Aborting Pipecat startup: No MCP session available.")
        return

    if os.environ.get("ZOOM_SESSION_URL"):
        # Bypass create_transport for Attendee integration
        transport_param_obj = transport_params["attendee"]()
        transport = AttendeeTransport(transport_param_obj)
        try:
            await zoom_mode(runner_args, webhook_server, transport)
        finally:
            mcp_task.cancel()
        return

    transport = await create_transport(runner_args, transport_params)
    try:
        await run_bot(transport, runner_args)
    finally:
        mcp_task.cancel()

if __name__ == "__main__":
    import argparse
    import requests
    
    # Check keys before any heavy imports
    if not os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") == "none":
        print("\n💥 FATAL ERROR: GEMINI_API_KEY is missing or empty!\n")
        sys.exit(1)

    # 1. Parse our custom flags first
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--magpie", action="store_true")
    parser.add_argument("--voice-selector", action="store_true")
    parser.add_argument("--zoom", type=str, help="Zoom meeting URL to join")
    parser.add_argument("--bot-name", type=str, default="Vedic Pathway Astrologer", help="Bot name in Zoom")
    args, unknown = parser.parse_known_args()
    
    if args.zoom:
        os.environ["ZOOM_SESSION_URL"] = args.zoom
        os.environ["ZOOM_BOT_NAME"] = args.bot_name
    
    # 2. Set an environment variable as a side-channel to the bot() function
    if args.magpie:
        os.environ["USE_MAGPIE"] = "true"
    elif args.voice_selector:
        # Standalone Voice Selector Mode (Used by start.sh)
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            sys.exit(0)
        try:
            res = requests.get("https://api.mistral.ai/v1/audio/voices?limit=100", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
            if res.status_code == 200:
                voices = res.json().get("items", [])
                print("\n--- Available Mistral Voices ---", file=sys.stderr)
                for i, v in enumerate(voices):
                    print(f"[{i+1}] {v.get('name')} ({v.get('id')})", file=sys.stderr)
                
                print("\nSelect a voice number (or press Enter for Paul - Neutral): ", end="", file=sys.stderr)
                sys.stderr.flush()
                choice = sys.stdin.readline().strip()
                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(voices):
                        # Output ONLY the ID to stdout so start.sh can capture it
                        print(voices[idx]['id'])
        except Exception:
            pass
        sys.exit(0)
    else:
        # Standard run - check if MISTRAL_VOICE_ID was already provided (e.g. by start.sh)
        if not os.getenv("MISTRAL_VOICE_ID") and sys.stdin.isatty() and not getattr(args, "zoom", None):
             # Fallback for direct python runs without start.sh
             api_key = os.getenv("MISTRAL_API_KEY")
             if api_key:
                try:
                    res = requests.get("https://api.mistral.ai/v1/audio/voices?limit=100", headers={"Authorization": f"Bearer {api_key}"}, timeout=5)
                    if res.status_code == 200:
                        voices = res.json().get("items", [])
                        print("\n--- Available Mistral Voices ---")
                        for i, v in enumerate(voices):
                            print(f"[{i+1}] {v.get('name')} ({v.get('id')})")
                        choice = input("\nSelect a voice (Enter for default): ").strip()
                        if choice.isdigit():
                            idx = int(choice) - 1
                            if 0 <= idx < len(voices):
                                os.environ["MISTRAL_VOICE_ID"] = voices[idx]['id']
                except: pass
        
    # 3. Clean up sys.argv so pipecat's main() doesn't complain about unknown args
    sys.argv = [sys.argv[0]] + unknown
    
    # Start Webhook Server background for validation (satisfies Zoom)
    # We do this here so it starts immediately, even before Pipecat runner.
    webhook_port = int(os.environ.get("ATTENDEE_WEBHOOK_PORT", "8766"))
    zoom_secret = os.environ.get("ZOOM_WEBHOOK_SECRET", "")
    webhook_server = WebhookServer(webhook_port, zoom_secret)

    if args.zoom:
        # 4a. Zoom mode: Bypass standard runner and start bot() directly
        logger.info(f"🚀 Starting Zoom standby mode for meeting {args.zoom}...")
        runner_args = RunnerArguments()
        # Set defaults if they are not already set correctly
        if hasattr(runner_args, "pipeline_idle_timeout_secs"):
            runner_args.pipeline_idle_timeout_secs = 600
        if hasattr(runner_args, "handle_sigint"):
            runner_args.handle_sigint = True
        
        async def zoom_init():
            await webhook_server.start()
            await bot(runner_args, webhook_server)
            
        asyncio.run(zoom_init())
    else:
        # 4b. Standard mode: Start webhook server in background thread, then start runner
        import threading
        def run_webhooks():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(webhook_server.start())
            loop.run_forever()
        
        threading.Thread(target=run_webhooks, daemon=True).start()
        
        logger.info("🚀 Starting standard WebRTC/Daily mode...")
        from pipecat.runner.run import main
        main()
