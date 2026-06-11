import base64
import hmac
import io
import os
import traceback
import re

from flask import request
from flask_restful import Resource

from config import secrets
from core.claude_processor import ClaudeOrderProcessor
from integrations.priority_api import PriorityAPIClient
from integrations.mongodb_handler import ResponseHandler
from utils.cache_manager import CacheManager
from utils.logging_config import get_logger

logger = get_logger(__name__)


class ProcessOrder(Resource):
    """POST /process — accept a PDF upload and run the full order processing pipeline."""

    def post(self):
        try:
            # ── Authentication ────────────────────────────────────────────────
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return {"error": "Authorization header is missing"}, 401

            username, password = _decode_basic_auth(auth_header)
            if username is None or password is None:
                return {"error": "Malformed Authorization header"}, 401
            valid_user = hmac.compare_digest(username, secrets.AUTHORIZATION_USERNAME or "")
            valid_pass = hmac.compare_digest(password, secrets.AUTHORIZATION_PASSWORD or "")
            if not (valid_user and valid_pass):
                return {"error": "Invalid credentials"}, 403

            # ── File validation ───────────────────────────────────────────────
            files = (request.files.getlist("files")
                     or request.files.getlist("Files")
                     or request.files.getlist("file"))
            if not files:
                return {"error": "No files uploaded"}, 400
            file = files[0]
            if not file.filename:
                return {"error": "Empty filename"}, 400

            logger.info("Processing file: %s", file.filename)
            file_stream = io.BytesIO(file.read())
            processor = ClaudeOrderProcessor(secrets.ANTHROPIC_API_KEY_AGILENT)

            # ── Step 1: Extract Customer PO from first page ───────────────────
            logger.info("Step 1: Extracting customer PO")
            customer_po, po_response = processor.process_pdf_from_memory(file_stream, file.filename)
            if not customer_po:
                return {
                    "error": "Could not extract valid customer PO from first page",
                    "po_response": po_response,
                }, 400
            logger.info("Extracted customer PO: %s", customer_po)

            # ── Step 2: Validate PO in Priority and retrieve PARTNAMEs ────────
            logger.info("Step 2: Checking Priority and extracting PARTNAMEs")
            priority_client = PriorityAPIClient(
                secrets.PRIORITY_URL,
                secrets.PRIORITY_TOKEN,
            )
            exists, priority_data, _ = priority_client.get_order_data(customer_po)
            if not exists:
                return {
                    "success": False,
                    "message": "Customer PO does not exist in Priority",
                    "customer_po": customer_po,
                    "step": "priority_check_failed",
                }, 404

            priority_items = priority_data.get("value", [{}])[0].get("PORDERITEMS_SUBFORM", [])
            priority_partnames = [i.get("PARTNAME") for i in priority_items if i.get("PARTNAME")]
            logger.info("Found %d PARTNAMEs in Priority: %s", len(priority_partnames), priority_partnames)

            # ── Step 3: Process full document with PARTNAMEs ──────────────────
            logger.info("Step 3: Processing full document")
            result_data = processor.process_full_document_from_memory(file_stream, priority_partnames, file.filename)
            if "order_info" not in result_data:
                result_data["order_info"] = {}
            result_data["order_info"]["customer_po"] = customer_po

            # ── Step 4: Validate extraction results ───────────────────────────
            logger.info("Step 4: Validating extraction results")
            validation_results = ResponseHandler.validate_extraction_results(result_data, priority_data)

            valid_items = [
                {"product_code": i.get("product_code"), "delivery_date": i.get("delivery_date")}
                for i in result_data.get("items", [])
                if i.get("product_code") and i.get("delivery_date")
            ]
            logger.info("Prepared %d items for Priority update", len(valid_items))

            order_info = result_data.get("order_info", {})
            delivery_address = _resolve_delivery_address(order_info)
            extracted_total_price = order_info.get("total_price", "") or order_info.get("order_total", "")
            ai_shipping_info = processor.extract_shipping_from_order_info(order_info, valid_items)

            if delivery_address:
                logger.info("Delivery address: %s", delivery_address)
            if ai_shipping_info:
                logger.info("AI shipping info found: %s", ai_shipping_info.get("raw_text", ""))

            # ── Step 5: Update Priority line items ────────────────────────────
            logger.info("Step 5: Updating Priority delivery dates")
            update_success, update_message, update_results, price_validation = priority_client.update_order_items(
                customer_po, valid_items, delivery_address,
                extracted_total_price, ai_shipping_info,
                result_data.get("items", []),
            )

            # ── Step 6: Update order number ───────────────────────────────────
            order_num_success = False
            order_num_message = "Skipped due to failed date updates"
            if update_success:
                logger.info("Step 6: Updating order number")
                order_num_success, order_num_message = priority_client.update_order_number(customer_po)
                logger.info("Order number update: %s", order_num_message)
            else:
                logger.warning("Skipping order number update — date updates failed")

            # ── Build response ────────────────────────────────────────────────
            full_response = {
                "success": True,
                "customer_po": customer_po,
                "filename": file.filename,
                "data": result_data,
                "priority_check": {
                    "exists": True,
                    "message": "Order exists in Priority",
                    "partnames_count": len(priority_partnames),
                },
                "extraction_validation": validation_results,
                "priority_update": {
                    "attempted": True,
                    "success": update_success,
                    "message": update_message,
                    "results": update_results,
                },
                "price_validation": price_validation,
                "order_number_update": {
                    "attempted": update_success,
                    "success": order_num_success,
                    "message": order_num_message,
                },
            }

            # ── Persist to MongoDB ────────────────────────────────────────────
            response_handler = ResponseHandler(
                mongodb_uri=secrets.MONGODB_URI_AGILENT,
                database_name=secrets.MONGODB_DBNAME_AGILENT,
                collection_name=secrets.MONGODB_COLLECTION_AGILENT,
            )
            if response_handler.mongo_client is not None:
                simplified = response_handler.save_to_mongodb(full_response, customer_po, file.filename)
                response_handler.close_mongodb_connection()
                return simplified

            logger.warning("MongoDB not configured — returning response without persistence")
            return _build_no_mongo_response(full_response, customer_po, file.filename)

        except Exception as e:
            logger.error("Unhandled error in ProcessOrder: %s\n%s", e, traceback.format_exc())
            return {"error": "Internal server error"}, 500


class CleanCache(Resource):
    """POST /clean-cache — remove temporary files from doc/ and output/."""

    def post(self):
        try:
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return {"error": "Authorization header is missing"}, 401
            username, password = _decode_basic_auth(auth_header)
            if username is None or password is None:
                return {"error": "Malformed Authorization header"}, 401
            valid_user = hmac.compare_digest(username, secrets.AUTHORIZATION_USERNAME or "")
            valid_pass = hmac.compare_digest(password, secrets.AUTHORIZATION_PASSWORD or "")
            if not (valid_user and valid_pass):
                return {"error": "Invalid credentials"}, 403

            # __file__ is api/routes.py — go up one level to the project root
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_manager = CacheManager(project_root)
            data = request.get_json()
            specific_file = data.get("filename") if data else None

            if specific_file:
                success, cleaned, error = cache_manager.clean_specific_cache(specific_file)
            else:
                success, cleaned, error = cache_manager.clean_cache()

            if success:
                return {"success": True, "message": "Cache cleaned successfully", "cleaned_files": cleaned}
            return {"success": False, "error": f"Error cleaning cache: {error}", "partially_cleaned": cleaned}, 500

        except Exception as e:
            return {"success": False, "error": str(e)}, 500


# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_basic_auth(auth_header: str) -> tuple[str | None, str | None]:
    """Decode an HTTP Basic Authorization header into (username, password), or (None, None) on error."""
    try:
        scheme, _, encoded = auth_header.partition(" ")
        if scheme.lower() != "basic" or not encoded:
            return None, None
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, sep, password = decoded.partition(":")
        if not sep:
            return None, None
        return username, password
    except Exception:
        return None, None


def _resolve_delivery_address(order_info: dict) -> str:
    """Try multiple field names and patterns to find the delivery address."""
    address = (
        order_info.get("delivery_address")
        or order_info.get("delivery_adress")   # handle typo present in config schema
        or order_info.get("address")
        or order_info.get("ship_to_address")
        or order_info.get("shipping_address")
        or order_info.get("customer_address")
        or order_info.get("billing_address")
        or ""
    )
    if address:
        return address

    # Fallback: scan all string values for address patterns
    patterns = [
        r"\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard)\.?",
        r"[A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Z]{2}\s+\d{5}",
        r"P\.?O\.?\s+Box\s+\d+",
    ]
    for key, value in order_info.items():
        if isinstance(value, str) and value.strip():
            for pattern in patterns:
                if re.search(pattern, value, re.IGNORECASE):
                    logger.debug("Found address pattern in field '%s': %s", key, value.strip())
                    return value.strip()
    return ""


def _build_no_mongo_response(full_response: dict, customer_po: str, filename: str) -> dict:
    ev = full_response.get("extraction_validation", {})
    pv = full_response.get("price_validation", {})
    oi = full_response.get("data", {}).get("order_info", {})
    return {
        "success": full_response.get("success", False),
        "customer_po": customer_po,
        "filename": filename,
        "extraction_validation": {
            "length_match": ev.get("length_match", False),
            "expected_count": ev.get("expected_count", 0),
            "extracted_count": ev.get("extracted_count", 0),
            "missing_partnames": ev.get("missing_partnames", []),
            "quantity_mismatches": ev.get("quantity_mismatches", []),
            "price_mismatches": ev.get("price_mismatches", []),
        },
        "price_validation": {
            "validation_attempted": pv.get("validation_attempted", False),
            "price_match": pv.get("price_match", False),
            "priority_totprice": pv.get("priority_totprice", 0),
            "ai_total_price": pv.get("ai_total_price", ""),
            "price_difference": pv.get("price_difference", 0),
            "validation_message": pv.get("validation_message", ""),
        },
        "delivery_address": oi.get("delivery_address", ""),
        "ai_extracted_total_price": oi.get("total_price", ""),
        "mongodb_saved": False,
    }
