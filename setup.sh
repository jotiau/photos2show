#!/usr/bin/env bash
# photos2show local setup for Linux and macOS: uv + app, Ollama, models matching this computer, .env, UI.
# Safe to run again: installed parts are skipped, models already pulled are only checked.
set -euo pipefail
cd "$(dirname "$0")"

OLLAMA_URL=http://localhost:11434
CONTEXT=32768  # the slideshow step sends one line per photo, see docs/configuration.md#local-models

ask() {  # ask "question" default -> answer (default on Enter or without a terminal)
  local answer=""
  printf '%s [%s]: ' "$1" "$2" >&2
  { read -r answer </dev/tty; } 2>/dev/null || echo >&2
  echo "${answer:-$2}"
}

# --- uv and the app -------------------------------------------------------------------------------
if ! command -v uv >/dev/null; then
  echo "Installing uv (Python package manager) ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "Installing photos2show ..."
uv sync --quiet

# --- Ollama -------------------------------------------------------------------------------------
if ! command -v ollama >/dev/null; then
  echo "Ollama (runs the AI models on this computer) is not installed."
  case "$(ask "Install it automatically (a) or install it yourself (m)?" m)" in  # nothing installed unasked
    a|A)
      if [ "$(uname)" = Darwin ]; then
        if command -v brew >/dev/null; then
          brew install ollama
        else
          echo "No Homebrew found. Download Ollama from https://ollama.com/download, then run ./setup.sh again."
          exit 1
        fi
      else
        curl -fsSL https://ollama.com/install.sh | sh
      fi ;;
    *)
      echo "Install Ollama from https://ollama.com/download, then run ./setup.sh again."
      exit 0 ;;
  esac
fi
if ! curl -fsS "$OLLAMA_URL/api/version" >/dev/null 2>&1; then
  echo "Starting the Ollama server (log: ollama.log) ..."
  nohup ollama serve >ollama.log 2>&1 &
  for _ in $(seq 30); do
    curl -fsS "$OLLAMA_URL/api/version" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "$OLLAMA_URL/api/version" >/dev/null || { echo "Ollama does not answer, see ollama.log."; exit 1; }
fi

# --- Hardware -> recommended pair (keep in sync with *Recommended pairs* in docs/configuration.md) ---
ram_gb=0; vram_gb=0; gpu="none"
if [ "$(uname)" = Darwin ]; then
  ram_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
  [ "$(uname -m)" = arm64 ] && gpu="Apple Silicon"
else
  ram_gb=$(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024 / 1024 ))
  if command -v nvidia-smi >/dev/null && mib=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -n | tail -1) && [ -n "$mib" ]; then
    gpu="NVIDIA"; vram_gb=$(( (mib + 512) / 1024 ))
  else
    for f in /sys/class/drm/card*/device/mem_info_vram_total; do  # AMD (amdgpu driver)
      [ -r "$f" ] || continue
      gb=$(( ($(cat "$f") + 512 * 1024 * 1024) / 1024 / 1024 / 1024 ))
      [ "$gb" -gt "$vram_gb" ] && { gpu="AMD"; vram_gb=$gb; }
    done
  fi
fi
memory=$vram_gb
if [ "$gpu" = "Apple Silicon" ]; then  # the GPU gets about two thirds to three quarters of the memory
  if [ "$ram_gb" -ge 36 ]; then memory=24; elif [ "$ram_gb" -ge 24 ]; then memory=16; elif [ "$ram_gb" -ge 16 ]; then memory=8; else memory=0; fi
fi
if   [ "$memory" -ge 22 ]; then analysis=qwen3-vl:30b-a3b-instruct; slideshow=qwen3:30b-instruct
elif [ "$memory" -ge 15 ]; then analysis=qwen3-vl:8b-instruct;      slideshow=gpt-oss:20b
elif [ "$memory" -ge 11 ]; then analysis=qwen3-vl:8b-instruct;      slideshow=gemma4:12b
elif [ "$memory" -ge 7 ];  then analysis=gemma4:e4b;                slideshow=gemma4:e4b
else                            analysis=gemma4:e2b;                slideshow=qwen3:4b-instruct
fi

echo
echo "This computer: ${ram_gb} GB RAM, GPU: ${gpu}$([ "$vram_gb" -gt 0 ] && echo " with ${vram_gb} GB")."
[ "$memory" -lt 7 ] && echo "No GPU with 7 GB or more found: the AI runs on the CPU, about a minute per photo."
echo "Press Enter to keep a suggestion or type another Ollama model (see docs/configuration.md#local-models)."
analysis=$(ask "Analysis model (describes each photo, needs vision)" "$analysis")
slideshow=$(ask "Slideshow model (chooses the photos, text only)" "$slideshow")

# --- Models ---------------------------------------------------------------------------------------
ollama pull "$analysis"
[ "$slideshow" = "$analysis" ] || ollama pull "$slideshow"
# Ollama's default context is too small for the slideshow list; this copy shares the downloaded files.
modelfile=$(mktemp)
printf 'FROM %s\nPARAMETER num_ctx %s\n' "$slideshow" "$CONTEXT" >"$modelfile"
ollama create photos2show-slideshow -f "$modelfile" >/dev/null
rm -f "$modelfile"

# --- .env ---------------------------------------------------------------------------------------
write_env=y
if [ -f .env ]; then
  write_env=$(ask "Replace the existing .env (it is kept as .env.bak)? y/n" y)
fi
if [ "$write_env" = y ] || [ "$write_env" = Y ]; then
  [ -f .env ] && cp .env .env.bak
  cat >.env <<EOF
# Written by setup.sh; see docs/configuration.md
OPENAI_BASE_URL=$OLLAMA_URL/v1
OPENAI_API_KEY=
MODEL=$analysis
ANALYSIS_MODEL=$analysis
# $slideshow with a $CONTEXT-token context (made by setup.sh)
SLIDESHOW_MODEL=photos2show-slideshow
AI_CONCURRENCY=1
AI_THINKING=on
EOF
  echo "Wrote .env."
fi

echo "Starting photos2show (stop with Ctrl+C; next time: uv run photos2show-web) ..."
exec uv run photos2show-web
