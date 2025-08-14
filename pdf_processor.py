import fitz  # PyMuPDF
import anthropic
from typing import Dict, List, Optional, Tuple
import json
import base64
import io
from PIL import Image

class PDFProcessor:
    """Handles PDF processing using image conversion for Claude Vision"""
    
    def __init__(self, config: Dict, api_key: Optional[str] = None):
        self.config = config
        self.api_key = api_key
        if self.api_key:
            self.client = anthropic.Anthropic(api_key=self.api_key)
        else:
            self.client = None
        self.model = "claude-sonnet-4-20250514"

    def get_page_count(self, file_path: str) -> int:
        """Get the total number of pages in a PDF file"""
        try:
            with fitz.open(file_path) as pdf:
                return pdf.page_count
        except Exception as e:
            print(f"Error getting page count: {str(e)}")
            return 0

    def pdf_to_images(self, file_path: str, dpi: int = 300, specific_page: int = None) -> List[str]:
        """Convert PDF pages to base64 encoded images"""
        images = []
        
        try:
            with fitz.open(file_path) as pdf:
                page_count = pdf.page_count
                
                # If specific page requested, only process that page
                pages_to_process = [specific_page] if specific_page is not None else range(page_count)
                
                for page_num in pages_to_process:
                    if page_num >= page_count:
                        continue
                        
                    page = pdf[page_num]
                    
                    # Convert page to image with high resolution
                    mat = fitz.Matrix(dpi/72, dpi/72)
                    pix = page.get_pixmap(matrix=mat)
                    
                    # Convert to PIL Image for better quality control
                    img_data = pix.tobytes("png")
                    img = Image.open(io.BytesIO(img_data))
                    
                    # Optimize image size while maintaining quality
                    if img.width > 2048:
                        ratio = 2048 / img.width
                        new_height = int(img.height * ratio)
                        img = img.resize((2048, new_height), Image.Resampling.LANCZOS)
                    
                    # Convert back to bytes
                    img_buffer = io.BytesIO()
                    img.save(img_buffer, format='PNG', optimize=True)
                    img_bytes = img_buffer.getvalue()
                    
                    # Encode to base64
                    img_base64 = base64.b64encode(img_bytes).decode('utf-8')
                    images.append(img_base64)
                    
                    print(f"Converted page {page_num + 1} to image")
                    
        except Exception as e:
            print(f"Error converting PDF to images: {str(e)}")
            raise
            
        return images

    def extract_customer_po_from_first_page(self, file_path: str, client: anthropic.Anthropic = None) -> Tuple[str, str]:
        """Extract only customer PO from first page"""
        try:
            # Use provided client or fallback to instance client
            api_client = client or self.client
            if not api_client:
                raise ValueError("No Anthropic client available")
            
            # Convert only first page to image
            first_page_images = self.pdf_to_images(file_path, specific_page=0)
            
            if not first_page_images:
                raise ValueError("Could not convert first page to image")
            
            print("Extracting customer PO from first page")
            
            # Create content for customer PO extraction
            content = [{
                "type": "text",
                "text": self._create_customer_po_prompt()
            }, {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": first_page_images[0]
                }
            }]
            
            # Make API call
            response = api_client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": content
                }]
            )
            
            # Parse response to extract customer PO
            result_text = response.content[0].text.strip()
            
            # Extract customer PO from response
            customer_po = self._extract_po_from_response(result_text)
            
            return customer_po, result_text
            
        except Exception as e:
            print(f"Error extracting customer PO: {str(e)}")
            raise

    def process_document_with_partnames(self, file_path: str, priority_partnames: List[str], client: anthropic.Anthropic = None) -> Dict:
        """Process PDF with specific PARTNAMES from Priority"""
        try:
            # Use provided client or fallback to instance client
            api_client = client or self.client
            if not api_client:
                raise ValueError("No Anthropic client available")
            
            # Convert all pages to images
            page_images = self.pdf_to_images(file_path)
            total_pages = len(page_images)
            
            print(f"Processing {total_pages} pages with {len(priority_partnames)} Priority PARTNAMEs")
            
            # Process based on document length
            if total_pages <= 6:
                return self._process_short_pdf_with_partnames(page_images, priority_partnames, api_client)
            else:
                return self._process_long_pdf_with_partnames(page_images, priority_partnames, api_client)
                
        except Exception as e:
            print(f"Error processing document with partnames: {str(e)}")
            raise

    def _create_customer_po_prompt(self) -> str:
        """Create prompt for customer PO extraction only"""
        customer_po_rules = self.config.get("extraction_rules", {}).get("customer_po", {})
        
        return f"""
You are analyzing the first page of an Agilent order confirmation PDF to extract ONLY the Customer PO number.

CRITICAL CUSTOMER PO EXTRACTION RULES:
- Customer PO (Your Order) MUST be exactly 12 characters long
- Format: PO followed by 10 digits (e.g., PO2410000285)
- If you find a shorter PO like "PO241000285", look for missing leading zeros
- Common pattern: PO + 2-digit year + 8-digit sequential number
- DO NOT accept POs shorter than 12 characters
- Look carefully in the document header, order details, or "Your Order" field

Customer PO Validation Rules:
{json.dumps(customer_po_rules, indent=2)}

RESPONSE FORMAT:
Return ONLY the customer PO number in this format:
Customer PO: [PO_NUMBER]

If no valid 12-character customer PO is found, return:
Customer PO: NOT_FOUND

Example valid response:
Customer PO: PO2410000285
"""

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

    def _process_short_pdf_with_partnames(self, page_images: List[str], priority_partnames: List[str], client: anthropic.Anthropic) -> Dict:
        """Process short PDFs with all images in one request"""
        try:
            # Create content with all images
            content = [{
                "type": "text",
                "text": self._create_partnames_prompt(len(page_images), priority_partnames)
            }]
            
            # Add all page images
            for i, img_base64 in enumerate(page_images):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_base64
                    }
                })
            
            # Make API call
            response = client.messages.create(
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

    def _process_long_pdf_with_partnames(self, page_images: List[str], priority_partnames: List[str], client: anthropic.Anthropic) -> Dict:
        """Process long PDFs in batches of images"""
        try:
            batch_size = 4
            total_pages = len(page_images)
            all_items = []
            order_info = {}
            
            for batch_start in range(0, total_pages, batch_size):
                batch_end = min(batch_start + batch_size, total_pages)
                batch_images = page_images[batch_start:batch_end]
                
                print(f"Processing pages {batch_start + 1}-{batch_end} of {total_pages}")
                
                # Create content for this batch
                content = [{
                    "type": "text",
                    "text": self._create_batch_partnames_prompt(
                        batch_start + 1, 
                        batch_end, 
                        total_pages,
                        priority_partnames,
                        batch_start == 0
                    )
                }]
                
                # Add batch images
                for i, img_base64 in enumerate(batch_images):
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_base64
                        }
                    })
                
                # Process batch
                response = client.messages.create(
                    model=self.model,
                    max_tokens=8000,
                    messages=[{
                        "role": "user",
                        "content": content
                    }]
                )
                
                # Parse batch results
                batch_result = self._parse_json_response(response.content[0].text)
                
                # Extract order info from first batch
                if batch_start == 0 and "order_info" in batch_result:
                    order_info = batch_result["order_info"]
                
                # Collect items from this batch
                batch_items = batch_result.get("items", [])
                all_items.extend(batch_items)
                print(f"Extracted {len(batch_items)} items from batch")
            
            # Combine all results
            final_result = {
                "order_info": order_info,
                "items": all_items
            }
            
            print(f"Total items extracted: {len(all_items)}")
            return final_result
            
        except Exception as e:
            print(f"Error processing long PDF with partnames: {str(e)}")
            raise

    def _create_partnames_prompt(self, total_pages: int, priority_partnames: List[str]) -> str:
        """Create prompt for processing with specific partnames"""
        schema = self.config.get("json_schema", {})
        
        # Ensure shipping_cost is included in the order_info section of the schema
        if "order_info" in schema and "shipping_cost" not in schema["order_info"]:
            schema["order_info"]["shipping_cost"] = ""
        elif "order_info" not in schema:
            # If no order_info section exists, create one with all required fields
            schema["order_info"] = {
                "order_number": "",
                "order_date": "",
                "delivery_date": "",
                "customer_number": "",
                "customer_po": "",
                "delivery_address": "",
                "total_price": "",
                "shipping_cost": ""
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
- item_number: The item line number
- product_code: Must match one of the required PARTNAMES
- description: Product description
- quantity: Quantity with unit (e.g., "1 EA")
- unit_price: Unit price with currency
- extended_price: Extended price with currency
- discount: Discount amount (if any)
- item_total: Final item total with currency
- delivery_date: Delivery date in DD.MM.YYYY format

ORDER INFO FIELDS (extract from first pages):
- order_number: The order number
- order_date: Order date
- delivery_date: Expected delivery date
- customer_number: Customer number
- customer_po: Customer PO
- delivery_address: Full delivery address
- total_price: Grand total/order total amount with currency
- shipping_cost: SHIPPING COST EXTRACTION - FOLLOW THESE SPECIFIC RULES:
  * Shipping costs are typically found on the page BEFORE the last page (e.g., page 5 of 6 pages, or page 6 of 7 pages)
  * Look specifically for "Shipping & Handling" or "Expedited Handling" entries
  * Common format: "Shipping & Handling: USD 166,00" or "Expedited Handling: USD 250,00"
  * This section usually appears with other totals like:
    - Shipping & Handling: USD 166,00
    - Total Net: USD 28.353,28
    - VAT: USD 0,00
    - CIP Port of import Total: USD 28.353,28
  * IMPORTANT: Not every document has shipping costs - if none found, leave empty
  * Extract only the amount (e.g., "USD 166,00") from shipping entries

CRITICAL VALIDATION:
- Each product_code MUST be from the required PARTNAMES list
- Extract exactly {len(priority_partnames)} items (one for each PARTNAME)
- If a PARTNAME is not found in the document, include it with empty fields except product_code

Required JSON Schema:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON matching the schema.
"""

    def _create_batch_partnames_prompt(self, start_page: int, end_page: int, total_pages: int, 
                                     priority_partnames: List[str], include_order_info: bool) -> str:
        """Create prompt for batch processing with partnames"""
        schema = self.config.get("json_schema", {})
        
        # Ensure shipping_cost is included in the order_info section of the schema
        if "order_info" in schema and "shipping_cost" not in schema["order_info"]:
            schema["order_info"]["shipping_cost"] = ""
        elif "order_info" not in schema:
            # If no order_info section exists, create one with all required fields
            schema["order_info"] = {
                "order_number": "",
                "order_date": "",
                "delivery_date": "",
                "customer_number": "",
                "customer_po": "",
                "delivery_address": "",
                "total_price": "",
                "shipping_cost": ""
            }
        
        order_info_instruction = ""
        if include_order_info:
            order_info_instruction = """
FIRST BATCH - EXTRACT ORDER INFO:
- Order number, date, delivery date
- Customer number and customer PO
- Delivery address (look for ship-to address, billing address, or any street address)
- Total price/order total (look for grand total, order total, or final amount with currency)
- Shipping cost - SHIPPING COST EXTRACTION RULES:
  * Shipping costs are typically found on the page BEFORE the last page (e.g., page 5 of 6 pages, or page 6 of 7 pages)
  * Look specifically for "Shipping & Handling" or "Expedited Handling" entries
  * Common format: "Shipping & Handling: USD 166,00" or "Expedited Handling: USD 250,00"
  * This section usually appears with other summary totals like:
    - Shipping & Handling: USD 166,00
    - Total Net: USD 28.353,28
    - VAT: USD 0,00
    - CIP Port of import Total: USD 28.353,28
  * IMPORTANT: Not every document has shipping costs - if none found, leave empty
  * Extract only the amount (e.g., "USD 166,00") from shipping entries
  * Do NOT confuse with regular product items - this is a summary charge section
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

Required JSON Schema:
{json.dumps(schema, indent=2)}

Return ONLY valid JSON matching the schema.
Extract only items that match the required PARTNAMES.
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
            
            # Try to extract any JSON-like content
            try:
                import re
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    result = json.loads(json_str)
                    print("Successfully extracted JSON using regex")
                    return result
            except:
                pass
                
            # If all else fails, return a basic structure
            print("Returning fallback JSON structure")
            return {
                "order_info": {
                    "order_number": "",
                    "order_date": "",
                    "delivery_date": "",
                    "customer_number": "",
                    "customer_po": "",
                    "delivery_address": "",
                    "total_price": "",
                    "shipping_cost": ""
                },
                "items": []
            }