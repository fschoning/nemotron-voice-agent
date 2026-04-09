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

from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

from pipecat_bots.nvidia_stt import NVidiaWebSocketSTTService
from pipecat_bots.magpie_websocket_tts import MagpieWebSocketTTSService
from pipecat_bots.v2v_metrics import V2VMetricsProcessor
from apps.prompts.vedic_astrologer import VEDIC_ASTROLOGER_AUDIO_PROMPT

load_dotenv(override=True)

NVIDIA_ASR_URL = os.getenv("NVIDIA_ASR_URL", "ws://127.0.0.1:8080")
NVIDIA_TTS_URL = os.getenv("NVIDIA_TTS_URL", "http://127.0.0.1:8001")
MCP_SERVER_URL = "http://192.168.1.121:16080/mcp/sse"
ENABLE_RECORDING = os.getenv("ENABLE_RECORDING", "false").lower() == "true"
RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"
VAD_STOP_SECS = 0.2

# --- Pipeline Swallower ---
class AudioSwallower(FrameProcessor):
    """Silently swallows raw audio frames before they reach the LLM aggregator.
    This prevents 'StartFrame not received' errors caused by transports 
    warming up and streaming audio before the pipeline officially starts.
    """
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        if isinstance(frame, InputAudioRawFrame):
            return
        await super().process_frame(frame, direction)

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
        async with sse_client(url=MCP_SERVER_URL) as streams:
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
        logger.info(f"✅ Tool {tool_name} returned successfully. Passing result back to LLM.")
        return result
    except asyncio.CancelledError:
        logger.warning(f"⚠️ Tool call {tool_name} was interrupted.")
        raise
    except Exception as e:
        logger.error(f"❌ MCP Tool Error ({tool_name}): {e}")
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

    active_tts = os.getenv("ACTIVE_TTS", "magpie").lower()
    if active_tts == "voxtral":
        voxtral_url = os.getenv("VOXTRAL_TTS_URL", "http://127.0.0.1:8002/v1")
        tts = OpenAITTSService(
            api_key=os.getenv("OPENAI_API_KEY", "dummy_key"),
            base_url=voxtral_url, model="mistralai/Voxtral-4B-TTS-2603", voice="casual_female"
        )
    else:
        tts = MagpieWebSocketTTSService(
            server_url=NVIDIA_TTS_URL, voice="aria", language="en",
            params=MagpieWebSocketTTSService.InputParams(
                language="en", streaming_preset="conservative", use_adaptive_mode=True
            ),
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

    messages = [
        {"role": "system", "content": VEDIC_ASTROLOGER_AUDIO_PROMPT},
        {"role": "user", "content": "A new caller has connected to the line. Please greet them warmly, state your identity as the Vedic Astrologer, and ask for their birth details to begin."},
    ]

    context = LLMContext(messages, tools=pipecat_tools)
    context_aggregator = LLMContextAggregatorPair(context)

    llm = GoogleLLMService(
        api_key=os.environ.get("GEMINI_API_KEY"),
        model="gemini-2.0-flash", # Updated to a valid model name
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
        transport.input(), rtvi, stt, AudioSwallower(), context_aggregator.user(), llm,
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

async def bot(runner_args: RunnerArguments):
    # Idiomatic Pipecat Init: Dependencies first, Transport second, Task third.
    global tool_call_queue
    tool_call_queue = asyncio.Queue()
    mcp_task = asyncio.create_task(manage_mcp_connection())
    await mcp_ready_event.wait()

    if not mcp_session:
        logger.error("Aborting Pipecat startup: No MCP session available.")
        return

    transport = await create_transport(runner_args, transport_params)
    try:
        await run_bot(transport, runner_args)
    finally:
        mcp_task.cancel()

if __name__ == "__main__":
    if not os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") == "none":
        print("\n💥 FATAL ERROR: GEMINI_API_KEY is missing or empty!\n")
        sys.exit(1)
    from pipecat.runner.run import main
    main()
