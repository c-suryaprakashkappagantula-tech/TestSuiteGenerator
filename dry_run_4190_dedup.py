"""Dry-run: regenerate MWTGPROV-4190 from DB cache and check dedup results."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_jira, load_chalk_as_object
from modules.test_engine import build_test_suite

feature_id = 'MWTGPROV-4190'
pi_label = 'PI-52'

print('Loading Jira from DB...')
jira_raw = load_jira(feature_id)
if not jira_raw:
    print('ERROR: Jira not found in DB for %s' % feature_id)
    sys.exit(1)

# Convert dict to JiraIssue if needed
from modules.jira_fetcher import JiraIssue
if isinstance(jira_raw, dict):
    jira = JiraIssue(key=jira_raw.get('key', feature_id))
    jira.summary = jira_raw.get('summary', '')
    jira.description = jira_raw.get('description', '')
    jira.acceptance_criteria = jira_raw.get('acceptance_criteria', '')
    jira.status = jira_raw.get('status', '')
    jira.priority = jira_raw.get('priority', '')
    jira.assignee = jira_raw.get('assignee', '')
    jira.reporter = jira_raw.get('reporter', '')
    jira.labels = jira_raw.get('labels', [])
    jira.channel = jira_raw.get('channel', '')
    jira.pi = jira_raw.get('pi', '')
    jira.linked_issues = jira_raw.get('linked_issues', [])
    jira.attachments = jira_raw.get('attachments', [])
    jira.comments = jira_raw.get('comments', [])
    jira.subtasks = jira_raw.get('subtasks', [])
else:
    jira = jira_raw

print('  Jira: %s | %s' % (jira.key, jira.summary[:60]))
print('  AC: %d chars' % len(jira.acceptance_criteria or ''))
print('  Description: %d chars' % len(jira.description or ''))

print('\nLoading Chalk from DB...')
chalk = load_chalk_as_object(feature_id, pi_label)
if chalk and chalk.scenarios:
    print('  Chalk: %d scenarios' % len(chalk.scenarios))
else:
    print('  Chalk: no scenarios found')

print('\nBuilding test suite (with new dedup)...')
options = {
    'channel': ['ITMBO', 'NBOP'],
    'devices': ['Mobile'],
    'networks': ['4G', '5G'],
    'sim_types': ['eSIM', 'pSIM'],
    'os_platforms': ['iOS', 'Android'],
    'include_positive': True,
    'include_negative': True,
    'include_e2e': True,
    'include_edge': True,
    'include_attachments': True,
    'strategy': 'Smart Suite (Recommended)',
    'custom_instructions': '',
}

suite = build_test_suite(jira, chalk, [], options, log=print)

print('\n' + '=' * 60)
print('RESULTS: %d TCs, %d steps' % (len(suite.test_cases), sum(len(tc.steps) for tc in suite.test_cases)))
print('=' * 60)

# Show all TC summaries
for i, tc in enumerate(suite.test_cases, 1):
    cat = tc.category or '?'
    steps = len(tc.steps)
    print('  TC%02d [%s] (%d steps): %s' % (i, cat, steps, tc.summary[:100]))

# Check for potential remaining duplicates
print('\n--- Duplicate check ---')
from collections import Counter
import re
step_sigs = []
for tc in suite.test_cases:
    sig = tuple(sorted(
        re.sub(r'\s+', ' ', s.summary).strip().lower()[:80]
        for s in tc.steps if s.summary
    ))
    step_sigs.append(sig)

sig_counts = Counter(step_sigs)
dups = {sig: count for sig, count in sig_counts.items() if count > 1}
if dups:
    print('WARNING: %d step-signature groups still have duplicates:' % len(dups))
    for sig, count in dups.items():
        matching = [i+1 for i, s in enumerate(step_sigs) if s == sig]
        print('  TCs %s share identical steps (%d steps each)' % (matching, len(sig)))
else:
    print('No step-level duplicates found.')
