# Automation Dashboards — Design & Implementation Guide

**TSG | TSE | MDA**
**Date:** April 2026 | **Version:** 1.0

---

## 1. Executive Summary

Our QA team spends a significant portion of each sprint on repetitive tasks: writing test cases from Jira stories, manually running API tests, capturing screenshots for evidence, and pulling weekly status reports from QMetry and Jira. These three automation dashboards eliminate that manual work.

| Dashboard | What It Does | Time Saved |
|-----------|-------------|------------|
| **TSG** (Test Suite Generator) | Reads Jira features and writes test cases automatically | Hours per feature → minutes |
| **TSE** (Test Suite Executor) | Runs those test cases against live APIs and captures proof | Full day of manual testing → automated |
| **MDA** (Jira Dashboard) | Pulls QMetry/Jira data and builds the weekly status deck | 2-3 hours of copy-paste → one click |

Together, they form an end-to-end pipeline: **Generate → Execute → Report**.

---

## 2. Why This Matters

**Before automation:**
- Test engineers manually read Jira stories, Chalk docs, and attachments to write test cases in Excel — often missing edge cases or duplicating effort across team members.
- Executing tests meant stepping through each API call by hand, taking screenshots, and assembling evidence documents.
- Weekly reporting required logging into QMetry, exporting data, filtering in Excel, building pivot tables, and pasting into PowerPoint.

**After automation:**
- TSG generates 100+ test cases per feature in minutes, covering happy paths, error scenarios, and integration checks.
- TSE executes every test case automatically with full evidence capture — screenshots, API responses, and validation results packaged into a Word document.
- MDA produces the weekly status deck in one click — QMetry exports, Jira defect summaries, and execution reports all flow into a ready-to-present PowerPoint.

**Key benefits:**
- Consistent test coverage across all features (no missed scenarios)
- Complete audit trail for every test execution
- Faster sprint cycles — less time on paperwork, more time on actual testing
- Vendor-aware reporting for multi-team coordination

---

## 3. How Each Dashboard Works

### 3.1 TSG — Test Suite Generator

**Purpose:** Automatically create comprehensive test suites from Jira feature specifications.

**How it works:**
1. You enter a Jira feature ID (e.g., MWTGPROV-3949)
2. TSG pulls the feature details from Jira — acceptance criteria, description, linked issues
3. It scrapes the Chalk documentation page for business scenarios and rules
4. It checks any attached test data files for additional coverage
5. The test engine reasons through the feature like a senior QA engineer — identifying what needs positive testing, negative testing, boundary checks, and integration validation
6. It outputs a QMetry-compatible Excel file with structured test cases and steps, plus a Feature Summary document

**Key capabilities:**
- Pulls data from multiple sources (Jira, Chalk, attachments, linked issues)
- Classifies feature type (API, UI portal, notification, batch process) and adjusts test strategy accordingly
- Removes duplicate scenarios and filters out irrelevant data
- Tracks every generation with version history for audit
- Caches all data locally so the dashboard loads instantly after first run

**Architecture overview:**

```
Jira Feature → [Jira API + Chalk Scraper + Attachment Parser]
                          ↓
              Test Engine (reasoning + classification)
                          ↓
              Dedup & Quality Gates
                          ↓
              QMetry Excel + Feature Summary + SQLite Cache
```

**Core modules:**
- **Test Engine** — Builds the test suite structure, applies generation strategies
- **Test Analyst** — Reasons about feature type, generates lifecycle-specific scenarios
- **Database** — SQLite cache for features, Jira data, Chalk content, generation history
- **Diff Engine** — Compares test suite versions to show what changed
- **QMetry Exporter** — Produces formatted Excel files ready for QMetry import
- **Linked Fetcher** — Traverses Jira issue links to pull related acceptance criteria

---

### 3.2 TSE — Test Suite Executor

**Purpose:** Automatically run generated test suites against live APIs and capture evidence.

**How it works:**
1. Load a test suite from the TSG database or an Excel file
2. TSE classifies each test case by type — is it an activation flow? A device change? A UI check?
3. For each test step, TSE maps it to the right action: call an API, check a portal screen, validate a report
4. It executes a pipeline for each test case:
   - Get authentication token
   - Validate the test device
   - Call the API
   - Pull the Century Report
   - Validate service grouping transactions
   - Check the NBOP portal
   - Capture evidence (screenshots + API responses)
5. Results are packaged into a Word document with inline screenshots and pass/fail status

**Key capabilities:**
- Loads test suites from database or Excel (flexible input)
- Maps test step descriptions to executable API actions automatically
- Falls back to raw curl file execution for APIs without dedicated modules (100% coverage)
- Auto-allocates test devices from the SharePoint device pool
- Self-healing: refreshes expired tokens, retries failed steps, falls back to alternative execution paths
- Generates complete evidence documents for compliance and audit

**Architecture overview:**

```
Test Suite (DB/Excel) → Suite Loader → Step Mapper
                                          ↓
                              Intent Classifier (V2)
                                          ↓
                              Test Data Collector
                                          ↓
              Step Handler Registry → [API Calls | Portal Checks | Report Validation]
                                          ↓
                              Evidence Capture → Word Document
```

**Execution pipeline (7 steps):**
1. **OAuth** — Obtain authentication token
2. **Device Validation** — Verify test device is certified
3. **API Call** — Execute the actual operation (activate, change, deactivate, etc.)
4. **Century Report** — Pull transaction report for verification
5. **Service Grouping** — Validate expected transactions against templates
6. **NBOP Check** — Verify portal reflects the changes
7. **Evidence** — Package screenshots and responses into the final document

---

### 3.3 MDA — Jira Dashboard

**Purpose:** Automate weekly QMetry/Jira data extraction and status report generation.

**How it works:**
1. **QMetry Export** — Navigates to QMetry in Jira, selects the test folder, exports test cases to Excel, and creates a pivot table grouped by labels and assignee
2. **Jira Defect Extract** — Runs a JQL search for defects, filters by reporter, and creates a status/priority pivot
3. **Test Execution Report** — Fetches QMetry execution summary data and generates a styled Excel report
4. **PowerPoint Generation** — When all three modules complete, MDA auto-builds a weekly status deck with:
   - Title slide
   - Delivery updates (from template)
   - QMetry test case summary
   - Jira defect summary
   - Test execution overview

**Key capabilities:**
- Vendor-aware filtering (All Vendors, Infy team, Other Vendors) for multi-team reporting
- Handles QMetry's complex UI quirks — lazy-loaded tree nodes, cold-session startup, virtual scrolling
- Produces presentation-ready PowerPoint with styled tables and charts
- Single browser session for performance (no repeated logins)

**Architecture overview:**

```
QMetry → Excel → Pivot Table ─┐
Jira JQL → REST API → Pivot ──┼──→ PowerPoint Deck
Execution Data → Excel ────────┘
```

---

## 4. How They Connect

```
┌─────────┐        ┌─────────┐        ┌─────────┐
│   TSG   │──────→ │   TSE   │──────→ │   MDA   │
│ Generate │  DB/  │ Execute │ Results│ Report  │
│ Tests   │ Excel │ Tests   │        │ Status  │
└─────────┘        └─────────┘        └─────────┘
```

- **TSG → TSE:** TSE reads test suites directly from the TSG database or exported Excel files
- **TSE → MDA:** TSE execution results feed into the weekly reporting cycle
- **Shared foundation:** All three dashboards use the same technology stack and design patterns

---

## 5. Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Language | Python 3.9+ | Standard, well-supported, rich library ecosystem |
| Dashboard UI | Streamlit | Fast to build, interactive, no frontend code needed |
| Browser Automation | Playwright | Reliable, cross-browser, handles modern web apps |
| Data Storage | SQLite (WAL mode) | Zero setup, ships with Python, handles concurrent reads |
| Excel Processing | openpyxl | Read/write Excel files with formatting |
| PowerPoint | python-pptx | Programmatic slide generation |
| Data Analysis | pandas | Pivot tables, filtering, data transformation |
| HTTP Client | requests | API calls to Jira, QMetry, TMO APIs |
| AI Analysis (optional) | OpenAI / Azure / Bedrock | Enhanced test case reasoning in TSG |

---

## 6. External Systems

| System | Used By | Purpose |
|--------|---------|---------|
| Jira | TSG, MDA | Feature specs, acceptance criteria, defect tracking |
| Chalk | TSG | Business scenario documentation |
| QMetry | TSG, MDA | Test case management, execution tracking |
| TMO APIs | TSE | API execution (activate, change, deactivate, etc.) |
| NBOP Portal | TSE | UI validation and field verification |
| Century Report | TSE | Transaction verification |
| SharePoint | TSG, TSE | Test device and SIM data |
| ALM Octane | OTM (optional) | Test management for Octane-based projects |

---

## 7. Setup & Installation

### Prerequisites
- Python 3.9 or higher
- Jira account with API access
- Network access to Chalk, QMetry, and TMO environments
- Chrome, Edge, or Chromium browser installed

### Step 1: Install Dependencies

```bash
pip install streamlit playwright pandas openpyxl python-pptx requests lxml
playwright install chromium
```

### Step 2: Configure Credentials

Copy `.env.example` to `.env` and fill in:

```
JIRA_USER=your_jira_username
JIRA_PASS=your_jira_password
HEADLESS=false
```

For TSG AI-powered analysis (optional):
```
OPENAI_API_KEY=your_key
```

### Step 3: Pre-load Data (TSG only, one-time)

```bash
python TestSuiteGenerator/preload_db.py
```

This fetches all PI features and Chalk data into the local database. Takes a few minutes on first run, then the dashboard loads instantly.

### Step 4: Launch a Dashboard

```bash
# Test Suite Generator
streamlit run TSG_Dashboard_V4.1.py

# Test Suite Executor
streamlit run TestSuiteExecutor/TSE_Dashboard_V1.0.py

# MDA Jira Dashboard
streamlit run MDA_Jira_Dashboard_V5.1.py
```

---

## 8. Folder Structure

```
project/
├── TestSuiteGenerator/
│   ├── modules/
│   │   ├── test_engine.py        # Suite builder
│   │   ├── test_analyst.py       # QA reasoning engine
│   │   ├── database.py           # SQLite cache
│   │   ├── diff_engine.py        # Version comparison
│   │   ├── qmetry_exporter.py    # Excel output
│   │   └── linked_fetcher.py     # Jira link traversal
│   ├── outputs/                  # Generated test suites
│   └── preload_db.py             # One-time data loader
│
├── TestSuiteExecutor/
│   ├── modules/
│   │   ├── suite_loader.py       # Load test suites
│   │   ├── step_mapper.py        # Map steps to actions
│   │   ├── orchestrator.py       # Pipeline execution
│   │   ├── curl_parser.py        # Fallback API execution
│   │   ├── device_pool.py        # Test device management
│   │   ├── evidence_doc.py       # Word document generation
│   │   ├── intent_classifier.py  # TC type classification (V2)
│   │   ├── step_engine.py        # Step dispatch engine (V2)
│   │   └── step_handler_registry.py  # Handler lookup (V2)
│   └── TSE_Dashboard_V1.0.py     # Dashboard entry point
│
├── MDA_Jira_Dashboard_V5.1.py    # MDA entry point
├── MDA-Jira-Dashboard/
│   └── templates/                # PowerPoint templates
│
├── .env                          # Credentials (not committed)
└── .env.example                  # Template for credentials
```

---

## 9. Design Decisions

| Decision | Rationale |
|----------|-----------|
| **SQLite over a database server** | Zero setup, single file, ships with Python. WAL mode handles concurrent reads from the UI while background writes happen. No DBA needed. |
| **Streamlit for the UI** | Rapid development, interactive widgets, no HTML/CSS/JS required. The team can focus on automation logic, not frontend code. |
| **Playwright over Selenium** | More reliable, better handling of modern SPAs, built-in wait strategies, cross-browser support with a single API. |
| **Local caching everywhere** | Jira and Chalk are slow to query. Caching locally means the dashboards load in seconds after the first data fetch. Stale data warnings appear after 24 hours. |
| **Curl fallback in TSE** | Not every API has a dedicated Dashboard module. Parsing raw curl files ensures 100% test coverage regardless of module availability. |
| **Modular architecture** | Each dashboard is split into focused modules (one per concern). Easy to maintain, test, and extend independently. |
| **Intent-based execution (TSE V2)** | Different test types need different execution strategies. Classifying test cases by intent (activation, change, UI check, validation) lets the engine pick the right handler automatically. |

---

## 10. Security Considerations

- Credentials are stored in `.env` files, never committed to source control
- Jira authentication uses existing corporate credentials
- OAuth tokens are refreshed automatically and never persisted to disk
- SQLite databases are local-only — no network exposure
- Browser automation runs with `CREATE_NO_WINDOW` on Windows to suppress popup windows

---

## 11. Summary

These three dashboards transform the QA workflow from manual, error-prone processes into an automated pipeline. TSG eliminates test case writing. TSE eliminates manual test execution. MDA eliminates report assembly. Together, they free the team to focus on what matters — finding real issues and improving quality.

| Metric | Before | After |
|--------|--------|-------|
| Test case creation | 2-4 hours/feature | 5 minutes |
| Test execution + evidence | Full day | Automated |
| Weekly status report | 2-3 hours | One click |
| Test coverage consistency | Varies by person | Standardized |
| Evidence documentation | Manual screenshots | Auto-generated Word doc |
