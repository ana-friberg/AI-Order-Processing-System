# Standard library imports
import os
import json
import io
import base64
import re
import fitz  # PyMuPDF
from typing import Optional, Dict, Tuple
import anthropic
from .pdf_processor import PDFProcessor
from .response_handler import ResponseHandler
from flask import request, jsonify
from flask_restful import Resource, Api
import traceback
from .priority_api import PriorityAPIClient
from .order_validator import OrderValidator
from .cache_manager import CacheManager
from PIL import Image
from io import BytesIO
from werkzeug.datastructures import FileStorage
from app.utils.authUtil import decodeauthorizationHeader
from app import app

api = Api(app)

class ClaudeOrderProcessor:
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the Claude Order Processor"""
        self.api_key = api_key or app.config.get("ANTHROPIC_API_KEY_AGILENT")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY_AGILENT not configured")
            
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = "claude-sonnet-4-20250514"
        self.pdf_processor = PDFProcessor(app.config.get("PDF_CONFIG_AGILENT", {}), self.api_key)
        self.response_handler = ResponseHandler(
            mongodb_uri=app.config.get("MONGODB_URI_AGILENT"),
            database_name=app.config.get("MONGODB_DBNAME_AGILENT"),
            collection_name=app.config.get("MONGODB_COLLECTION_AGILENT")
        )

    def process_pdf_from_memory(self, file_stream: io.BytesIO, filename: str) -> Tuple[str, str]:
        """Extract customer PO from first page of PDF in memory"""
        try:
            # Process PDF to image
            pdf_document = fitz.open(stream=file_stream.read(), filetype="pdf")
            
            if len(pdf_document) == 0:
                return "", "Empty PDF document"
            
            # Get first page
            first_page = pdf_document.load_page(0)
            pix = first_page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
            img_bytes = pix.tobytes("jpeg")
            
            # Convert to PIL Image
            first_image = Image.open(BytesIO(img_bytes))
            
            # Resize if too large
            if first_image.width > 2048:
                ratio = 2048 / first_image.width
                new_height = int(first_image.height * ratio)
                first_image = first_image.resize((2048, new_height), Image.Resampling.LANCZOS)
            
            # Convert to base64
            img_byte_arr = BytesIO()
            first_image.save(img_byte_arr, format='JPEG', quality=85)
            img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
            
            # Create customer PO extraction prompt
            prompt = """
You are analyzing the first page of an Agilent order confirmation PDF to extract ONLY the Customer PO number.

CRITICAL CUSTOMER PO EXTRACTION RULES:
- Customer PO (Your Order) MUST be exactly 12 characters long
- Format: PO followed by 10 digits (e.g., PO2410000285)  
- If you find a shorter PO like "PO241000285", look for missing leading zeros
- Common pattern: PO + 2-digit year + 8-digit sequential number
- DO NOT accept POs shorter than 12 characters
- Look carefully in the document header, order details, or "Your Order" field
- Check both "Customer PO" and "Your Order" sections
- Look for PO numbers in tables, headers, and billing information

RESPONSE FORMAT:
Return ONLY the customer PO number in this format:
Customer PO: [PO_NUMBER]

If no valid 12-character customer PO is found, return:
Customer PO: NOT_FOUND

Example valid response:
Customer PO: PO2410000285
            """
            
            # Make API call
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": img_base64
                            }
                        }
                    ]
                }]
            )
            
            # Extract customer PO from response
            result_text = response.content[0].text.strip()
            customer_po = self._extract_po_from_response(result_text)
            
            return customer_po, result_text
            
        except Exception as e:
            print(f"Error processing PDF from memory: {str(e)}")
            return "", str(e)
    
    def _extract_po_from_response(self, response_text: str) -> str:
        """Extract customer PO from AI response"""
        import re
        
        # Look for "Customer PO: " pattern
        po_match = re.search(r'Customer PO:\s*([A-Z0-9]+)', response_text)
        if po_match:
            po = po_match.group(1)
            if po != "NOT_FOUND" and len(po) == 12 and po.startswith('PO'):
                return po
        
        # Fallback: look for PO pattern directly
        po_pattern = re.search(r'PO\d{10}', response_text)
        if po_pattern:
            return po_pattern.group(0)
        
        return ""

    def process_full_document_from_memory(self, file_stream: io.BytesIO, priority_partnames: list, filename: str) -> Dict:
        """Process entire PDF document with specific PARTNAMEs from memory"""
        try:
            # Reset stream position
            file_stream.seek(0)
            
            # Convert all pages to images
            pdf_document = fitz.open(stream=file_stream.read(), filetype="pdf")
            page_images = []
            
            for page_num in range(len(pdf_document)):
                page = pdf_document.load_page(page_num)
                pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0))
                img_bytes = pix.tobytes("jpeg")
                
                # Convert to PIL Image
                image = Image.open(BytesIO(img_bytes))
                
                # Resize if too large
                if image.width > 2048:
                    ratio = 2048 / image.width
                    new_height = int(image.height * ratio)
                    image = image.resize((2048, new_height), Image.Resampling.LANCZOS)
                
                # Convert to base64
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='JPEG', quality=85)
                img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode('utf-8')
                page_images.append(img_base64)
            
            total_pages = len(page_images)
            print(f"Processing {total_pages} pages with {len(priority_partnames)} Priority PARTNAMEs")
            
            # Process based on document length
            if total_pages <= 6:
                return self._process_short_pdf_with_partnames(page_images, priority_partnames)
            else:
                return self._process_long_pdf_with_partnames(page_images, priority_partnames)
                
        except Exception as e:
            print(f"Error processing full document from memory: {str(e)}")
            raise

    def _process_short_pdf_with_partnames(self, page_images: list, priority_partnames: list) -> Dict:
        """Process short PDFs with all images in one request"""
        try:
            # Create partnames prompt
            prompt = self._create_partnames_prompt(len(page_images), priority_partnames)
            
            # Create content with all images
            content = [{"type": "text", "text": prompt}]
            
            # Add all page images
            for i, img_base64 in enumerate(page_images):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_base64
                    }
                })
            
            # Make API call
            response = self.client.messages.create(
                model=self.model,
                max_tokens=15000,
                messages=[{
                    "role": "user",
                    "content": content
                }]
            )
            
            # Parse response
            result_text = response.content[0].text
            return self._parse_json_response(result_text)
            
        except Exception as e:
            print(f"Error processing short PDF with partnames: {str(e)}")
            raise

    def _process_long_pdf_with_partnames(self, page_images: list, priority_partnames: list) -> Dict:
        """Process long PDFs in batches of images"""
        try:
            batch_size = 4
            total_pages = len(page_images)
            all_items = []
            order_info = {}
            
            for batch_start in range(0, total_pages, batch_size):
                batch_end = min(batch_start + batch_size, total_pages)
                batch_images = page_images[batch_start:batch_end]
                
                print(f"Processing batch {batch_start//batch_size + 1}: pages {batch_start+1}-{batch_end}")
                
                # Create batch prompt
                include_order_info = (batch_start == 0)  # Only extract order info from first batch
                prompt = self._create_batch_partnames_prompt(
                    batch_start + 1, batch_end, total_pages, 
                    priority_partnames, include_order_info
                )
                
                # Create content for this batch
                content = [{"type": "text", "text": prompt}]
                
                # Add batch images
                for img_base64 in batch_images:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_base64
                        }
                    })
                
                # Make API call for this batch
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=15000,
                    messages=[{
                        "role": "user",
                        "content": content
                    }]
                )
                
                # Parse batch response
                batch_result = self._parse_json_response(response.content[0].text)
                
                # Extract order info from first batch
                if include_order_info and batch_result.get("order_info"):
                    order_info = batch_result["order_info"]
                
                # Collect items from this batch
                batch_items = batch_result.get("items", [])
                all_items.extend(batch_items)
                
                print(f"Batch {batch_start//batch_size + 1} extracted {len(batch_items)} items")
            
            # Combine all results
            final_result = {
                "order_info": order_info,
                "items": all_items
            }
            
            print(f"Total items extracted from all batches: {len(all_items)}")
            return final_result
            
        except Exception as e:
            print(f"Error processing long PDF with partnames: {str(e)}")
            raise

    def _create_batch_partnames_prompt(self, start_page: int, end_page: int, total_pages: int, 
                                     priority_partnames: list, include_order_info: bool) -> str:
        """Create prompt for batch processing with partnames"""
        schema = app.config.get("PDF_CONFIG_AGILENT", {}).get("json_schema", {})
        
        order_info_instruction = ""
        if include_order_info:
            order_info_instruction = """
FIRST BATCH - EXTRACT ORDER INFO:
- Order number, date, delivery date
- Customer number and customer PO
- Delivery address (look for ship-to address, billing address, or any street address)
- Total price/order total (look for grand total, order total, or final amount with currency)
- Shipping cost (look for "Shipping & Handling", "Expedited Handling", shipping charges, freight, or delivery charges)
"""
        
        return f"""
You are analyzing pages {start_page}-{end_page} of {total_pages} from an Agilent order confirmation PDF.

{order_info_instruction}

CRITICAL INSTRUCTIONS FOR ITEM EXTRACTION:
You MUST find ONLY items that match these specific product codes (PARTNAMES):

REQUIRED PARTNAMES TO FIND:
{json.dumps(priority_partnames, indent=2)}

EXTRACTION RULES FOR THIS BATCH:
1. Extract ONLY items whose product_code matches one of the above PARTNAMES exactly
2. Follow the exact sequence for each item: Header → Description → Origin → HTS → Discount → Item Total
3. If you find items in this batch that match the PARTNAMES, extract all their details
4. Ignore any items that don't match the required PARTNAMES

DETAILED ITEM EXTRACTION PATTERN:
For each item, look for this EXACT structure in the PDF:
- Line 1: [item_number] [product_code] [quantity] EA USD [unit_price] USD [extended_price]
- Line 2: [description] (product description text)
- Line 3: Country of Origin: [country] [delivery_date in DD.MM.YYYY format]
- Line 4: HTS Code: [hts_code]
- Line 5: Discount: USD [discount_amount]-
- Line 6: Item Total: USD [item_total]

Required JSON Schema:
{json.dumps(schema, indent=2)}

IMPORTANT: If the schema shows "delivery_adress" (with typo), please use "delivery_address" (correct spelling) in your response.

Return ONLY valid JSON matching the schema.
Extract only items that match the required PARTNAMES.
        """

    def _create_partnames_prompt(self, total_pages: int, priority_partnames: list) -> str:
        """Create prompt for processing with specific partnames"""
        schema = app.config.get("PDF_CONFIG_AGILENT", {}).get("json_schema", {})
        
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

DETAILED ITEM EXTRACTION PATTERN:
For each item, look for this EXACT structure in the PDF:
- Line 1: [item_number] [product_code] [quantity] EA USD [unit_price] USD [extended_price]
- Line 2: [description] (product description text)
- Line 3: Country of Origin: [country] [delivery_date in DD.MM.YYYY format]
- Line 4: HTS Code: [hts_code]
- Line 5: Discount: USD [discount_amount]-
- Line 6: Item Total: USD [item_total]

MANDATORY FIELDS FOR EACH ITEM:
- item_number: The item line number from first line
- product_code: Must match one of the required PARTNAMES from first line
- description: Product description from second line
- quantity: Quantity with unit from first line (e.g., "1 EA")
- unit_price: Unit price with currency from first line (e.g., "USD 383.04")
- extended_price: Extended price with currency from first line (e.g., "USD 383.04")
- discount: Discount amount from line 5 (if any, e.g., "USD 0.00")
- item_total: Final item total from line 6 (e.g., "USD 383.04")
- delivery_date: Delivery date from line 3 in DD.MM.YYYY format

ORDER INFO FIELDS (extract from first pages):
- order_number: The order number
- order_date: Order date
- delivery_date: Expected delivery date
- customer_number: Customer number
- customer_po: Customer PO (Your Order)
- delivery_address: Full delivery address (ship-to address, billing address, or customer address)
- total_price: Grand total/order total amount with currency
- shipping_cost: Extract "Shipping & Handling", "Expedited Handling", or any shipping/freight charges with amounts

SHIPPING EXTRACTION PRIORITY:
1. Look for "Shipping & Handling: USD [amount]" format
2. Look for "Expedited Handling: USD [amount]" format  
3. Look for any line containing "shipping", "handling", "freight", or "delivery" with a price
4. Extract the full text and amount for shipping validation

CRITICAL VALIDATION:
- Each product_code MUST be from the required PARTNAMES list
- Extract exactly {len(priority_partnames)} items (one for each PARTNAME)
- If a PARTNAME is not found in the document, include it with empty fields except product_code
- Ensure delivery_address is properly extracted from ship-to or billing sections
- Ensure shipping_cost captures the shipping line with amount

Required JSON Schema:
{json.dumps(schema, indent=2)}

IMPORTANT: If the schema shows "delivery_adress" (with typo), please use "delivery_address" (correct spelling) in your response.

Return ONLY valid JSON matching the schema with all required fields populated.
        """

    def _parse_json_response(self, response_text: str) -> Dict:
        """Parse JSON response from Claude"""
        try:
            # Clean the response - remove markdown formatting
            cleaned = response_text.replace("```json", "").replace("```", "").strip()
            
            # Find JSON boundaries
            json_start = cleaned.find('{')
            json_end = cleaned.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_str = cleaned[json_start:json_end]
                result = json.loads(json_str)
                print(f"Successfully parsed JSON response")
                return result
            else:
                # If no clear JSON boundaries found, try parsing the whole cleaned text
                result = json.loads(cleaned)
                print(f"Successfully parsed JSON response (fallback)")
                return result
                
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON response: {str(e)}")
            print(f"Response text preview: {response_text[:500]}...")
            
            # Return a basic structure
            print("Returning fallback JSON structure")
            return {
                "order_info": {
                    "order_number": "",
                    "order_date": "",
                    "delivery_date": "",
                    "customer_number": "",
                    "customer_po": "",
                    "delivery_address": ""
                },
                "items": []
            }

    def validate_customer_po(self, customer_po: str) -> Tuple[bool, str]:
        """Validate customer PO format and length"""
        if not customer_po:
            return False, "Customer PO is missing"
        
        if len(customer_po) != 12:
            return False, f"Customer PO must be 12 characters long, got {len(customer_po)} characters: '{customer_po}'"
        
        if not customer_po.startswith('PO'):
            return False, f"Customer PO must start with 'PO', got: '{customer_po}'"
        
        if not customer_po[2:].isdigit():
            return False, f"Customer PO must have 10 digits after 'PO', got: '{customer_po}'"
        
        return True, "Valid customer PO"

    def _extract_shipping_from_order_info(self, order_info: Dict, items: list) -> Optional[Dict]:
        """
        Extract shipping information from order_info or items list
        Looks for shipping-related fields and creates standardized shipping info
        Maps PDF shipping formats to Priority SH item formats
        """
        shipping_fields = [
            'shipping_cost', 'shipping_charge', 'shipping_price', 'shipping_total',
            'handling_cost', 'handling_charge', 'handling_price', 'handling_total',
            'freight_cost', 'freight_charge', 'freight_price', 'freight_total',
            'delivery_cost', 'delivery_charge', 'delivery_price', 'delivery_total'
        ]
        
        # First check order_info level for shipping fields
        for field in shipping_fields:
            if field in order_info and order_info[field]:
                shipping_value = str(order_info[field])
                if shipping_value and shipping_value.strip() and shipping_value.strip() != "0":
                    # Extract numeric value from shipping string
                    import re
                    price_cleaned = re.sub(r'[^\d\.]', '', shipping_value)
                    price_numeric = float(price_cleaned) if price_cleaned else 0
                    
                    if price_numeric > 0:  # Only return if there's actually a shipping cost
                        return {
                            "source": f"order_info.{field}",
                            "raw_text": shipping_value,
                            "price_raw": shipping_value,
                            "price_numeric": price_numeric,
                            "priority_mapping": "SH-AGILENT",  # Maps to Priority SH item format
                            "extraction_type": "direct_field"
                        }
        
        # Check for specific shipping keywords in order_info values
        shipping_keywords_mapping = {
            "shipping & handling": "SH-AGILENT",
            "expedited handling": "SH-EXPEDITED", 
            "shipping": "SH-STANDARD",
            "handling": "SH-HANDLING",
            "freight": "SH-FREIGHT",
            "delivery charge": "SH-DELIVERY"
        }
        
        for key, value in order_info.items():
            value_str = str(value).lower()
            for keyword, priority_code in shipping_keywords_mapping.items():
                if keyword in value_str:
                    # Extract numeric value
                    import re
                    price_cleaned = re.sub(r'[^\d\.]', '', str(value))
                    price_numeric = float(price_cleaned) if price_cleaned else 0
                    
                    if price_numeric > 0:  # Only return if there's actually a shipping cost
                        return {
                            "source": f"order_info.{key}",
                            "found_keyword": keyword,
                            "raw_text": str(value),
                            "price_raw": str(value),
                            "price_numeric": price_numeric,
                            "priority_mapping": priority_code,  # Maps to Priority SH item format
                            "extraction_type": "keyword_match"
                        }
        
        # Check items list for shipping entries (some PDFs include shipping as a line item)
        for item in items:
            if isinstance(item, dict):
                # Check if this item might be a shipping item
                item_desc = str(item.get('description', '')).lower()
                item_code = str(item.get('product_code', '')).lower()
                
                for keyword, priority_code in shipping_keywords_mapping.items():
                    if keyword in item_desc or keyword in item_code:
                        # Extract price from item
                        item_total = item.get('item_total', '') or item.get('extended_price', '')
                        if item_total:
                            import re
                            price_cleaned = re.sub(r'[^\d\.]', '', str(item_total))
                            price_numeric = float(price_cleaned) if price_cleaned else 0
                            
                            if price_numeric > 0:
                                return {
                                    "source": f"items[{items.index(item)}]",
                                    "found_keyword": keyword,
                                    "raw_text": f"{item_desc} - {item_total}",
                                    "price_raw": str(item_total),
                                    "price_numeric": price_numeric,
                                    "priority_mapping": priority_code,
                                    "extraction_type": "item_line"
                                }
        
        # If no shipping found at order level, fall back to items search
        # This will be handled by the existing _extract_ai_shipping_info method in priority_api
        return None


class ProcessOrder(Resource):
    def post(self):
        print(request, "REQUEST RECEIVED")
        """Process an order PDF from form-data upload"""
        try:
            print("Processing order from form-data upload")
            
            # Authentication - same pattern as ClaudeMultiPDFExtractor
            authorization_header = request.headers.get('Authorization')
            if not authorization_header:
                return {"error": "Authorization header is missing"}, 401
            
            username, password = decodeauthorizationHeader(authorization_header)
            if username != app.config.get("AUTHORIZATION_USERNAME") or password != app.config.get("AUTHORIZATION_PASSWORD"):
                return {"error": "Invalid credentials"}, 403
            
            # Check for files in request - handle both 'files' and 'Files'
            if 'files' not in request.files and 'Files' not in request.files:
                return {"error": "No files part in the request"}, 400
            
            # Get files list - try both possible field names
            files = request.files.getlist('files') or request.files.getlist('Files')
            if not files or len(files) == 0:
                return {"error": "No files uploaded"}, 400
            
            # Process the first file
            file = files[0]
            if not file.filename:
                return {"error": "Empty filename"}, 400
            
            print(f"Processing file: {file.filename}")
            
            # Read file into memory
            file_stream = io.BytesIO(file.read())
            
            # Initialize processor
            processor = ClaudeOrderProcessor(app.config.get("ANTHROPIC_API_KEY_AGILENT"))
            
            # STEP 1: Extract customer PO from first page
            print("STEP 1: Extracting customer PO from first page")
            customer_po, po_response = processor.process_pdf_from_memory(file_stream, file.filename)
            
            if not customer_po:
                return {
                    "error": "Could not extract valid customer PO from first page",
                    "po_response": po_response
                }, 400
            
            print(f"Extracted customer PO: {customer_po}")
            
            # STEP 2: Check if customer PO exists in Priority and get PARTNAMEs
            print("STEP 2: Checking Priority and extracting PARTNAMEs")
            priority_client = PriorityAPIClient(
                app.config.get("PRIORITY_URL"),
                app.config.get("PRIORITY_TOKEN")
            )
            
            exists, priority_data, _ = priority_client.get_order_data(customer_po)  # Ignore price validation for this initial check
            
            if not exists:
                return {
                    "success": False,
                    "message": "Customer PO does not exist in Priority",
                    "customer_po": customer_po,
                    "step": "priority_check_failed"
                }, 404
            
            # Extract PARTNAMEs from Priority
            priority_items = priority_data.get('value', [{}])[0].get('PORDERITEMS_SUBFORM', [])
            priority_partnames = [item.get('PARTNAME') for item in priority_items if item.get('PARTNAME')]
            
            print(f"Found {len(priority_partnames)} PARTNAMEs in Priority: {priority_partnames}")
            
            # STEP 3: Process entire document with specific PARTNAMEs
            print("STEP 3: Processing entire document with Priority PARTNAMEs")
            result_data = processor.process_full_document_from_memory(file_stream, priority_partnames, file.filename)
            
            # Add customer PO to result data
            if 'order_info' not in result_data:
                result_data['order_info'] = {}
            result_data['order_info']['customer_po'] = customer_po
            
            # STEP 4: Validate extraction results
            print("STEP 4: Validating extraction results")
            validation_results = ResponseHandler.validate_extraction_results(result_data, priority_data)
            
            # Extract items data for Priority update
            items_data = result_data.get("items", [])
            valid_items = []
            for item in items_data:
                product_code = item.get("product_code", "")
                delivery_date = item.get("delivery_date", "")
                if product_code and delivery_date:
                    valid_items.append({
                        "product_code": product_code,
                        "delivery_date": delivery_date
                    })
            
            print(f"Preparing Priority update - {len(valid_items)} items to update")
            
            # Extract delivery address from order info for address-based date calculation
            order_info = result_data.get("order_info", {})
            delivery_address = order_info.get("delivery_address", "")
            
            # Enhanced address extraction with multiple fallback strategies
            if not delivery_address:
                # Try alternative field names for address (including typo fix)
                delivery_address = (order_info.get("delivery_adress", "") or  # Handle potential typo in config
                                  order_info.get("address", "") or 
                                  order_info.get("ship_to_address", "") or 
                                  order_info.get("shipping_address", "") or
                                  order_info.get("customer_address", "") or
                                  order_info.get("billing_address", "") or "")
            
            # If still no address, search for address patterns in all order_info values
            if not delivery_address:
                import re
                address_patterns = [
                    r'\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Rd|Road|Dr|Drive|Blvd|Boulevard)\.?',
                    r'[A-Za-z\s]+,\s*[A-Za-z\s]+,\s*[A-Z]{2}\s+\d{5}',  # City, State, ZIP
                    r'P\.?O\.?\s+Box\s+\d+',  # PO Box
                ]
                
                for key, value in order_info.items():
                    if isinstance(value, str) and value.strip():
                        for pattern in address_patterns:
                            if re.search(pattern, value, re.IGNORECASE):
                                delivery_address = value.strip()
                                print(f"Found address pattern in field '{key}': {delivery_address}")
                                break
                        if delivery_address:
                            break
            
            print(f"Delivery address for calculation: '{delivery_address}'")
            
            # Extract total price from order info for price validation
            extracted_total_price = order_info.get("total_price", "") or order_info.get("order_total", "") or ""
            print(f"Extracted total price for validation: '{extracted_total_price}'")
            
            # Extract shipping information if available
            ai_shipping_info = processor._extract_shipping_from_order_info(order_info, valid_items)
            if ai_shipping_info:
                print(f"Found AI shipping information: {ai_shipping_info}")
            
            # Attempt to update Priority using update_order_items with address, total price, and shipping info
            update_success, update_message, update_results, price_validation = priority_client.update_order_items(
                customer_po, 
                valid_items,
                delivery_address,
                extracted_total_price,
                ai_shipping_info,
                items_data  # Pass full items data for validation
            )
            
            # Update order number after date updates (if successful)
            order_number_update_success = False
            order_number_message = ""
            
            if update_success:
                print("STEP 5: Updating order number in Priority")
                order_number_update_success, order_number_message = priority_client.update_order_number(customer_po)
                print(f"Order number update result: {order_number_message}")
            else:
                print("Skipping order number update due to failed date updates")
                order_number_message = "Skipped due to failed date updates"
            
            # Return comprehensive response
            response = {
                "success": True,
                "customer_po": customer_po,
                "filename": file.filename,
                "data": result_data,
                "priority_check": {
                    "exists": True,
                    "message": "Order exists in Priority",
                    "partnames_count": len(priority_partnames)
                },
                "extraction_validation": validation_results,
                "priority_update": {
                    "attempted": True,
                    "success": update_success,
                    "message": update_message,
                    "results": update_results
                },
                "price_validation": price_validation,
                "order_number_update": {
                    "attempted": update_success,  # Only attempted if date updates succeeded
                    "success": order_number_update_success,
                    "message": order_number_message
                },
                "processing_steps": [
                    "Customer PO extracted from first page",
                    "Priority order validated and PARTNAMEs retrieved",
                    "Full document processed with specific PARTNAMEs",
                    "Extraction results validated against Priority data",
                    "Priority TOTPRICE validated against AI-extracted total price",
                    "Priority order updated with delivery dates",
                    "Priority order number updated" if order_number_update_success else "Priority order number update skipped/failed"
                ]
            }
            
            # Initialize response handler with MongoDB config  
            response_handler = ResponseHandler(
                mongodb_uri=app.config.get("MONGODB_URI_AGILENT"),
                database_name=app.config.get("MONGODB_DBNAME_AGILENT"),
                collection_name=app.config.get("MONGODB_COLLECTION_AGILENT")
            )

            # Save to MongoDB and get simplified response
            if response_handler.mongo_client is not None:
                simplified_response = response_handler.save_to_mongodb(response, customer_po, file.filename)
                print(f"MongoDB save result: {simplified_response.get('mongodb_saved', False)}")
                
                # Close MongoDB connection when done
                response_handler.close_mongodb_connection()
                
                # Return the simplified response instead of the full response
                return simplified_response
            else:
                print("MongoDB not configured, skipping database save")
                # Create simplified response without MongoDB save
                extraction_validation = response.get('extraction_validation', {})
                price_validation = response.get('price_validation', {})
                order_info = response.get('data', {}).get('order_info', {})
                simplified_response = {
                    "success": response.get('success', False),
                    "customer_po": customer_po,
                    "filename": file.filename,
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
                        "validation_message": price_validation.get('validation_message', '')
                    },
                    "delivery_address": order_info.get('delivery_address', ''),
                    "ai_extracted_total_price": order_info.get('total_price', ''),
                    "mongodb_saved": False
                }
                
                return simplified_response
            
        except Exception as e:
            print(f"Error in process_order: {str(e)}")
            traceback.print_exc()
            return {
                "error": str(e),
                "trace": traceback.format_exc()
            }, 500


class CleanCache(Resource):
    def post(self):
        """Clean all cached files"""
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            cache_manager = CacheManager(current_dir)
            
            # Get specific file from request if provided
            data = request.get_json()
            specific_file = data.get('filename') if data else None
            
            if specific_file:
                success, cleaned_files, error = cache_manager.clean_specific_cache(specific_file)
            else:
                success, cleaned_files, error = cache_manager.clean_cache()
            
            if success:
                return {
                    "success": True,
                    "message": "Cache cleaned successfully",
                    "cleaned_files": cleaned_files
                }
            else:
                return {
                    "success": False,
                    "error": f"Error cleaning cache: {error}",
                    "partially_cleaned": cleaned_files
                }, 500

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }, 500

# Add Agilent resources
api.add_resource(ProcessOrder, '/process')
api.add_resource(CleanCache, '/clean-cache')