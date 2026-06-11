# ── Claude AI ────────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS_FULL_DOC = 15_000
MAX_TOKENS_BATCH = 8_000
MAX_TOKENS_PO_EXTRACTION = 1_000

# ── PDF processing ────────────────────────────────────────────────────────────
PDF_BATCH_SIZE = 4              # pages per Claude API call for long documents
PDF_MAX_PAGE_WIDTH = 2048       # pixels; wider pages are downscaled before encoding
PDF_JPEG_QUALITY = 85
PDF_DPI = 300
PDF_SHORT_DOC_THRESHOLD = 6    # documents with ≤ this many pages are sent in one request

# ── Customer PO validation ────────────────────────────────────────────────────
CUSTOMER_PO_LENGTH = 12         # always "PO" + 10 digits
CUSTOMER_PO_PREFIX = "PO"

# ── Priority ERP / OData ──────────────────────────────────────────────────────
PRIORITY_TIMEZONE_OFFSET = "+03:00"  # Israel Standard / Daylight Time
PRIORITY_DATE_OFFSET_DAYS = 6        # required date = delivery date minus this many days

# ── Address-based date logic (Bet HaKerem warehouse) ─────────────────────────
SPECIAL_ADDRESS_KEYWORD = "12 Bet"
SPECIAL_ADDRESS_SUFFIX = "St."

# ── Price validation tolerances ───────────────────────────────────────────────
PRICE_MATCH_TOLERANCE = 0.01    # differences below 1 cent are treated as a match
PRICE_FLAG_THRESHOLD = 0.005    # differences at or above 0.5 cents are flagged in logs

# ── Priority order status values (Hebrew) ────────────────────────────────────
STATUS_SUPPLIER_APPROVED = "אישור ספק"   # all validations passed
STATUS_SENT_TO_SUPPLIER = "נשלח לספק"   # processing complete but discrepancies found
