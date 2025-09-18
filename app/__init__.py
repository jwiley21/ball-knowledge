import os
import pytz
from datetime import datetime
from flask import Flask
from dotenv import load_dotenv
from supabase import create_client, Client

supabase: Client | None = None

def get_today_et():
    tz = pytz.timezone(os.getenv("TIMEZONE", "America/New_York"))
    return datetime.now(tz).date()

def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev")
    app.config["APP_NAME"] = os.getenv("APP_NAME", "Ball Knowledge")

    # Supabase (optional for local JSON mode)
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_ANON_KEY")
    if url and key:
        global supabase
        supabase = create_client(url, key)
    else:
        print("[WARN] Supabase env vars missing. Running in local/JSON mode.")

    # Routes
    from .routes import bp as main_bp
    app.register_blueprint(main_bp)

    # Jinja globals
    app.jinja_env.globals.update(APP_NAME=app.config["APP_NAME"], get_today_et=get_today_et)

    return app
