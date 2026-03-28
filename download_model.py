from huggingface_hub import hf_hub_download

print("Starting model download. This might take a few minutes...")
file_path = hf_hub_download(
    repo_id="unsloth/Nemotron-3-Nano-30B-A3B-GGUF",
    filename="Nemotron-3-Nano-30B-A3B-Q4_1.gguf",
    revision="9ad8b366c308f931b2a96b9306f0b41aef9cd405"
)
print(f"Success! Model saved safely to: {file_path}")
