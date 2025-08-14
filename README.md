# 🚀 AI-Powered Order Processing System

**Zero-touch automation that transforms PDF orders into enterprise system updates using Claude AI.**

## The Problem
Manual order processing from PDFs takes hours, creates errors, and doesn't scale.

## The Solution
A fully automated AI pipeline that reads, understands, validates, and processes orders in minutes—no human intervention required.

```
📧 Email → 🤖 AI Processing → ✅ System Updates → 📬 Notifications
```

## What It Does

🧠 **Smart PDF Analysis** - Claude AI extracts complex order data from any document format  
⚡ **Real-time Validation** - Cross-references with ERP systems and applies business logic  
🔄 **Complete Automation** - Workato orchestrates the entire workflow from email to completion  
💾 **Intelligent Storage** - MongoDB tracks everything with full audit trails  

## Technical Highlights

- **Claude AI Integration** - Advanced document understanding and data extraction
- **Enterprise ERP Connection** - Real-time Priority system validation and updates  
- **Workato Automation** - End-to-end workflow orchestration
- **Flask API Architecture** - Scalable, secure processing engine
- **MongoDB Analytics** - Complete data intelligence and reporting

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

## Key Results

⚡ **5 minutes** vs hours of manual work  
🎯 **100% automated** - zero human intervention  
📈 **Enterprise scale** - handles unlimited volume  
✅ **AI-validated** - eliminates processing errors  

## Quick Start

```bash
git clone https://github.com/anafri96/agilent.git
pip install -r requirements.txt
python main.py
```

**API Usage:**
```bash
curl -X POST /process -F "files=@order.pdf"
```

---

**Why This Matters:** Demonstrates advanced AI integration, enterprise system architecture, and complete process automation—the future of business operations.

**Built with:** Python • Claude AI • Workato • MongoDB • Enterprise APIs
