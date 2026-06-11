import os
import io
import json
import base64
import re
from typing import Optional, Dict, Tuple
import fitz  # PyMuPDF
import anthropic
from PIL import Image
from io import BytesIO

from config.constants import (
    CLAUDE_MODEL,
    MAX_TOKENS_FULL_DOC,
    MAX_TOKENS_BATCH,
    MAX_TOKENS_PO_EXTRACTION,
    PDF_BATCH_SIZE,
    PDF_MAX_PAGE_WIDTH,
    PDF_JPEG_QUALITY,
    PDF_SHORT_DOC_THRESHOLD,
    CUSTOMER_PO_LENGTH,
    CUSTOMER_PO_PREFIX,
)
from utils.logging_config import get_logger
from core.pdf_processor import PDFProcessor
from config import secrets

logger = get_logger(__name__)


class ClaudeOrderProcessor:
    """Orchestrate end-to-end order extraction from in-memory PDF streams.

    Processes PDFs that are already loaded into memory (BytesIO), unlike
    PDFProcessor which works with file paths.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or secrets.ANTHROPIC_API_KEY_AGILENT
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY_AGILENT not configured")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = CLAUDE_MODEL
        self.pdf_processor = PDFProcessor({}, self.api_key)

    # ── Public interface ──────────────────────────────────────────────────────

    def process_pdf_from_memory(self, file_stream: io.BytesIO, filename: str) -> Tuple[str, str]:
        """Extract the Customer PO number from the first page of an in-memory PDF."""
        try:
            pdf_document = fitz.open(stream=file_stream.read(), filetype="pdf")
            if len(pdf_document) == 0:
                return "", "Empty PDF document"

            img_b64 = self._page_to_jpeg_b64(pdf_document.load_page(0))
            prompt = self._build_po_extraction_prompt()
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS_PO_EXTRACTION,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    ],
                }],
            )
            result_text = response.content[0].text.strip()
            return self._extract_po_from_response(result_text), result_text

        except Exception as e:
            logger.error("Error extracting PO from memory for %s: %s", filename, e)
            return "", str(e)

    def process_full_document_from_memory(
        self, file_stream: io.BytesIO, priority_partnames: list, filename: str
    ) -> Dict:
        """Process the entire PDF against a list of Priority PARTNAMEs."""
        try:
            file_stream.seek(0)
            pdf_document = fitz.open(stream=file_stream.read(), filetype="pdf")
            page_images = [self._page_to_jpeg_b64(pdf_document.load_page(i)) for i in range(len(pdf_document))]
            total_pages = len(page_images)
            logger.info("Processing %d pages with %d PARTNAMEs from %s", total_pages, len(priority_partnames), filename)

            if total_pages <= PDF_SHORT_DOC_THRESHOLD:
                return self._process_short_pdf_with_partnames(page_images, priority_partnames)
            return self._process_long_pdf_with_partnames(page_images, priority_partnames)

        except Exception as e:
            logger.error("Error processing full document %s: %s", filename, e)
            raise

    def validate_customer_po(self, customer_po: str) -> Tuple[bool, str]:
        if not customer_po:
            return False, "Customer PO is missing"
        if len(customer_po) != CUSTOMER_PO_LENGTH:
            return False, f"Customer PO must be {CUSTOMER_PO_LENGTH} characters, got {len(customer_po)}: '{customer_po}'"
        if not customer_po.startswith(CUSTOMER_PO_PREFIX):
            return False, f"Customer PO must start with '{CUSTOMER_PO_PREFIX}', got: '{customer_po}'"
        if not customer_po[len(CUSTOMER_PO_PREFIX):].isdigit():
            return False, f"Customer PO must end with 10 digits, got: '{customer_po}'"
        return True, "Valid customer PO"

    def extract_shipping_from_order_info(self, order_info: Dict, items: list) -> Optional[Dict]:
        """Extract shipping information from order_info fields or the items list.

        Returns a structured shipping dict or None if no shipping charges are found.
        """
        shipping_fields = [
            "shipping_cost", "shipping_charge", "shipping_price", "shipping_total",
            "handling_cost", "handling_charge", "handling_price", "handling_total",
            "freight_cost", "freight_charge", "freight_price", "freight_total",
            "delivery_cost", "delivery_charge", "delivery_price", "delivery_total",
        ]
        keyword_to_code = {
            "shipping & handling": "SH-AGILENT",
            "expedited handling":  "SH-EXPEDITED",
            "shipping":            "SH-STANDARD",
            "handling":            "SH-HANDLING",
            "freight":             "SH-FREIGHT",
            "delivery charge":     "SH-DELIVERY",
        }

        # Check for well-known field names
        for field in shipping_fields:
            raw = str(order_info.get(field, "")).strip()
            if raw and raw != "0":
                price = self._numeric_from_raw(raw)
                if price > 0:
                    return {
                        "source": f"order_info.{field}",
                        "raw_text": raw, "price_raw": raw,
                        "price_numeric": price,
                        "priority_mapping": "SH-AGILENT",
                        "extraction_type": "direct_field",
                    }

        # Check all order_info values for shipping keywords
        for key, value in order_info.items():
            value_lower = str(value).lower()
            for keyword, code in keyword_to_code.items():
                if keyword in value_lower:
                    price = self._numeric_from_raw(str(value))
                    if price > 0:
                        return {
                            "source": f"order_info.{key}",
                            "found_keyword": keyword,
                            "raw_text": str(value), "price_raw": str(value),
                            "price_numeric": price,
                            "priority_mapping": code,
                            "extraction_type": "keyword_match",
                        }

        # Check items list for shipping line items
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_text = f"{item.get('description', '')} {item.get('product_code', '')}".lower()
            for keyword, code in keyword_to_code.items():
                if keyword in item_text:
                    raw = str(item.get("item_total", "") or item.get("extended_price", ""))
                    price = self._numeric_from_raw(raw)
                    if price > 0:
                        return {
                            "source": f"items[{idx}]",
                            "found_keyword": keyword,
                            "raw_text": f"{item_text} — {raw}",
                            "price_raw": raw,
                            "price_numeric": price,
                            "priority_mapping": code,
                            "extraction_type": "item_line",
                        }
        return None

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _page_to_jpeg_b64(page) -> str:
        """Render a fitz page to a base64-encoded JPEG string."""
        pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
        img = Image.open(BytesIO(pix.tobytes("jpeg")))
        if img.width > PDF_MAX_PAGE_WIDTH:
            ratio = PDF_MAX_PAGE_WIDTH / img.width
            img = img.resize((PDF_MAX_PAGE_WIDTH, int(img.height * ratio)), Image.Resampling.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=PDF_JPEG_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def _process_short_pdf_with_partnames(self, page_images: list, priority_partnames: list) -> Dict:
        schema = {}
        content = [{"type": "text", "text": self._build_full_doc_prompt(len(page_images), priority_partnames, schema)}]
        for img_b64 in page_images:
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}})
        response = self.client.messages.create(
            model=self.model, max_tokens=MAX_TOKENS_FULL_DOC,
            messages=[{"role": "user", "content": content}],
        )
        return self._parse_json_response(response.content[0].text)

    def _process_long_pdf_with_partnames(self, page_images: list, priority_partnames: list) -> Dict:
        schema = {}
        total_pages = len(page_images)
        all_items: list = []
        order_info: dict = {}

        for batch_start in range(0, total_pages, PDF_BATCH_SIZE):
            batch_end = min(batch_start + PDF_BATCH_SIZE, total_pages)
            batch_images = page_images[batch_start:batch_end]
            include_order_info = batch_start == 0
            logger.debug("Batch %d: pages %d–%d", batch_start // PDF_BATCH_SIZE + 1, batch_start + 1, batch_end)

            content = [{"type": "text", "text": self._build_batch_prompt(
                batch_start + 1, batch_end, total_pages, priority_partnames, schema, include_order_info,
            )}]
            for img_b64 in batch_images:
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}})

            response = self.client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS_BATCH,
                messages=[{"role": "user", "content": content}],
            )
            batch_result = self._parse_json_response(response.content[0].text)
            if include_order_info and batch_result.get("order_info"):
                order_info = batch_result["order_info"]
            batch_items = batch_result.get("items", [])
            all_items.extend(batch_items)
            logger.debug("Batch %d extracted %d items", batch_start // PDF_BATCH_SIZE + 1, len(batch_items))

        logger.info("Total items extracted: %d", len(all_items))
        return {"order_info": order_info, "items": all_items}

    def _build_po_extraction_prompt(self) -> str:
        return f"""
You are analyzing the first page of an Agilent order confirmation PDF to extract ONLY the Customer PO number.

CRITICAL CUSTOMER PO EXTRACTION RULES:
- Customer PO (Your Order) MUST be exactly {CUSTOMER_PO_LENGTH} characters long
- Format: {CUSTOMER_PO_PREFIX} followed by 10 digits (e.g., PO2410000285)
- If you find a shorter PO like "PO241000285", look for missing leading zeros
- DO NOT accept POs shorter than {CUSTOMER_PO_LENGTH} characters

RESPONSE FORMAT:
Return ONLY the customer PO number in this format:
Customer PO: [PO_NUMBER]

If no valid {CUSTOMER_PO_LENGTH}-character customer PO is found, return:
Customer PO: NOT_FOUND
"""

    @staticmethod
    def _build_full_doc_prompt(total_pages: int, priority_partnames: list, schema: dict) -> str:
        return f"""
You are analyzing {total_pages} pages of an Agilent order confirmation PDF provided as images.

CRITICAL INSTRUCTIONS:
Extract ONLY items matching these PARTNAMEs:
{json.dumps(priority_partnames, indent=2)}

EXTRACTION RULES:
1. Extract ONLY items whose product_code matches one of the above PARTNAMES exactly
2. Extract exactly {len(priority_partnames)} items (one per PARTNAME)
3. If a PARTNAME is not found, include it with empty fields except product_code

MANDATORY ITEM FIELDS: item_number, product_code, description, quantity ("1 EA"),
unit_price, extended_price, discount, item_total, delivery_date (DD.MM.YYYY)

ORDER INFO FIELDS: order_number, order_date, delivery_date, customer_number, customer_po,
delivery_address, total_price (grand total), shipping_cost (e.g. "USD 166,00" — leave empty if absent)

IMPORTANT: If the schema shows "delivery_adress" (typo), use "delivery_address" in your response.

Required JSON Schema:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON matching the schema.
"""

    @staticmethod
    def _build_batch_prompt(
        start: int, end: int, total: int,
        priority_partnames: list, schema: dict, include_order_info: bool,
    ) -> str:
        order_info_block = ""
        if include_order_info:
            order_info_block = """
FIRST BATCH — extract order_info:
- order_number, order_date, delivery_date, customer_number, customer_po
- delivery_address (ship-to or billing)
- total_price (grand total with currency)
- shipping_cost: look for "Shipping & Handling" or "Expedited Handling"; extract amount only; leave empty if not found
"""
        return f"""
You are analyzing pages {start}–{end} of {total} from an Agilent order confirmation PDF.

{order_info_block}

Extract ONLY items matching these PARTNAMEs:
{json.dumps(priority_partnames, indent=2)}

IMPORTANT: If the schema shows "delivery_adress" (typo), use "delivery_address" in your response.

Required JSON Schema:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON matching the schema.
"""

    @staticmethod
    def _extract_po_from_response(response_text: str) -> str:
        match = re.search(r"Customer PO:\s*([A-Z0-9]+)", response_text)
        if match:
            po = match.group(1)
            if po != "NOT_FOUND" and len(po) == CUSTOMER_PO_LENGTH and po.startswith(CUSTOMER_PO_PREFIX):
                return po
        fallback = re.search(rf"{CUSTOMER_PO_PREFIX}\d{{10}}", response_text)
        return fallback.group(0) if fallback else ""

    @staticmethod
    def _parse_json_response(response_text: str) -> Dict:
        cleaned = response_text.replace("```json", "").replace("```", "").strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end])
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.error("Could not parse Claude JSON response; returning empty structure")
        return {
            "order_info": {
                "order_number": "", "order_date": "", "delivery_date": "",
                "customer_number": "", "customer_po": "", "delivery_address": "",
                "total_price": "", "shipping_cost": "",
            },
            "items": [],
        }

    @staticmethod
    def _numeric_from_raw(raw: str) -> float:
        cleaned = re.sub(r"[^\d\.]", "", raw)
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0
