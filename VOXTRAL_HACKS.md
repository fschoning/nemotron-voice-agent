# Voxtral-4B-TTS on DGX Spark GB10 - Deployment Notes

## The Memory Bandwidth Bottleneck
* Unquantized Voxtral + default 65k KV cache demands ~130GB VRAM.
* The GB10 holds it easily (120GB LPDDR5x), but its 273 GB/s memory bandwidth chokes the decode phase to ~0.6x real-time generation speed. 

## The Applied Hacks
1. **vLLM-Omni YAML Override:** The `vllm-omni` library hardcodes an 80% VRAM allocation for Stage 0. We bypassed this using a `sed` command in `Dockerfile.voxtral` to force `0.35` (35%) utilization per stage.
2. **KV Cache Precision:** Forced `--kv-cache-dtype fp8` in `docker-compose.yml` to halve the cache payload on the memory bus.
3. **Context Clamping:** Set `--max-model-len 1024` to prevent the active KV cache from growing over long conversations and further dragging down generation speed.
4. **Pipecat Validation Patch:** Injected a `sed` command into the `agent-core` Dockerfile to bypass Pipecat's hardcoded OpenAI voice validation, allowing the `"casual_female"` embedding to pass through to Voxtral.

## Future Roadmap (Activation)
Currently waiting for `vllm-omni` to merge support for `bitsandbytes` or AWQ/INT4 quantization on the custom flow-matching acoustic layers. Shrinking the 8GB weights to 2GB/4GB will leave enough headroom on the 273 GB/s memory bus to achieve real-time, zero-latency conversational speeds.
