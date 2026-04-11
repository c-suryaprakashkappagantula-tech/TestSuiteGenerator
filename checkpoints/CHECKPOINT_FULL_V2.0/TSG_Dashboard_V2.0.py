"""
TSG_Dashboard_V2.0.py -- Test Suite Generator Dashboard V2.0
Premium glassmorphism UI with neon accents and animations.

Usage:  streamlit run TSG_Dashboard_V2.0.py
"""
import sys, os, time, traceback, shutil, io
from pathlib import Path
from datetime import datetime
from html import escape
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from playwright.sync_api import sync_playwright

from modules.config import (ROOT, OUTPUTS, CHECKPOINTS, ATTACHMENTS, INPUTS,
                             CHANNELS, DEVICE_TYPES, NETWORK_TYPES, SIM_TYPES, OS_PLATFORMS,
                             BROWSER_CHANNEL, BROWSER_HEADLESS, ts_short, EXCEL_HEADERS,
                             get_browser_channel)
from modules.jira_fetcher import fetch_jira_issue, download_attachments
from modules.chalk_parser import discover_pi_links, fetch_feature_from_pi, discover_features_on_pi
from modules.doc_parser import parse_file
from modules.test_engine import build_test_suite
from modules.excel_generator import generate_excel
from modules.theme_v2 import CSS
from modules.transaction_log import log_generation, get_history

if sys.platform.startswith('win'):
    try:
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
    # Suppress CMD window popups from Playwright subprocess (Point 1: safer patch)
    import subprocess
    if not getattr(subprocess.Popen, '_tsg_patched', False):
        _orig_popen = subprocess.Popen.__init__
        def _patched_popen(self, *args, **kwargs):
            if 'creationflags' not in kwargs:
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            _orig_popen(self, *args, **kwargs)
        subprocess.Popen.__init__ = _patched_popen
        subprocess.Popen._tsg_patched = True

# ================================================================
# PAGE CONFIG
# ================================================================
st.set_page_config(page_title='TSG - Test Suite Generator', page_icon='https://em-content.zobj.net/source/twitter/408/test-tube_1f9ea.png', layout='wide')
st.markdown(CSS, unsafe_allow_html=True)

# ================================================================
# DEFAULT PI LIST
# ================================================================
CHALK_PI_BASE = 'https://chalk.charter.com/spaces/MDA/pages'
_DEFAULT_PIS = [
    ('PI-46', f'{CHALK_PI_BASE}/3007682660/PI-46'),
    ('PI-47', f'{CHALK_PI_BASE}/3007682684/PI-47'),
    ('PI-48', f'{CHALK_PI_BASE}/3007682700/PI-48'),
    ('PI-49', f'{CHALK_PI_BASE}/3034265360/PI-49'),
    ('PI-50', f'{CHALK_PI_BASE}/3055797856/PI-50'),
    ('PI-51', f'{CHALK_PI_BASE}/3146012807/PI-51'),
    ('PI-52', f'{CHALK_PI_BASE}/3146012810/PI-52'),
    ('PI-53', f'{CHALK_PI_BASE}/3281127794/PI-53'),
    ('PI-54', f'{CHALK_PI_BASE}/3281128572/PI-54'),
    ('PI-55', f'{CHALK_PI_BASE}/3281128730/PI-55'),
]

# ================================================================
# SESSION STATE
# ================================================================
ss = st.session_state
defaults = {
    'pi_list': list(_DEFAULT_PIS),
    'selected_pi': None,
    'selected_pi_url': '',
    'all_pi_features': {},       # {PI_label: [(fid, title), ...]} — cached for ALL PIs
    'pi_features': [],           # current PI's features (filtered from cache)
    'feature_mode': 'dropdown',
    'logs': [],
    'result_path': None,
    'cp_path': None,
    'suite_info': None,
    'exit_report': None,
    '_fetching_features': False,
}
for k, v in defaults.items():
    if k not in ss:
        ss[k] = v

# ================================================================
# BANNER
# ================================================================
st.markdown("""<div class='banner'>
  <div>
    <div class='title'>TSG &mdash; Test Suite Generator</div>
    <div class='sub'>Chalk + Jira + Attachments &rarr; Production-Ready Test Suites</div>
  </div>
  <div style='display:flex; gap:8px; align-items:center; flex-wrap:wrap;'>
    <div class='badge'>V2.0</div>
    <div class='badge'>Any Feature ID</div>
    <div class='badge'>Auto Matrix</div>
  </div>
</div>""", unsafe_allow_html=True)

# ================================================================
# LAYOUT
# ================================================================
left, right = st.columns([1.2, 1])

# ────────────────────────────────────────────────────────────────
# LEFT PANEL
# ────────────────────────────────────────────────────────────────
with left:
    # ── Step 1: PI Selection ──
    st.markdown("<div class='sec-title'><span class='icon'>&#127919;</span> Step 1: Select PI Iteration</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)

    pi_list = ss['pi_list']
    cols = st.columns(len(pi_list))
    for j, (label, url) in enumerate(pi_list):
        with cols[j]:
            is_selected = ss['selected_pi'] == label
            btn_type = 'primary' if is_selected else 'secondary'
            if st.button(label, key=f'pi_{label}', type=btn_type, use_container_width=True):
                if ss['selected_pi'] == label:
                    ss['selected_pi'] = None
                    ss['selected_pi_url'] = ''
                    ss['pi_features'] = []
                else:
                    ss['selected_pi'] = label
                    ss['selected_pi_url'] = url
                    # Use cache if available
                    ss['pi_features'] = ss.get('all_pi_features', {}).get(label, [])
                st.rerun()

    rc1, rc2 = st.columns([3, 1])
    with rc1:
        if ss['selected_pi']:
            st.success(f'Selected: **{ss["selected_pi"]}**')
        else:
            st.caption('Select a PI iteration above')
    with rc2:
        refresh_pi_btn = st.button('Refresh from Chalk', key='refresh_pi', type='secondary', use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 2: Feature ID ──
    st.markdown("<div class='sec-title'><span class='icon'>&#128269;</span> Step 2: Feature ID</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)

    # Auto-fetch all PI features on first load (one-time, cached in session)
    if not ss.get('all_pi_features') and not ss.get('_fetching_features'):
        ss['_fetching_features'] = True
        with st.spinner('First load: fetching features from ALL PIs (one-time, will be cached)...'):
            try:
                _pw = sync_playwright().start()
                _br = _pw.chromium.launch(headless=True, channel=get_browser_channel())
                _cx = _br.new_context(viewport={'width': 1920, 'height': 1080})
                _pg = _cx.new_page()
                _all = {}
                for _pi_label, _pi_url in ss['pi_list']:
                    st.toast('Scanning %s...' % _pi_label)
                    _feats = discover_features_on_pi(_pg, _pi_url, log=lambda m: None)
                    _all[_pi_label] = _feats
                ss['all_pi_features'] = _all
                if ss['selected_pi']:
                    ss['pi_features'] = _all.get(ss['selected_pi'], [])
                _cx.close(); _br.close(); _pw.stop()
                ss['_fetching_features'] = False
                st.rerun()
            except Exception as _e:
                st.error('Failed to fetch features: %s' % _e)
                ss['_fetching_features'] = False
                try: _cx.close()
                except: pass
                try: _br.close()
                except: pass
                try: _pw.stop()
                except: pass

    # When PI is selected, pull features from cache
    if ss['selected_pi'] and not ss['pi_features']:
        cached = ss.get('all_pi_features', {}).get(ss['selected_pi'], [])
        if cached:
            ss['pi_features'] = cached
            st.rerun()

    fc1, fc2 = st.columns([5, 1])
    with fc2:
        manual_mode = st.checkbox('Manual', value=(ss['feature_mode'] == 'manual'), key='manual_toggle')
        ss['feature_mode'] = 'manual' if manual_mode else 'dropdown'

    feature_id = ''
    if ss['feature_mode'] == 'manual' or not ss['pi_features']:
        feature_id = st.text_input('Jira Feature ID', value='', placeholder='e.g. MWTGPROV-4254')
    else:
        options = ['-- Select a Feature --'] + [
            '%s - %s' % (fid, title) for fid, title in ss['pi_features']
        ]
        selected = st.selectbox(
            'Available Features (%d found in %s)' % (len(ss['pi_features']), ss['selected_pi']),
            options=options, key='feature_dropdown')
        if selected and selected != '-- Select a Feature --':
            feature_id = selected.split(' - ')[0].strip()

    if ss['pi_features'] and ss['feature_mode'] != 'manual':
        st.caption('%d features available in %s' % (len(ss['pi_features']), ss['selected_pi']))

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 3: Test Matrix ──
    st.markdown("<div class='sec-title'><span class='icon'>&#9881;</span> Step 3: Test Matrix & Strategy</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)

    # Suite Strategy selector
    strategy = st.radio('Suite Strategy', ['Smart Suite (Recommended)', 'Full Matrix', 'Custom Instructions'],
                        horizontal=True, key='suite_strategy',
                        help='Smart=representative combos | Full=every combination | Custom=your rules')

    mc1, mc2, mc3 = st.columns(3)
    with mc1:
        channel = st.multiselect('Channel', CHANNELS, default=['ITMBO'])
        devices = st.multiselect('Device Types', DEVICE_TYPES, default=['Mobile'])
    with mc2:
        networks = st.multiselect('Network Types', NETWORK_TYPES, default=['4G', '5G'])
        sim_types = st.multiselect('SIM Types', SIM_TYPES, default=['eSIM', 'pSIM'])
    with mc3:
        os_platforms = st.multiselect('OS / Platform', OS_PLATFORMS, default=['iOS', 'Android'])

    # Custom Instructions (only shown for Custom mode)
    custom_instructions = ''
    if strategy == 'Custom Instructions':
        st.markdown("**Custom Instructions** — tell the engine what you want:")
        suggestions = [
            'Focus on eSIM only, skip pSIM',
            'Only NBOP channel',
            'Include wearable scenarios',
            'Skip 4G completely, 5G only',
            'Add extra negative cases for timeout and rollback',
            'Include rollback for every swap type',
            'Only Mobile devices, no Tablet',
            'Prioritize E2E scenarios',
            'Limit to 5 test cases per group',
            'Include boundary testing for MDN format',
            'Add API authentication failure scenarios',
            'Focus on Change BCD and Change Rateplan only',
            'Include Syniverse and MBO integration checks',
            'Add data integrity checks after each operation',
        ]
        st.caption('Suggestions (click to copy):')
        # Show suggestions in 2 columns of chips
        sg1, sg2 = st.columns(2)
        with sg1:
            for s in suggestions[:7]:
                st.code(s, language=None)
        with sg2:
            for s in suggestions[7:]:
                st.code(s, language=None)
        custom_instructions = st.text_area('Your instructions:', value='', height=100,
            placeholder='Type your instructions here...\ne.g. Focus on eSIM Mobile 5G, add rollback scenarios, skip 4G')

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 4: Options ──
    st.markdown("<div class='sec-title'><span class='icon'>&#128736;</span> Step 4: Options</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)
    oc1, oc2 = st.columns(2)
    with oc1:
        inc_positive = st.checkbox('Positive scenarios', value=True)
        inc_negative = st.checkbox('Negative scenarios', value=True)
        inc_attachments = st.checkbox('Include Jira Attachments', value=True)
    with oc2:
        inc_e2e = st.checkbox('E2E scenarios', value=True)
        inc_edge = st.checkbox('Edge Cases', value=True)
        headed = st.checkbox('Show Browser (debug only)', value=False, help='Keep unchecked for headless mode')
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 5: Upload ──
    st.markdown("<div class='sec-title'><span class='icon'>&#128206;</span> Step 5: Additional Docs (Optional)</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)
    uploaded_files = st.file_uploader('Upload HLD/LLD/Solution docs',
                                      type=['docx', 'xlsx', 'pdf', 'txt', 'html', 'htm'],
                                      accept_multiple_files=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Action Buttons ──
    bc1, bc2, bc3, bc4, bc5 = st.columns([1.5, 0.5, 0.5, 0.5, 0.5])
    with bc1:
        run_btn = st.button('Execute - Generate Test Suite', type='primary', use_container_width=True)
    with bc2:
        clear_btn = st.button('Clear All', use_container_width=True)
    with bc3:
        reload_btn = st.button('Reload', use_container_width=True)
    with bc4:
        history_btn = st.button('History', use_container_width=True)
    with bc5:
        cp_btn = st.button('Checkpoints', use_container_width=True)

# ────────────────────────────────────────────────────────────────
# RIGHT PANEL
# ────────────────────────────────────────────────────────────────
with right:
    st.markdown("<div id='cli'></div>", unsafe_allow_html=True)
    st.markdown("<div class='sec-title'><span class='icon'>&#128187;</span> Live Terminal</div>", unsafe_allow_html=True)
    cli_header = st.empty()
    cli_log = st.empty()
    cli_tools = st.empty()

    if ss['logs']:
        view = '\n'.join(reversed(ss['logs'][-1200:]))
        cli_log.markdown("<div class='cli-box'><pre>%s</pre></div>" % escape(view), unsafe_allow_html=True)

    # ── Output Panel ──
    st.markdown("<div id='output'></div>", unsafe_allow_html=True)
    st.markdown("<div class='sec-title'><span class='icon'>&#128230;</span> Output</div>", unsafe_allow_html=True)
    output_area = st.container()

    if ss.get('result_path') and Path(ss['result_path']).exists():
        # Auto-scroll to output
        st.markdown("<script>document.getElementById('output').scrollIntoView({behavior:'smooth'});</script>",
                    unsafe_allow_html=True)
        with output_area:
            info = ss.get('suite_info', {})
            st.markdown("""<div class='stats-row'>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#8b5cf6,#6366f1);'>
                    <div class='icon'>&#128203;</div>
                    <div class='label'>Test Cases</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#3b82f6,#06b6d4);'>
                    <div class='icon'>&#128221;</div>
                    <div class='label'>Steps</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#22c55e,#10b981);'>
                    <div class='icon'>&#128196;</div>
                    <div class='label'>Sheets</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#f59e0b,#f97316);'>
                    <div class='icon'>&#9989;</div>
                    <div class='label'>Status</div><div class='value'>Done</div></div>
            </div>""" % (info.get('tc_count', 0), info.get('step_count', 0), info.get('sheet_count', 3)),
            unsafe_allow_html=True)

            st.download_button('Download Test Suite',
                data=Path(ss['result_path']).read_bytes(),
                file_name=Path(ss['result_path']).name,
                use_container_width=True, key='dl_main')

    # ── Exit Report ──
    if ss.get('exit_report'):
        rpt = ss['exit_report']
        cls = 'exit-report error' if 'FAILED' in rpt.get('title', '') else 'exit-report'
        st.markdown("<div class='%s'>" % cls +
            "<h4>%s</h4>" % escape(rpt.get('title', 'Completed')) +
            "<ul>" + ''.join("<li>%s</li>" % escape(item) for item in rpt.get('items', [])) + "</ul>" +
            "<p style='color:#a78bfa;font-size:11px;margin-top:8px;'>%s</p>" % escape(rpt.get('footer', '')) +
            "</div>", unsafe_allow_html=True)

# ================================================================
# LIVE LOG HELPER
# ================================================================
class LiveLog:
    def __init__(self, header_ph, log_ph, tools_ph):
        self.header = header_ph
        self.log_ph = log_ph
        self.tools = tools_ph
        self.lines = list(ss.get('logs', []))
        self._ver = 0

    def set(self, text):
        self.header.markdown("<div class='cli-header'>>> %s</div>" % escape(text), unsafe_allow_html=True)

    def write(self, s):
        parts = s.splitlines(True)
        if not parts: return
        for part in parts:
            if part.strip():
                self.lines.append('[%s] %s' % (ts_short(), part.rstrip()))
        ss['logs'] = list(self.lines)
        view = '\n'.join(reversed(self.lines[-1200:]))
        self.log_ph.markdown("<div class='cli-box'><pre>%s</pre></div>" % escape(view), unsafe_allow_html=True)
        self._ver += 1

    def flush(self):
        pass

    def __call__(self, msg):
        self.write(msg + '\n')

# ================================================================
# ACTIONS
# ================================================================

if clear_btn:
    for k in defaults:
        ss[k] = defaults[k]
    st.rerun()

if reload_btn:
    st.cache_resource.clear()
    st.cache_data.clear()
    for k in defaults:
        ss[k] = defaults[k]
    ss['all_pi_features'] = {}  # clear feature cache
    st.toast('Modules reloaded! Feature cache cleared.')
    st.rerun()

if cp_btn:
    cps = sorted(CHECKPOINTS.glob('*.xlsx'), reverse=True)
    if cps:
        st.sidebar.title('Checkpoints')
        for cp in cps[:15]:
            st.sidebar.download_button(cp.name, data=cp.read_bytes(), file_name=cp.name, key=f'cp_{cp.stem}')
    else:
        st.sidebar.info('No checkpoints yet.')

if history_btn:
    hist = get_history()
    st.sidebar.title('Generation History')
    if hist:
        for h in hist[:20]:
            with st.sidebar.expander('%s | %s | %d TCs' % (h['timestamp'][:16], h['feature_id'], h['tc_count'])):
                st.write('PI: %s' % h.get('pi', 'N/A'))
                st.write('Strategy: %s' % h.get('strategy', 'N/A'))
                st.write('Steps: %d' % h.get('step_count', 0))
                st.write('Status: %s' % h.get('status', 'N/A'))
                fp = Path(h.get('file_path', ''))
                if fp.exists():
                    st.download_button('Download', data=fp.read_bytes(), file_name=fp.name,
                                       key='hist_%s' % h['timestamp'].replace(' ','_').replace(':',''))
    else:
        st.sidebar.info('No history yet.')

if refresh_pi_btn:
    with st.spinner('Refreshing ALL PIs and features from Chalk...'):
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
            ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = ctx.new_page()
            # Refresh PI list
            pi_links = discover_pi_links(page, log=lambda m: None)
            ss['pi_list'] = [(p.label, p.url) for p in pi_links]
            # Re-fetch ALL features
            _all = {}
            for _pi_label, _pi_url in ss['pi_list']:
                st.toast('Scanning %s...' % _pi_label)
                _feats = discover_features_on_pi(page, _pi_url, log=lambda m: None)
                _all[_pi_label] = _feats
            ss['all_pi_features'] = _all
            # Keep current PI selection, just refresh its features from new cache
            if ss['selected_pi'] and ss['selected_pi'] in _all:
                ss['pi_features'] = _all[ss['selected_pi']]
            else:
                ss['selected_pi'] = None
                ss['selected_pi_url'] = ''
                ss['pi_features'] = []
            ctx.close(); browser.close(); pw.stop()
            st.toast('Refreshed %d PIs with %d total features' % (
                len(_all), sum(len(v) for v in _all.values())))
            st.rerun()
        except Exception as e:
            st.error('Failed: %s' % e)
            try: ctx.close()
            except: pass
            try: browser.close()
            except: pass
            try: pw.stop()
            except: pass

# ================================================================
# MAIN EXECUTION
# ================================================================
if run_btn:
    if not feature_id.strip():
        st.error('Please enter a Feature ID.')
    elif not ss.get('selected_pi'):
        st.error('Please select a PI iteration first.')
    else:
        feature_id = feature_id.strip().upper()
        ss['logs'] = []
        ss['result_path'] = None
        ss['cp_path'] = None
        ss['exit_report'] = None

        logger = LiveLog(cli_header, cli_log, cli_tools)
        logger.set('Starting: %s | %s' % (feature_id, ss['selected_pi']))

        st.markdown("<script>document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>",
                    unsafe_allow_html=True)
        import streamlit.components.v1 as components
        components.html("<script>parent.document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>", height=0)

        t0 = time.time()
        exit_items = []

        with redirect_stdout(logger):
            try:
                logger.set('[1/8] Launching browser...')
                print('[INIT] Launching browser...', flush=True)
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
                context = browser.new_context(accept_downloads=True, viewport={'width': 1920, 'height': 1080})
                page = context.new_page()
                exit_items.append('Browser launched')

                logger.set('[2/8] Fetching Jira: %s...' % feature_id)
                jira = fetch_jira_issue(page, feature_id, log=logger)
                exit_items.append('Jira fetched: %s' % jira.summary[:50])

                parsed_docs = []
                if inc_attachments and jira.attachments:
                    logger.set('[3/8] Downloading %d attachment(s)...' % len(jira.attachments))
                    att_paths = download_attachments(page, jira, log=logger)
                    for ap in att_paths:
                        logger.set('Parsing: %s...' % ap.name)
                        parsed_docs.append(parse_file(ap, log=logger))
                    exit_items.append('Attachments: %d downloaded & parsed' % len(att_paths))
                else:
                    exit_items.append('Attachments: skipped')

                logger.set('[4/8] Fetching Chalk: %s...' % ss['selected_pi'])
                chalk = fetch_feature_from_pi(page, ss['selected_pi_url'], feature_id, log=logger)
                if chalk.scenarios:
                    exit_items.append('Chalk: %d scenarios from %s' % (len(chalk.scenarios), ss['selected_pi']))
                else:
                    exit_items.append('Chalk: Feature %s not found in %s' % (feature_id, ss['selected_pi']))

                if uploaded_files:
                    for uf in uploaded_files:
                        logger.set('[4b/8] Parsing upload: %s...' % uf.name)
                        save_path = INPUTS / uf.name
                        save_path.write_bytes(uf.getvalue())
                        parsed_docs.append(parse_file(save_path, log=logger))
                    exit_items.append('Uploads: %d parsed' % len(uploaded_files))

                context.close(); browser.close(); pw.stop()
                exit_items.append('Browser closed')

                logger.set('[5/8] Building test suite...')
                options = {
                    'channel': channel, 'devices': devices, 'networks': networks,
                    'sim_types': sim_types, 'os_platforms': os_platforms,
                    'include_positive': inc_positive,
                    'include_negative': inc_negative, 'include_e2e': inc_e2e,
                    'include_edge': inc_edge, 'include_attachments': inc_attachments,
                    'strategy': strategy,
                    'custom_instructions': custom_instructions,
                }
                suite = build_test_suite(jira, chalk, parsed_docs, options, log=logger)
                total_steps = sum(len(tc.steps) for tc in suite.test_cases)
                exit_items.append('Suite built: %d TCs | %d steps' % (len(suite.test_cases), total_steps))

                logger.set('[6/8] Generating Excel...')
                out_path = generate_excel(suite, log=logger)
                exit_items.append('Excel: %s' % out_path.name)

                cps = sorted(CHECKPOINTS.glob('CHECKPOINT_%s*' % feature_id), reverse=True)
                cp_path = str(cps[0]) if cps else None
                if cp_path:
                    exit_items.append('Checkpoint: %s' % Path(cp_path).name)

                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                logger.set('[8/8] DONE in %dm %ds' % (m, s))
                print('\n[DONE] Total time: %dm %ds' % (m, s), flush=True)

                sheet_count = len(suite.groups) + 2 if len(suite.groups) > 1 else 3
                if hasattr(suite, 'combinations') and suite.combinations and len(suite.combinations) > 1:
                    sheet_count += 1

                ss['result_path'] = str(out_path)
                ss['cp_path'] = cp_path
                ss['suite_info'] = {'tc_count': len(suite.test_cases), 'step_count': total_steps, 'sheet_count': sheet_count}
                ss['exit_report'] = {
                    'title': 'Generation Complete - %s' % feature_id,
                    'items': exit_items,
                    'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss['selected_pi']),
                }

                # Log transaction
                log_generation(feature_id, ss['selected_pi'],
                    len(suite.test_cases), total_steps, strategy, str(out_path))

                st.rerun()

            except Exception as e:
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                logger.set('FAILED after %dm %ds' % (m, s))
                print('\n[ERROR] %s' % e, flush=True)
                traceback.print_exc()
                exit_items.append('ERROR: %s' % str(e)[:100])
                ss['exit_report'] = {
                    'title': 'Generation FAILED - %s' % feature_id,
                    'items': exit_items,
                    'footer': 'Failed at %s | Duration: %dm %ds' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s),
                }
                log_generation(feature_id, ss.get('selected_pi',''), 0, 0, strategy, '', status='FAILED')
                # Point 3: safe cleanup — variables may not exist if crash was early
                for _obj_name in ('context', 'browser', 'pw'):
                    _obj = locals().get(_obj_name)
                    if _obj:
                        try:
                            if _obj_name == 'pw': _obj.stop()
                            else: _obj.close()
                        except: pass
                st.rerun()
