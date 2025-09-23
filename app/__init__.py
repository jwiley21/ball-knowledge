# app/__init__.py
import os
from datetime import datetime, date as _date, timedelta
from flask import Flask
from dotenv import load_dotenv
from typing import Optional
from typing import Any



# Try to import Supabase client safely
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client = None
    Client = None  # type: ignore

# Load .env as early as possible so env vars are available everywhere
load_dotenv()

# Global Supabase client (filled in create_app when env vars exist)
supabase: Any = None


# Timezone config
TIMEZONE = os.getenv("TIMEZONE", "America/New_York")

# Prefer stdlib zoneinfo; gracefully fall back if tzdata isn't present on Windows
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # Python 3.9+

    def get_today_et() -> _date:
        try:
            return datetime.now(ZoneInfo(TIMEZONE)).date()
        except ZoneInfoNotFoundError:
            # Fallback to pytz if zoneinfo db isn't available (common on Windows)
            try:
                import pytz  # type: ignore
                tz = pytz.timezone(TIMEZONE)
                return datetime.now(tz).date()
            except Exception:
                print("[WARN] Neither zoneinfo nor pytz could resolve timezone; using system local date.")
                return datetime.now().date()

except Exception:
    # zoneinfo module not available (very old Python) → try pytz; else local time
    def get_today_et() -> _date:
        try:
            import pytz  # type: ignore
            tz = pytz.timezone(TIMEZONE)
            return datetime.now(tz).date()
        except Exception:
            print("[WARN] No timezone library available; using system local date.")
            return datetime.now().date()


def create_app() -> Flask:
    """Application factory."""
    global supabase

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")  # set a strong value in Render
    app.config["APP_NAME"] = os.getenv("APP_NAME", "Ball Knowledge")

    # Persist sessions across browser restarts
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=180)
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True  # Render is HTTPS; set False only for local http


    # Initialize Supabase client if env vars are present
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if url and key and create_client:
        try:
            supabase = create_client(url, key)
            print("[INFO] Supabase configured.")
        except Exception as e:
            supabase = None
            print(f"[WARN] Supabase client init failed: {e}")
    else:
        print("[WARN] Supabase env vars missing or client unavailable. Running in local/JSON mode.")

    # Register blueprints AFTER supabase is set so routes import the filled global
    from .routes import bp as main_bp
    app.register_blueprint(main_bp)

    # Make APP_NAME available in templates (base.html uses this)
    @app.context_processor
    def inject_globals():
        return {"APP_NAME": app.config["APP_NAME"]}

    return app
