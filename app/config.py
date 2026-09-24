import os

from dotenv import load_dotenv

load_dotenv()

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "false").strip().lower() == "true"
