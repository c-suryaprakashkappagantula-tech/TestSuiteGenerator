# 🧪 Test Suite Generator Dashboard V1.0

Streamlit-based dashboard that generates production-ready test suite Excel files from Jira + Chalk + Attachments.
Works for **ANY** Jira Feature ID. Fully self-contained.

## Run
```bash
cd TestSuiteGenerator
streamlit run TSG_Dashboard_V1.0.py
```

## Features
- **Jira Fetcher** — Playwright + REST API (uses browser SSO, no credentials needed)
- **Chalk Parser** — Extracts feature-specific test scenarios from Confluence pages
- **Attachment Handler** — Downloads & parses Jira attachments (docx/xlsx/pdf)
- **Upload Support** — Upload additional HLD/LLD/Solution docs
- **Test Engine** — Builds positive, negative, E2E, edge case scenarios
- **AC Traceability** — Maps acceptance criteria to covering test cases
- **Self-Healing** — Cross-checks attachments vs Chalk, auto-fixes gaps
- **Excel Output** — 3 sheets: Summary, Test Cases (merged/styled), Traceability
- **Checkpoints** — Auto-saves after every generation

## Folder Structure
```
TestSuiteGenerator/
├── TSG_Dashboard_V1.0.py      # Main Streamlit app
├── requirements.txt            # Python dependencies
├── README.md                   # This file
├── modules/
│   ├── __init__.py
│   ├── config.py               # Central config, paths, constants
│   ├── jira_fetcher.py         # Jira REST API via Playwright
│   ├── chalk_parser.py         # Chalk/Confluence page extraction
│   ├── doc_parser.py           # docx/xlsx/pdf parser
│   ├── test_engine.py          # Core TC builder + matrix + AC matching
│   └── excel_generator.py      # Excel output with merges/styling
├── inputs/                     # User-uploaded files
├── outputs/                    # Generated test suites
├── checkpoints/                # Auto-saved checkpoints
├── attachments/                # Downloaded Jira attachments
├── templates/                  # Reference/sample files
└── logs/                       # Execution logs
```

## Test Matrix Options
- **Channel**: TMO / NBOP
- **Device**: Mobile / Tablet / Smartwatch
- **Network**: 4G / 5G
- **SIM**: eSIM / pSIM
