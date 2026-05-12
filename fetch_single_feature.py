"""
Fetch a single Jira feature into the DB cache.
Usage: python fetch_single_feature.py MWTGPROV-4230
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

from playwright.sync_api import sync_playwright
from modules.config import get_browser_channel, BROWSER_HEADLESS
from modules.jira_fetcher import fetch_jira_issue
from modules.database import init_db, save_jira

init_db()

feature_id = sys.argv[1] if len(sys.argv) > 1 else 'MWTGPROV-4230'
print('Fetching: %s' % feature_id)

with sync_playwright() as pw:
    browser = pw.chromium.launch(
        channel=get_browser_channel(),
        headless=BROWSER_HEADLESS,
    )
    context = browser.new_context()
    page = context.new_page()

    t0 = time.time()
    jira = fetch_jira_issue(page, feature_id, log=print)
    elapsed = time.time() - t0

    if jira and jira.summary:
        save_jira(jira)
        print('\nSaved to DB cache:')
        print('  Key: %s' % jira.key)
        print('  Summary: %s' % jira.summary[:80])
        print('  AC length: %d chars' % len(jira.acceptance_criteria or ''))
        print('  Subtasks: %d' % len(jira.subtasks))
        print('  Channel: %s' % jira.channel)
        print('  Time: %.1fs' % elapsed)
    else:
        print('ERROR: Could not fetch %s' % feature_id)

    context.close()
    browser.close()
