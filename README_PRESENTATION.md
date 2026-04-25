# 2-Minute Presentation — Buena Context Engine

This file is a short presentation guide for explaining the project in about 2 minutes.

## One-Line Pitch

Buena Context Engine turns messy property-management data like emails, letters, invoices, bank statements, and master data into a fast, queryable, and traceable living context store.

## 2-Minute Script

### 1. Problem — 20 seconds

- Property management data is scattered across PDFs, emails, CSVs, invoices, and bank exports.
- A normal AI assistant can read these files, but it struggles to keep a reliable, always-updated memory of the property.
- The challenge is not just answering one question, but maintaining a living context that updates quickly and stays linked to the original source.

### 2. Solution — 30 seconds

- Our solution is the **Buena Context Engine**, built as a FastAPI application with SQL as the context layer.
- Instead of storing one large summary, we break information into structured tables for entities like units, owners, tenants, providers, invoices, and bank transactions.
- For flexible information that does not fit fixed columns, we use a `facts` table.
- For traceability, every extracted fact is linked back to a `source_events` record, so we always know where the information came from.

### 3. How It Works — 35 seconds

- Static CSV master data is loaded deterministically into the database without using the LLM.
- Unstructured files like emails, letters, and invoice PDFs go through the LLM extraction path.
- The model returns structured JSON, and the backend writes that into SQL tables.
- This gives us two advantages:
  - fast ingestion and updates
  - fast querying later using normal SQL or API endpoints

### 4. Why This Is Strong — 20 seconds

- It is **surgical**: we update only the rows that changed.
- It is **traceable**: every answer can be tied back to the original document.
- It is **scalable**: SQL queries are much faster and more reliable than re-reading every file every time.
- It is **agent-ready**: an AI agent can query the context store in milliseconds instead of rebuilding context from scratch.

### 5. Example Use Cases — 10 seconds

- Who owns a specific unit?
- Which tenant reported a maintenance issue?
- Which invoice belongs to which provider?
- What new facts arrived in recent emails?

### 6. Closing — 5 seconds

- In short, we turned raw property documents into a living database that is fast to update, easy to query, and grounded in source evidence.

## Suggested Slide Flow

### Slide 1 — Title

- Buena Context Engine
- Living context store for property management

### Slide 2 — The Problem

- Data is fragmented across many file types
- Hard to maintain reliable AI memory
- Hard to trace answers back to evidence

### Slide 3 — The Architecture

- FastAPI backend
- SQL database as context engine
- Structured tables + `facts` + `source_events`

### Slide 4 — Ingestion Strategy

- CSV master data loaded locally
- Emails, letters, and PDFs extracted with LLM
- All writes stored in normalized SQL tables

### Slide 5 — Why It Wins

- Fast updates
- Fast queries
- Provenance for every fact
- Better than one giant summary document

## Optional Demo Talk Track

- Show one source file such as an email or invoice.
- Show the API route that extracts or loads the file.
- Show the resulting rows in the database.
- End by asking a query like:
  - "What do we know about unit WE 45?"
  - "Which provider sent this invoice?"

## Short Closing Version

If you only have 30 seconds:

- We built a context engine for property management data.
- CSV data is loaded directly, while emails, letters, and invoices are extracted into structured records.
- Everything lands in SQL tables with full provenance.
- That makes the system fast to update, fast to query, and reliable for AI agents.
