import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

SYSTEM_PROMPT = "respond only with a number or a word that the user asks for"

N_WORKERS = 3
LEASE_DURATION_S = 120
RENEW_EVERY_S = 10
MAX_ATTEMPTS = 5

DB_PATH = Path(os.getenv("LLM_BIAS_DB", ROOT / "data.db"))
STATIC_DIR = ROOT / "static"

PROJECT_MODELS = (
    "Qwen3.5-4B-Q3_K_M",
    "Qwen3.5-4B-Q4_0",
    "Qwen3.5-4B-Q5_K_M",
    "Qwen3.5-4B-Q6_K",
    "Qwen3.5-4B-Q8_0",
)
RUN_DIR = ROOT / ".run"
LLAMA_BIN = os.getenv("LLAMA_BIN", "llama-server")
LLAMA_HOST = os.getenv("LLAMA_HOST", "127.0.0.1")
LLAMA_PORT = int(os.getenv("LLAMA_PORT", "8013"))
LLAMA_MODELS_DIR = Path(os.getenv("LLAMA_MODELS_DIR", Path.home() / "models"))
LLAMA_MODELS_LINK_DIR = ROOT / "llama" / "models"
LLAMA_CTX = int(os.getenv("LLAMA_CTX", "8192"))
LLAMA_FIT_CTX = int(os.getenv("LLAMA_FIT_CTX", "4096"))
LLAMA_MODELS_MAX = int(os.getenv("LLAMA_MODELS_MAX", "5"))
LLAMA_PARALLEL = int(os.getenv("LLAMA_PARALLEL", str(N_WORKERS)))
LLAMA_FIT_TARGET = os.getenv("LLAMA_FIT_TARGET", "3200")
LLAMA_THREADS = int(os.getenv("LLAMA_THREADS", "8"))
LLAMA_PID = RUN_DIR / "llama-server.pid"
LLAMA_LOG = RUN_DIR / "llama-server.log"

if N_WORKERS > LLAMA_MODELS_MAX:
    raise RuntimeError(
        f"N_WORKERS ({N_WORKERS}) must not exceed LLAMA_MODELS_MAX ({LLAMA_MODELS_MAX}); "
        "otherwise workers will request more models than the router can keep resident."
    )

API_URL = os.getenv("API_URL") or f"http://{LLAMA_HOST}:{LLAMA_PORT}"
