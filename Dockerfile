FROM nvcr.io/nvidia/pytorch:26.02-py3

WORKDIR /app

# Match the host machine's graphical libraries for OpenCV
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install uv (bypassing OS protections)
RUN pip install uv --break-system-packages

# Copy the entire project directory
COPY . .

# Install dependencies directly into the system Python
RUN uv pip install --system --break-system-packages -r pyproject.toml

# Expose the correct Uvicorn port
EXPOSE 7860

# Launch the agent and force it to listen on all network interfaces
CMD ["python", "pipecat_bots/bot_interleaved_streaming.py", "--host", "0.0.0.0", "--port", "7860"]
RUN pip install openai --break-system-packages
