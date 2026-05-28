#!/bin/bash
set -e
# LLM: unsloth/Qwen3.6-35B-A3B-NVFP4 on :8000
# Stop tokens (generation_config, no override): eos_token_id [248046, 248044]
#   248046 = <|im_end|>   248044 = <|endoftext|>  (vocab 248044; NOT base-Qwen 151643)
# Thinking: --reasoning-parser qwen3; enable_thinking via chat_template_kwargs in client
# Tools: --tool-call-parser qwen3_coder (not hermes for Qwen3.6 native format)

echo "📦 Installing image libs for torchvision (if missing)..."
apt-get update -qq && \
apt-get install -y --no-install-recommends libjpeg-dev libpng-dev git || true

echo "🔄 Ensuring transformers>=5.4 for Qwen3.6 + vLLM 0.20..."
pip install -q --upgrade "transformers>=5.4.0,<6" || pip install -q --upgrade "transformers>=5.4.0,<6"

export FLASHINFER_DISABLE_VERSION_CHECK=1
export VLLM_ALLOW_LONG_MAX_MODEL_LEN=1
export VLLM_FLOAT32_MATMUL_PRECISION=high
python3 -c "import torch; torch.set_float32_matmul_precision(\"high\"); print(\"✅ TF32 enabled\")"

echo "🚀 vLLM Qwen3.6-35B-A3B-NVFP4 (max-model-len=262144)..."
exec vllm serve unsloth/Qwen3.6-35B-A3B-NVFP4 \
  --served-model-name=qwen-3.6-35b-nvfp4 \
  --host=0.0.0.0 \
  --port=8000 \
  --trust-remote-code \
  --reasoning-parser=qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser=qwen3_coder \
  --gpu-memory-utilization=0.76 \
  --max-model-len=262144 \
  --enable-chunked-prefill \
  --enable-prefix-caching \
  --max-num-batched-tokens=131072 \
  --max-num-seqs=8
