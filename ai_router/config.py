import os
from dotenv import load_dotenv

PACKAGE_DIR = os.path.dirname(__file__)
PROJECT_DIR = os.path.dirname(PACKAGE_DIR)

load_dotenv(os.path.join(PROJECT_DIR, ".env"))
load_dotenv(os.path.join(PACKAGE_DIR, ".env"), override=False)

PORT = int(os.getenv("AI_ROUTER_PORT", "32128"))
HOST = os.getenv("AI_ROUTER_HOST", "0.0.0.0")
DB_PATH = os.getenv("AI_ROUTER_DB", os.path.join(PACKAGE_DIR, "data", "ai-router.db"))
AUTH_ENABLED = os.getenv("AI_ROUTER_AUTH", "false").lower() == "true"
AUTH_PASSWORD = os.getenv("AI_ROUTER_PASSWORD", "ABC12345")
