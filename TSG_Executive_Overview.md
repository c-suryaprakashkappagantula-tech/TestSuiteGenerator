# Test Suite Generator (TSG) — Executive Overview

## What It Does

TSG is an AI-powered test suite generation platform that automatically creates production-ready test cases for TMO/Spectrum Mobile provisioning features. It replaces weeks of manual test case writing with automated, intelligent generation in minutes.

## How It Works

```
Chalk (Design Specs) + Jira (Requirements, Subtasks, Comments) + NBOP UI Crawler
                              ↓
                    TSG Engine (AI-Powered)
                              ↓
              Production-Ready Test Suites (Excel)
                              ↓
                    TSE (Test Suite Executor)
                              ↓
              Automated Test Execution + Evidence
```

### Data Sources (Automatic)
- **Chalk** — Feature specifications, test scenarios, validation rules, transaction flows
- **Jira** — Acceptance criteria, subtask tables (e.g., YL state change matrix), comments, attachments
- **NBOP UI Crawler** — Real portal menu paths, field names, button labels for UI test steps
- **Genesis Portal Crawler** — TMO carrier portal structure

### Intelligence Layers
- **Integration Contract** — Knows which external systems (Syniverse, ITMBO, EMM, APOLLO_NE) each operation calls and which it must NOT call
- **Feature Classification** — Automatically detects API, UI, Hybrid, CDR/Mediation, Notification, Inquiry, Sync, and Batch features
- **Step Templates** — 15+ domain-specific step chains (Swap MDN, Activation, Sync Subscriber, Order Inquiry, CDR Processing, etc.)
- **Quality Gate** — Validates every TC for correctness, cross-contamination, and completeness

## Key Numbers (PI-52 & PI-53)

| Metric | Value |
|--------|-------|
| Features covered | **54** |
| Test cases generated | **1,789** |
| Test steps generated | **7,233** |
| Quality audit pass rate | **100%** |
| Chalk scenario alignment | **97.5%** |
| Avg generation time per feature | **~30 seconds** |

## What Makes It Different

### 1. Context-Aware Steps
Not generic "verify the result" — each step is specific to the operation:
- **Swap MDN**: 19-step pipeline with Syniverse Change IMSI, PSIM vs ESIM differentiation, company ID verification
- **Sync Subscriber**: Dynamic steps from Jira subtask tables — TMO Status × NSL Status × Syniverse Action matrix
- **CDR/ILD**: Mediation pipeline steps with PRR file verification, no API contamination

### 2. Dual Assertion Pattern
For every Syniverse-integrated operation, the suite validates both:
- What **MUST** happen (e.g., CreateSubscriber for activation)
- What **MUST NOT** happen (e.g., NO Syniverse call for Hotline)

### 3. UI Verification Mirror
For every API operation that has an NBOP counterpart, an additional UI verification TC is auto-generated with real navigation paths from the NBOP crawler.

### 4. Jira Subtask Table Intelligence
When Jira subtasks contain structured tables (e.g., YL state change matrix), the engine parses them and generates TCs with steps derived directly from the table data — including the exact Syniverse action per row.

## Test Suite Executor (TSE)

The generated test suites feed directly into TSE for automated execution:
- **API steps** → OAuth + cURL execution with response validation
- **UI steps** → Playwright-driven NBOP portal automation with screenshots
- **Century Report steps** → Automated report download and validation
- **Evidence capture** → Screenshots, API responses, and DOCX evidence documents per TC

## Dashboards

| Dashboard | Purpose | Tech |
|-----------|---------|------|
| **TSG Dashboard** | Generate test suites | Streamlit + Python |
| **TSE Dashboard** | Execute test suites | Streamlit + Playwright |
| **MDA Jira Dashboard** | Project tracking | Streamlit + Jira API |

## Architecture

- **Language**: Python 3.13
- **UI**: Streamlit
- **Browser Automation**: Playwright (Edge/Chrome)
- **Database**: SQLite (local cache for Chalk, Jira, test suites)
- **Source Control**: GitHub
- **No external AI/LLM dependency** — all intelligence is rule-based and deterministic
