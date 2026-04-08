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

CHANNELS = ['ITMBO', 'NBOP']
DEVICE_TYPES = ['Mobile', 'Tablet', 'Smartwatch']
NETWORK_TYPES = ['4G', '5G']
SIM_TYPES = ['eSIM', 'pSIM']

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
def output_path(fid): return OUTPUTS / f'TESTPLAN_{fid}_{ts()}.xlsx'
def checkpoint_path(fid, ver='v1'): return CHECKPOINTS / f'CHECKPOINT_{fid}_{ver}_{ts()}.xlsx'
