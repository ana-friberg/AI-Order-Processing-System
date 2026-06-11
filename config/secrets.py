import os
from dotenv import load_dotenv

load_dotenv()

# ── Flask API Auth ────────────────────────────────────────────────────────────
AUTHORIZATION_USERNAME = os.getenv("AUTHORIZATION_USERNAME")
AUTHORIZATION_PASSWORD = os.getenv("AUTHORIZATION_PASSWORD")

# ── Claude / Anthropic ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY_AGILENT = os.getenv("ANTHROPIC_API_KEY_AGILENT")

# ── Priority ERP ──────────────────────────────────────────────────────────────
PRIORITY_URL = os.getenv("PRIORITY_URL")
PRIORITY_TOKEN = os.getenv("PRIORITY_TOKEN")
PRIORITY_MAIN_SCREEN = os.getenv("PRIORITY_MAIN_SCREEN", "PORDERS")
PRIORITY_MAIN_SUB_SCREEN = os.getenv("PRIORITY_MAIN_SUB_SCREEN", "PORDERITEMS_SUBFORM")

# ── MongoDB Atlas ─────────────────────────────────────────────────────────────
MONGODB_URI_AGILENT = os.getenv("MONGODB_URI_AGILENT")
MONGODB_DBNAME_AGILENT = os.getenv("MONGODB_DBNAME_AGILENT", "agilent_orders")
MONGODB_COLLECTION_AGILENT = os.getenv("MONGODB_COLLECTION_AGILENT", "order_responses")
