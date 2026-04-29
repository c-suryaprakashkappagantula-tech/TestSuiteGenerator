"""
config.py — Central configuration for TestSuiteGenerator
All paths relative to project root. Works for ANY feature ID.
"""
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent.resolve()
MODULES = ROOT / 'modules'
INPUTS = ROOT / 'inputs'
OUTPUTS = ROOT / 'outputs'
CHECKPOINTS = ROOT / 'checkpoints'
TEMPLATES = ROOT / 'templates'
LOGS = ROOT / 'logs'
ATTACHMENTS = ROOT / 'attachments'

for d in [INPUTS, OUTPUTS, CHECKPOINTS, TEMPLATES, LOGS, ATTACHMENTS]:
    d.mkdir(exist_ok=True)

JIRA_BASE_URL = 'https://jira.charter.com'
JIRA_REST_V2 = f'{JIRA_BASE_URL}/rest/api/2'
CHALK_BASE_URL = 'https://chalk.charter.com'

BROWSER_CHANNEL = 'msedge'
BROWSER_HEADLESS = True
PAGE_LOAD_TIMEOUT_MS = 120000
NETWORK_IDLE_TIMEOUT_MS = 30000

# Point 17: Browser channel fallback chain
def get_browser_channel():
    """Try msedge -> chrome -> chromium (None = bundled)."""
    import shutil
    for ch in ['msedge', 'chrome']:
        # Playwright uses channel names, not exe paths — but we can check if the exe exists
        if ch == 'msedge' and shutil.which('msedge'):
            return 'msedge'
        if ch == 'chrome' and shutil.which('chrome'):
            return 'chrome'
    # On Windows, check common paths
    import os
    edge_paths = [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
    ]
    chrome_paths = [
        os.path.expandvars(r'%ProgramFiles%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe'),
    ]
    for p in edge_paths:
        if os.path.isfile(p):
            return 'msedge'
    for p in chrome_paths:
        if os.path.isfile(p):
            return 'chrome'
    return None  # Playwright bundled Chromium

CHANNELS = ['ITMBO', 'NBOP']
DEVICE_TYPES = ['Mobile', 'Tablet', 'Smartwatch']
NETWORK_TYPES = ['4G', '5G']
SIM_TYPES = ['eSIM', 'pSIM']
OS_PLATFORMS = ['iOS', 'Android']

EXCEL_HEADERS = ['S.No', 'Summary', 'Description', 'Preconditions',
                 'Step #', 'Step Summary', 'Expected Result',
                 'Story Linkages', 'Labels', 'References']
MERGE_COLS = [1, 2, 3, 4, 8, 9, 10]

NAVY = '0B1D39'
LIGHT_BLUE = 'DCE6F1'
WHITE = 'FFFFFF'
CAT_COLORS = {'Happy Path': 'C6EFCE', 'Positive': 'C6EFCE', 'Edge Case': 'FFEB9C',
              'Negative': 'FFC7CE', 'E2E': 'BDD7EE', 'End-to-End': 'BDD7EE'}

def ts(): return datetime.now().strftime('%Y%m%d_%H%M%S')
def ts_short(): return datetime.now().strftime('%H:%M:%S')

def _pi_to_iteration(pi_label):
    """Convert PI-52 → 52.2, PI-51 → 51.2, PI-50 → 50.1, etc."""
    import re
    m = re.search(r'(\d+)', pi_label or '')
    if not m:
        return ''
    num = int(m.group(1))
    # PI-46 to PI-49 = x.1, PI-50+ = x.2
    suffix = '.2' if num >= 50 else '.1'
    return '%d%s' % (num, suffix)

def _clean_filename(text, max_len=80):
    """Clean text for use in filename — strips Chalk/Jira metadata for a crisp name.
    Removes [TAG, TAG] prefix, 'New MVNO -', and all non-alphanumeric punctuation."""
    import re
    t = text.strip()
    # Strip leading dashes/spaces
    t = t.lstrip(' -')
    # Strip [NSLNM, NBOP, INTG] style tag prefixes
    t = re.sub(r'^\[?(?:[A-Z]{2,10})(?:\s*,\s*(?:[A-Z]{2,10}))*\]?\s*:?\s*', '', t)
    # Strip "New MVNO -" or "New MVNO:" prefix
    t = re.sub(r'^New\s+MVNO\s*[-:—]\s*', '', t, flags=re.IGNORECASE)
    # Strip leftover brackets, commas, semicolons, and other punctuation
    t = re.sub(r'[\[\],;(){}!?@#$%^&*+=~`]', '', t)
    # Replace characters that are invalid in filenames
    t = re.sub(r'[<>:"/\\|?*]', '', t)
    # Collapse multiple spaces/dashes
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'-{2,}', '-', t)
    # Truncate if too long
    if len(t) > max_len:
        t = t[:max_len].rsplit(' ', 1)[0]
    # Replace spaces with underscores for filename
    t = t.replace(' ', '_')
    return t.rstrip('_.-')

def output_path(fid, pi='', title=''):
    iteration = _pi_to_iteration(pi)
    clean_title = _clean_filename(title) if title else ''
    # Format: MWTGPROV-4254_53.2_[MED,_INTG]_New_MVNO_-_ILD_and_..._20260416.xlsx
    name_parts = [fid]
    if iteration:
        name_parts.append(iteration)
    if clean_title:
        name_parts.append(clean_title)
    name_parts.append(ts())
    return OUTPUTS / ('_'.join(name_parts) + '.xlsx')

def checkpoint_path(fid, ver='v1', pi='', title=''):
    iteration = _pi_to_iteration(pi)
    clean_title = _clean_filename(title) if title else ''
    name_parts = ['CHECKPOINT', fid]
    if iteration:
        name_parts.append(iteration)
    if clean_title:
        name_parts.append(clean_title)
    name_parts.append(ver)
    name_parts.append(ts())
    return CHECKPOINTS / ('_'.join(name_parts) + '.xlsx')
