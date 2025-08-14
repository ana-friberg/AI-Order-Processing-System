import json
import os
from typing import Tuple, Dict, Optional, List
from datetime import datetime
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, PyMongoError
import traceback

class ResponseHandler:
    """Handles Claude API response processing"""
    
    def __init__(self, mongodb_uri: Optional[str] = None, database_name: Optional[str] = None, collection_name: Optional[str] = None):
        """Initialize ResponseHandler with MongoDB configuration"""
        self.mongodb_uri = mongodb_uri
        self.database_name = database_name or 'agilent_orders'
        self.collection_name = collection_name or 'order_responses'
        self.mongo_client = None
        self.db = None
        self.collection = None
        
        if mongodb_uri:
            self._init_mongodb()
    
    def _init_mongodb(self):
        """Initialize MongoDB connection"""
        try:
            if not self.mongodb_uri:
                print("MongoDB URI not provided")
                return
            
            # Create MongoDB client with Atlas connection string
            self.mongo_client = MongoClient(self.mongodb_uri)
            
            # Test connection
            self.mongo_client.admin.command('ping')
            
            # Get database and collection
            self.db = self.mongo_client[self.database_name]
            self.collection = self.db[self.collection_name]
            
            print(f"MongoDB Atlas connected successfully to database: {self.database_name}, collection: {self.collection_name}")
            
        except ConnectionFailure as e:
            print(f"MongoDB Atlas connection failed: {str(e)}")
            self.mongo_client = None
        except Exception as e:
            print(f"Error initializing MongoDB Atlas: {str(e)}")
            self.mongo_client = None
    
    def save_to_mongodb(self, response_data: Dict, customer_po: str, filename: str) -> Dict:
        """Save response data to MongoDB and return simplified response"""
        try:
            if self.collection is None:
                print("MongoDB collection not initialized")
                return {
                    "success": False,
                    "customer_po": customer_po,
                    "filename": filename,
                    "error": "MongoDB not initialized",
                    "mongodb_saved": False
                }
            
            # Prepare document for MongoDB
            document = {
                'customer_po': customer_po,
                'filename': filename,
                'timestamp': datetime.utcnow(),
                'response_data': response_data,
                'processing_info': {
                    'extraction_validation': response_data.get('extraction_validation', {}),
                    'priority_update': response_data.get('priority_update', {}),
                    'priority_check': response_data.get('priority_check', {}),
                    'price_validation': response_data.get('price_validation', {}),
                    'item_validation': response_data.get('price_validation', {}).get('item_validation', {}),
                    'shipping_validation': response_data.get('price_validation', {}).get('shipping_validation', {})
                },
                'order_info': response_data.get('data', {}).get('order_info', {}),
                'items': response_data.get('data', {}).get('items', []),
                'success': response_data.get('success', False),
                # Extract and save delivery address and total price separately for easy querying
                'delivery_address': response_data.get('data', {}).get('order_info', {}).get('delivery_address', ''),
                'ai_extracted_total_price': response_data.get('data', {}).get('order_info', {}).get('total_price', ''),
                'priority_totprice': response_data.get('price_validation', {}).get('priority_totprice', 0),
                'price_match': response_data.get('price_validation', {}).get('price_match', False),
                'shipping_validation': response_data.get('price_validation', {}).get('shipping_validation', {}),
                'overall_validation_passed': response_data.get('price_validation', {}).get('overall_validation_pass', False)
            }
            
            # Insert or update document (upsert based on customer_po)
            result = self.collection.replace_one(
                {'customer_po': customer_po},
                document,
                upsert=True
            )
            
            if result.upserted_id:
                print(f"New document inserted to MongoDB for customer PO: {customer_po}")
            else:
                print(f"Document updated in MongoDB for customer PO: {customer_po}")
            
            # Create simplified response
            extraction_validation = response_data.get('extraction_validation', {})
            price_validation = response_data.get('price_validation', {})
            shipping_validation = price_validation.get('shipping_validation', {})
            simplified_response = {
                "success": response_data.get('success', False),
                "customer_po": customer_po,
                "filename": filename,
                "extraction_validation": {
                    "length_match": extraction_validation.get('length_match', False),
                    "expected_count": extraction_validation.get('expected_count', 0),
                    "extracted_count": extraction_validation.get('extracted_count', 0),
                    "missing_partnames": extraction_validation.get('missing_partnames', []),
                    "quantity_mismatches": extraction_validation.get('quantity_mismatches', []),
                    "price_mismatches": extraction_validation.get('price_mismatches', [])
                },
                "price_validation": {
                    "validation_attempted": price_validation.get('validation_attempted', False),
                    "price_match": price_validation.get('price_match', False),
                    "priority_totprice": price_validation.get('priority_totprice', 0),
                    "ai_total_price": price_validation.get('ai_total_price', ''),
                    "price_difference": price_validation.get('price_difference', 0),
                    "validation_message": price_validation.get('validation_message', ''),
                    "overall_validation_passed": price_validation.get('overall_validation_pass', False)
                },
                "shipping_validation": {
                    "shipping_validation_passed": shipping_validation.get('shipping_validation_passed', True),
                    "validation_case": shipping_validation.get('validation_case', ''),
                    "shipping_match": shipping_validation.get('shipping_match', False),
                    "priority_shipping_total": shipping_validation.get('priority_shipping_total', 0),
                    "ai_shipping_price": shipping_validation.get('ai_shipping_info', {}).get('price_numeric', 0) if shipping_validation.get('ai_shipping_info') else 0,
                    "validation_message": shipping_validation.get('validation_message', '')
                },
                "delivery_address": document.get('delivery_address', ''),
                "ai_extracted_total_price": document.get('ai_extracted_total_price', ''),
                "mongodb_saved": True
            }
            
            # Print the response being sent back
            print("=" * 80)
            print("RESPONSE BEING SENT BACK TO WORKATO:")
            print("=" * 80)
            print(json.dumps(simplified_response, indent=2, ensure_ascii=False))
            print("=" * 80)
            
            return simplified_response
            
        except PyMongoError as e:
            print(f"MongoDB error saving document: {str(e)}")
            error_response = {
                "success": False,
                "customer_po": customer_po,
                "filename": filename,
                "error": f"MongoDB error: {str(e)}",
                "mongodb_saved": False
            }
            
            # Print the error response being sent back
            print("=" * 80)
            print("ERROR RESPONSE BEING SENT BACK TO WORKATO:")
            print("=" * 80)
            print(json.dumps(error_response, indent=2, ensure_ascii=False))
            print("=" * 80)
            
            return error_response
        except Exception as e:
            print(f"Error saving to MongoDB: {str(e)}")
            traceback.print_exc()
            error_response = {
                "success": False,
                "customer_po": customer_po,
                "filename": filename,
                "error": f"Unexpected error: {str(e)}",
                "mongodb_saved": False
            }
            
            # Print the error response being sent back
            print("=" * 80)
            print("ERROR RESPONSE BEING SENT BACK TO WORKATO:")
            print("=" * 80)
            print(json.dumps(error_response, indent=2, ensure_ascii=False))
            print("=" * 80)
            
            return error_response
    
    def get_from_mongodb(self, customer_po: str) -> Optional[Dict]:
        """Retrieve response data from MongoDB by customer PO"""
        try:
            if self.collection is None:
                print("MongoDB collection not initialized")
                return None
            
            document = self.collection.find_one({'customer_po': customer_po})
            
            if document:
                print(f"Retrieved document from MongoDB for customer PO: {customer_po}")
                # Remove MongoDB internal fields
                document.pop('_id', None)
                return document
            else:
                print(f"No document found in MongoDB for customer PO: {customer_po}")
                return None
                
        except PyMongoError as e:
            print(f"MongoDB error retrieving document: {str(e)}")
            return None
        except Exception as e:
            print(f"Error retrieving from MongoDB: {str(e)}")
            return None
    
    def get_recent_orders(self, limit: int = 10) -> List[Dict]:
        """Get recent orders from MongoDB"""
        try:
            if self.collection is None:
                print("MongoDB collection not initialized")
                return []
            
            cursor = self.collection.find().sort('timestamp', -1).limit(limit)
            orders = []
            
            for doc in cursor:
                doc.pop('_id', None)  # Remove MongoDB internal field
                orders.append(doc)
            
            print(f"Retrieved {len(orders)} recent orders from MongoDB")
            return orders
            
        except PyMongoError as e:
            print(f"MongoDB error retrieving recent orders: {str(e)}")
            return []
        except Exception as e:
            print(f"Error retrieving recent orders: {str(e)}")
            return []
    
    def delete_from_mongodb(self, customer_po: str) -> bool:
        """Delete document from MongoDB by customer PO"""
        try:
            if self.collection is None:
                print("MongoDB collection not initialized")
                return False
            
            result = self.collection.delete_one({'customer_po': customer_po})
            
            if result.deleted_count > 0:
                print(f"Document deleted from MongoDB for customer PO: {customer_po}")
                return True
            else:
                print(f"No document found to delete for customer PO: {customer_po}")
                return False
                
        except PyMongoError as e:
            print(f"MongoDB error deleting document: {str(e)}")
            return False
        except Exception as e:
            print(f"Error deleting from MongoDB: {str(e)}")
            return False
    
    def close_mongodb_connection(self):
        """Close MongoDB connection"""
        try:
            if self.mongo_client:
                self.mongo_client.close()
                print("MongoDB connection closed")
        except Exception as e:
            print(f"Error closing MongoDB connection: {str(e)}")
    
    @staticmethod
    def clean_and_parse_json(response_text: str) -> Tuple[Optional[Dict], Optional[str]]:
        """Clean and parse JSON from Claude's response"""
        cleaned = response_text.replace("```json", "").replace("```", "").strip()
        json_start = cleaned.find('{')
        json_end = cleaned.rfind('}') + 1
        
        if json_start >= 0 and json_end > json_start:
            json_str = cleaned[json_start:json_end]
            try:
                return json.loads(json_str), None
            except json.JSONDecodeError as e:
                return None, str(e)
        return None, "No JSON found in response"

    @staticmethod
    def validate_extraction_results(extracted_data: Dict, priority_data: Dict) -> Dict:
        """Validate extraction results against Priority data"""
        try:
            # Get Priority PARTNAMES and their quantities/prices
            priority_items = priority_data.get('value', [{}])[0].get('PORDERITEMS_SUBFORM', [])
            priority_partnames = {item.get('PARTNAME'): item for item in priority_items}
            
            # Get extracted items
            extracted_items = extracted_data.get('items', [])
            extracted_partnames = {item.get('product_code'): item for item in extracted_items if item.get('product_code')}
            
            # Find missing partnames
            missing_partnames = []
            for partname in priority_partnames.keys():
                if partname not in extracted_partnames:
                    missing_partnames.append(partname)
            
            # Find quantity and price mismatches
            quantity_mismatches = []
            price_mismatches = []
            
            for partname, priority_item in priority_partnames.items():
                if partname in extracted_partnames:
                    extracted_item = extracted_partnames[partname]
                    
                    # Check quantity mismatch
                    priority_qty = priority_item.get('TQUANT', 0)
                    extracted_qty_str = extracted_item.get('quantity', '')
                    
                    # Extract numeric part from quantity string (e.g., "1 EA" -> 1)
                    extracted_qty = ResponseHandler._extract_numeric_quantity(extracted_qty_str)
                    
                    if extracted_qty != priority_qty:
                        quantity_mismatches.append({
                            'partname': partname,
                            'priority_quantity': priority_qty,
                            'extracted_quantity': extracted_qty_str,
                            'extracted_numeric': extracted_qty
                        })
                    
                    # Check price mismatch (compare VATPRICE from Priority with item_total from extracted)
                    priority_total = priority_item.get('VATPRICE', 0)
                    extracted_total_str = extracted_item.get('item_total', '')
                    
                    # Extract numeric part from price string (e.g., "USD 383.04" -> 383.04)
                    extracted_total = ResponseHandler._extract_numeric_price(extracted_total_str)
                    
                    # Calculate the absolute difference
                    price_difference = abs(extracted_total - priority_total)
                    
                    # Use smaller tolerance to catch small differences (0.005 instead of 0.01)
                    # This will catch differences of 0.01 or more (like 941.18 vs 941.17)
                    if price_difference >= 0.005:
                        price_mismatches.append({
                            'partname': partname,
                            'priority_total': priority_total,
                            'extracted_total': extracted_total_str,
                            'extracted_numeric': extracted_total,
                            'difference': price_difference
                        })
                        print(f"Price mismatch found for {partname}: Priority={priority_total}, Extracted={extracted_total_str} (numeric={extracted_total}), Difference={price_difference}")
                    else:
                        print(f"Price match for {partname}: Priority={priority_total}, Extracted={extracted_total_str} (numeric={extracted_total}), Difference={price_difference}")
            
            # Check if lengths match
            length_match = len(extracted_items) == len(priority_items)
            
            validation_result = {
                'length_match': length_match,
                'expected_count': len(priority_items),
                'extracted_count': len(extracted_items),
                'missing_partnames': missing_partnames,
                'quantity_mismatches': quantity_mismatches,
                'price_mismatches': price_mismatches,
                'validation_summary': {
                    'missing_count': len(missing_partnames),
                    'quantity_mismatch_count': len(quantity_mismatches),
                    'price_mismatch_count': len(price_mismatches),
                    'is_valid': len(missing_partnames) == 0 and len(quantity_mismatches) == 0 and len(price_mismatches) == 0 and length_match
                }
            }
            
            return validation_result
            
        except Exception as e:
            return {
                'error': f"Validation error: {str(e)}",
                'validation_summary': {
                    'is_valid': False
                }
            }

    @staticmethod
    def _extract_numeric_quantity(quantity_str: str) -> float:
        """Extract numeric quantity from string like '1 EA' -> 1.0"""
        try:
            import re
            # Find first number in the string
            match = re.search(r'(\d+(?:\.\d+)?)', str(quantity_str))
            if match:
                return float(match.group(1))
            return 0.0
        except:
            return 0.0

    @staticmethod
    def _extract_numeric_price(price_str: str) -> float:
        """
        Extract numeric price from string with European formatting support
        Examples:
        - 'USD 15,57' -> 15.57 (European format: comma as decimal)
        - 'USD 129,00' -> 129.0 (European format: comma as decimal)
        - 'USD 3.872,49' -> 3872.49 (European format: period as thousands, comma as decimal)
        - 'USD 941,17' -> 941.17 (European format: comma as decimal)
        - 'USD 1.181,38' -> 1181.38 (European format: period as thousands, comma as decimal)
        - 'USD 1,234.56' -> 1234.56 (US format: comma as thousands, period as decimal)
        """
        try:
            import re
            if not price_str:
                return 0.0
                
            original_str = str(price_str)
            print(f"Extracting price from: '{original_str}'")
            
            # Remove currency symbols and extra spaces
            cleaned = re.sub(r'[^\d\.,]', '', original_str)
            
            if not cleaned:
                print(f"  No numeric content found, returning 0.0")
                return 0.0
            
            print(f"  After removing non-numeric: '{cleaned}'")
            
            # Determine format based on the position of commas and periods
            if ',' in cleaned and '.' in cleaned:
                # Both comma and period present - determine which is decimal separator
                last_comma = cleaned.rfind(',')
                last_period = cleaned.rfind('.')
                
                if last_comma > last_period:
                    # European format: "3.872,49" (comma is decimal separator)
                    # Remove periods (thousands separators) and replace comma with period
                    cleaned = cleaned.replace('.', '').replace(',', '.')
                    print(f"  Detected European format (periods as thousands, comma as decimal): '{cleaned}'")
                else:
                    # US format: "1,234.56" (period is decimal separator)
                    # Remove commas (thousands separators)
                    cleaned = cleaned.replace(',', '')
                    print(f"  Detected US format (commas as thousands, period as decimal): '{cleaned}'")
                    
            elif ',' in cleaned and '.' not in cleaned:
                # Only comma present - need to determine if it's decimal or thousands separator
                parts = cleaned.split(',')
                
                # If there are exactly 2 parts and the second part has 1-2 digits, treat as decimal
                if len(parts) == 2 and len(parts[1]) <= 2:
                    # European decimal separator: "129,00" -> "129.00"
                    cleaned = cleaned.replace(',', '.')
                    print(f"  Detected European decimal comma: '{cleaned}'")
                elif len(parts) == 2 and len(parts[1]) == 3:
                    # Could be thousands separator: "1,234" -> "1234"
                    # But need to check if the first part is reasonable for thousands
                    if len(parts[0]) <= 3:
                        # First part is 1-3 digits, likely thousands separator
                        cleaned = cleaned.replace(',', '')
                        print(f"  Detected thousands separator comma: '{cleaned}'")
                    else:
                        # First part is too long, treat as decimal
                        cleaned = cleaned.replace(',', '.')
                        print(f"  Treating as decimal comma (first part too long): '{cleaned}'")
                else:
                    # Multiple commas or other pattern - remove commas as thousands separators
                    cleaned = cleaned.replace(',', '')
                    print(f"  Multiple commas detected, treating as thousands separators: '{cleaned}'")
                    
            elif '.' in cleaned and ',' not in cleaned:
                # Only period present - could be decimal or thousands separator
                parts = cleaned.split('.')
                if len(parts) == 2 and len(parts[1]) <= 2:
                    # Likely decimal separator (1-2 digits after period)
                    print(f"  Detected decimal period (US format): '{cleaned}'")
                    pass  # Keep as is
                else:
                    # Likely thousands separator - remove periods
                    cleaned = cleaned.replace('.', '')
                    print(f"  Detected thousands separator periods: '{cleaned}'")
            
            # Convert to float
            result = float(cleaned) if cleaned else 0.0
            print(f"  Final numeric value: {result}")
            return result
            
        except (ValueError, TypeError) as e:
            print(f"  Error converting '{price_str}' to numeric: {e}")
            return 0.0

    @staticmethod
    def save_output(content: str, file_path: str, is_json: bool = True) -> bool:
        """Save response to file"""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                if is_json:
                    json.dump(json.loads(content), f, indent=2)
                else:
                    f.write(content)
            return True
        except Exception as e:
            print(f"Error saving output: {str(e)}")
            return False

    @staticmethod
    def generate_summary(json_str: str) -> str:
        """Generate a human-readable summary from JSON response"""
        try:
            data = json.loads(json_str)
            order_info = data.get("order_info", {})
            items = data.get("items", [])
            
            summary = ["Order Summary:"]
            summary.append("-" * 40)
            
            # Add order information
            for key, value in order_info.items():
                summary.append(f"{key.replace('_', ' ').title()}: {value}")
            
            summary.append("\nItems:")
            summary.append("-" * 40)
            
            # Add item information
            for item in items:
                summary.append(f"\nItem Number: {item.get('item_number')}")
                summary.append(f"Product: {item.get('product_code')} - {item.get('description')}")
                summary.append(f"Quantity: {item.get('quantity')}")
                summary.append(f"Price: {item.get('unit_price')}")
                summary.append(f"Total: {item.get('item_total')}")
            
            return "\n".join(summary)
        except Exception as e:
            return f"Error generating summary: {str(e)}"
    
    @staticmethod
    def test_price_extraction():
        """Test function to verify price extraction works correctly"""
        test_cases = [
            "USD 15,57",        # Should be 15.57
            "USD 129,00",       # Should be 129.0 (the user's specific problem case)
            "USD 3.872,49",     # Should be 3872.49  
            "USD 715,06",       # Should be 715.06
            "USD 941,17",       # Should be 941.17
            "USD 1.181,38",     # Should be 1181.38
            "USD 1,234.56",     # Should be 1234.56 (US format)
            "USD941.17",        # Should be 941.17
            "$941.17",          # Should be 941.17
            "941.17"            # Should be 941.17
        ]
        
        expected_results = [15.57, 129.0, 3872.49, 715.06, 941.17, 1181.38, 1234.56, 941.17, 941.17, 941.17]
        
        print("Testing price extraction:")
        for i, test_case in enumerate(test_cases):
            result = ResponseHandler._extract_numeric_price(test_case)
            expected = expected_results[i] if i < len(expected_results) else "unknown"
            status = "✓ PASS" if (isinstance(expected, float) and abs(result - expected) < 0.01) else "✗ FAIL"
            print(f"  '{test_case}' -> {result} (expected: {expected}) {status}")
        
        # Test the specific cases from the user's problem
        print(f"\nUser's specific problem cases:")
        problem_cases = [
            ("USD 15,57", 15.57),
            ("USD 129,00", 129.0),   # The specific shipping case that was broken
            ("USD 3.872,49", 3872.49),
            ("USD 715,06", 715.06),
            ("USD 941,17", 941.17),
            ("USD 1.181,38", 1181.38)
        ]
        
        for test_str, expected_val in problem_cases:
            extracted_numeric = ResponseHandler._extract_numeric_price(test_str)
            difference = abs(extracted_numeric - expected_val)
            status = "✓ FIXED" if difference < 0.01 else "✗ STILL BROKEN"
            print(f"  '{test_str}' -> {extracted_numeric} (expected: {expected_val}) {status}")
            print(f"    Difference: {difference}")
            print(f"    Should be flagged as mismatch: {difference >= 0.005}")
            print()