# 🧪 TSG — Test Suite Generator Dashboard V2.0

Generates production-ready test suite Excel files from Chalk + Jira + Attachments.
Works for ANY Jira Feature ID. SQLite cache for instant repeat loads.

## Quick Start (Windows)

```
1. Double-click: setup.bat        (one-time: installs dependencies)
2. Double-click: run_dashboard.bat (starts the dashboard)
3. Browser opens at http://localhost:8501
```

## Manual Setup

```bash
cd TestSuiteGenerator
pip install -r requirements.txt
playwright install chromium
streamlit run TSG_Dashboard_V2.0.py
```

## For Team Distribution

### Zero-Install Portable Build (Recommended)

Build once on your machine, share with anyone. No Python, no pip, no installs needed on tester machines.

**On your machine (one-time build):**
```
1. Double-click: build_portable.bat
2. Wait ~5 minutes (downloads embedded Python + dependencies)
3. Output: dist\TSG_Portable\ folder (~500MB)
4. Optional: Run dashboard first, click "Refresh from Chalk" to pre-populate DB
5. Zip the dist\TSG_Portable\ folder
```

**For testers:**
```
1. Extract the zip to any folder
2. Double-click: START_TSG.bat
3. Browser opens at http://localhost:8501
4. That's it. No installs.
```

**What's inside the portable folder:**
```
TSG_Portable/
├── START_TSG.bat           ← Double-click to run
├── TSG_Dashboard_V2.0.py   ← The app
├── modules/                 ← All engine modules
├── python/                  ← Embedded Python + all packages
├── tsg_cache.db            ← Pre-built feature cache (if included)
├── outputs/                 ← Generated files go here
└── templates/               ← Reference files
```

### Alternative: Manual Setup (developers)
```bash
pip install -r requirements.txt
playwright install chromium
streamlit run TSG_Dashboard_V2.0.py
```

### Alternative: Docker
```bash
docker build -t tsg-dashboard .
docker run -p 8501:8501 tsg-dashboard
```
Note: Docker won't have access to your Windows SSO for Jira/Chalk.
Use Docker only if you have API token auth configured.

## How the DB Cache Works

- `tsg_cache.db` — SQLite file, auto-created on first run
- Stores: PI pages, features, Jira metadata, Chalk data, generation history
- First run: scrapes Chalk (~60s) → saves to DB
- Every subsequent run: loads from DB (<100ms)
- "Refresh from Chalk" button: re-scrapes and updates DB
- "Reload" button: clears session memory, DB persists
- Delete the .db file to force a fresh scrape

## Architecture

```
TestSuiteGenerator/
├── TSG_Dashboard_V2.0.py       # Main Streamlit app
├── TSG_Dashboard_V2.1.py       # V2.1 with QMetry/Diff/Linked Issues
├── setup.bat                   # One-click setup for Windows
├── run_dashboard.bat            # One-click run
├── Dockerfile                  # Docker deployment
├── requirements.txt            # Python dependencies
├── tsg_cache.db                # SQLite cache (auto-created)
├── modules/
│   ├── config.py               # Central config, paths, constants
│   ├── database.py             # SQLite persistence layer
│   ├── jira_fetcher.py         # Jira REST API via Playwright
│   ├── chalk_parser.py         # Chalk page extraction
│   ├── doc_parser.py           # docx/xlsx/pdf/html parser
│   ├── test_engine.py          # Core TC builder + matrix + enricher
│   ├── excel_generator.py      # Excel output with merges/styling
│   ├── scenario_enricher.py    # 9-layer gap filler
│   ├── step_templates.py       # Domain-specific step chains
│   ├── instruction_parser.py   # Custom instruction parser
│   ├── qmetry_exporter.py      # QMetry-compatible export (V2.1)
│   ├── diff_engine.py          # Suite comparison (V2.1)
│   ├── linked_fetcher.py       # Linked issue deep-fetch (V2.1)
│   ├── transaction_log.py      # JSON-based history (legacy)
│   └── theme_v2.py             # UI theme/CSS
├── outputs/                    # Generated test suites
├── checkpoints/                # Auto-saved checkpoints
├── attachments/                # Downloaded Jira attachments
├── inputs/                     # User uploads
└── templates/                  # Reference/sample files
```

## TC Generation Priority

1. **Chalk** — Primary source (scenarios from PI page)
2. **Jira** — Fallback when Chalk is empty (parses description/AC)
3. **Attachments** — Gap-fill from Jira attachments + user uploads
4. **Auto-negative** — Invalid input, auth, upstream failure
5. **Jira comments/subtasks** — Edge cases from discussions
6. **Enricher** — 9-layer universal gap filler
7. **Custom instructions** — User-specified scenarios
8. **Device matrix** — Expands Happy Path TCs by device combos

## Test Matrix

| Dimension | Options |
|-----------|---------|
| Channel   | ITMBO, NBOP |
| Device    | Mobile, Tablet, Smartwatch |
| Network   | 4G, 5G |
| SIM       | eSIM, pSIM |
| OS        | iOS, Android |

Smart Suite: 4 representative combos. Full Matrix: all combinations.
