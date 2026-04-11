"""
TSG_Dashboard_V3.0.py -- Test Suite Generator Dashboard V3.0
V3.0 adds: Feature-aware TC naming, Jira subtask analysis.

Usage:  streamlit run TSG_Dashboard_V3.0.py
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
from modules.database import (init_db, save_pi_pages, load_pi_pages, save_features,
                               load_features, load_all_features, get_features_count,
                               save_jira, load_jira, is_jira_stale, save_chalk, load_chalk,
                               load_chalk_as_object, get_chalk_cache_count,
                               log_generation_db, get_history_db, get_db_stats, is_data_stale)

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
}
for k, v in defaults.items():
    if k not in ss:
        ss[k] = v

# ================================================================
# BANNER
# ================================================================
_db_stats = get_db_stats()
_chalk_cached = get_chalk_cache_count()
_db_badge = 'DB: %d features | %d cached' % (_db_stats['feature_count'], _chalk_cached) if _db_stats['feature_count'] > 0 else 'DB: empty'
st.markdown("""<div class='banner'>
  <div>
    <div class='title'>TSG &mdash; Test Suite Generator</div>
    <div class='sub'>Chalk + Jira + Attachments &rarr; Production-Ready Test Suites</div>
  </div>
  <div style='display:flex; gap:8px; align-items:center; flex-wrap:wrap;'>
    <div class='badge'>V3.0</div>
    <div class='badge'>Any Feature ID</div>
    <div class='badge'>Auto Matrix</div>
    <div class='badge'>%s</div>
  </div>
</div>""" % _db_badge, unsafe_allow_html=True)

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
                    ss['pi_features'] = ss.get('all_pi_features', {}).get(label, [])
                # Clear previous execution state
                ss['logs'] = []
                ss['result_path'] = None
                ss['exit_report'] = None
                ss['suite_info'] = None
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

    # Load features: DB first (instant), Chalk scrape as fallback
    if not ss.get('all_pi_features'):
        # Try DB first
        db_features = load_all_features()
        if db_features and get_features_count() > 0:
            ss['all_pi_features'] = db_features
            if ss['selected_pi']:
                ss['pi_features'] = db_features.get(ss['selected_pi'], [])
            # Show DB stats
            stats = get_db_stats()
            chalk_count = get_chalk_cache_count()
            st.caption('Loaded %d features (%d with full Chalk data) from DB cache (%dKB)' % (
                stats['feature_count'], chalk_count, stats['db_size_kb']))
        else:
            # DB empty — scrape Chalk features + full data and save to DB
            with st.spinner('First run: fetching ALL PI features + Chalk data (one-time, cached to DB)...'):
                try:
                    _pw = sync_playwright().start()
                    _br = _pw.chromium.launch(headless=True, channel=get_browser_channel())
                    _cx = _br.new_context(viewport={'width': 1920, 'height': 1080})
                    _pg = _cx.new_page()
                    _all = {}
                    save_pi_pages(ss['pi_list'])
                    for _pi_label, _pi_url in ss['pi_list']:
                        # Step 1: Get feature list for this PI
                        _feats = discover_features_on_pi(_pg, _pi_url, log=lambda m: None)
                        _all[_pi_label] = _feats
                        save_features(_pi_label, _feats)
                        # Step 2: Fetch full Chalk data for each feature on this PI
                        for _fid, _ftitle in _feats:
                            try:
                                _chalk = fetch_feature_from_pi(_pg, _pi_url, _fid, log=lambda m: None)
                                if _chalk and _chalk.scenarios:
                                    save_chalk(_fid, _pi_label, _chalk)
                            except:
                                pass  # skip failures, don't block the whole fetch
                    ss['all_pi_features'] = _all
                    _cx.close(); _br.close(); _pw.stop()
                    if ss['selected_pi']:
                        ss['pi_features'] = _all.get(ss['selected_pi'], [])
                    st.rerun()
                except Exception as _e:
                    st.error('Feature fetch failed: %s. Use Manual mode.' % _e)
                    for _obj in ('_cx', '_br', '_pw'):
                        try: locals()[_obj].close() if _obj != '_pw' else locals()[_obj].stop()
                        except: pass

    # When PI is selected, pull features from cache instantly
    if ss['selected_pi'] and not ss['pi_features']:
        cached = ss.get('all_pi_features', {}).get(ss['selected_pi'], [])
        if cached:
            ss['pi_features'] = cached
            st.rerun()
        elif ss.get('all_pi_features'):
            st.caption('No features found for %s on Chalk page.' % ss['selected_pi'])

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

    # Suite Strategy selector (Smart Suite / Full Matrix as radio, Custom Instructions as checkbox)
    strategy = st.radio('Suite Strategy', ['Smart Suite (Recommended)', 'Full Matrix'],
                        horizontal=True, key='suite_strategy',
                        help='Smart=representative combos | Full=every combination')

    use_custom = st.checkbox('Custom Instructions', key='custom_instructions_toggle')

    # Default values — Smart Suite includes both channels
    channel = ['ITMBO', 'NBOP']
    devices = ['Mobile']
    networks = ['4G', '5G']
    sim_types = ['eSIM', 'pSIM']
    os_platforms = ['iOS', 'Android']

    if strategy == 'Smart Suite (Recommended)':
        st.caption('Smart Suite: ITMBO + NBOP | Mobile | eSIM+pSIM | iOS+Android | 4G+5G')
    elif strategy == 'Full Matrix':
        st.caption('Full Matrix generates ALL combinations. Customize below:')
        mc1, mc2, mc3 = st.columns(3)
        with mc1:
            channel = st.multiselect('Channel', CHANNELS, default=['ITMBO'])
            devices = st.multiselect('Device Types', DEVICE_TYPES, default=['Mobile'])
        with mc2:
            networks = st.multiselect('Network Types', NETWORK_TYPES, default=['4G', '5G'])
            sim_types = st.multiselect('SIM Types', SIM_TYPES, default=['eSIM', 'pSIM'])
        with mc3:
            os_platforms = st.multiselect('OS / Platform', OS_PLATFORMS, default=['iOS', 'Android'])

    # Custom Instructions (only shown when checkbox is checked)
    custom_instructions = ''
    if use_custom:
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
        selected_suggestion = st.selectbox(
            'Pick a preset instruction:',
            options=['-- Select --'] + suggestions,
            key='custom_suggestion_dropdown')
        custom_instructions = st.text_area('Or type your own instructions:', value='', height=100,
            placeholder='Type your instructions here...\ne.g. Focus on eSIM Mobile 5G, add rollback scenarios, skip 4G')
        # If user picked a preset but didn't type anything, use the preset
        if not custom_instructions.strip() and selected_suggestion != '-- Select --':
            custom_instructions = selected_suggestion

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
    ss['all_pi_features'] = {}  # clear session cache (DB persists — will reload from DB on next render)
    st.toast('Session cleared! Features will reload from DB.')
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
    with st.spinner('Refreshing ALL PIs, features, and Chalk data + updating DB...'):
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
            ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = ctx.new_page()
            # Refresh PI list
            pi_links = discover_pi_links(page, log=lambda m: None)
            ss['pi_list'] = [(p.label, p.url) for p in pi_links]
            save_pi_pages(ss['pi_list'])
            # Re-fetch ALL features + full Chalk data
            _all = {}
            _chalk_count = 0
            for _pi_label, _pi_url in ss['pi_list']:
                st.toast('Scanning %s...' % _pi_label)
                _feats = discover_features_on_pi(page, _pi_url, log=lambda m: None)
                _all[_pi_label] = _feats
                save_features(_pi_label, _feats)
                # Fetch full Chalk data for each feature
                for _fid, _ftitle in _feats:
                    try:
                        _chalk = fetch_feature_from_pi(page, _pi_url, _fid, log=lambda m: None)
                        if _chalk and _chalk.scenarios:
                            save_chalk(_fid, _pi_label, _chalk)
                            _chalk_count += 1
                    except:
                        pass
            ss['all_pi_features'] = _all
            # Keep current PI selection, just refresh its features from new cache
            if ss['selected_pi'] and ss['selected_pi'] in _all:
                ss['pi_features'] = _all[ss['selected_pi']]
            else:
                ss['selected_pi'] = None
                ss['selected_pi_url'] = ''
                ss['pi_features'] = []
            ctx.close(); browser.close(); pw.stop()
            st.toast('Refreshed %d PIs | %d features | %d with full Chalk data — saved to DB' % (
                len(_all), sum(len(v) for v in _all.values()), _chalk_count))
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
        timings = []  # (step_name, duration_secs)

        def _tick(name):
            """Record timing for a step."""
            now = time.time()
            if timings:
                prev_name, prev_start = timings[-1]
                dur = now - prev_start
                timings[-1] = (prev_name, dur)
                print('[TIME] %s: %.1fs' % (prev_name, dur), flush=True)
            timings.append((name, now))

        with redirect_stdout(logger):
            try:
                _tick('Browser Launch')
                logger.set('[1/8] Launching browser...')
                print('[INIT] Launching browser...', flush=True)
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
                context = browser.new_context(accept_downloads=True, viewport={'width': 1920, 'height': 1080})
                page = context.new_page()
                exit_items.append('Browser launched')

                _tick('Jira Fetch')
                logger.set('[2/8] Fetching Jira: %s...' % feature_id)
                jira = fetch_jira_issue(page, feature_id, log=logger)
                exit_items.append('Jira fetched: %s' % jira.summary[:50])

                parsed_docs = []
                if inc_attachments and jira.attachments:
                    _tick('Attachments')
                    logger.set('[3/8] Downloading %d attachment(s)...' % len(jira.attachments))
                    att_paths = download_attachments(page, jira, log=logger)
                    for ap in att_paths:
                        logger.set('Parsing: %s...' % ap.name)
                        parsed_docs.append(parse_file(ap, log=logger))
                    exit_items.append('Attachments: %d downloaded & parsed' % len(att_paths))
                else:
                    exit_items.append('Attachments: skipped')

                _tick('Chalk Fetch')
                logger.set('[4/8] Fetching Chalk: %s...' % ss['selected_pi'])
                chalk = None
                _chalk_source = ''

                # ── SELF-HEAL CHAIN ──
                # Step A: DB cache — selected PI
                chalk = load_chalk_as_object(feature_id, ss['selected_pi'])
                if chalk and chalk.scenarios:
                    _chalk_source = 'DB cache (%s)' % ss['selected_pi']
                    print('[CHALK] Step A: DB hit (%s): %d scenarios' % (ss['selected_pi'], len(chalk.scenarios)), flush=True)
                else:
                    # Step B: DB cache — any PI
                    from modules.database import _conn as _db_conn
                    _c = _db_conn()
                    _row = _c.execute('SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != "[]" LIMIT 1',
                                      (feature_id,)).fetchone()
                    _c.close()
                    if _row:
                        chalk = load_chalk_as_object(feature_id, _row['pi_label'])
                    if chalk and chalk.scenarios:
                        _chalk_source = 'DB cache (%s)' % _row['pi_label']
                        print('[CHALK] Step B: DB hit (%s): %d scenarios' % (_row['pi_label'], len(chalk.scenarios)), flush=True)
                    else:
                        # Step C: Live fetch — selected PI
                        print('[CHALK] Step B: DB miss. Live fetching %s...' % ss['selected_pi'], flush=True)
                        chalk = fetch_feature_from_pi(page, ss['selected_pi_url'], feature_id, log=logger)
                        if chalk and chalk.scenarios:
                            save_chalk(feature_id, ss['selected_pi'], chalk)
                            _chalk_source = '%s (live, cached)' % ss['selected_pi']
                        else:
                            # Step D: Live scan — ALL PIs
                            print('[CHALK] Step C: Not on %s. Scanning all PIs...' % ss['selected_pi'], flush=True)
                            for _sl, _su in ss['pi_list']:
                                if _sl == ss['selected_pi']:
                                    continue
                                try:
                                    _sc = fetch_feature_from_pi(page, _su, feature_id, log=lambda m: None)
                                    if _sc and _sc.scenarios:
                                        chalk = _sc
                                        save_chalk(feature_id, _sl, chalk)
                                        _chalk_source = '%s (scanned, cached)' % _sl
                                        print('[CHALK] Step D: Found on %s: %d scenarios' % (_sl, len(chalk.scenarios)), flush=True)
                                        break
                                except:
                                    pass
                            if not chalk or not chalk.scenarios:
                                print('[CHALK] Step D: Not found on any PI', flush=True)
                                _chalk_source = 'not found'
                                from modules.chalk_parser import ChalkData
                                chalk = ChalkData(feature_id=feature_id)

                if chalk and chalk.scenarios:
                    exit_items.append('Chalk: %d scenarios from %s' % (len(chalk.scenarios), _chalk_source))
                else:
                    exit_items.append('Chalk: Feature %s — %s' % (feature_id, _chalk_source))

                if uploaded_files:
                    _tick('Upload Parse')
                    for uf in uploaded_files:
                        logger.set('[4b/8] Parsing upload: %s...' % uf.name)
                        save_path = INPUTS / uf.name
                        save_path.write_bytes(uf.getvalue())
                        parsed_docs.append(parse_file(save_path, log=logger))
                    exit_items.append('Uploads: %d parsed' % len(uploaded_files))

                _tick('Browser Close')
                context.close(); browser.close(); pw.stop()
                exit_items.append('Browser closed')

                _tick('Test Engine')
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

                _tick('Excel Generation')
                logger.set('[6/8] Generating Excel...')
                out_path = generate_excel(suite, log=logger)
                exit_items.append('Excel: %s' % out_path.name)

                _tick('Finalize')
                cps = sorted(CHECKPOINTS.glob('CHECKPOINT_%s*' % feature_id), reverse=True)
                cp_path = str(cps[0]) if cps else None
                if cp_path:
                    exit_items.append('Checkpoint: %s' % Path(cp_path).name)

                # Finalize last timing entry
                _tick('_end')
                timings.pop()  # remove the _end placeholder

                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                logger.set('[8/8] DONE in %dm %ds' % (m, s))

                # Print time matrix
                print('\n' + '=' * 50, flush=True)
                print('  TIME MATRIX', flush=True)
                print('  %-20s %10s %8s' % ('Step', 'Duration', '%'), flush=True)
                print('  ' + '-' * 42, flush=True)
                for step_name, dur in timings:
                    pct = (dur / elapsed * 100) if elapsed > 0 else 0
                    print('  %-20s %8.1fs %7.1f%%' % (step_name, dur, pct), flush=True)
                print('  ' + '-' * 42, flush=True)
                print('  %-20s %8.1fs %7s' % ('TOTAL', elapsed, '100%'), flush=True)
                print('=' * 50, flush=True)

                sheet_count = len(suite.groups) + 2 if len(suite.groups) > 1 else 3
                if hasattr(suite, 'combinations') and suite.combinations and len(suite.combinations) > 1:
                    sheet_count += 1

                ss['result_path'] = str(out_path)
                ss['cp_path'] = cp_path
                ss['suite_info'] = {'tc_count': len(suite.test_cases), 'step_count': total_steps, 'sheet_count': sheet_count}

                # Build timing summary for exit report
                timing_items = ['⏱ %s: %.1fs' % (name, dur) for name, dur in timings]
                ss['exit_report'] = {
                    'title': 'Generation Complete - %s' % feature_id,
                    'items': exit_items + [''] + timing_items,
                    'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss['selected_pi']),
                }

                # Log transaction (both JSON and DB)
                log_generation(feature_id, ss['selected_pi'],
                    len(suite.test_cases), total_steps, strategy, str(out_path))
                log_generation_db(feature_id, ss['selected_pi'],
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
                log_generation_db(feature_id, ss.get('selected_pi',''), 0, 0, strategy, '', status='FAILED')
                # Point 3: safe cleanup — variables may not exist if crash was early
                for _obj_name in ('context', 'browser', 'pw'):
                    _obj = locals().get(_obj_name)
                    if _obj:
                        try:
                            if _obj_name == 'pw': _obj.stop()
                            else: _obj.close()
                        except: pass
                st.rerun()
