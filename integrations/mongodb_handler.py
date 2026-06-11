import json
import os
from typing import Tuple, Dict, Optional, List
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError
import traceback

from utils.logging_config import get_logger
from utils.price_utils import extract_numeric_price, extract_numeric_quantity
from config.constants import PRICE_FLAG_THRESHOLD

logger = get_logger(__name__)


class ResponseHandler:
    """Persist order processing results to MongoDB Atlas and build API responses."""

    def __init__(
        self,
        mongodb_uri: Optional[str] = None,
        database_name: Optional[str] = None,
        collection_name: Optional[str] = None,
    ):
        self.mongodb_uri = mongodb_uri
        self.database_name = database_name or "agilent_orders"
        self.collection_name = collection_name or "order_responses"
        self.mongo_client = None
        self.db = None
        self.collection = None
        if mongodb_uri:
            self._init_mongodb()

    # ── MongoDB lifecycle ─────────────────────────────────────────────────────

    def _init_mongodb(self) -> None:
        if not self.mongodb_uri:
            logger.warning("MongoDB URI not provided — skipping connection")
            return
        try:
            self.mongo_client = MongoClient(self.mongodb_uri)
            self.mongo_client.admin.command("ping")
            self.db = self.mongo_client[self.database_name]
            self.collection = self.db[self.collection_name]
            logger.info(
                "MongoDB connected: database=%s, collection=%s",
                self.database_name, self.collection_name,
            )
        except ConnectionFailure as e:
            logger.error("MongoDB connection failed: %s", e)
            self.mongo_client = None
        except Exception as e:
            logger.error("Error initialising MongoDB: %s", e)
            self.mongo_client = None

    def close_mongodb_connection(self) -> None:
        if self.mongo_client:
            self.mongo_client.close()
            logger.debug("MongoDB connection closed")

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_to_mongodb(self, response_data: Dict, customer_po: str, filename: str) -> Dict:
        """Upsert an order document and return a simplified response for the caller."""
        if self.collection is None:
            logger.warning("MongoDB not initialised — cannot save document for PO %s", customer_po)
            return {
                "success": False,
                "customer_po": customer_po,
                "filename": filename,
                "error": "MongoDB not initialised",
                "mongodb_saved": False,
            }
        try:
            document = {
                "customer_po": customer_po,
                "filename": filename,
                "timestamp": datetime.utcnow(),
                "response_data": response_data,
                "processing_info": {
                    "extraction_validation": response_data.get("extraction_validation", {}),
                    "priority_update": response_data.get("priority_update", {}),
                    "priority_check": response_data.get("priority_check", {}),
                    "price_validation": response_data.get("price_validation", {}),
                    "item_validation": response_data.get("price_validation", {}).get("item_validation", {}),
                    "shipping_validation": response_data.get("price_validation", {}).get("shipping_validation", {}),
                },
                "order_info": response_data.get("data", {}).get("order_info", {}),
                "items": response_data.get("data", {}).get("items", []),
                "success": response_data.get("success", False),
                "delivery_address": response_data.get("data", {}).get("order_info", {}).get("delivery_address", ""),
                "ai_extracted_total_price": response_data.get("data", {}).get("order_info", {}).get("total_price", ""),
                "priority_totprice": response_data.get("price_validation", {}).get("priority_totprice", 0),
                "price_match": response_data.get("price_validation", {}).get("price_match", False),
                "shipping_validation": response_data.get("price_validation", {}).get("shipping_validation", {}),
                "overall_validation_passed": response_data.get("price_validation", {}).get("overall_validation_pass", False),
            }

            result = self.collection.replace_one({"customer_po": customer_po}, document, upsert=True)
            if result.upserted_id:
                logger.info("Inserted new MongoDB document for PO: %s", customer_po)
            else:
                logger.info("Updated existing MongoDB document for PO: %s", customer_po)

            simplified = self._build_simplified_response(response_data, customer_po, filename, document)
            logger.debug("Response for PO %s: %s", customer_po, json.dumps(simplified, ensure_ascii=False))
            return simplified

        except PyMongoError as e:
            logger.error("MongoDB error saving PO %s: %s", customer_po, e)
            return {"success": False, "customer_po": customer_po, "filename": filename,
                    "error": f"MongoDB error: {e}", "mongodb_saved": False}
        except Exception as e:
            logger.error("Unexpected error saving PO %s: %s\n%s", customer_po, e, traceback.format_exc())
            return {"success": False, "customer_po": customer_po, "filename": filename,
                    "error": f"Unexpected error: {e}", "mongodb_saved": False}

    def get_from_mongodb(self, customer_po: str) -> Optional[Dict]:
        if self.collection is None:
            logger.warning("MongoDB not initialised")
            return None
        try:
            doc = self.collection.find_one({"customer_po": customer_po})
            if doc:
                doc.pop("_id", None)
                logger.debug("Retrieved document for PO: %s", customer_po)
            else:
                logger.debug("No document found for PO: %s", customer_po)
            return doc
        except Exception as e:
            logger.error("Error retrieving PO %s: %s", customer_po, e)
            return None

    def get_recent_orders(self, limit: int = 10) -> List[Dict]:
        if self.collection is None:
            logger.warning("MongoDB not initialised")
            return []
        try:
            orders = []
            for doc in self.collection.find().sort("timestamp", -1).limit(limit):
                doc.pop("_id", None)
                orders.append(doc)
            logger.debug("Retrieved %d recent orders", len(orders))
            return orders
        except Exception as e:
            logger.error("Error retrieving recent orders: %s", e)
            return []

    def delete_from_mongodb(self, customer_po: str) -> bool:
        if self.collection is None:
            logger.warning("MongoDB not initialised")
            return False
        try:
            result = self.collection.delete_one({"customer_po": customer_po})
            deleted = result.deleted_count > 0
            logger.info("Deleted document for PO %s: %s", customer_po, deleted)
            return deleted
        except Exception as e:
            logger.error("Error deleting PO %s: %s", customer_po, e)
            return False

    # ── Validation ────────────────────────────────────────────────────────────

    @staticmethod
    def validate_extraction_results(extracted_data: Dict, priority_data: Dict) -> Dict:
        """Compare AI-extracted items against Priority PORDERITEMS_SUBFORM data."""
        try:
            priority_items = priority_data.get("value", [{}])[0].get("PORDERITEMS_SUBFORM", [])
            priority_by_partname = {item.get("PARTNAME"): item for item in priority_items}
            extracted_items = extracted_data.get("items", [])
            extracted_by_code = {
                item.get("product_code"): item
                for item in extracted_items if item.get("product_code")
            }

            missing_partnames = [p for p in priority_by_partname if p not in extracted_by_code]
            quantity_mismatches, price_mismatches = [], []

            for partname, priority_item in priority_by_partname.items():
                if partname not in extracted_by_code:
                    continue
                ext_item = extracted_by_code[partname]

                priority_qty = priority_item.get("TQUANT", 0)
                ext_qty = ResponseHandler._extract_numeric_quantity(ext_item.get("quantity", ""))
                if ext_qty != priority_qty:
                    quantity_mismatches.append({
                        "partname": partname,
                        "priority_quantity": priority_qty,
                        "extracted_quantity": ext_item.get("quantity", ""),
                        "extracted_numeric": ext_qty,
                    })

                priority_total = priority_item.get("VATPRICE", 0)
                ext_total_str = ext_item.get("item_total", "")
                ext_total = ResponseHandler._extract_numeric_price(ext_total_str)
                diff = abs(ext_total - priority_total)
                if diff >= PRICE_FLAG_THRESHOLD:
                    price_mismatches.append({
                        "partname": partname,
                        "priority_total": priority_total,
                        "extracted_total": ext_total_str,
                        "extracted_numeric": ext_total,
                        "difference": diff,
                    })
                    logger.debug(
                        "Price mismatch for %s: Priority=%.4f Extracted=%s (%.4f) diff=%.4f",
                        partname, priority_total, ext_total_str, ext_total, diff,
                    )

            length_match = len(extracted_items) == len(priority_items)
            return {
                "length_match": length_match,
                "expected_count": len(priority_items),
                "extracted_count": len(extracted_items),
                "missing_partnames": missing_partnames,
                "quantity_mismatches": quantity_mismatches,
                "price_mismatches": price_mismatches,
                "validation_summary": {
                    "missing_count": len(missing_partnames),
                    "quantity_mismatch_count": len(quantity_mismatches),
                    "price_mismatch_count": len(price_mismatches),
                    "is_valid": not missing_partnames and not quantity_mismatches
                                and not price_mismatches and length_match,
                },
            }
        except Exception as e:
            return {"error": f"Validation error: {e}", "validation_summary": {"is_valid": False}}

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_numeric_price(price_str: str) -> float:
        return extract_numeric_price(price_str)

    @staticmethod
    def _extract_numeric_quantity(quantity_str: str) -> float:
        return float(extract_numeric_quantity(quantity_str))

    @staticmethod
    def clean_and_parse_json(response_text: str) -> Tuple[Optional[Dict], Optional[str]]:
        cleaned = response_text.replace("```json", "").replace("```", "").strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end]), None
            except json.JSONDecodeError as e:
                return None, str(e)
        return None, "No JSON found in response"

    @staticmethod
    def save_output(content: str, file_path: str, is_json: bool = True) -> bool:
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                if is_json:
                    json.dump(json.loads(content), f, indent=2)
                else:
                    f.write(content)
            return True
        except Exception as e:
            logger.error("Error saving output to %s: %s", file_path, e)
            return False

    @staticmethod
    def generate_summary(json_str: str) -> str:
        try:
            data = json.loads(json_str)
            order_info = data.get("order_info", {})
            items = data.get("items", [])
            lines = ["Order Summary:", "-" * 40]
            for key, value in order_info.items():
                lines.append(f"{key.replace('_', ' ').title()}: {value}")
            lines += ["\nItems:", "-" * 40]
            for item in items:
                lines += [
                    f"\nItem Number: {item.get('item_number')}",
                    f"Product: {item.get('product_code')} - {item.get('description')}",
                    f"Quantity: {item.get('quantity')}",
                    f"Price: {item.get('unit_price')}",
                    f"Total: {item.get('item_total')}",
                ]
            return "\n".join(lines)
        except Exception as e:
            return f"Error generating summary: {e}"

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _build_simplified_response(
        response_data: Dict, customer_po: str, filename: str, document: Dict
    ) -> Dict:
        ev = response_data.get("extraction_validation", {})
        pv = response_data.get("price_validation", {})
        sv = pv.get("shipping_validation", {})
        return {
            "success": response_data.get("success", False),
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
                "overall_validation_passed": pv.get("overall_validation_pass", False),
            },
            "shipping_validation": {
                "shipping_validation_passed": sv.get("shipping_validation_passed", True),
                "validation_case": sv.get("validation_case", ""),
                "shipping_match": sv.get("shipping_match", False),
                "priority_shipping_total": sv.get("priority_shipping_total", 0),
                "ai_shipping_price": (
                    sv.get("ai_shipping_info", {}).get("price_numeric", 0)
                    if sv.get("ai_shipping_info") else 0
                ),
                "validation_message": sv.get("validation_message", ""),
            },
            "delivery_address": document.get("delivery_address", ""),
            "ai_extracted_total_price": document.get("ai_extracted_total_price", ""),
            "mongodb_saved": True,
        }
