import os
import secrets

from dotenv import load_dotenv

load_dotenv()  # reads .env for local runs; real environment variables (hosting) always win

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kirana.db")
DEMO_MODE = os.getenv("DEMO_MODE", "0") == "1"
DEMO_OTP = os.getenv("DEMO_OTP", "123456")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-5-5")
# A random key per process is safe for dev (sessions reset on restart); set SECRET_KEY in prod.
SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # set to 1 behind HTTPS (deployment)
