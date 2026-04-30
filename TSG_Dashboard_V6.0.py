"""
TSG_Dashboard_V6.0.py -- Test Suite Generator Dashboard V6.0
V6.0 Cabot Rebuild: API Endpoint Extraction → Cabot TC Generation → Cabot Excel Export.
Pipeline: Jira fetch → Cabot Chalk DB scan → extract endpoints/fields/DB refs
          → generate Cabot TCs → generate Cabot Excel.

Usage:  streamlit run TSG_Dashboard_V6.0.py
"""
import sys, os, time, traceback, shutil, io, re
from pathlib import Path
from datetime import datetime
from html import escape
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).parent))

# Auto-clear __pycache__ on startup to ensure fresh module loading
for _cache_dir in Path(__file__).parent.rglob('__pycache__'):
    try:
        shutil.rmtree(_cache_dir)
    except Exception:
        pass

import streamlit as st
from playwright.sync_api import sync_playwright

from modules.config import (ROOT, OUTPUTS, CHECKPOINTS, ATTACHMENTS, INPUTS,
                             CHANNELS, DEVICE_TYPES, NETWORK_TYPES, SIM_TYPES, OS_PLATFORMS,
                             BROWSER_CHANNEL, BROWSER_HEADLESS, ts_short,
                             get_browser_channel)
from modules.jira_fetcher import fetch_jira_issue, download_attachments
from modules.chalk_parser import discover_pi_links, fetch_feature_from_pi, discover_features_on_pi
from modules.doc_parser import parse_file
# ── V6 Cabot imports — standalone, no test_engine dependency ──
from modules.endpoint_tc_generator import build_cabot_test_suite, CabotTestCase, CabotTestStep
from modules.cabot_excel_generator import generate_cabot_excel
from modules.endpoint_extractor import extract_endpoints
from modules.field_extractor import extract_fields
from modules.db_reference_extractor import extract_db_references
from modules.theme_v2 import CSS
from modules.transaction_log import log_generation, get_history
from modules.pipeline import Pipeline, PipelineError, block_jira_fetch, block_chalk_db, block_chalk_live, block_parse_docs
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
    # Suppress CMD window popups from Playwright subprocess
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
st.set_page_config(page_title='TSG V6.0 - Cabot Test Suite Generator', page_icon='https://em-content.zobj.net/source/twitter/408/test-tube_1f9ea.png', layout='wide')
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
    'all_pi_features': {},
    'pi_features': [],
    'feature_mode': 'dropdown',
    'input_mode': 'pi_scope',    # 'pi_scope' or 'manual_links'
    'manual_jira_url': '',
    'manual_chalk_url': '',
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
    <div class='title'>TSG &mdash; Cabot Test Suite Generator</div>
    <div class='sub'>Jira + Cabot Chalk DB + API Endpoints &rarr; Cabot Excel Test Suites</div>
  </div>
  <div style='display:flex; gap:8px; align-items:center; flex-wrap:wrap;'>
    <div class='badge'>V6.0</div>
    <div class='badge'>Cabot Pipeline</div>
    <div class='badge'>API Endpoints</div>
    <div class='badge'>Manual Links</div>
    <div class='badge'>%s</div>
  </div>
</div>""" % _db_badge, unsafe_allow_html=True)

# ================================================================
# INPUT MODE TOGGLE — PI Scope vs Manual Links
# ================================================================
_mode_col1, _mode_col2, _mode_col3 = st.columns([1, 1, 2])
with _mode_col1:
    _pi_mode = st.button('📋 PI Scope', key='mode_pi',
                          type='primary' if ss['input_mode'] == 'pi_scope' else 'secondary',
                          use_container_width=True)
with _mode_col2:
    _manual_mode = st.button('🔗 Manual Links', key='mode_manual',
                              type='primary' if ss['input_mode'] == 'manual_links' else 'secondary',
                              use_container_width=True)
with _mode_col3:
    if ss['input_mode'] == 'manual_links':
        st.markdown(
            '<div style="padding:6px 14px;border-radius:8px;margin-top:4px;'
            'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);'
            'font-size:12px;color:#FBBF24;font-weight:600;">'
            '🔗 Manual mode — provide Jira URL directly (Cabot features only)</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="padding:6px 14px;border-radius:8px;margin-top:4px;'
            'background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.2);'
            'font-size:12px;color:#34D399;font-weight:600;">'
            '📋 PI Scope — select from preloaded PI iterations (Cabot features only)</div>',
            unsafe_allow_html=True)

if _pi_mode and ss['input_mode'] != 'pi_scope':
    ss['input_mode'] = 'pi_scope'
    ss['logs'] = []
    ss['result_path'] = None
    ss['exit_report'] = None
    ss['suite_info'] = None
    st.rerun()
if _manual_mode and ss['input_mode'] != 'manual_links':
    ss['input_mode'] = 'manual_links'
    ss['logs'] = []
    ss['result_path'] = None
    ss['exit_report'] = None
    ss['suite_info'] = None
    st.rerun()

_is_manual_mode = ss['input_mode'] == 'manual_links'

# ================================================================
# LAYOUT
# ================================================================
left, right = st.columns([1.2, 1])

# ────────────────────────────────────────────────────────────────
# LEFT PANEL
# ────────────────────────────────────────────────────────────────
with left:
    # ================================================================
    # MANUAL LINKS MODE
    # ================================================================
    if _is_manual_mode:
        st.markdown("<div class='sec-title'><span class='icon'>&#128279;</span> Provide Jira URL (Cabot Feature)</div>", unsafe_allow_html=True)
        st.markdown("<div class='glass'>", unsafe_allow_html=True)

        st.markdown(
            '<div style="color:#64748b;font-size:12px;margin-bottom:10px;">'
            'Paste the Jira feature URL. The Cabot pipeline will fetch Jira data, '
            'scan the Cabot Chalk DB, extract endpoints/fields/DB refs, and generate '
            'Cabot-format test cases.</div>',
            unsafe_allow_html=True)

        _manual_jira = st.text_input(
            '🔗 Jira Feature URL',
            value=ss.get('manual_jira_url', ''),
            placeholder='https://jira.charter.com/browse/MOBIT2-62376',
            key='manual_jira_input',
            help='Full Jira URL — feature ID will be extracted automatically')

        _manual_chalk = st.text_input(
            '🔗 Chalk Page URL (optional)',
            value=ss.get('manual_chalk_url', ''),
            placeholder='https://chalk.charter.com/spaces/MDA/pages/3281127794/PI-53',
            key='manual_chalk_input',
            help='Optional Chalk page URL — Cabot Chalk DB is the primary source')

        ss['manual_jira_url'] = _manual_jira
        ss['manual_chalk_url'] = _manual_chalk

        # Extract feature ID from Jira URL
        _manual_fid = ''
        if _manual_jira:
            _jira_match = re.search(r'([A-Z][A-Z0-9]+-\d+)', _manual_jira.upper())
            if _jira_match:
                _manual_fid = _jira_match.group(1)

        # Validation badges
        _vc1, _vc2 = st.columns(2)
        with _vc1:
            if _manual_fid:
                st.markdown(
                    f'<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    f'background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.2);'
                    f'font-size:12px;color:#34D399;">✅ Feature ID: <b>{_manual_fid}</b></div>',
                    unsafe_allow_html=True)
            elif _manual_jira:
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(251,113,133,0.08);border:1px solid rgba(251,113,133,0.2);'
                    'font-size:12px;color:#FB7185;">❌ Could not extract a Jira ID (e.g., PROJ-123) from URL</div>',
                    unsafe_allow_html=True)
        with _vc2:
            # Show Cabot Chalk DB status
            _cabot_db_path = Path(__file__).parent / 'CABOT_CHALK_DB.db'
            if _cabot_db_path.exists():
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.2);'
                    'font-size:12px;color:#34D399;">✅ Cabot Chalk DB found</div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);'
                    'font-size:12px;color:#FBBF24;">⚠️ Cabot Chalk DB not found — endpoints from Jira only</div>',
                    unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

        # Set variables for downstream compatibility
        feature_id = _manual_fid
        feature_ids = [_manual_fid] if _manual_fid else []
        manual_mode = False
        batch_mode = False
        _sync_in_progress = False

    # ================================================================
    # PI SCOPE MODE (Cabot features only)
    # ================================================================
    if not _is_manual_mode:
        # ── Step 1: PI Selection ──
        st.markdown("<div class='sec-title'><span class='icon'>&#127919;</span> Step 1: Select PI Iteration (Cabot Features)</div>", unsafe_allow_html=True)
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
                    ss['logs'] = []
                    ss['result_path'] = None
                    ss['exit_report'] = None
                    ss['suite_info'] = None
                    ss['_batch_default'] = []
                    ss['feature_mode'] = 'dropdown'
                    ss['batch_results'] = []
                    ss['cp_path'] = None
                    ss['_reset_toggles'] = True
                    st.rerun()

        rc1, rc2 = st.columns([3, 1])
        with rc1:
            if ss['selected_pi']:
                st.success(f'Selected: **{ss["selected_pi"]}**')
            else:
                st.caption('Select a PI iteration above')
        with rc2:
            refresh_pi_btn = st.button('🔄 Sync from Chalk', key='refresh_pi', type='secondary', use_container_width=True)
            if refresh_pi_btn:
                ss['_sync_confirm'] = True
                st.rerun()

        # Chalk Sync confirmation dialog
        if ss.get('_sync_confirm'):
            st.markdown("---")
            st.markdown("#### 🔄 Sync from Chalk")

            _sync_scope = st.radio('Sync Scope:', ['All Iterations', 'Specific Iteration(s)'],
                                    key='sync_scope', horizontal=True)

            if _sync_scope == 'Specific Iteration(s)':
                _available_pis = [label for label, url in ss.get('pi_list', [])]
                if _available_pis:
                    _selected_sync_pis = st.multiselect(
                        'Select PI(s) to sync:', options=_available_pis,
                        default=[ss['selected_pi']] if ss.get('selected_pi') else [],
                        key='sync_pi_select')
                    ss['_sync_specific_pis'] = _selected_sync_pis
                else:
                    st.caption('No PIs cached yet. Use "All Iterations" for first sync.')
                    ss['_sync_specific_pis'] = []
                _est_time = '%d-%.0f minutes' % (len(ss.get('_sync_specific_pis', [])), len(ss.get('_sync_specific_pis', [])) * 2)
            else:
                ss['_sync_specific_pis'] = []
                _est_time = '5-10 minutes'

            st.warning('⚠️ This will re-fetch features from Chalk and update the DB. Estimated time: %s. '
                       'New PIs (e.g., PI-56) will be auto-discovered.' % _est_time)

            _cf1, _cf2, _cf3 = st.columns([2, 1, 1])
            with _cf2:
                if st.button('Yes, Sync Now', key='sync_yes', type='primary', use_container_width=True):
                    ss['_sync_confirm'] = False
                    ss['_sync_running'] = True
                    ss['_sync_scope'] = _sync_scope
                    st.rerun()
            with _cf3:
                if st.button('Cancel', key='sync_cancel', use_container_width=True):
                    ss['_sync_confirm'] = False
                    st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)

        # ── Block UI during sync ──
        _sync_in_progress = ss.get('_sync_running', False) or ss.get('_sync_confirm', False)

        # ── Step 2: Feature ID ──
        if _sync_in_progress:
            st.markdown("<div class='sec-title' style='opacity:0.4'><span class='icon'>&#128269;</span> Step 2: Feature ID (blocked during sync)</div>", unsafe_allow_html=True)
            st.info('⏳ Sync in progress — feature selection blocked until sync completes.')
            manual_mode = False
            batch_mode = False
            feature_id = ''
            feature_ids = []
        else:
            st.markdown("<div class='sec-title'><span class='icon'>&#128269;</span> Step 2: Cabot Feature ID</div>", unsafe_allow_html=True)

        # Load features: DB first (instant), Chalk scrape as fallback
        if not ss.get('all_pi_features'):
            db_features = load_all_features()
            if db_features and get_features_count() > 0:
                ss['all_pi_features'] = db_features
                if ss['selected_pi']:
                    ss['pi_features'] = db_features.get(ss['selected_pi'], [])
                stats = get_db_stats()
                chalk_count = get_chalk_cache_count()
                st.caption('Loaded %d features (%d with full Chalk data) from DB cache (%dKB)' % (
                    stats['feature_count'], chalk_count, stats['db_size_kb']))
            else:
                with st.spinner('First run: fetching ALL PI features + Chalk data (one-time, cached to DB)...'):
                    try:
                        _pw = sync_playwright().start()
                        _br = _pw.chromium.launch(headless=True, channel=get_browser_channel())
                        _cx = _br.new_context(viewport={'width': 1920, 'height': 1080})
                        _pg = _cx.new_page()
                        _all = {}
                        save_pi_pages(ss['pi_list'])
                        for _pi_label, _pi_url in ss['pi_list']:
                            _feats = discover_features_on_pi(_pg, _pi_url, log=lambda m: None)
                            _all[_pi_label] = _feats
                            save_features(_pi_label, _feats)
                            for _fid, _ftitle in _feats:
                                try:
                                    _chalk = fetch_feature_from_pi(_pg, _pi_url, _fid, log=lambda m: None)
                                    if _chalk and _chalk.scenarios:
                                        save_chalk(_fid, _pi_label, _chalk)
                                except:
                                    pass
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

        if not ss.get('_sync_confirm'):
            _batch_default_val = False
            _manual_default_val = (ss['feature_mode'] == 'manual')
            if ss.get('_reset_toggles'):
                _batch_default_val = False
                _manual_default_val = False
                ss['feature_mode'] = 'dropdown'
                ss['_reset_toggles'] = False

            _chk1, _chk2, _chk3 = st.columns([4, 2, 2])
            with _chk2:
                manual_mode = st.checkbox('Manual', value=_manual_default_val, key='manual_toggle')
                ss['feature_mode'] = 'manual' if manual_mode else 'dropdown'
            with _chk3:
                batch_mode = st.checkbox('Batch', value=_batch_default_val, key='batch_toggle')
        else:
            manual_mode = False
            batch_mode = False

        feature_id = ''
        feature_ids = []
        if batch_mode and ss['pi_features']:
            options = ['%s - %s' % (fid, title) for fid, title in ss['pi_features']]
            _ba1, _ba2 = st.columns([1, 1])
            with _ba1:
                if st.button('Select All', key='batch_select_all', use_container_width=True):
                    ss['_batch_default'] = list(options)
                    st.rerun()
            with _ba2:
                if st.button('Clear All', key='batch_clear_all', use_container_width=True):
                    ss['_batch_default'] = []
                    st.rerun()
            _default = ss.get('_batch_default', [])
            _default = [d for d in _default if d in options]
            selected_multi = st.multiselect(
                'Select Cabot Features (%d available in %s)' % (len(ss['pi_features']), ss['selected_pi']),
                options=options, default=_default, key='feature_multiselect')
            feature_ids = [s.split(' - ')[0].strip() for s in selected_multi]
            feature_id = feature_ids[0] if feature_ids else ''
        elif batch_mode:
            feature_id = st.text_input('Jira Feature IDs (comma-separated)', value='',
                placeholder='e.g. MOBIT2-62376, MOBIT2-62400')
            if feature_id:
                feature_ids = [f.strip().upper() for f in feature_id.split(',') if f.strip()]
                feature_id = feature_ids[0] if feature_ids else ''
        elif ss['feature_mode'] == 'manual' or not ss['pi_features']:
            feature_id = st.text_input('Jira Feature ID', value='', placeholder='e.g. MOBIT2-62376')
        else:
            options = ['-- Select a Cabot Feature --'] + [
                '%s - %s' % (fid, title) for fid, title in ss['pi_features']
            ]
            selected = st.selectbox(
                'Available Cabot Features (%d found in %s)' % (len(ss['pi_features']), ss['selected_pi']),
                options=options, key='feature_dropdown')
            if selected and selected != '-- Select a Cabot Feature --':
                feature_id = selected.split(' - ')[0].strip()
                if ss.get('_last_feature') != feature_id:
                    ss['_last_feature'] = feature_id
                    ss['logs'] = []
                    ss['result_path'] = None
                    ss['exit_report'] = None
                    ss['suite_info'] = None
                    ss['cp_path'] = None
                    ss['batch_results'] = []
                    ss['_batch_default'] = []
                    ss['_reset_toggles'] = True
                    st.rerun()

        if feature_ids and len(feature_ids) > 1:
            st.caption('Batch mode: %d Cabot features selected' % len(feature_ids))
        elif ss['pi_features'] and ss['feature_mode'] != 'manual':
            st.caption('%d Cabot features available in %s' % (len(ss['pi_features']), ss['selected_pi']))

    # ── Step 3: Options ──
    st.markdown("<div class='sec-title'><span class='icon'>&#128736;</span> Step 3: Cabot Pipeline Options</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)
    oc1, oc2 = st.columns(2)
    with oc1:
        inc_attachments = st.checkbox('Include Jira Attachments', value=True)
        inc_chalk_live = st.checkbox('Chalk Live Fallback', value=True, help='Fetch Chalk live if DB misses')
    with oc2:
        headed = st.checkbox('Show Browser (debug only)', value=False, help='Keep unchecked for headless mode')
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 4: Upload ──
    st.markdown("<div class='sec-title'><span class='icon'>&#128206;</span> Step 4: Additional Docs (Optional)</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)
    uploaded_files = st.file_uploader('Upload HLD/LLD/Solution docs',
                                      type=['docx', 'xlsx', 'pdf', 'txt', 'html', 'htm'],
                                      accept_multiple_files=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Action Buttons ──
    bc1, bc2, bc3, bc4, bc5 = st.columns([1.5, 0.5, 0.5, 0.5, 0.5])
    with bc1:
        run_btn = st.button('Execute - Generate Cabot Test Suite', type='primary', use_container_width=True)
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
                    <div class='label'>Endpoints</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#f59e0b,#f97316);'>
                    <div class='icon'>&#9989;</div>
                    <div class='label'>Status</div><div class='value'>Done</div></div>
            </div>""" % (info.get('tc_count', 0), info.get('step_count', 0), info.get('endpoint_count', 0)),
            unsafe_allow_html=True)

            _dl_c1, _dl_c2 = st.columns([2, 1])
            with _dl_c1:
                st.download_button('📥 Download: %s (%d TCs)' % (
                        ss.get('last_feature_id', 'Suite'), ss.get('suite_info', {}).get('tc_count', 0)),
                    data=Path(ss['result_path']).read_bytes(),
                    file_name=Path(ss['result_path']).name,
                    use_container_width=True, key='dl_main')
            with _dl_c2:
                if ss.get('batch_results'):
                    pass  # batch downloads below
                elif ss.get('logs'):
                    _cli_text = '\n'.join(ss['logs'])
                    _log_fn = 'TSG_CLI_Log_%s_%s.txt' % (
                        ss.get('last_feature_id', 'Suite'),
                        datetime.now().strftime('%Y%m%d_%H%M%S'))
                    st.download_button(
                        '📋 CLI Log',
                        data=_cli_text.encode('utf-8'),
                        file_name=_log_fn,
                        mime='text/plain',
                        use_container_width=True,
                        key='dl_cli_log_single')

            # Batch mode: show download buttons for ALL generated suites
            if ss.get('batch_results') and len(ss['batch_results']) > 1:
                st.markdown("**All generated Cabot suites:**")
                for _bi, _br in enumerate(ss['batch_results']):
                    _bp = Path(_br.get('file_path', _br.get('path', '')))
                    if _bp.exists():
                        st.download_button(
                            '📥 %s — %d TCs' % (_br['feature_id'], _br['tc_count']),
                            data=_bp.read_bytes(),
                            file_name=_bp.name,
                            use_container_width=True,
                            key='dl_batch_%d' % _bi)

            # ── CLI Log Download Button ──
            if ss.get('logs'):
                _cli_text = '\n'.join(ss['logs'])
                _log_filename = 'TSG_CLI_Log_%s_%s.txt' % (
                    (ss.get('selected_pi') or 'Manual').replace(' ', '_'),
                    datetime.now().strftime('%Y%m%d_%H%M%S'))
                st.download_button(
                    '📋 Download CLI Log (%d lines)' % len(ss['logs']),
                    data=_cli_text.encode('utf-8'),
                    file_name=_log_filename,
                    mime='text/plain',
                    use_container_width=True,
                    key='dl_cli_log')

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
    """Smart CLI logger — shows only high-level progress to user."""

    _SHOW_PREFIXES = ['[PIPELINE]', '[CABOT]', '[ERROR]', '[WARN]', 'Block ', 'DONE', 'FAILED',
                       'Batch:', '[CABOT_TC]', '[CABOT_DB]',
                       '[JIRA] Found', '[JIRA]   Epic child', '[JIRA] 🔍 Fetching epic']
    _HIDE_PREFIXES = ['[JIRA]   ', '[CHALK] Step', '[ENDPOINT]   ', '[FIELDS]   ',
                       '[DB_REF]   ', '[TIME]', '[INIT]', '[DOC]', 'DEBUG']

    def __init__(self, header_ph, log_ph, tools_ph):
        self.header = header_ph
        self.log_ph = log_ph
        self.tools = tools_ph
        self.lines = list(ss.get('logs', []))
        self._all_lines = []
        self._ver = 0

    def set(self, text):
        self.header.markdown("<div class='cli-header'>>> %s</div>" % escape(text), unsafe_allow_html=True)

    def _should_show(self, text):
        t = text.strip()
        if not t:
            return False
        if 'ERROR' in t or 'FAIL' in t or 'error' in t.lower()[:20]:
            return True
        if any(t.startswith(p) or p in t for p in self._SHOW_PREFIXES):
            return True
        if any(t.startswith(p) for p in self._HIDE_PREFIXES):
            return False
        if '✅' in t or '✓' in t or '═' in t:
            return True
        return False

    def write(self, s):
        parts = s.splitlines(True)
        if not parts: return
        for part in parts:
            text = part.rstrip()
            if not text:
                continue
            self._all_lines.append(text)
            if self._should_show(text):
                self.lines.append('[%s] %s' % (ts_short(), text))
        ss['logs'] = list(self.lines)
        view = '\n'.join(reversed(self.lines[-500:]))
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
    ss['all_pi_features'] = {}
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
                st.write('Steps: %d' % h.get('step_count', 0))
                st.write('Status: %s' % h.get('status', 'N/A'))
                fp = Path(h.get('file_path', ''))
                if fp.exists():
                    st.download_button('Download', data=fp.read_bytes(), file_name=fp.name,
                                       key='hist_%s' % h['timestamp'].replace(' ','_').replace(':',''))
    else:
        st.sidebar.info('No history yet.')

# ================================================================
# CHALK SYNC (PI Scope mode)
# ================================================================
if ss.get('_sync_running'):
    ss['_sync_running'] = False
    ss['logs'] = []
    ss['result_path'] = None
    ss['exit_report'] = None

    _sync_scope = ss.get('_sync_scope', 'All Iterations')
    _specific_pis = ss.get('_sync_specific_pis', [])

    _sync_header = cli_header
    _sync_log = cli_log

    _sync_lines = []
    def _sync_msg(msg):
        _sync_lines.append('[%s] %s' % (ts_short(), msg))
        view = '\n'.join(reversed(_sync_lines[-200:]))
        _sync_log.markdown("<div class='cli-box'><pre>%s</pre></div>" % escape(view), unsafe_allow_html=True)

    _sync_header.markdown("<div class='cli-header'>>> Syncing from Chalk (%s)...</div>" % _sync_scope, unsafe_allow_html=True)

    try:
        _sync_msg('Launching browser...')
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
        ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = ctx.new_page()
        _sync_msg('Browser launched')

        _sync_msg('Discovering PI pages (auto-detecting new PIs)...')
        pi_links = discover_pi_links(page, log=lambda m: None)
        _new_pi_list = [(p.label, p.url) for p in pi_links]

        _old_labels = set(label for label, url in ss.get('pi_list', []))
        _new_labels = set(label for label, url in _new_pi_list)
        _added_pis = _new_labels - _old_labels
        if _added_pis:
            _sync_msg('NEW PIs detected: %s' % ', '.join(sorted(_added_pis)))

        ss['pi_list'] = _new_pi_list
        save_pi_pages(ss['pi_list'])
        _sync_msg('Found %d PIs%s' % (len(ss['pi_list']),
            ' (NEW: %s)' % ', '.join(sorted(_added_pis)) if _added_pis else ''))

        if _sync_scope == 'Specific Iteration(s)' and _specific_pis:
            _pis_to_sync = [(label, url) for label, url in ss['pi_list'] if label in _specific_pis]
            _sync_msg('Syncing %d specific PI(s): %s' % (len(_pis_to_sync), ', '.join(_specific_pis)))
        else:
            _pis_to_sync = ss['pi_list']
            _sync_msg('Syncing ALL %d PIs' % len(_pis_to_sync))

        _all = ss.get('all_pi_features', {})
        _chalk_count = 0
        _total_feats = 0
        for _pi_idx, (_pi_label, _pi_url) in enumerate(_pis_to_sync, 1):
            _sync_msg('[%d/%d] Scanning %s...' % (_pi_idx, len(_pis_to_sync), _pi_label))
            _feats = discover_features_on_pi(page, _pi_url, log=lambda m: None)
            _all[_pi_label] = _feats
            save_features(_pi_label, _feats)
            _total_feats += len(_feats)
            _sync_msg('[%d/%d] %s: %d features found' % (_pi_idx, len(_pis_to_sync), _pi_label, len(_feats)))

            _pi_chalk = 0
            for _fi, (_fid, _ftitle) in enumerate(_feats, 1):
                try:
                    _chalk = fetch_feature_from_pi(page, _pi_url, _fid, log=lambda m: None)
                    if _chalk and _chalk.scenarios:
                        save_chalk(_fid, _pi_label, _chalk)
                        _chalk_count += 1
                        _pi_chalk += 1
                except:
                    pass
            _sync_msg('[%d/%d] %s: %d features, %d with Chalk data' % (
                _pi_idx, len(_pis_to_sync), _pi_label, len(_feats), _pi_chalk))

        ss['all_pi_features'] = _all
        if ss['selected_pi'] and ss['selected_pi'] in _all:
            ss['pi_features'] = _all[ss['selected_pi']]
        else:
            ss['selected_pi'] = None
            ss['selected_pi_url'] = ''
            ss['pi_features'] = []
        ctx.close(); browser.close(); pw.stop()

        _done_msg = 'DONE: %d PIs synced | %d features | %d with Chalk data' % (
            len(_pis_to_sync), _total_feats, _chalk_count)
        if _added_pis:
            _done_msg += ' | NEW PIs added: %s' % ', '.join(sorted(_added_pis))
        _sync_msg(_done_msg)
        _sync_header.markdown("<div class='cli-header'>>> Sync complete!</div>", unsafe_allow_html=True)
        ss['logs'] = _sync_lines

    except Exception as e:
        _sync_msg('ERROR: %s' % str(e)[:200])
        _sync_header.markdown("<div class='cli-header'>>> Sync FAILED</div>", unsafe_allow_html=True)
        ss['logs'] = _sync_lines
        for _obj_name in ('ctx', 'browser', 'pw'):
            _obj = locals().get(_obj_name)
            if _obj:
                try:
                    if _obj_name == 'pw': _obj.stop()
                    else: _obj.close()
                except: pass
            try: pw.stop()
            except: pass

# ================================================================
# MAIN EXECUTION — Cabot Pipeline
# ================================================================
if run_btn:
    # Build the list of features to process
    _features_to_run = []
    if feature_ids and len(feature_ids) >= 1:
        _features_to_run = [f.strip().upper() for f in feature_ids if f.strip()]
    elif feature_id and feature_id.strip():
        _features_to_run = [feature_id.strip().upper()]

    if not _features_to_run and ss.get('_batch_default'):
        _features_to_run = [s.split(' - ')[0].strip().upper() for s in ss['_batch_default'] if s.strip()]

    if not _features_to_run:
        st.error('Please enter a Cabot Feature ID.' if not _is_manual_mode else 'Please provide a valid Jira URL with a feature ID.')
    elif not _is_manual_mode and not ss.get('selected_pi'):
        st.error('Please select a PI iteration first.')
    else:
        feature_id = _features_to_run[0]
        ss['logs'] = []
        ss['result_path'] = None
        ss['cp_path'] = None
        ss['exit_report'] = None
        ss['batch_results'] = []

        logger = LiveLog(cli_header, cli_log, cli_tools)
        if len(_features_to_run) > 1:
            logger.set('Cabot Batch: %d features | %s' % (len(_features_to_run), ss.get('selected_pi') or 'Manual Links'))
        else:
            logger.set('Cabot Pipeline: %s | %s' % (feature_id, ss.get('selected_pi') or 'Manual Links'))

        st.markdown("<script>document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>",
                    unsafe_allow_html=True)
        import streamlit.components.v1 as components
        components.html("<script>parent.document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>", height=0)

        t0 = time.time()
        exit_items = []

        with redirect_stdout(logger):
            try:
                # ── Block 0: Browser Launch ──
                pipe = Pipeline(log=logger)
                logger.set('Launching browser...')
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
                context = browser.new_context(accept_downloads=True, viewport={'width': 1920, 'height': 1080})
                page = context.new_page()
                exit_items.append('Browser launched')

                # ── BATCH LOOP ──
                _batch_count = len(_features_to_run)
                _batch_failed = []
                for _fi, _current_fid in enumerate(_features_to_run, 1):
                  try:
                    feature_id = _current_fid
                    _bp = '[%d/%d] ' % (_fi, _batch_count) if _batch_count > 1 else ''

                    # ── Block 1: Jira Fetch ──
                    logger.set('%sBlock 1: Fetching Jira %s...' % (_bp, feature_id))
                    jira_result = pipe.run('Jira_%s' % feature_id,
                        lambda fid=feature_id: block_jira_fetch(page, fid, log=logger))
                    jira = jira_result['jira']
                    att_paths = jira_result['att_paths'] if inc_attachments else []
                    exit_items.append('%s%s: Jira fetched — %s' % (_bp, feature_id, jira.summary[:40]))

                    # ── Block 2: Cabot Chalk DB Scan ──
                    chalk = None
                    if not _is_manual_mode:
                        logger.set('%sBlock 2: Cabot Chalk DB lookup %s...' % (_bp, feature_id))
                        chalk_db = pipe.run('ChalkDB_%s' % feature_id,
                            lambda fid=feature_id: block_chalk_db(fid, ss.get('selected_pi', ''), log=logger))
                        chalk = chalk_db['chalk']

                        # Block 2b: Chalk Live Fallback (only if DB missed and enabled)
                        if (not chalk or not chalk.scenarios) and inc_chalk_live:
                            logger.set('%sBlock 2b: Chalk live fetch %s...' % (_bp, feature_id))
                            chalk_live = pipe.run('ChalkLive_%s' % feature_id,
                                lambda fid=feature_id: block_chalk_live(
                                    page, fid, ss['selected_pi_url'], ss['selected_pi'], ss['pi_list'], log=logger))
                            chalk = chalk_live['chalk']
                    else:
                        # Manual mode: optional Chalk live fetch
                        _manual_chalk_url = ss.get('manual_chalk_url', '')
                        if _manual_chalk_url:
                            logger.set('%sBlock 2: Chalk live fetch from manual URL...' % _bp)
                            chalk_live = pipe.run('ChalkLive_%s' % feature_id,
                                lambda fid=feature_id, curl=_manual_chalk_url: block_chalk_live(
                                    page, fid, curl, 'Manual', [(ss.get('selected_pi', 'Manual'), curl)], log=logger))
                            chalk = chalk_live['chalk']

                    # ── Block 3: Document Parsing ──
                    _uploads = uploaded_files if _fi == 1 else None
                    parsed_docs = []
                    if att_paths or _uploads:
                        logger.set('%sBlock 3: Parsing documents...' % _bp)
                        parsed_docs = pipe.run('Docs_%s' % feature_id,
                            lambda: block_parse_docs(att_paths, _uploads, INPUTS, log=logger))

                    # ── Block 4: Cabot TC Generation ──
                    # Pipeline: extract endpoints → extract fields → extract DB refs → generate TCs
                    logger.set('%sBlock 4: Cabot TC generation %s...' % (_bp, feature_id))

                    # Build Cabot folder path and tag chain from Jira metadata
                    _jira_summary = jira.summary or feature_id
                    _parent_key = ''
                    _parent_summary = ''
                    # Try to extract parent key from Jira links
                    try:
                        for link in (getattr(jira, 'links', None) or []):
                            if isinstance(link, dict):
                                _inward = link.get('inwardIssue', {})
                                _outward = link.get('outwardIssue', {})
                                _linked = _inward or _outward
                                if _linked:
                                    _pk = _linked.get('key', '')
                                    _ps = _linked.get('fields', {}).get('summary', '')
                                    if _pk and _pk != feature_id:
                                        _parent_key = _pk
                                        _parent_summary = _ps
                                        break
                        if not _parent_key and hasattr(jira, 'parent') and jira.parent:
                            _parent_key = getattr(jira.parent, 'key', '') if hasattr(jira.parent, 'key') else str(jira.parent)
                    except Exception:
                        pass

                    # Build the Cabot folder path matching sample format:
                    # MobileIT_DevTest\In-Sprint\CABOT\MBR-26-MAY-20\Network and Provisioning Management\
                    # MOBIT2-58898 SDIT INT - Dev Placeholder (May) Cox Migration\
                    # MOBIT2-62376 Cabot Integration – Add new attributes...
                    _pi_label = ss.get('selected_pi', '') or 'Manual'
                    _folder_path = (
                        'MobileIT_DevTest\\In-Sprint\\CABOT\\%s\\'
                        'Network and Provisioning Management' % (
                            _pi_label.replace('PI-', 'MBR-26-') if _pi_label.startswith('PI-') else _pi_label
                        )
                    )
                    if _parent_key:
                        _folder_path += '\\%s %s' % (_parent_key, _parent_summary[:60] if _parent_summary else 'Parent Feature')
                    _folder_path += '\\%s %s' % (feature_id, _jira_summary[:80])

                    # Build tag chain matching sample:
                    # Network and Provisioning, Network and Provisioning Management, MBR-26-MAY-20, CHPROJECT-30818, MOBIT2-58898,MOBIT2-62376
                    _tag_parts = ['Network and Provisioning', 'Network and Provisioning Management']
                    if _pi_label.startswith('PI-'):
                        _tag_parts.append(_pi_label.replace('PI-', 'MBR-26-'))
                    if _parent_key:
                        _tag_parts.append(_parent_key)
                    _tag_parts.append(feature_id)
                    _tag_chain = ', '.join(_tag_parts)

                    def _cabot_generate(fid=feature_id, j=jira, c=chalk, pd=parsed_docs,
                                        fp=_folder_path, tc=_tag_chain, js=_jira_summary, pk=_parent_key):
                        cabot_tcs = build_cabot_test_suite(
                            jira_description=j.description or '',
                            jira_comments=j.comments or [],
                            jira_subtasks=j.subtasks or [],
                            feature_id=fid,
                            cabot_chalk_db_path=Path(__file__).parent / 'CABOT_CHALK_DB.db',
                            jira=j,
                            chalk=c,
                            parsed_docs=pd if pd else [],
                            jira_summary=js,
                            parent_key=pk,
                            folder_path=fp,
                            tag_chain=tc,
                            log=logger,
                        )
                        total_steps = sum(len(tc.steps) for tc in cabot_tcs)
                        return {'test_cases': cabot_tcs, 'total_steps': total_steps}

                    cabot_result = pipe.run('CabotTC_%s' % feature_id, _cabot_generate)
                    cabot_tcs = cabot_result['test_cases']
                    total_steps = cabot_result['total_steps']

                    if not cabot_tcs:
                        logger('[CABOT] WARNING: No test cases generated for %s — no endpoints found' % feature_id)
                        exit_items.append('%s%s: ⚠️ No TCs — no endpoints found' % (_bp, feature_id))
                        continue

                    # ── Block 5: Cabot Excel Generation ──
                    logger.set('%sBlock 5: Generating Cabot Excel %s...' % (_bp, feature_id))

                    def _cabot_excel(tcs=cabot_tcs, fid=feature_id):
                        out_path = generate_cabot_excel(
                            test_cases=tcs,
                            feature_id=fid,
                            output_dir=OUTPUTS,
                        )
                        # Also save a checkpoint copy
                        cp_path = CHECKPOINTS / ('CHECKPOINT_' + out_path.name)
                        shutil.copy2(str(out_path), str(cp_path))
                        logger('[CABOT] ✅ Saved: %s' % out_path.name)
                        logger('[CABOT] ✅ Checkpoint: %s' % cp_path.name)
                        return {'out_path': out_path, 'cp_path': cp_path}

                    excel_result = pipe.run('CabotExcel_%s' % feature_id, _cabot_excel)
                    out_path = excel_result['out_path']

                    # Log to transaction history
                    try:
                        log_generation(feature_id, ss.get('selected_pi') or 'Manual',
                                       len(cabot_tcs), total_steps, 'Cabot Pipeline', str(out_path))
                        log_generation_db(feature_id, ss.get('selected_pi') or 'Manual',
                                          len(cabot_tcs), total_steps, 'Cabot Pipeline', str(out_path))
                    except Exception:
                        pass

                    # Count endpoints for stats
                    _endpoint_count = len(cabot_tcs) // 2 if cabot_tcs else 0

                    ss['result_path'] = str(out_path)
                    ss['suite_info'] = {
                        'tc_count': len(cabot_tcs),
                        'step_count': total_steps,
                        'endpoint_count': _endpoint_count,
                    }
                    ss['last_feature_id'] = feature_id

                    ss['batch_results'].append({
                        'feature_id': feature_id,
                        'tc_count': len(cabot_tcs),
                        'step_count': total_steps,
                        'file': out_path.name,
                        'file_path': str(out_path),
                        'title': jira.summary[:60],
                    })
                    exit_items.append('%s%s: ✅ %d TCs | %s' % (_bp, feature_id, len(cabot_tcs), out_path.name))

                  except PipelineError as _pe:
                    _err_msg = '%s block "%s" failed: %s' % (feature_id, _pe.block_name, str(_pe.error_msg)[:100])
                    _batch_failed.append((feature_id, _err_msg))
                    exit_items.append('%s%s: ❌ FAILED — %s' % (_bp, feature_id, _pe.block_name))
                    logger.set('%s%s FAILED at %s — skipping to next' % (_bp, feature_id, _pe.block_name))
                    print('[BATCH] %s' % _err_msg, flush=True)

                  except Exception as _ex:
                    _err_msg = '%s: %s' % (feature_id, str(_ex)[:150])
                    _batch_failed.append((feature_id, _err_msg))
                    exit_items.append('%s%s: ❌ FAILED — %s' % (_bp, feature_id, str(_ex)[:60]))
                    logger.set('%s%s FAILED — skipping to next' % (_bp, feature_id))
                    print('[BATCH] Error on %s: %s' % (feature_id, str(_ex)[:200]), flush=True)
                    import traceback as _tb
                    _tb.print_exc()

                # ── END BATCH LOOP — close browser ──
                context.close(); browser.close(); pw.stop()

                # ── Finalize ──
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                _feat_label = '%d features' % len(_features_to_run) if len(_features_to_run) > 1 else feature_id
                logger.set('DONE: %s in %dm %ds' % (_feat_label, m, s))

                print('', flush=True)
                print('*' * 50, flush=True)
                print('***   CABOT TEST SUITE PREPARED SUCCESSFULLY   ***', flush=True)
                print('***   %s | %dm %ds                    ***' % (_feat_label, m, s), flush=True)
                print('*' * 50, flush=True)

                import streamlit.components.v1 as components
                components.html("<script>parent.document.getElementById('output').scrollIntoView({behavior:'smooth'});</script>", height=0)

                # Print pipeline timing
                print('\n' + '=' * 55, flush=True)
                print('  CABOT PIPELINE TIMING', flush=True)
                print('  %-30s %10s' % ('Block', 'Duration'), flush=True)
                print('  ' + '-' * 44, flush=True)
                for bname, bdur in pipe.get_timing_summary():
                    print('  %-30s %8.1fs' % (bname, bdur), flush=True)
                print('  ' + '-' * 44, flush=True)
                print('  %-30s %8.1fs' % ('TOTAL', elapsed), flush=True)
                print('=' * 55, flush=True)

                # Build exit report
                timing_items = ['⏱ %s: %.1fs' % (n, d) for n, d in pipe.get_timing_summary()]
                if len(_features_to_run) > 1:
                    _completed = len(ss['batch_results'])
                    _failed = len(_batch_failed)
                    _batch_summary = ['Cabot Batch: %d/%d features completed | %d failed' % (_completed, _batch_count, _failed)]
                    for br in ss['batch_results']:
                        _batch_summary.append('  ✅ %s: %d TCs | %s' % (br['feature_id'], br['tc_count'], br['file']))
                    if _batch_failed:
                        _batch_summary.append('')
                        _batch_summary.append('FAILED features:')
                        for _ff_id, _ff_err in _batch_failed:
                            _batch_summary.append('  ❌ %s: %s' % (_ff_id, _ff_err[:80]))
                    _title = 'Cabot Batch Complete — %d/%d features' % (_completed, _batch_count)
                    if _failed:
                        _title = 'Cabot Batch Partial — %d/%d completed, %d failed' % (_completed, _batch_count, _failed)
                    ss['exit_report'] = {
                        'title': _title,
                        'items': _batch_summary + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss.get('selected_pi') or 'Manual'),
                    }
                else:
                    ss['exit_report'] = {
                        'title': 'Cabot Generation Complete — %s' % feature_id,
                        'items': exit_items + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss.get('selected_pi') or 'Manual'),
                    }

                st.rerun()

            except PipelineError as pe:
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                _completed = len(ss.get('batch_results', []))
                _total = len(_features_to_run)
                logger.set('PIPELINE FAILED — %s (%d/%d completed before crash)' % (pe.block_name, _completed, _total))
                print('\n[PIPELINE ERROR] %s' % pe, flush=True)
                _progress_items = []
                if _completed > 0:
                    _progress_items.append('✅ %d features completed before failure:' % _completed)
                    for _br in ss.get('batch_results', []):
                        _progress_items.append('  %s: %d TCs | %s' % (_br['feature_id'], _br['tc_count'], _br['file']))
                    _progress_items.append('')
                ss['exit_report'] = {
                    'title': 'Cabot Generation FAILED — %s (%d/%d completed)' % (feature_id, _completed, _total),
                    'items': _progress_items + [
                        'Pipeline block "%s" failed after %d attempts.' % (pe.block_name, pe.attempts),
                        '',
                        'Please Contact Dashboard Admin with below error message:',
                        'Block: %s' % pe.block_name,
                        'Error: %s' % pe.error_msg[:200],
                    ] + exit_items,
                    'footer': 'Failed at %s | Duration: %dm %ds | %d/%d features completed' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, _completed, _total),
                }
                for _obj_name in ('context', 'browser', 'pw'):
                    _obj = locals().get(_obj_name)
                    if _obj:
                        try:
                            if _obj_name == 'pw': _obj.stop()
                            else: _obj.close()
                        except: pass
                st.rerun()

            except Exception as e:
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                _completed = len(ss.get('batch_results', []))
                _total = len(_features_to_run)
                logger.set('FAILED after %dm %ds (%d/%d completed)' % (m, s, _completed, _total))
                print('\n[ERROR] %s' % e, flush=True)
                traceback.print_exc()
                _progress_items = []
                if _completed > 0:
                    _progress_items.append('✅ %d features completed before crash:' % _completed)
                    for _br in ss.get('batch_results', []):
                        _progress_items.append('  %s: %d TCs | %s' % (_br['feature_id'], _br['tc_count'], _br['file']))
                    _progress_items.append('')
                ss['exit_report'] = {
                    'title': 'Cabot Generation FAILED — %s (%d/%d completed)' % (feature_id, _completed, _total),
                    'items': _progress_items + exit_items + ['', 'ERROR: %s' % str(e)[:200]],
                    'footer': 'Failed at %s | Duration: %dm %ds | %d/%d features completed' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, _completed, _total),
                }
                for _obj_name in ('context', 'browser', 'pw'):
                    _obj = locals().get(_obj_name)
                    if _obj:
                        try:
                            if _obj_name == 'pw': _obj.stop()
                            else: _obj.close()
                        except: pass
                st.rerun()
