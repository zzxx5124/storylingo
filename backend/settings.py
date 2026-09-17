import os
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORAGE_DIR = os.path.join(ROOT_DIR, "storage")
UPLOAD_DIR = os.path.join(STORAGE_DIR, "uploads")
BOOKS_DIR = os.path.join(STORAGE_DIR, "books")
BANNER_DIR = os.path.join(STORAGE_DIR, "banners")
PROFILE_DIR = os.path.join(STORAGE_DIR, "profiles")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
DATA_DIR = os.path.join(ROOT_DIR, "data")
REPLACEMENTS_FILE = os.path.join(DATA_DIR, "文字差異.xlsx")

# ---------- AI 分析設定 ----------
# AI_PROVIDER: "openai" 或 "nvidia"（NVIDIA NIM 免費端點）
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai").strip().lower()

AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()
AI_MODEL_FALLBACK = os.getenv("AI_MODEL_FALLBACK", "").strip()

# 向後相容 OPENAI_* 設定
if not AI_API_KEY:
    AI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not AI_MODEL:
    AI_MODEL = os.getenv("OPENAI_MODEL", "").strip()

if AI_PROVIDER == "nvidia":
    AI_BASE_URL = AI_BASE_URL or "https://integrate.api.nvidia.com/v1"
    # NVIDIA NIM 免費層：deepseek-v4-flash 回應快、中文好；備援用 deepseek-v4-pro
    AI_MODEL = AI_MODEL or "deepseek-ai/deepseek-v4-flash"
    AI_MODEL_FALLBACK = AI_MODEL_FALLBACK or "deepseek-ai/deepseek-v4-pro"
else:
    AI_MODEL = AI_MODEL or "gpt-4o-mini"

PORT = int(os.getenv("PORT", "8000"))
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
APP_VERSION = os.getenv("APP_VERSION", "storylingo-v4").strip() or "storylingo-v4"
# Deployment identity exposed to the safe release endpoint/header.  It is
# intentionally independent from user data and may be overridden per release.
RELEASE_ID = os.getenv("RELEASE_ID", APP_VERSION).strip() or APP_VERSION
WORKER_ENABLED = os.getenv("WORKER_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
DEV_AUTH_BYPASS = os.getenv("DEV_AUTH_BYPASS", "false").strip().lower() in ("1", "true", "yes")
PUBLIC_ORIGINS = [x.strip() for x in os.getenv("PUBLIC_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()]
REDIS_URL = os.getenv("REDIS_URL", "").strip()

# ---------- 管理員登入 ----------
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
# 平台多使用者：admin 起始帳號（users 表空且 ADMIN_PASSWORD 有值時 bootstrap）
ADMIN_USER = os.getenv("ADMIN_USER", "admin").strip() or "admin"

# ---------- Authentication / OAuth ----------
AUTH_MAIL_BACKEND = os.getenv("AUTH_MAIL_BACKEND", "console").strip().lower() or "console"
AUTH_MAIL_FROM = os.getenv("AUTH_MAIL_FROM", "mailbox@example.com").strip()
AUTH_MAIL_FROM_NAME = os.getenv("AUTH_MAIL_FROM_NAME", "語閱 StoryLingo").strip() or "語閱 StoryLingo"
AUTH_BASE_URL = os.getenv("AUTH_BASE_URL", "http://localhost:8000").strip().rstrip("/")
AUTH_MAIL_TIMEOUT_SECONDS = float(os.getenv("AUTH_MAIL_TIMEOUT_SECONDS", "15") or 15)
AUTH_MAIL_M365_TENANT_ID = os.getenv("AUTH_MAIL_M365_TENANT_ID", "").strip()
AUTH_MAIL_M365_CLIENT_ID = os.getenv("AUTH_MAIL_M365_CLIENT_ID", "").strip()
AUTH_MAIL_M365_CLIENT_SECRET = os.getenv("AUTH_MAIL_M365_CLIENT_SECRET", "").strip()
AUTH_MAIL_M365_MAILBOX = os.getenv("AUTH_MAIL_M365_MAILBOX", "").strip()
AUTH_MAIL_M365_GRAPH_BASE_URL = os.getenv(
    "AUTH_MAIL_M365_GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0").strip().rstrip("/")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_ISSUER = os.getenv("GOOGLE_ISSUER", "https://accounts.google.com").strip().rstrip("/")
GOOGLE_AUTHORIZATION_ENDPOINT = os.getenv(
    "GOOGLE_AUTHORIZATION_ENDPOINT", "https://accounts.google.com/o/oauth2/v2/auth").strip()
GOOGLE_TOKEN_ENDPOINT = os.getenv("GOOGLE_TOKEN_ENDPOINT", "https://oauth2.googleapis.com/token").strip()
GOOGLE_JWKS_URI = os.getenv("GOOGLE_JWKS_URI", "https://www.googleapis.com/oauth2/v3/certs").strip()
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/oauth/google/callback").strip()

# ---------- SQLite 資料層 ----------
DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "app.db"))
SESSION_KEY_FILE = os.path.join(DATA_DIR, ".session_key")

# ---------- 語言分類 ----------
CAT_ZH = "zh"
CAT_VOCAB = "vocab"
CAT_BILINGUAL = "bilingual"
CAT_EN = "en"
CAT_OTHER = "other"
CATEGORIES = [CAT_ZH, CAT_VOCAB, CAT_BILINGUAL, CAT_EN, CAT_OTHER]
CAT_LABELS = {
    CAT_ZH: "純中文", CAT_VOCAB: "中英單字", CAT_BILINGUAL: "雙語",
    CAT_EN: "純英文", CAT_OTHER: "其它",
}


def validate_env() -> None:
    """正式環境（production）啟動前安全檢查：缺 Admin 密碼或 AI 金鑰即拒絕啟動。

    依 R6：此函式絕不輸出金鑰內容，只輸出是否缺漏。
    """
    if APP_ENV != "production":
        return
    problems = []
    if not ADMIN_PASSWORD:
        problems.append("ADMIN_PASSWORD 未設定")
    elif len(ADMIN_PASSWORD) < 8:
        problems.append("ADMIN_PASSWORD 長度不足 8 字元")
    if not AI_API_KEY:
        problems.append("AI_API_KEY 未設定")
    if problems:
        raise RuntimeError("正式環境設定檢查失敗（禁止啟動）：" + "；".join(problems))


def effective_category(book: dict) -> str:
    """依「主分類 + 分類標籤」決定實際的分析/生成行為。

    多分類時依優先級取主風格：純英文 > 雙語 > 中英單字 > 純中文。
    ``other`` 本身不啟用學習模式；只有明確搭配其他學習分類標籤時才會
    依既有優先級取相應風格。
    讓「主分類為 zh、但標籤含 vocab/bilingual」的書也能正確產生單字教學。
    """
    cats = []
    merged = [book.get("category")] + list((book.get("categories") or []))
    for c in merged:
        if c in CATEGORIES and c not in cats:
            cats.append(c)
    # 若標籤同時含全部類別（「放入全部分類」），標籤無區別作用，退回主分類，
    # 避免被最高優先級「純英文」劫持而與使用者選的主分類不符。
    if all(value in cats for value in (CAT_ZH, CAT_VOCAB, CAT_BILINGUAL, CAT_EN)):
        main = book.get("category")
        return main if main in CATEGORIES else CAT_VOCAB
    for c in (CAT_EN, CAT_BILINGUAL, CAT_VOCAB, CAT_ZH):
        if c in cats:
            return c
    return str(book.get("category") or CAT_VOCAB)

# 單字難度（AUTO = LLM 自動逐字定級）
VOCAB_LEVELS = ["AUTO", "A1", "A2", "B1", "B2"]
VOCAB_LABELS = {"AUTO": "自動判定", "A1": "A1", "A2": "A2", "B1": "B1", "B2": "B2"}

# ---------- 預設遠端 TTS provider ----------
TTS_PROVIDER_ID = int(os.getenv("TTS_PROVIDER_ID", "0") or 0)

PREVIEW_CACHE_DIR = os.path.join(STORAGE_DIR, "preview_cache")

for d in (STORAGE_DIR, UPLOAD_DIR, BOOKS_DIR, BANNER_DIR, PROFILE_DIR, DATA_DIR, PREVIEW_CACHE_DIR):
    os.makedirs(d, exist_ok=True)


# ---------- 檔案路徑 helper（§2.2） ----------

def storage_path(rel: str) -> str:
    """相對路徑以專案根目錄解析成絕對路徑。"""
    return os.path.join(ROOT_DIR, rel) if rel else ""


def chapter_rel_dir(bid: str) -> str:
    return f"storage/books/{bid}/chapters"


def chapter_file(bid: str, seq: int) -> str:
    return f"storage/books/{bid}/chapters/{seq:04d}.json"


def audio_file(bid: str, seq: int) -> str:
    return f"storage/books/{bid}/audio/{seq:04d}.mp3"


def timing_file(bid: str, seq: int) -> str:
    return f"storage/books/{bid}/audio/{seq:04d}.timing.json"
