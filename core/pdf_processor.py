import fitz  # PyMuPDF
import anthropic
from typing import Dict, List, Optional, Tuple
import json
import base64
import io
import re
from PIL import Image

from config.constants import (
    CLAUDE_MODEL,
    MAX_TOKENS_FULL_DOC,
    MAX_TOKENS_BATCH,
    MAX_TOKENS_PO_EXTRACTION,
    PDF_BATCH_SIZE,
    PDF_MAX_PAGE_WIDTH,
    PDF_JPEG_QUALITY,
    PDF_DPI,
    PDF_SHORT_DOC_THRESHOLD,
    CUSTOMER_PO_LENGTH,
    CUSTOMER_PO_PREFIX,
)
from utils.logging_config import get_logger

logger = get_logger(__name__)


class PDFProcessor:
    """Convert PDF pages to images and delegate extraction to Claude Vision.

    This class works with file paths.  For in-memory stream processing see
    ClaudeOrderProcessor in core/claude_processor.py.
    """

    def __init__(self, config: Dict, api_key: Optional[str] = None):
        self.config = config
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else None
        self.model = CLAUDE_MODEL

    # ── Public interface ──────────────────────────────────────────────────────

    def get_page_count(self, file_path: str) -> int:
        try:
            with fitz.open(file_path) as pdf:
                return pdf.page_count
        except Exception as e:
            logger.error("Error getting page count for %s: %s", file_path, e)
            return 0

    def pdf_to_images(
        self, file_path: str, dpi: int = PDF_DPI, specific_page: int = None
    ) -> List[str]:
        """Convert PDF pages to base64-encoded JPEG strings."""
        images: List[str] = []
        try:
            with fitz.open(file_path) as pdf:
                pages = [specific_page] if specific_page is not None else range(pdf.page_count)
                for page_num in pages:
                    if page_num >= pdf.page_count:
                        continue
                    pix = pdf[page_num].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    if img.width > PDF_MAX_PAGE_WIDTH:
                        ratio = PDF_MAX_PAGE_WIDTH / img.width
                        img = img.resize(
                            (PDF_MAX_PAGE_WIDTH, int(img.height * ratio)),
                            Image.Resampling.LANCZOS,
                        )
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=PDF_JPEG_QUALITY)
                    images.append(base64.b64encode(buf.getvalue()).decode("utf-8"))
                    logger.debug("Converted page %d to image", page_num + 1)
        except Exception as e:
            logger.error("Error converting PDF to images: %s", e)
            raise
        return images

    def extract_customer_po_from_first_page(
        self, file_path: str, client: anthropic.Anthropic = None
    ) -> Tuple[str, str]:
        """Extract the Customer PO from the first page of a PDF file."""
        api_client = client or self.client
        if not api_client:
            raise ValueError("No Anthropic client available")
        first_page = self.pdf_to_images(file_path, specific_page=0)
        if not first_page:
            raise ValueError("Could not convert first page to image")
        logger.info("Extracting customer PO from first page of %s", file_path)
        response = api_client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS_PO_EXTRACTION,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": self._create_customer_po_prompt()},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": first_page[0],
                    }},
                ],
            }],
        )
        result_text = response.content[0].text.strip()
        return self._extract_po_from_response(result_text), result_text

    def process_document_with_partnames(
        self,
        file_path: str,
        priority_partnames: List[str],
        client: anthropic.Anthropic = None,
    ) -> Dict:
        """Process a PDF against a list of Priority PARTNAMEs and return extracted JSON."""
        api_client = client or self.client
        if not api_client:
            raise ValueError("No Anthropic client available")
        page_images = self.pdf_to_images(file_path)
        total_pages = len(page_images)
        logger.info("Processing %d pages with %d PARTNAMEs", total_pages, len(priority_partnames))
        if total_pages <= PDF_SHORT_DOC_THRESHOLD:
            return self._process_short_pdf_with_partnames(page_images, priority_partnames, api_client)
        return self._process_long_pdf_with_partnames(page_images, priority_partnames, api_client)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _create_customer_po_prompt(self) -> str:
        customer_po_rules = self.config.get("extraction_rules", {}).get("customer_po", {})
        return f"""
You are analyzing the first page of an Agilent order confirmation PDF to extract ONLY the Customer PO number.

CRITICAL CUSTOMER PO EXTRACTION RULES:
- Customer PO (Your Order) MUST be exactly {CUSTOMER_PO_LENGTH} characters long
- Format: {CUSTOMER_PO_PREFIX} followed by 10 digits (e.g., PO2410000285)
- If you find a shorter PO like "PO241000285", look for missing leading zeros
- Common pattern: {CUSTOMER_PO_PREFIX} + 2-digit year + 8-digit sequential number
- DO NOT accept POs shorter than {CUSTOMER_PO_LENGTH} characters
- Look carefully in the document header, order details, or "Your Order" field

Customer PO Validation Rules:
{json.dumps(customer_po_rules, indent=2)}

RESPONSE FORMAT:
Return ONLY the customer PO number in this format:
Customer PO: [PO_NUMBER]

If no valid {CUSTOMER_PO_LENGTH}-character customer PO is found, return:
Customer PO: NOT_FOUND

Example valid response:
Customer PO: PO2410000285
"""

    def _extract_po_from_response(self, response_text: str) -> str:
        po_match = re.search(r"Customer PO:\s*([A-Z0-9]+)", response_text)
        if po_match:
            po = po_match.group(1)
            if po != "NOT_FOUND" and len(po) == CUSTOMER_PO_LENGTH and po.startswith(CUSTOMER_PO_PREFIX):
                return po
        fallback = re.search(rf"{CUSTOMER_PO_PREFIX}\d{{10}}", response_text)
        return fallback.group(0) if fallback else ""

    def _process_short_pdf_with_partnames(
        self, page_images: List[str], priority_partnames: List[str], client: anthropic.Anthropic
    ) -> Dict:
        content = [{"type": "text", "text": self._create_partnames_prompt(len(page_images), priority_partnames)}]
        for img_b64 in page_images:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": img_b64,
            }})
        response = client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS_FULL_DOC,
            messages=[{"role": "user", "content": content}],
        )
        return self._parse_json_response(response.content[0].text)

    def _process_long_pdf_with_partnames(
        self, page_images: List[str], priority_partnames: List[str], client: anthropic.Anthropic
    ) -> Dict:
        total_pages = len(page_images)
        all_items: list = []
        order_info: dict = {}

        for batch_start in range(0, total_pages, PDF_BATCH_SIZE):
            batch_end = min(batch_start + PDF_BATCH_SIZE, total_pages)
            batch_images = page_images[batch_start:batch_end]
            logger.debug("Processing pages %d–%d of %d", batch_start + 1, batch_end, total_pages)

            content = [{"type": "text", "text": self._create_batch_partnames_prompt(
                batch_start + 1, batch_end, total_pages,
                priority_partnames, batch_start == 0,
            )}]
            for img_b64 in batch_images:
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": img_b64,
                }})

            response = client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS_BATCH,
                messages=[{"role": "user", "content": content}],
            )
            batch_result = self._parse_json_response(response.content[0].text)

            if batch_start == 0 and "order_info" in batch_result:
                order_info = batch_result["order_info"]

            batch_items = batch_result.get("items", [])
            all_items.extend(batch_items)
            logger.debug("Extracted %d items from batch", len(batch_items))

        logger.info("Total items extracted across all batches: %d", len(all_items))
        return {"order_info": order_info, "items": all_items}

    def _create_partnames_prompt(self, total_pages: int, priority_partnames: List[str]) -> str:
        schema = self.config.get("json_schema", {})
        if "order_info" in schema and "shipping_cost" not in schema["order_info"]:
            schema["order_info"]["shipping_cost"] = ""
        elif "order_info" not in schema:
            schema["order_info"] = {
                "order_number": "", "order_date": "", "delivery_date": "",
                "customer_number": "", "customer_po": "", "delivery_address": "",
                "total_price": "", "shipping_cost": "",
            }
        return f"""
You are analyzing {total_pages} pages of an Agilent order confirmation PDF provided as images.

CRITICAL INSTRUCTIONS:
You MUST extract ONLY the items that match these specific product codes (PARTNAMES) from Priority:

REQUIRED PARTNAMES TO FIND:
{json.dumps(priority_partnames, indent=2)}

EXTRACTION RULES:
1. Extract ONLY items whose product_code matches one of the above PARTNAMES exactly
2. The number of items you extract MUST match the number of PARTNAMES provided ({len(priority_partnames)} items)
3. If you cannot find all PARTNAMES, still extract what you find and note missing ones
4. Follow the exact sequence for each item: Header → Description → Origin → HTS → Discount → Item Total

MANDATORY FIELDS FOR EACH ITEM:
- item_number, product_code, description, quantity (e.g. "1 EA"), unit_price, extended_price,
  discount, item_total, delivery_date (DD.MM.YYYY)

ORDER INFO FIELDS (extract from first pages):
- order_number, order_date, delivery_date, customer_number, customer_po, delivery_address,
  total_price (grand total), shipping_cost (see rules below)

SHIPPING COST EXTRACTION:
- Look for "Shipping & Handling: USD X" or "Expedited Handling: USD X" in summary pages
- If none found, leave empty

Required JSON Schema:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON matching the schema.
"""

    def _create_batch_partnames_prompt(
        self,
        start_page: int, end_page: int, total_pages: int,
        priority_partnames: List[str], include_order_info: bool,
    ) -> str:
        schema = self.config.get("json_schema", {})
        if "order_info" in schema and "shipping_cost" not in schema["order_info"]:
            schema["order_info"]["shipping_cost"] = ""
        elif "order_info" not in schema:
            schema["order_info"] = {
                "order_number": "", "order_date": "", "delivery_date": "",
                "customer_number": "", "customer_po": "", "delivery_address": "",
                "total_price": "", "shipping_cost": "",
            }

        order_info_block = ""
        if include_order_info:
            order_info_block = """
FIRST BATCH — EXTRACT ORDER INFO:
- Order number, date, delivery date, customer number, customer PO
- Delivery address (ship-to or billing)
- Total price (grand total with currency)
- Shipping cost: look for "Shipping & Handling" or "Expedited Handling" entries in summary pages.
  Extract the amount only (e.g. "USD 166,00"). Leave empty if not found.
"""

        return f"""
You are analyzing pages {start_page}–{end_page} of {total_pages} from an Agilent order confirmation PDF.

{order_info_block}

CRITICAL INSTRUCTIONS FOR ITEM EXTRACTION:
You MUST find ONLY items that match these specific product codes (PARTNAMES):

REQUIRED PARTNAMES TO FIND:
{json.dumps(priority_partnames, indent=2)}

EXTRACTION RULES FOR THIS BATCH:
1. Extract ONLY items whose product_code matches one of the above PARTNAMES exactly
2. Follow the exact sequence: Header → Description → Origin → HTS → Discount → Item Total
3. Ignore items that don't match the required PARTNAMES

Required JSON Schema:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON matching the schema.
"""

    def _parse_json_response(self, response_text: str) -> Dict:
        cleaned = response_text.replace("```json", "").replace("```", "").strip()
        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}") + 1

        if json_start >= 0 and json_end > json_start:
            try:
                return json.loads(cleaned[json_start:json_end])
            except json.JSONDecodeError:
                pass

        # Regex fallback
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        logger.error("Could not parse JSON from Claude response; returning empty structure")
        return {
            "order_info": {
                "order_number": "", "order_date": "", "delivery_date": "",
                "customer_number": "", "customer_po": "", "delivery_address": "",
                "total_price": "", "shipping_cost": "",
            },
            "items": [],
        }
