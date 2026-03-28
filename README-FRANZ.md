# Nemotron Cloud-Agent: DGX Spark Edition
**A 100% Reproducible, Hardware-Accelerated, Hybrid Voice AI Architecture.**

## 📖 The Premise: What We Built and Why
The original `nemotron-voice-agent` was a monolithic, fully-local AI agent. While highly secure, running a 30-billion parameter LLM locally introduced conversational latency, and the manual shell-script deployment was prone to port collisions and environment degradation. 

**The Goal:** Perform a "brain transplant." We surgically extracted the heavy local LLM and replaced it with Google Gemini 2.5 Flash for sub-second cognitive routing, while preserving the ultra-fast, local NVIDIA DGX hardware acceleration for Hearing (ASR) and Speaking (TTS). 

**The Result:** A 3-node, 1-click `docker-compose` cluster ("Cloud Brain, Local Voice") that is fully reproducible on any blank DGX Spark machine. 

---

## 🏗️ Technical Architecture
The system orchestrates three isolated Docker containers running directly on the host network to bypass WebRTC NAT traversal issues.

1. **`asr-engine` (The Ears):** Runs NVIDIA's Parakeet ASR model natively on the host network (`127.0.0.1:8080`).
2. **`tts-engine` (The Voice):** Runs NVIDIA's Magpie TTS model natively isolated on the host network (`127.0.0.1:8001`).
3. **`agent-core` (The Brain):** A custom Python/Pipecat container. It handles the WebRTC video/audio stream, uses Silero VAD to detect speech, routes transcribed text to the Gemini API, and passes the response to Magpie for vocalization. Binds to `7860`.

---

## 📂 File Manifest: What Changed
To achieve this 1-click architecture, the following files were introduced or heavily modified:

* **`docker-compose.yml`**: The master orchestrator. It defines the GPU passthrough (`deploy.reservations`), host networking (`network_mode: host`), shared memory (`ipc: host`), and precise environment overrides required to suppress the default LLM (`ENABLE_LLM=false`). It also correctly mounts the local Hugging Face cache.
* **`Dockerfile`**: The custom build file for the `agent-core`. Based on `nvcr.io/nvidia/pytorch:26.02-py3` to support CUDA 13.0 natively. It specifically installs host-level graphical libraries (`libgl1`, `libglib2.0-0`) to prevent OpenCV/WebRTC crashes, and forces `pip` installations via `--break-system-packages`.
* **`pipecat_bots/bot_interleaved_streaming.py`**: The Pipecat application. Configured to point to the local DGX models at `ws://127.0.0.1:8080` (ASR) and `http://127.0.0.1:8001` (TTS). 
* **`.env`**: Stores the required `GEMINI_API_KEY`.

*(Note: `Dockerfile.gb10` and `Dockerfile.isolated` remain intact to build the ASR and TTS images from scratch).*

---

## 🚀 Installation & Launch Guide

### Prerequisites
1. An NVIDIA DGX Spark GB10 (or equivalent CUDA 13.1 capable machine).
2. Hugging Face models cached locally at `~/.cache/huggingface` (Parakeet ASR and Magpie TTS).
3. A Google Gemini API key.

### Step 1: Clone and Configure
Clone the repository and set up your environment variables:
```bash
git clone git@github.com:<USERNAME>/nemotron-voice-agent.git
cd nemotron-voice-agent
echo "GEMINI_API_KEY=your_actual_api_key_here" > .env
```

### Step 2: Ignite the Cluster
Build and launch the architecture in detached mode. Docker will automatically build the ASR, TTS, and Core images if they do not exist locally.
```bash
docker compose up -d --build
```

### Step 3: Connect
1. Verify the core is ready: `docker compose logs -f agent-core`
2. Wait for the `🚀 Bot ready!` message.
3. Open your browser and navigate to the UI: `http://<DGX_IP>:7860/client`
4. Click Connect.

### Step 4: Graceful Shutdown
To tear down the cluster and free up the hardware:
```bash
docker compose down
```

---

## 🔧 Maintenance & Quirks (The "Gotchas")

* **Host Networking is Mandatory:** WebRTC requires dynamic UDP ports. If you remove `network_mode: host` from the compose file, the browser will authenticate but audio tracks will permanently hang on `connecting`.
* **The TTS Protocol:** The Pipecat Magpie integration requires the initial handshake URL to be `http://` (not `ws://`), even though it upgrades to a WebSocket later. 
* **Volume Mounts:** The ASR and TTS containers will instantly crash if they cannot access the local Hugging Face cache. Ensure `- ~/.cache/huggingface:/root/.cache/huggingface` remains in the `volumes` block.
* **Changing Voices:** To change the TTS voice, edit `voice="aria"` in the `bot_interleaved_streaming.py` file to another locally cached Magpie profile (make sure the profile actually exists in your cache first!), then rebuild the core: `docker compose build agent-core && docker compose up -d`.
