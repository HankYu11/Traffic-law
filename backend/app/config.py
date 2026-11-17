import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
PROCESSED = DATA / "processed"

# LLM：任何 OpenAI 相容端點。
#  開發：Gemini API 上的 Gemma 4（https://generativelanguage.googleapis.com/v1beta/openai）
#  警局內網：H200 上的 vLLM（例如 http://llm.internal:8000/v1），模型權重相同。
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
LLM_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemma-4-26b-a4b-it")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "120"))

# Embedding：地端執行（ONNX / CPU 即可），不送外部服務。
EMBED_MODEL = os.getenv("EMBED_MODEL", "google/embeddinggemma-300m")
MODEL_CACHE = os.getenv("MODEL_CACHE", str(ROOT / "models" / "fastembed"))

DB_PATH = os.getenv("DB_PATH", str(DATA / "app.db"))

# 示範用「今天」：可用環境變數固定，方便重現示範情境。
TODAY = date.fromisoformat(os.getenv("DEMO_TODAY", date.today().isoformat()))

# 檢索
TOP_K = int(os.getenv("TOP_K", "8"))
MIN_DENSE_SCORE = float(os.getenv("MIN_DENSE_SCORE", "0.45"))
