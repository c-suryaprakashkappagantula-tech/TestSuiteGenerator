"""
Full pipeline dry run for MWTGPROV-4230.
Simulates EXACTLY what the dashboard does:
  1. Fetch Jira via browser (with subtask deep-fetch)
  2. Download subtask attachments
  3. Parse documents
  4. Run V8 engine with parsed_docs

This proves the full flow works end-to-end.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from playwright.sync_api import sync_playwright
from modules.config import get_browser_channel, BROWSER_HEADLESS, ATTACHMENTS
from modules.jira_fetcher import fetch_jira_issue, download_attachments
from modules.database import init_db, save_jira
from modules.deep_miner import DeepMineResult, _mine_subtask
from modules.doc_parser import parse_file
from modules.data_first_engine import build_test_suite_v8

init_db()
ATTACHMENTS.mkdir(parents=True, exist_ok=True)

feature_id = 'MWTGPROV-4230'

print('=' * 70)
print('FULL PIPELINE DRY RUN: %s' % feature_id)
print('  Simulates dashboard: Jira fetch → subtask attachments → parse → engine')
print('=' * 70)

# ================================================================
# STEP 1: Fetch Jira via browser (like dashboard does)
# ================================================================
print('\n--- Step 1: Fetch Jira via browser ---')
t0 = time.time()

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        channel=get_browser_channel(),
        headless=BROWSER_HEADLESS,
    )
    context = browser.new_context()
    page = context.new_page()

    jira = fetch_jira_issue(page, feature_id, log=print)
    save_jira(jira)

    print('\n  Jira: %s' % jira.summary[:70])
    print('  Subtasks: %d' % len(jira.subtasks))
    print('  Parent attachments: %d' % len(jira.attachments))

    # ================================================================
    # STEP 2: Download parent + subtask attachments
    # ================================================================
    print('\n--- Step 2: Download attachments ---')
    att_paths = []

    # Parent attachments
    if jira.attachments:
        att_paths = download_attachments(page, jira, log=print)
        print('  Parent attachments downloaded: %d' % len(att_paths))

    # Subtask attachments
    subtask_att_count = 0
    for st in jira.subtasks:
        st_attachments = st.get('attachments', [])
        st_key = st.get('key', 'unknown')
        print('  Subtask %s: %d attachments' % (st_key, len(st_attachments)))
        for att in st_attachments:
            fname = att.get('filename', '')
            url = att.get('url', '')
            if not fname or not url:
                continue
            size = att.get('size', 0)
            if size > 10 * 1024 * 1024:
                print('    SKIP (too large): %s (%d MB)' % (fname, size // (1024*1024)))
                continue
            ext = Path(fname).suffix.lower()
            if ext not in ('.docx', '.xlsx', '.pdf', '.txt', '.html', '.zip', '.csv'):
                print('    SKIP (unsupported type): %s' % fname)
                continue
            save_path = ATTACHMENTS / ('%s_%s' % (st_key, fname))
            if save_path.exists():
                att_paths.append(save_path)
                subtask_att_count += 1
                print('    CACHED: %s' % fname)
                continue
            try:
                response = page.request.get(url)
                if response.ok:
                    save_path.write_bytes(response.body())
                    att_paths.append(save_path)
                    subtask_att_count += 1
                    print('    DOWNLOADED: %s (%d KB)' % (fname, len(response.body()) // 1024))
                else:
                    print('    FAILED (HTTP %d): %s' % (response.status, fname))
            except Exception as e:
                print('    ERROR: %s — %s' % (fname, str(e)[:60]))

    print('\n  Total attachment paths: %d (parent: %d, subtask: %d)' % (
        len(att_paths), len(att_paths) - subtask_att_count, subtask_att_count))

    context.close()
    browser.close()

elapsed_fetch = time.time() - t0
print('  Fetch time: %.1fs' % elapsed_fetch)

# ================================================================
# STEP 3: Parse documents
# ================================================================
print('\n--- Step 3: Parse documents ---')
parsed_docs = []
for p in att_paths:
    try:
        doc = parse_file(p, log=lambda x: None)
        if doc and (doc.paragraphs or doc.tables):
            parsed_docs.append(doc)
            print('  PARSED: %s (%d paragraphs, %d tables)' % (doc.filename, len(doc.paragraphs), len(doc.tables)))
        else:
            print('  EMPTY: %s' % p.name)
    except Exception as e:
        print('  ERROR parsing %s: %s' % (p.name, str(e)[:60]))

print('\n  Total parsed docs: %d' % len(parsed_docs))

# ================================================================
# STEP 4: Build deep mine result
# ================================================================
print('\n--- Step 4: Deep mine subtasks ---')
subtask_mines = []
for st in jira.subtasks:
    mine = _mine_subtask(st, log=lambda x: None)
    subtask_mines.append(mine)
    print('  %s [%s]: %d AC items' % (mine.key, mine.component, len(mine.ac_items)))

deep_mine = DeepMineResult(
    feature_id=jira.key,
    subtask_mines=subtask_mines,
    data_sources_used=['Jira subtasks'],
)

# ================================================================
# STEP 5: Run V8 engine WITH parsed docs
# ================================================================
print('\n--- Step 5: Run V8 engine ---')
options = {
    'channel': ['NBOP'],
    'engine_version': '8',
    'custom_instructions': '',
}

suite = build_test_suite_v8(jira, None, parsed_docs, options, deep_mine, log=print)

# ================================================================
# RESULTS
# ================================================================
print('\n' + '=' * 70)
print('RESULT: %d TCs, %d steps' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
print('Route: %s (%.2f)' % (suite.routing_audit.classification, suite.routing_audit.confidence))
print('=' * 70)

print('\nData Sources:')
for src in suite.data_inventory.sources:
    print('  %-20s | %-10s | %d items | %s' % (
        src.source_name[:20], src.source_type, src.items_extracted, src.status))
print('  Total testable items: %d' % suite.data_inventory.total_testable_items)

print('\nTest Cases:')
for tc in suite.test_cases:
    print('  %s %-12s %s' % (tc.sno, tc.category, tc.summary[:65]))

print('\n' + '=' * 70)
print('FULL PIPELINE DRY RUN COMPLETE')
print('  Jira fetch: %.1fs | TCs: %d | Steps: %d | Parsed docs: %d' % (
    elapsed_fetch, len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases), len(parsed_docs)))
print('=' * 70)
