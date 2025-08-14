# Agilent Order Processing System

A comprehensive Python-based system for processing Agilent order confirmation PDFs using Claude AI for intelligent data extraction, validation against Priority ERP system, and automated order management.

## Overview

This system automates the processing of Agilent order confirmation PDFs by:
1. **PDF Processing**: Converting PDF pages to images for AI analysis
2. **Customer PO Extraction**: Extracting customer purchase orders from document headers
3. **Priority ERP Integration**: Validating orders against Priority system data
4. **AI-Powered Data Extraction**: Using Claude AI to extract detailed order information
5. **Comprehensive Validation**: Validating pricing, quantities, and shipping details
6. **Automated Updates**: Updating Priority with delivery dates and order status
7. **Data Storage**: Storing results in MongoDB for tracking and reporting

## 100% Automated Workflow

**The entire process is completely automatic with zero manual intervention required.**

### Workato Integration

The system seamlessly integrates with **Workato** to provide end-to-end automation:

1. **Email Monitoring**: Workato monitors incoming emails for Agilent order confirmations
2. **PDF Extraction**: Automatically extracts PDF attachments from new emails
3. **Processing Trigger**: Sends the PDF to the Agilent Order Processing System
4. **AI Processing**: System processes the PDF using Claude AI and validates against Priority ERP
5. **Response Handling**: Processing results are sent back to Workato
6. **User Notification**: Workato automatically sends a user-friendly email with all necessary details including:
   - Order validation status
   - Delivery date updates
   - Priority system updates
   - Any discrepancies or issues found
   - Complete processing summary

This integration ensures that from the moment an Agilent order confirmation email arrives, the entire processing, validation, and notification workflow happens automatically without any human intervention.

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Email Monitor │───▶│   Workato        │───▶│   PDF Upload    │
│   (Workato)     │    │   Automation     │    │   (Flask API)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                │                        ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   User Email    │◀───│   Response       │    │   Claude AI     │
│   Notification  │    │   Processing     │    │   Processing    │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                │                        ▼
                                │              ┌─────────────────┐
                                │              │   Priority ERP  │
                                │              │   Integration   │
                                │              └─────────────────┘
                                ▼                        │
                     ┌──────────────────┐               ▼
                     │   MongoDB        │    ┌─────────────────┐
                     │   Storage        │    │   Order Updates │
                     └──────────────────┘    │   & Status      │
                                             └─────────────────┘
```

## Core Components

### Python Files

- **`main.py`** - Main Flask application with REST API endpoints
- **`pdf_processor.py`** - PDF processing and Claude AI integration for data extraction
- **`priority_api.py`** - Priority ERP system integration and API communication
- **`response_handler.py`** - Response processing and MongoDB data storage
- **`order_validator.py`** - Order validation logic and business rules
- **`cache_manager.py`** - File cache management and cleanup utilities

### Database & Models

- **`models/agilent.schema.js`** - MongoDB schema definition for order data
- **`utils/db.js`** - Database connection utilities and configuration
- **`app.js`** - MongoDB operations and database examples

## Prerequisites

### Required Software
- **Python 3.8+**
- **Node.js 14+** (for MongoDB models)
- **MongoDB** (Atlas or local instance)
- **Priority ERP System** access with API credentials
- **Workato** account with automation capabilities

### Python Dependencies
```bash
pip install flask flask-restful anthropic pymongo PyMuPDF pillow requests
```

### Required Libraries
- `PyMuPDF (fitz)` - PDF processing and image conversion
- `anthropic` - Claude AI API integration
- `PIL (Pillow)` - Image processing and optimization
- `pymongo` - MongoDB database operations
- `flask` - Web framework for API endpoints

## Environment Configuration

Create environment variables or update your Flask configuration:

```python
# Anthropic Claude AI Configuration
ANTHROPIC_API_KEY_AGILENT = "your_claude_api_key_here"

# MongoDB Configuration
MONGODB_URI_AGILENT = "mongodb+srv://username:password@cluster.mongodb.net/"
MONGODB_DBNAME_AGILENT = "agilent_orders"
MONGODB_COLLECTION_AGILENT = "order_responses"

# Priority ERP Configuration
PRIORITY_URL = "https://your-priority-server.com"
PRIORITY_TOKEN = "your_priority_auth_token"

# API Authentication
AUTHORIZATION_USERNAME = "your_api_username"
AUTHORIZATION_PASSWORD = "your_api_password"

# Workato Integration
WORKATO_WEBHOOK_URL = "your_workato_webhook_endpoint"
WORKATO_API_TOKEN = "your_workato_api_token"

# PDF Processing Configuration
PDF_CONFIG_AGILENT = {
    "json_schema": {
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
    },
    "extraction_rules": {
        "customer_po": {
            "format": "PO + 10 digits",
            "length": 12,
            "example": "PO2410000285"
        }
    }
}
```

## Getting Started

### 1. Clone and Setup
```bash
git clone https://github.com/anafri96/agilent.git
cd agilent
pip install -r requirements.txt
```

### 2. Environment Setup
Create a `.env` file or set system environment variables:
```bash
export ANTHROPIC_API_KEY_AGILENT="your_claude_api_key"
export MONGODB_URI_AGILENT="your_mongodb_connection_string"
export PRIORITY_URL="your_priority_api_url"
export PRIORITY_TOKEN="your_priority_auth_token"
export WORKATO_WEBHOOK_URL="your_workato_webhook"
export WORKATO_API_TOKEN="your_workato_token"
```

### 3. Database Initialization
```bash
# Initialize MongoDB models (if using Node.js components)
npm install mongoose
node app.js
```

### 4. Workato Configuration
Set up Workato recipes to:
- Monitor email inbox for Agilent order confirmations
- Extract PDF attachments automatically
- Send PDFs to the processing system API
- Handle response data and send user notifications

### 5. Start the Application
```bash
# Run Flask application
python main.py

# Alternative: Using Flask CLI
python -m flask run --host=0.0.0.0 --port=5000
```

### 6. Test the API
```bash
# Test PDF processing endpoint
curl -X POST \
  http://localhost:5000/process \
  -H "Authorization: Basic <base64_encoded_credentials>" \
  -F "files=@sample_agilent_order.pdf"

# Clean cache endpoint
curl -X POST \
  http://localhost:5000/clean-cache \
  -H "Authorization: Basic <base64_encoded_credentials>"
```

## API Endpoints

### Process Order PDF
**POST** `/process`
- **Purpose**: Process Agilent order confirmation PDFs (typically called by Workato)
- **Input**: PDF file upload via multipart form-data
- **Output**: Comprehensive processing results with validation status
- **Authentication**: Basic Auth required

**Response Format:**
```json
{
  "success": true,
  "customer_po": "PO2410000285",
  "extraction_results": {
    "order_info": { ... },
    "items": [ ... ]
  },
  "validation_results": { ... },
  "priority_updates": { ... }
}
```

### Clean Cache
**POST** `/clean-cache`
- **Purpose**: Clean temporary files and application cache
- **Input**: Optional filename for targeted cleanup
- **Output**: Cache cleanup status

## Key Features

### PDF Processing (`pdf_processor.py`)
- **High-Resolution Conversion**: PDF pages converted to optimized PNG images (300 DPI)
- **Image Optimization**: Automatic resizing for Claude AI compatibility (max 2048px width)
- **Batch Processing**: Handles large PDFs through intelligent batching (4 pages per batch)
- **Memory Efficient**: Optimized image compression and memory management

### Customer PO Extraction
- **Format Validation**: Ensures exactly 12-character format (PO + 10 digits)
- **Pattern Recognition**: Intelligent extraction from document headers
- **Error Handling**: Comprehensive validation with fallback mechanisms

### Priority ERP Integration
- **Real-time Validation**: Live validation against Priority system data
- **PARTNAME Matching**: Exact matching of product codes with Priority database
- **Delivery Date Updates**: Automated delivery date calculations with business rules
- **Status Management**: Order status updates based on validation results

### Business Logic & Validation

#### Date Calculation Rules
1. **Special Address Rule**: 
   - If delivery address contains "12 Bet" AND "St."
   - Subtract 6 days from original delivery date
   - Use Thursday of that week as Priority date

2. **Default Rule**: 
   - Subtract 6 days from original delivery date
   - If result falls on Saturday, use Friday instead

#### Validation Process
1. **Extraction Validation**: Ensures all Priority PARTNAMEs are found in PDF
2. **Price Validation**: Compares total prices between AI extraction and Priority
3. **Item Validation**: Validates individual item prices and quantities
4. **Shipping Validation**: Compares shipping charges between systems

#### Status Updates
- **Supplier Approval**: All validations pass perfectly
- **Sent to Supplier**: Discrepancies found but processable

### Data Storage
- **MongoDB Integration**: Complete audit trail and result storage
- **Structured Schema**: Consistent data format across all operations
- **Query Optimization**: Indexed searches by customer PO for fast retrieval

## Automated Processing Workflow

```
1. Email Arrival → Workato monitors incoming Agilent emails
2. PDF Extraction → Workato automatically extracts PDF attachments
3. API Call → Workato sends PDF to processing system
4. Customer PO Extraction → Extract from first page of PDF
5. Priority Validation → Validate Customer PO exists in Priority ERP
6. Priority Query → Get required PARTNAMEs for targeted extraction
7. AI Processing → Claude AI extraction using specific PARTNAMEs
8. Data Validation → Comprehensive validation against Priority data
9. Business Rules → Apply delivery date calculation rules
10. Priority Updates → Update delivery dates and order status
11. Data Storage → Save complete results to MongoDB
12. Response to Workato → Return processing results
13. User Notification → Workato sends user-friendly email with details
```

## Error Handling & Monitoring

### Comprehensive Error Handling
- **PDF Format Validation**: Handles corrupted or invalid PDF files
- **Missing Data Detection**: Identifies missing customer PO numbers
- **API Connectivity**: Robust handling of Priority and Claude API issues
- **Validation Failures**: Detailed reporting of validation discrepancies
- **Workato Integration**: Error responses properly formatted for Workato handling

### Logging & Monitoring
- **Detailed Console Logging**: Step-by-step processing information
- **MongoDB Audit Trail**: Complete processing history storage
- **Error Reporting**: Full stack trace capture for debugging
- **Performance Metrics**: Processing time and success rate tracking
- **Workato Notifications**: Automated error notifications through Workato

## Security Features

- **Basic Authentication**: Secure API access with username/password
- **Environment Variables**: Sensitive data stored in environment configuration
- **Input Validation**: File upload validation and sanitization
- **Database Security**: Secure MongoDB connections with authentication
- **Workato Security**: Secure webhook endpoints and API token authentication

## Performance Optimization

- **Image Compression**: Optimized image sizes for faster Claude AI processing
- **Batch Processing**: Efficient handling of large multi-page documents
- **Connection Pooling**: Optimized database and API connections
- **Cache Management**: Intelligent file caching with automatic cleanup
- **Workato Efficiency**: Optimized response formatting for Workato processing

## Development & Customization

### Adding New Validation Rules
Extend the validation logic in `order_validator.py`:
```python
def custom_validation_rule(self, order_data, priority_data):
    # Your custom validation logic here
    pass
```

### Modifying PDF Processing
Update prompts in `pdf_processor.py`:
```python
def _create_custom_prompt(self, context):
    # Your custom prompt engineering here
    pass
```

### Database Schema Changes
Update schema in `models/agilent.schema.js` and corresponding validation logic.

### Workato Recipe Modifications
Update Workato recipes to handle new data formats or add additional processing steps.

## Troubleshooting

### Common Issues

1. **"Customer PO not found"**
   - Ensure PO format is exactly 12 characters (PO + 10 digits)
   - Check PDF quality and text readability
   - Verify Claude AI API connectivity

2. **"Priority connection failed"**
   - Check Priority URL and authentication token
   - Verify network connectivity to Priority server
   - Confirm API permissions

3. **"Claude API error"**
   - Verify Anthropic API key validity
   - Check API rate limits and quotas
   - Ensure image size is within limits (2048px max width)

4. **"MongoDB connection failed"**
   - Check MongoDB URI and credentials
   - Verify network connectivity
   - Confirm database permissions

5. **"Workato integration issues"**
   - Verify webhook URLs and API tokens
   - Check Workato recipe status and logs
   - Confirm response format compatibility

### Debug Mode
Enable detailed logging by setting Flask to DEBUG mode:
```python
app.debug = True
```

### Performance Issues
- Monitor image sizes and PDF complexity
- Check Claude AI response times
- Verify MongoDB query performance
- Review Priority API response times
- Check Workato recipe execution times

## Contributing

1. **Code Standards**: Follow existing code structure and naming conventions
2. **Error Handling**: Add comprehensive error handling for new features
3. **Documentation**: Update README and code comments for any changes
4. **Testing**: Test with various PDF formats and edge cases
5. **Security**: Ensure secure handling of sensitive data
6. **Workato Integration**: Test automation workflow end-to-end


---

**Last Updated**: August 2025  
**Version**: 1.0.0  
**Author**: anafri96
