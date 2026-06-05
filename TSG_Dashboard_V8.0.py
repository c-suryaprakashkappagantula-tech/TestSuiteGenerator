"""
TSG_Dashboard_V8.0.py -- Test Suite Generator Dashboard V8.0 (Data-First Engine)
STANDALONE dashboard — no V7 toggle, no shared engine logic with V7.
Uses ONLY the V8.0 Data-First Engine for test suite generation.

V8.0 = Data-First approach:
  - Dimension Extraction from all data sources
  - Smart Combination Planning (no manual matrix config)
  - TC Building with full traceability
  - Zero-Generic Validation

Usage:  streamlit run TSG_Dashboard_V8.0.py
"""
import sys, os, time, traceback, shutil, io
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
                             BROWSER_CHANNEL, BROWSER_HEADLESS, ts_short,
                             get_browser_channel)
from modules.jira_fetcher import fetch_jira_issue, download_attachments
from modules.chalk_parser import discover_pi_links, fetch_feature_from_pi, discover_features_on_pi
from modules.doc_parser import parse_file
from modules.data_first_engine import build_test_suite_v8
from modules.excel_generator import generate_excel
from modules.theme_v2 import CSS
from modules.transaction_log import log_generation, get_history
from modules.pipeline import (Pipeline, PipelineError, block_jira_fetch, block_chalk_db,
                               block_chalk_live, block_parse_docs, block_build_suite_v8,
                               block_generate_output, block_deep_mine)
from modules.database import (init_db, save_pi_pages, load_pi_pages, save_features,
                               load_features, load_all_features, get_features_count,
                               save_jira, load_jira, is_jira_stale, save_chalk, load_chalk,
                               load_chalk_as_object, get_chalk_cache_count,
                               log_generation_db, get_history_db, get_db_stats, is_data_stale,
                               save_test_suite, load_latest_suite,
                               get_all_suite_history, save_artifact_hash, check_staleness)

# ================================================================
# WINDOWS PLATFORM FIXES
# ================================================================
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
# INIT DB (also called on import, but explicit for clarity)
# ================================================================
init_db()

# ================================================================
# PAGE CONFIG
# ================================================================
st.set_page_config(
    page_title='TSG V8.0 - Data-First Test Suite Generator',
    page_icon='https://em-content.zobj.net/source/twitter/408/test-tube_1f9ea.png',
    layout='wide',
    initial_sidebar_state='collapsed',
)
st.markdown(CSS, unsafe_allow_html=True)

# Force sidebar dark theme to match main dashboard
st.markdown("""<style>
[data-testid="stSidebar"] {
    background-color: #0f172a !important;
}
[data-testid="stSidebar"] * {
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] .stMarkdown p, [data-testid="stSidebar"] .stMarkdown span {
    color: #94a3b8 !important;
}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] [data-testid="stExpander"] {
    background-color: rgba(30, 41, 59, 0.5) !important;
    border: 1px solid rgba(99, 102, 241, 0.2) !important;
    border-radius: 8px !important;
}
</style>""", unsafe_allow_html=True)

# ================================================================
# DEFAULT PI LIST
# ================================================================
CHALK_PI_BASE = 'https://chalk.charter.com/spaces/MDA/pages'

def _load_pi_pages():
    """Load PI pages from config/chalk_pi_pages.json (editable without code changes)."""
    config_path = Path(__file__).parent / 'config' / 'chalk_pi_pages.json'
    try:
        import json
        data = json.loads(config_path.read_text(encoding='utf-8'))
        base = data.get('base_url', CHALK_PI_BASE)
        return [(p['label'], f"{base}/{p['page_id']}/{p['label']}") for p in data['pages']]
    except Exception:
        # Fallback to hardcoded if JSON is missing/corrupt
        return [
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

_DEFAULT_PIS = _load_pi_pages()

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
    'input_mode': 'pi_scope',
    'manual_jira_url': '',
    'manual_chalk_url': '',
    'logs': [],
    'result_path': None,
    'cp_path': None,
    'suite_info': None,
    'exit_report': None,
    'batch_results': [],
    'v8_data_sources': None,
    'v8_combination_plan': None,
    'v8_routing_audit': None,
}
for k, v in defaults.items():
    if k not in ss:
        ss[k] = v

# ================================================================
# BANNER
# ================================================================
# Cache DB stats so every Streamlit rerun doesn't re-query SQLite
@st.cache_data(ttl=60)
def _cached_db_stats():
    return get_db_stats()

@st.cache_data(ttl=60)
def _cached_chalk_count():
    return get_chalk_cache_count()

@st.cache_data(ttl=120)
def _cached_load_all_features():
    """Load all features from DB — cached for 2 minutes so PI selection is instant."""
    return load_all_features()

_db_stats = _cached_db_stats()
_chalk_cached = _cached_chalk_count()
_db_badge = 'DB: %d features | %d cached' % (_db_stats['feature_count'], _chalk_cached) if _db_stats['feature_count'] > 0 else 'DB: empty'
st.markdown("""<div class='banner'>
  <div>
    <div class='title'>TSG V8.0 &mdash; Data-First Test Suite Generator</div>
    <div class='sub'>Data-Driven &bull; Full Traceability &bull; Zero-Generic Validation</div>
  </div>
  <div style='display:flex; gap:8px; align-items:center; flex-wrap:wrap;'>
    <div class='badge'>V8.0</div>
    <div class='badge'>Data-First</div>
    <div class='badge'>Traceability</div>
    <div class='badge'>Batch Mode</div>
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
            '🔗 Manual mode — provide Jira and/or Chalk URLs directly</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="padding:6px 14px;border-radius:8px;margin-top:4px;'
            'background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.2);'
            'font-size:12px;color:#34D399;font-weight:600;">'
            '📋 PI Scope — select from preloaded PI iterations</div>',
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
        st.markdown("<div class='sec-title'><span class='icon'>&#128279;</span> Provide Jira &amp; Chalk URLs</div>", unsafe_allow_html=True)
        st.markdown("<div class='glass'>", unsafe_allow_html=True)

        st.markdown(
            '<div style="color:#64748b;font-size:12px;margin-bottom:10px;">'
            'Provide at least one URL below. Both URLs together give the best results.</div>',
            unsafe_allow_html=True)

        _manual_jira = st.text_input(
            '🔗 Jira Feature URL',
            value=ss.get('manual_jira_url', ''),
            placeholder='https://jira.charter.com/browse/MWTGPROV-3985',
            key='manual_jira_input',
            help='Full Jira URL — feature ID will be extracted automatically')

        _manual_chalk = st.text_input(
            '🔗 Chalk Page URL (optional)',
            value=ss.get('manual_chalk_url', ''),
            placeholder='https://chalk.charter.com/spaces/MDA/pages/3281127794/PI-53',
            key='manual_chalk_input',
            help='Optional — Chalk page URL containing the feature scope/scenarios.')

        ss['manual_jira_url'] = _manual_jira
        ss['manual_chalk_url'] = _manual_chalk

        # Extract feature ID from Jira URL
        import re as _re_manual
        _manual_fid = ''
        if _manual_jira:
            _jira_match = _re_manual.search(r'([A-Z][A-Z0-9]+-\d+)', _manual_jira.upper())
            if _jira_match:
                _manual_fid = _jira_match.group(1)

        _has_jira = bool(_manual_fid)
        _has_chalk = bool(_manual_chalk and _manual_chalk.strip())

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
                    'font-size:12px;color:#FB7185;">❌ Could not extract a Jira ID from URL</div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);'
                    'font-size:12px;color:#FBBF24;">⚠️ No Jira URL — Chalk-only mode</div>',
                    unsafe_allow_html=True)
        with _vc2:
            if _has_chalk and 'chalk.charter.com' in _manual_chalk.lower():
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.2);'
                    'font-size:12px;color:#34D399;">✅ Chalk URL valid</div>',
                    unsafe_allow_html=True)
            elif _has_chalk:
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);'
                    'font-size:12px;color:#FBBF24;">⚠️ URL does not look like a Chalk page</div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);'
                    'font-size:12px;color:#FBBF24;">⚠️ No Chalk URL — Jira-only mode</div>',
                    unsafe_allow_html=True)

        # When no Jira URL but Chalk URL is provided, ask for a Feature ID
        _manual_fid_override = ''
        if _has_chalk and not _has_jira:
            _manual_fid_override = st.text_input(
                '🏷️ Feature ID (required when no Jira URL)',
                value='', placeholder='e.g. MWTGPROV-4254',
                key='manual_fid_override',
                help='Enter the Jira feature ID manually since no Jira URL was provided.')

        if (_has_jira or _has_chalk) and not (_has_jira and _has_chalk):
            _missing = 'Chalk URL' if not _has_chalk else 'Jira URL'
            _impact = ('Test suite will be built from Jira description only (no Chalk scenarios).'
                       if not _has_chalk else
                       'Test suite will be built from Chalk scenarios only (no Jira metadata/AC).')
            st.warning('⚠️ **%s not provided.** %s' % (_missing, _impact))

        st.markdown("</div>", unsafe_allow_html=True)

        # Resolve final feature ID
        _final_fid = _manual_fid or _manual_fid_override.strip().upper()
        feature_id = _final_fid
        feature_ids = [_final_fid] if _final_fid else []
        manual_mode = False
        batch_mode = False
        _sync_in_progress = False

    # ================================================================
    # PI SCOPE MODE
    # ================================================================
    if not _is_manual_mode:
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

        rc1, rc2, rc3 = st.columns([3, 1, 1])
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
        with rc3:
            refresh_jira_btn = st.button('🔄 Sync from Jira', key='refresh_jira', type='secondary', use_container_width=True)
            if refresh_jira_btn:
                ss['_jira_sync_confirm'] = True
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

            st.warning('⚠️ This will re-fetch features from Chalk and update the DB. Estimated time: %s.' % _est_time)
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

        # Jira Sync confirmation dialog
        if ss.get('_jira_sync_confirm'):
            st.markdown("---")
            st.markdown("#### 🔄 Sync from Jira")
            _jira_sync_scope = st.radio('Jira Sync Scope:', ['Current PI Only', 'All PIs'],
                                         key='jira_sync_scope', horizontal=True)
            _jira_features_to_sync = []
            if _jira_sync_scope == 'Current PI Only' and ss.get('selected_pi'):
                _jira_features_to_sync = ss.get('pi_features', [])
                _est_jira_time = '%.0f-%.0f minutes' % (len(_jira_features_to_sync) * 0.1, len(_jira_features_to_sync) * 0.3)
                st.info('Will sync %d features from %s' % (len(_jira_features_to_sync), ss['selected_pi']))
            elif _jira_sync_scope == 'All PIs':
                _all_feats = ss.get('all_pi_features', {})
                for _pi_feats in _all_feats.values():
                    _jira_features_to_sync.extend(_pi_feats)
                _est_jira_time = '%.0f-%.0f minutes' % (len(_jira_features_to_sync) * 0.1, len(_jira_features_to_sync) * 0.3)
                st.info('Will sync %d features across all PIs' % len(_jira_features_to_sync))
            else:
                st.caption('Select a PI first, or choose "All PIs".')
                _est_jira_time = 'unknown'

            st.warning('⚠️ This will re-fetch Jira data for all features and update the local DB. Estimated time: %s.' % _est_jira_time)
            _jf1, _jf2, _jf3 = st.columns([2, 1, 1])
            with _jf2:
                if st.button('Yes, Sync Jira', key='jira_sync_yes', type='primary', use_container_width=True):
                    ss['_jira_sync_confirm'] = False
                    ss['_jira_sync_running'] = True
                    ss['_jira_sync_features'] = _jira_features_to_sync
                    st.rerun()
            with _jf3:
                if st.button('Cancel', key='jira_sync_cancel', use_container_width=True):
                    ss['_jira_sync_confirm'] = False
                    st.rerun()

        # ── Block UI during sync ──
        _sync_in_progress = (ss.get('_sync_running', False) or ss.get('_sync_confirm', False) or
                             ss.get('_jira_sync_running', False) or ss.get('_jira_sync_confirm', False))

        # ── Step 2: Feature ID ──
        if _sync_in_progress:
            st.markdown("<div class='sec-title' style='opacity:0.4'><span class='icon'>&#128269;</span> Step 2: Feature ID (blocked during sync)</div>", unsafe_allow_html=True)
            st.info('⏳ Sync in progress — feature selection blocked until sync completes.')
            manual_mode = False
            batch_mode = False
            feature_id = ''
            feature_ids = []
        else:
            st.markdown("<div class='sec-title'><span class='icon'>&#128269;</span> Step 2: Feature ID</div>", unsafe_allow_html=True)

        # Load features: DB first (instant), Chalk scrape as fallback
        if not ss.get('all_pi_features'):
            db_features = _cached_load_all_features()
            if db_features and get_features_count() > 0:
                ss['all_pi_features'] = db_features
                if ss['selected_pi']:
                    ss['pi_features'] = db_features.get(ss['selected_pi'], [])
                stats = _cached_db_stats()
                chalk_count = _cached_chalk_count()
                st.caption('Loaded %d features (%d with full Chalk data) from DB cache (%dKB)' % (
                    stats['feature_count'], chalk_count, stats['db_size_kb']))
            else:
                with st.spinner('First run: fetching ALL PI features + Chalk data (one-time, cached to DB)...'):
                    import re as _re_firstrun
                    def _firstrun_page_id(url):
                        m = _re_firstrun.search(r'/pages/(\d+)/', url)
                        return m.group(1) if m else ''

                    # Try REST-first path (no browser needed)
                    _rest_ok = False
                    try:
                        _shared_p = str(Path(__file__).resolve().parent.parent / 'shared')
                        if _shared_p not in sys.path:
                            sys.path.insert(0, _shared_p)
                        from rest_clients import ChalkRestClient
                        from modules.chalk_parser import discover_features_rest, fetch_feature_rest

                        _rc = ChalkRestClient(logger_fn=lambda m: None)
                        if _rc.health_check_with_refresh():
                            _rest_ok = True
                    except Exception:
                        pass

                    try:
                        _all = {}
                        save_pi_pages(ss['pi_list'])

                        if _rest_ok:
                            # REST fast-path
                            for _pi_label, _pi_url in ss['pi_list']:
                                _pid = _firstrun_page_id(_pi_url)
                                if not _pid:
                                    continue
                                _feats = discover_features_rest(_pid, log=lambda m: None)
                                if _feats is None:
                                    continue
                                _all[_pi_label] = _feats
                                save_features(_pi_label, _feats)
                                for _fid, _ftitle in _feats:
                                    try:
                                        _chalk = fetch_feature_rest(_pid, _fid, log=lambda m: None)
                                        if _chalk and _chalk.scenarios:
                                            save_chalk(_fid, _pi_label, _chalk)
                                    except:
                                        pass
                        else:
                            # Browser fallback
                            _pw = sync_playwright().start()
                            _br = _pw.chromium.launch(headless=True, channel=get_browser_channel())
                            _cx = _br.new_context(viewport={'width': 1920, 'height': 1080})
                            _pg = _cx.new_page()
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
                            _cx.close(); _br.close(); _pw.stop()

                        ss['all_pi_features'] = _all
                        if ss['selected_pi']:
                            ss['pi_features'] = _all.get(ss['selected_pi'], [])
                        st.rerun()
                    except Exception as _e:
                        st.error('Feature fetch failed: %s. Use Manual mode or run preload_db.py first.' % _e)
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
            options_list = ['%s - %s' % (fid, title) for fid, title in ss['pi_features']]
            _ba1, _ba2 = st.columns([1, 1])
            with _ba1:
                if st.button('Select All', key='batch_select_all', use_container_width=True):
                    ss['_batch_default'] = list(options_list)
                    st.rerun()
            with _ba2:
                if st.button('Clear All', key='batch_clear_all', use_container_width=True):
                    ss['_batch_default'] = []
                    st.rerun()
            _default = ss.get('_batch_default', [])
            _default = [d for d in _default if d in options_list]
            selected_multi = st.multiselect(
                'Select Features (%d available in %s)' % (len(ss['pi_features']), ss['selected_pi']),
                options=options_list, default=_default, key='feature_multiselect')
            feature_ids = [s.split(' - ')[0].strip() for s in selected_multi]
            feature_id = feature_ids[0] if feature_ids else ''
        elif batch_mode:
            feature_id = st.text_input('Jira Feature IDs (comma-separated)', value='',
                placeholder='e.g. MWTGPROV-4254, MWTGPROV-3949')
            if feature_id:
                feature_ids = [f.strip().upper() for f in feature_id.split(',') if f.strip()]
                feature_id = feature_ids[0] if feature_ids else ''
        elif ss['feature_mode'] == 'manual' or not ss['pi_features']:
            feature_id = st.text_input('Jira Feature ID', value='', placeholder='e.g. MWTGPROV-4254')
        else:
            options_list = ['-- Select a Feature --'] + [
                '%s - %s' % (fid, title) for fid, title in ss['pi_features']
            ]
            selected = st.selectbox(
                'Available Features (%d found in %s)' % (len(ss['pi_features']), ss['selected_pi']),
                options=options_list, key='feature_dropdown')
            if selected and selected != '-- Select a Feature --':
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
            st.caption('Batch mode: %d features selected' % len(feature_ids))
        elif ss['pi_features'] and ss['feature_mode'] != 'manual':
            st.caption('%d features available in %s' % (len(ss['pi_features']), ss['selected_pi']))

    # ── Step 3: Custom Instructions (V8 doesn't need matrix/strategy config) ──
    st.markdown("<div class='sec-title'><span class='icon'>&#9881;</span> Step 3: Custom Instructions (Optional)</div>", unsafe_allow_html=True)
    st.markdown("<div class='glass'>", unsafe_allow_html=True)

    st.markdown(
        '<div style="color:#64748b;font-size:12px;margin-bottom:10px;">'
        'V8.0 automatically extracts dimensions from your data sources. '
        'Use custom instructions to focus or constrain the generated suite.</div>',
        unsafe_allow_html=True)

    use_custom = st.checkbox('Enable Custom Instructions', key='custom_instructions_toggle')
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
        ]
        selected_suggestion = st.selectbox(
            'Pick a preset instruction:',
            options=['-- Select --'] + suggestions,
            key='custom_suggestion_dropdown')
        custom_instructions = st.text_area('Or type your own instructions:', value='', height=100,
            placeholder='Type your instructions here...\ne.g. Focus on eSIM Mobile 5G, add rollback scenarios')
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
    bc1, bc2, bc3, bc4 = st.columns([1.5, 0.5, 0.5, 0.5])
    with bc1:
        run_btn = st.button('🚀 Execute - Generate Test Suite (V8)', type='primary', use_container_width=True)
    with bc2:
        clear_btn = st.button('Clear All', use_container_width=True)
    with bc3:
        reload_btn = st.button('Reload', use_container_width=True)
    with bc4:
        history_btn = st.button('History', use_container_width=True)

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
        # Use same structured format as LiveLog for consistency
        _static_lines = []
        for _line in reversed(ss['logs'][-80:]):
            _t = _line.strip()
            if not _t:
                continue
            _color = '#94a3b8'
            _icon = '•'
            if 'ERROR' in _t or 'FAIL' in _t:
                _color = '#f87171'; _icon = '❌'
            elif '✅' in _t or 'OK' in _t or 'DONE' in _t or 'PASSED' in _t:
                _color = '#34d399'; _icon = '✅'
            elif 'WARNING' in _t or '⚠️' in _t:
                _color = '#fbbf24'; _icon = '⚠️'
            elif '[V8-ENGINE]' in _t or '[V8]' in _t:
                _color = '#a78bfa'; _icon = '🔧'
            elif '[PIPELINE]' in _t:
                _color = '#60a5fa'; _icon = '⚡'
            elif '[EXCEL]' in _t:
                _color = '#34d399'; _icon = '📊'
            elif '[NMNO' in _t:
                _color = '#fb923c'; _icon = '🗄️'
            elif '═' in _t:
                _static_lines.append('<div style="color:#4f46e5;font-size:10px;margin:4px 0;">%s</div>' % ('─' * 50))
                continue
            _static_lines.append(
                '<div style="padding:2px 0;color:%s;font-size:12px;font-family:monospace;">%s %s</div>' % (
                    _color, _icon, escape(_t)))
        _html = ('<div style="background:#0f172a;border:1px solid rgba(99,102,241,0.2);'
                 'border-radius:12px;padding:16px;max-height:450px;overflow-y:auto;">'
                 + ''.join(_static_lines) + '</div>')
        cli_log.markdown(_html, unsafe_allow_html=True)

    # ── Output Panel ──
    st.markdown("<div id='output'></div>", unsafe_allow_html=True)
    st.markdown("<div class='sec-title'><span class='icon'>&#128230;</span> Output</div>", unsafe_allow_html=True)
    output_area = st.container()

    if ss.get('result_path') and Path(ss['result_path']).exists():
        with output_area:
            info = ss.get('suite_info', {})
            _grounding_pct = info.get('grounding_pct', -1)
            _grounding_badge = info.get('grounding_badge', '')
            _grounding_display = ('%s %g%%' % (_grounding_badge, _grounding_pct)) if _grounding_pct >= 0 else 'V8'
            _grounding_color = (
                'linear-gradient(90deg,#22c55e,#10b981)' if _grounding_pct >= 80
                else 'linear-gradient(90deg,#f59e0b,#f97316)' if _grounding_pct >= 60
                else 'linear-gradient(90deg,#ef4444,#dc2626)' if _grounding_pct >= 0
                else 'linear-gradient(90deg,#f59e0b,#f97316)'
            )
            st.markdown("""<div class='stats-row'>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#8b5cf6,#6366f1);'>
                    <div class='icon'>&#128203;</div>
                    <div class='label'>Test Cases</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#3b82f6,#06b6d4);'>
                    <div class='icon'>&#128221;</div>
                    <div class='label'>Steps</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: linear-gradient(90deg,#22c55e,#10b981);'>
                    <div class='icon'>&#128196;</div>
                    <div class='label'>Data Sources</div><div class='value'>%d</div></div>
                <div class='stat-card' style='--accent: %s;'>
                    <div class='icon'>&#127919;</div>
                    <div class='label'>Grounding</div><div class='value'>%s</div></div>
            </div>""" % (info.get('tc_count', 0), info.get('step_count', 0),
                         info.get('data_source_count', 0), _grounding_color, _grounding_display),
            unsafe_allow_html=True)

            # ── Auto-diff changelog ──
            _diff = info.get('diff_report')
            if _diff and (_diff.get('new', 0) + _diff.get('changed', 0) + _diff.get('removed', 0)) > 0:
                _diff_parts = []
                if _diff.get('new'): _diff_parts.append('**+%d new**' % _diff['new'])
                if _diff.get('changed'): _diff_parts.append('~%d changed' % _diff['changed'])
                if _diff.get('removed'): _diff_parts.append('-%d removed' % _diff['removed'])
                st.info('📊 vs previous run: %s | %d unchanged' % (
                    ', '.join(_diff_parts), _diff.get('matched', 0)))

            # ── Coverage Scorecard risk badge ──
            _sc_risk = info.get('scorecard_risk', '')
            _sc_badge = info.get('scorecard_badge', '')
            if _sc_risk:
                _sc_color = {'HIGH': 'error', 'MEDIUM': 'warning', 'LOW': 'success'}.get(_sc_risk, 'info')
                if _sc_color == 'error':
                    st.error('%s Coverage Risk: %s — see Coverage Scorecard sheet in Excel' % (_sc_badge, _sc_risk))
                elif _sc_color == 'warning':
                    st.warning('%s Coverage Risk: %s — see Coverage Scorecard sheet in Excel' % (_sc_badge, _sc_risk))
                else:
                    st.success('%s Coverage Risk: %s' % (_sc_badge, _sc_risk))

            _dl_c1, _dl_c2 = st.columns([2, 1])
            with _dl_c1:
                st.download_button('📥 Download: %s (%d TCs)' % (
                        ss.get('last_feature_id', 'Suite'), info.get('tc_count', 0)),
                    data=Path(ss['result_path']).read_bytes(),
                    file_name=Path(ss['result_path']).name,
                    use_container_width=True, key='dl_main')
            with _dl_c2:
                if ss.get('logs'):
                    _cli_text = '\n'.join(ss['logs'])
                    _log_fn = 'TSG_V8_CLI_Log_%s_%s.txt' % (
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
                st.markdown("**All generated suites:**")
                for _bi, _br in enumerate(ss['batch_results']):
                    _bp = Path(_br.get('file_path', _br.get('path', '')))
                    if _bp.exists():
                        st.download_button(
                            '📥 %s — %d TCs' % (_br['feature_id'], _br['tc_count']),
                            data=_bp.read_bytes(),
                            file_name=_bp.name,
                            use_container_width=True,
                            key='dl_batch_%d' % _bi)

    # ── V8 Routing Classification Badge ──
    if ss.get('v8_routing_audit'):
        ra = ss['v8_routing_audit']
        _cls = ra['classification']
        _conf = ra['confidence']
        _badge_colors = {
            'api': ('#3b82f6', '🔌'),
            'ui': ('#8b5cf6', '🖥️'),
            'hybrid': ('#f59e0b', '🔀'),
        }
        _color, _icon = _badge_colors.get(_cls, ('#64748b', '❓'))
        _conf_pct = int(_conf * 100)
        st.markdown("""<div style='background:linear-gradient(135deg,%s22,%s11);border:1px solid %s44;
            border-radius:8px;padding:10px 16px;margin:8px 0;display:flex;align-items:center;gap:12px;'>
            <span style='font-size:20px;'>%s</span>
            <div>
                <span style='color:%s;font-weight:600;font-size:14px;text-transform:uppercase;'>%s</span>
                <span style='color:#94a3b8;font-size:12px;margin-left:8px;'>Confidence: %d%%</span>
            </div>
            <div style='margin-left:auto;display:flex;gap:16px;font-size:12px;color:#94a3b8;'>
                <span>API TCs: <b style='color:#3b82f6;'>%d</b></span>
                <span>UI TCs: <b style='color:#8b5cf6;'>%d</b></span>
                <span>Negative: <b style='color:#ef4444;'>%d</b></span>
            </div>
        </div>""" % (_color, _color, _color, _icon, _color, _cls, _conf_pct,
                     ra['api_tcs_generated'], ra['ui_tcs_generated'], ra['negative_tcs_generated']),
        unsafe_allow_html=True)

    # ── V8 Data Sources Expander ──
    if ss.get('v8_data_sources'):
        with st.expander('📊 Data Sources Inventory', expanded=False):
            inv = ss['v8_data_sources']
            st.markdown('**Total testable items:** %d' % inv.get('total_testable_items', 0))
            if inv.get('sources'):
                for src in inv['sources']:
                    _status_icon = '✅' if src['status'] == 'success' else ('⚠️' if src['status'] == 'partial' else '❌')
                    st.markdown('%s **%s** (%s) — %d items' % (
                        _status_icon, src['source_name'], src['source_type'], src['items_extracted']))
                    if src.get('items_detail'):
                        for detail in src['items_detail'][:5]:
                            st.caption('  • %s' % detail)
            if inv.get('warnings'):
                st.markdown('---')
                st.markdown('**Warnings:**')
                for w in inv['warnings']:
                    st.warning(w)
            if inv.get('gaps'):
                st.markdown('**Gaps (empty sources):**')
                for g in inv['gaps']:
                    st.caption('• %s' % g)

    # ── V8 Combination Plan Expander ──
    if ss.get('v8_combination_plan'):
        with st.expander('🧮 Combination Plan', expanded=False):
            plan = ss['v8_combination_plan']
            st.markdown('**Planned TCs:** %d' % plan.get('total_planned_tcs', 0))
            if plan.get('independent_dimensions'):
                st.markdown('**Independent Dimensions:** %d' % len(plan['independent_dimensions']))
                for dim in plan['independent_dimensions']:
                    st.caption('• %s: %s' % (dim['name'], ', '.join(dim['values'][:8])))
            if plan.get('crossed_dimensions'):
                st.markdown('**Crossed Dimensions:** %d pairs' % len(plan['crossed_dimensions']))
                for pair in plan['crossed_dimensions']:
                    st.caption('• %s × %s' % (pair[0], pair[1]))
            if plan.get('reduction_notes'):
                st.markdown('---')
                st.markdown('**Reduction Notes:**')
                for note in plan['reduction_notes']:
                    st.caption('• %s' % note)

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
    """Smart CLI logger — structured, beautified output with icons and color coding."""

    _SHOW_PREFIXES = ['[PIPELINE]', '[V8-ENGINE]', '[ERROR]', '[WARN]', 'Block ', 'DONE', 'FAILED',
                       'Batch:', '[EXCEL]', '[JIRA] Found', '[JIRA]   Epic child',
                       '[JIRA] 🔍 Fetching epic', '[V8]']
    _HIDE_PREFIXES = ['[JIRA]   ', '[CHALK] Step', '[TIME]', '[INIT]', '[DOC]', 'DEBUG',
                       '[CUSTOM]', '[AUDIT]   ', '[DIM-EXTRACT]', '[COMBINE]', '[TC-BUILD]',
                       '[VALIDATE]', '[V8-CUSTOM]']

    # Icon mapping for structured display
    _ICONS = {
        'PIPELINE': '⚡',
        'V8-ENGINE': '🔧',
        'V8': '🔧',
        'EXCEL': '📊',
        'JIRA': '🎫',
        'CHALK': '📝',
        'ERROR': '❌',
        'WARN': '⚠️',
        'DONE': '✅',
        'FAILED': '❌',
    }

    def __init__(self, header_ph, log_ph, tools_ph):
        self.header = header_ph
        self.log_ph = log_ph
        self.tools = tools_ph
        self.lines = list(ss.get('logs', []))
        self._all_lines = []
        self._ver = 0

    def set(self, text):
        self.header.markdown(
            "<div style='padding:8px 16px;border-radius:8px;background:rgba(99,102,241,0.15);"
            "border:1px solid rgba(99,102,241,0.3);margin-bottom:8px;'>"
            "<span style='color:#a5b4fc;font-weight:700;'>▶</span> "
            "<span style='color:#e2e8f0;font-weight:600;font-size:13px;'>%s</span></div>" % escape(text),
            unsafe_allow_html=True)

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

    def _format_line(self, text):
        """Format a log line with icon and color based on content."""
        t = text.strip()

        # Determine icon and color
        icon = '•'
        color = '#94a3b8'  # default gray

        if '═' in t:
            return '<div style="color:#4f46e5;font-size:10px;margin:4px 0;">%s</div>' % ('─' * 50)

        for prefix, ic in self._ICONS.items():
            if prefix in t:
                icon = ic
                break

        # Color coding
        if 'ERROR' in t or 'FAIL' in t or '❌' in t:
            color = '#f87171'  # red
        elif '✅' in t or 'OK' in t or 'DONE' in t or 'PASSED' in t:
            color = '#34d399'  # green
        elif 'WARNING' in t or '⚠️' in t:
            color = '#fbbf24'  # yellow
        elif 'Block' in t and 'attempt' in t:
            color = '#818cf8'  # indigo
        elif '[V8-ENGINE]' in t or '[V8]' in t:
            color = '#a78bfa'  # purple
        elif '[PIPELINE]' in t:
            color = '#60a5fa'  # blue
        elif '[EXCEL]' in t:
            color = '#34d399'  # green
        elif '[JIRA]' in t:
            color = '#fb923c'  # orange

        # Clean up the text - remove redundant prefixes for cleaner display
        display = t
        for prefix in ['[PIPELINE] ', '[V8-ENGINE] ', '[V8] ', '[EXCEL] ', '[JIRA] ']:
            if display.startswith(prefix):
                display = display[len(prefix):]
                break

        return '<div style="padding:2px 0;color:%s;font-size:12px;font-family:\'JetBrains Mono\',monospace;">%s %s</div>' % (
            color, icon, escape(display))

    def write(self, s):
        parts = s.splitlines(True)
        if not parts:
            return
        for part in parts:
            text = part.rstrip()
            if not text:
                continue
            self._all_lines.append(text)
            if self._should_show(text):
                self.lines.append(text)

        ss['logs'] = list(self.lines)

        # Build structured HTML output (most recent first)
        html_lines = []
        for line in reversed(self.lines[-80:]):
            html_lines.append(self._format_line(line))

        html = (
            "<div style='background:#0f172a;border:1px solid rgba(99,102,241,0.2);"
            "border-radius:12px;padding:16px;max-height:450px;overflow-y:auto;"
            "font-family:\"JetBrains Mono\",\"Fira Code\",monospace;'>"
            + ''.join(html_lines) +
            "</div>"
        )
        self.log_ph.markdown(html, unsafe_allow_html=True)
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

if history_btn:
    hist = get_history()
    st.sidebar.title('Generation History (V8)')
    if hist:
        for h in hist[:20]:
            with st.sidebar.expander('%s | %s | %d TCs' % (h['timestamp'][:16], h['feature_id'], h['tc_count'])):
                st.write('PI: %s' % h.get('pi', 'N/A'))
                st.write('Engine: V8.0 Data-First')
                st.write('Steps: %d' % h.get('step_count', 0))
                st.write('Status: %s' % h.get('status', 'N/A'))
                fp = Path(h.get('file_path', ''))
                if fp.exists():
                    st.download_button('Download', data=fp.read_bytes(), file_name=fp.name,
                                       key='hist_%s' % h['timestamp'].replace(' ','_').replace(':',''))
    else:
        st.sidebar.info('No history yet.')

# ================================================================
# CHALK SYNC EXECUTION
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
        time.sleep(0.05)  # Yield to Streamlit renderer so UI updates are visible

    _sync_header.markdown("<div class='cli-header'>>> Syncing from Chalk (%s)...</div>" % _sync_scope, unsafe_allow_html=True)

    # ── Helper: extract page_id from Chalk URL ──
    import re as _re_sync
    def _sync_extract_page_id(url):
        m = _re_sync.search(r'/pages/(\d+)/', url)
        return m.group(1) if m else ''

    # ── REST-FIRST SYNC PATH ──
    _rest_available = False
    try:
        _shared_path = str(Path(__file__).resolve().parent.parent / 'shared')
        if _shared_path not in sys.path:
            sys.path.insert(0, _shared_path)
        from rest_clients import ChalkRestClient
        from modules.chalk_parser import discover_features_rest, fetch_feature_rest

        _rest_client = ChalkRestClient(logger_fn=_sync_msg)
        if _rest_client.health_check_with_refresh():
            _rest_available = True
            _sync_msg('✅ REST API available — using fast-path (no browser needed)')
        else:
            _sync_msg('⚠️ REST health check failed — falling back to browser')
    except Exception as _rest_err:
        _sync_msg(f'⚠️ REST client unavailable ({_rest_err}) — using browser')

    try:
        if _rest_available:
            # ── REST SYNC (no browser) ──
            # Discover PI pages via REST
            _sync_msg('Discovering PI pages via REST...')
            _pi_pages = _rest_client.discover_pi_pages(pi_range=range(46, 60))
            if _pi_pages:
                _new_pi_list = [(p.label, p.url) for p in _pi_pages]
                _old_labels = set(label for label, url in ss.get('pi_list', []))
                _new_labels = set(label for label, url in _new_pi_list)
                _added_pis = _new_labels - _old_labels
                if _added_pis:
                    _sync_msg('NEW PIs detected: %s' % ', '.join(sorted(_added_pis)))
                ss['pi_list'] = _new_pi_list
                save_pi_pages(ss['pi_list'])
                _sync_msg('Found %d PIs via REST%s' % (len(ss['pi_list']),
                    ' (NEW: %s)' % ', '.join(sorted(_added_pis)) if _added_pis else ''))
            else:
                _sync_msg('REST PI discovery returned empty — using existing PI list')

            if _sync_scope == 'Specific Iteration(s)' and _specific_pis:
                _pis_to_sync = [(label, url) for label, url in ss['pi_list'] if label in _specific_pis]
                _sync_msg('Syncing %d specific PI(s): %s' % (len(_pis_to_sync), ', '.join(_specific_pis)))
            else:
                _pis_to_sync = ss['pi_list']
                _sync_msg('Syncing ALL %d PIs via REST' % len(_pis_to_sync))

            _all = ss.get('all_pi_features', {})
            _chalk_count = 0
            _total_feats = 0
            for _pi_idx, (_pi_label, _pi_url) in enumerate(_pis_to_sync, 1):
                _page_id = _sync_extract_page_id(_pi_url)
                if not _page_id:
                    _sync_msg('[%d/%d] %s: ⚠️ No page_id — skipping' % (_pi_idx, len(_pis_to_sync), _pi_label))
                    continue

                _sync_msg('[%d/%d] %s (REST)...' % (_pi_idx, len(_pis_to_sync), _pi_label))
                _feats = discover_features_rest(_page_id, log=_sync_msg)
                if _feats is None:
                    _sync_msg('[%d/%d] %s: REST feature discovery failed — skipping' % (_pi_idx, len(_pis_to_sync), _pi_label))
                    continue

                _all[_pi_label] = _feats
                save_features(_pi_label, _feats)
                _total_feats += len(_feats)
                _sync_msg('[%d/%d] %s: Found %d features — fetching Chalk data...' % (_pi_idx, len(_pis_to_sync), _pi_label, len(_feats)))

                _pi_chalk = 0
                for _fi, (_fid, _ftitle) in enumerate(_feats, 1):
                    try:
                        _chalk = fetch_feature_rest(_page_id, _fid, log=lambda m: None)
                        if _chalk and _chalk.scenarios:
                            save_chalk(_fid, _pi_label, _chalk)
                            _chalk_count += 1
                            _pi_chalk += 1
                            if _fi % 3 == 0 or _fi == len(_feats):
                                _sync_msg('[%d/%d] %s: %d/%d features processed...' % (_pi_idx, len(_pis_to_sync), _pi_label, _fi, len(_feats)))
                    except:
                        pass
                _sync_msg('[%d/%d] %s: %d features, %d with Chalk data ✅' % (
                    _pi_idx, len(_pis_to_sync), _pi_label, len(_feats), _pi_chalk))

            ss['all_pi_features'] = _all
            if ss['selected_pi'] and ss['selected_pi'] in _all:
                ss['pi_features'] = _all[ss['selected_pi']]
            else:
                ss['selected_pi'] = None
                ss['selected_pi_url'] = ''
                ss['pi_features'] = []

            _done_msg = 'DONE (REST): %d PIs synced | %d features | %d with Chalk data' % (
                len(_pis_to_sync), _total_feats, _chalk_count)
            _sync_msg(_done_msg)
            _sync_header.markdown("<div class='cli-header'>>> Sync complete! (REST fast-path)</div>", unsafe_allow_html=True)
            ss['logs'] = _sync_lines

        else:
            # ── BROWSER FALLBACK SYNC ──
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

# ================================================================
# JIRA SYNC EXECUTION
# ================================================================
if ss.get('_jira_sync_running'):
    ss['_jira_sync_running'] = False
    ss['logs'] = []
    ss['result_path'] = None
    ss['exit_report'] = None

    _jira_features = ss.get('_jira_sync_features', [])
    _jsync_header = cli_header
    _jsync_log = cli_log

    _jsync_lines = []
    def _jsync_msg(msg):
        _jsync_lines.append('[%s] %s' % (ts_short(), msg))
        view = '\n'.join(reversed(_jsync_lines[-200:]))
        _jsync_log.markdown("<div class='cli-box'><pre>%s</pre></div>" % escape(view), unsafe_allow_html=True)
        time.sleep(0.05)  # Yield to Streamlit renderer so UI updates are visible

    _jsync_header.markdown("<div class='cli-header'>>> Syncing Jira data for %d features...</div>" % len(_jira_features), unsafe_allow_html=True)

    # ── REST-FIRST JIRA SYNC ──
    _jira_rest_available = False
    try:
        _shared_p_jira = str(Path(__file__).resolve().parent.parent / 'shared')
        if _shared_p_jira not in sys.path:
            sys.path.insert(0, _shared_p_jira)
        from rest_clients import JiraRestClient
        from modules.jira_fetcher import fetch_jira_issue_rest

        _jira_rc = JiraRestClient(logger_fn=_jsync_msg)
        if _jira_rc.health_check():
            _jira_rest_available = True
            _jsync_msg('✅ Jira REST API available — using fast-path (no browser needed)')
        else:
            _jsync_msg('⚠️ Jira REST health check failed — falling back to browser')
    except Exception as _jira_rest_err:
        _jsync_msg(f'⚠️ Jira REST client unavailable ({_jira_rest_err}) — using browser')

    try:
        _jira_ok = 0
        _jira_fail = 0
        _jira_total = len(_jira_features)
        _jira_rest_failures = []  # Features that failed REST, need browser

        if _jira_rest_available:
            # ── REST SYNC (no browser) ──
            for _ji, (_jfid, _jtitle) in enumerate(_jira_features, 1):
                try:
                    _jsync_msg('[%d/%d] Fetching Jira (REST): %s — %s' % (_ji, _jira_total, _jfid, _jtitle[:40]))
                    jira = fetch_jira_issue_rest(_jfid, log=lambda m: None)
                    if jira and jira.summary:
                        save_jira(jira)
                        # Backfill feature title from Jira summary if Chalk gave us "(no title)"
                        if not _jtitle or _jtitle in ('(no title)', '(no title found on page)'):
                            save_features(ss.get('selected_pi') or '', [(_jfid, jira.summary[:120])])
                            ss['all_pi_features'] = None  # invalidate cache so dropdown refreshes
                            _cached_load_all_features.clear()
                        _jsync_msg('[%d/%d] ✅ %s (REST)' % (_ji, _jira_total, _jfid))
                        _jira_ok += 1
                    else:
                        _jsync_msg('[%d/%d] ⚠️ %s: REST returned empty — queued for browser' % (_ji, _jira_total, _jfid))
                        _jira_rest_failures.append((_jfid, _jtitle))
                except Exception as _je:
                    _jsync_msg('[%d/%d] ⚠️ %s: REST failed — queued for browser' % (_ji, _jira_total, _jfid))
                    _jira_rest_failures.append((_jfid, _jtitle))

            # Browser fallback for REST failures
            if _jira_rest_failures:
                _jsync_msg(f'Launching browser for {len(_jira_rest_failures)} REST failures...')
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
                ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
                page = ctx.new_page()

                for _ji2, (_jfid2, _jtitle2) in enumerate(_jira_rest_failures, 1):
                    try:
                        _jsync_msg('[%d/%d] Fetching Jira (browser): %s' % (_ji2, len(_jira_rest_failures), _jfid2))
                        jira = fetch_jira_issue(page, _jfid2, log=lambda m: None)
                        save_jira(jira)
                        if jira and jira.summary and (not _jtitle2 or _jtitle2 in ('(no title)', '(no title found on page)')):
                            save_features(ss.get('selected_pi') or '', [(_jfid2, jira.summary[:120])])
                            ss['all_pi_features'] = None
                            _cached_load_all_features.clear()
                        _jsync_msg('[%d/%d] ✅ %s (browser)' % (_ji2, len(_jira_rest_failures), _jfid2))
                        _jira_ok += 1
                    except Exception as _je2:
                        _jsync_msg('[%d/%d] ❌ %s: FAILED — %s' % (_ji2, len(_jira_rest_failures), _jfid2, str(_je2)[:80]))
                        _jira_fail += 1

                ctx.close(); browser.close(); pw.stop()
            else:
                _jira_fail = 0

            _method = 'REST' if not _jira_rest_failures else 'REST + Browser'
        else:
            # ── BROWSER-ONLY SYNC ──
            _jsync_msg('Launching browser for Jira fetch...')
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
            ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = ctx.new_page()
            _jsync_msg('Browser launched')

            for _ji, (_jfid, _jtitle) in enumerate(_jira_features, 1):
                try:
                    _jsync_msg('[%d/%d] Fetching Jira: %s — %s' % (_ji, _jira_total, _jfid, _jtitle[:40]))
                    jira = fetch_jira_issue(page, _jfid, log=lambda m: None)
                    save_jira(jira)
                    if jira and jira.summary and (not _jtitle or _jtitle in ('(no title)', '(no title found on page)')):
                        save_features(ss.get('selected_pi') or '', [(_jfid, jira.summary[:120])])
                        ss['all_pi_features'] = None
                        _cached_load_all_features.clear()
                    _jsync_msg('[%d/%d] ✅ %s' % (_ji, _jira_total, _jfid))
                    _jira_ok += 1
                except Exception as _je:
                    _jsync_msg('[%d/%d] ❌ %s: FAILED — %s' % (_ji, _jira_total, _jfid, str(_je)[:80]))
                    _jira_fail += 1

            ctx.close(); browser.close(); pw.stop()
            _method = 'Browser'

        _jsync_msg('JIRA SYNC COMPLETE (%s): %d/%d succeeded | %d failed' % (_method, _jira_ok, _jira_total, _jira_fail))
        _jsync_header.markdown("<div class='cli-header'>>> Jira sync complete! %d/%d features (%s)</div>" % (_jira_ok, _jira_total, _method), unsafe_allow_html=True)
        ss['logs'] = _jsync_lines

    except Exception as e:
        _jsync_msg('ERROR: %s' % str(e)[:200])
        _jsync_header.markdown("<div class='cli-header'>>> Jira Sync FAILED</div>", unsafe_allow_html=True)
        ss['logs'] = _jsync_lines
        for _obj_name in ('ctx', 'browser', 'pw'):
            _obj = locals().get(_obj_name)
            if _obj:
                try:
                    if _obj_name == 'pw': _obj.stop()
                    else: _obj.close()
                except: pass

# ================================================================
# MAIN EXECUTION — V8 Data-First Engine
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
        st.error('Please enter a Feature ID.' if not _is_manual_mode else 'Please provide at least one URL (Jira or Chalk) to proceed.')
    elif not _is_manual_mode and not ss.get('selected_pi'):
        st.error('Please select a PI iteration first.')
    elif _is_manual_mode and not ss.get('manual_jira_url', '').strip() and not ss.get('manual_chalk_url', '').strip():
        st.error('Please provide at least one URL — either Jira or Chalk — to generate a test suite.')
    elif _is_manual_mode and not _features_to_run:
        st.error('Please provide a Feature ID.')
    else:
        feature_id = _features_to_run[0]
        ss['logs'] = []
        ss['result_path'] = None
        ss['cp_path'] = None
        ss['exit_report'] = None
        ss['batch_results'] = []
        ss['v8_data_sources'] = None
        ss['v8_combination_plan'] = None
        ss['v8_routing_audit'] = None

        logger = LiveLog(cli_header, cli_log, cli_tools)
        if len(_features_to_run) > 1:
            logger.set('Batch: %d features | V8 Data-First | %s' % (len(_features_to_run), ss.get('selected_pi') or 'Manual Links'))
        else:
            logger.set('Starting V8: %s | %s' % (feature_id, ss.get('selected_pi') or 'Manual Links'))

        st.markdown("<script>document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>",
                    unsafe_allow_html=True)
        import streamlit.components.v1 as components
        components.html("<script>parent.document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>", height=0)

        t0 = time.time()
        exit_items = []

        with redirect_stdout(logger):
            try:
                # ── Block 0: REST health check — browser only opened if needed ──
                pipe = Pipeline(log=logger)

                # Check if REST is available (Chalk + Jira)
                _rest_ok = False
                try:
                    _shared_p = str(Path(__file__).resolve().parent.parent / 'shared')
                    if _shared_p not in sys.path:
                        sys.path.insert(0, _shared_p)
                    from rest_clients import ChalkRestClient as _CRC, JiraRestClient as _JRC
                    _rest_ok = _CRC(logger_fn=lambda m: None).health_check_with_refresh()
                    if not _rest_ok:
                        _rest_ok = _JRC(logger_fn=lambda m: None).health_check()
                except Exception:
                    pass

                # Open browser only if REST is unavailable (cache miss scenario)
                pw = None
                browser = None
                context = None
                page = None
                if not _rest_ok:
                    logger.set('REST unavailable — launching browser...')
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch(headless=not headed, channel=get_browser_channel())
                    context = browser.new_context(accept_downloads=True, viewport={'width': 1920, 'height': 1080})
                    page = context.new_page()
                    exit_items.append('Browser launched (REST unavailable)')
                else:
                    logger.set('REST available — browser-free mode')
                    exit_items.append('REST mode (no browser)')

                # ── BATCH LOOP ──
                _batch_count = len(_features_to_run)
                _batch_failed = []
                _batch_skipped = []
                for _fi, _current_fid in enumerate(_features_to_run, 1):
                  try:
                    feature_id = _current_fid
                    _bp = '[%d/%d] ' % (_fi, _batch_count) if _batch_count > 1 else ''

                    # Block 1: Jira Fetch
                    _has_jira_url = bool(ss.get('manual_jira_url', '').strip()) if _is_manual_mode else True
                    if _has_jira_url:
                        logger.set('%sBlock 1: Fetching Jira %s...' % (_bp, feature_id))
                        jira_result = pipe.run('Jira_%s' % feature_id,
                            lambda fid=feature_id: block_jira_fetch(page, fid, log=logger))
                        jira = jira_result['jira']
                        att_paths = jira_result['att_paths'] if inc_attachments else []
                        exit_items.append('%s%s: Jira fetched — %s' % (_bp, feature_id, jira.summary[:40]))
                    else:
                        from modules.jira_fetcher import JiraIssue
                        logger.set('%sBlock 1: No Jira URL — Chalk-only mode for %s' % (_bp, feature_id))
                        print('[MANUAL] No Jira URL provided. Creating minimal Jira stub for %s.' % feature_id, flush=True)
                        jira = JiraIssue(key=feature_id, summary='%s (Chalk-only)' % feature_id,
                                         description='No Jira data — test suite built from Chalk scenarios only.',
                                         channel=['ITMBO', 'NBOP'])
                        att_paths = []
                        exit_items.append('%s%s: Chalk-only mode (no Jira)' % (_bp, feature_id))

                    # Block 2 & 3: Chalk Data
                    if _is_manual_mode:
                        _manual_chalk_url = ss.get('manual_chalk_url', '').strip()
                        if _manual_chalk_url:
                            logger.set('%sBlock 2: Chalk live fetch from manual URL %s...' % (_bp, feature_id))
                            chalk_live = pipe.run('ChalkLive_%s' % feature_id,
                                lambda fid=feature_id, curl=_manual_chalk_url: block_chalk_live(
                                    page, fid, curl, 'Manual', [(ss.get('selected_pi') or 'Manual', curl)], log=logger))
                            chalk = chalk_live['chalk']
                        else:
                            from modules.chalk_parser import ChalkData
                            logger.set('%sBlock 2: No Chalk URL — Jira-only mode %s' % (_bp, feature_id))
                            print('[MANUAL] No Chalk URL provided. Building from Jira data only.', flush=True)
                            chalk = ChalkData(feature_id=feature_id)
                    else:
                        logger.set('%sBlock 2: Chalk DB lookup %s...' % (_bp, feature_id))
                        chalk_db = pipe.run('ChalkDB_%s' % feature_id,
                            lambda fid=feature_id: block_chalk_db(fid, ss.get('selected_pi') or '', log=logger))
                        chalk = chalk_db['chalk']

                        if not chalk or not chalk.scenarios:
                            logger.set('%sBlock 3: Chalk live fetch %s...' % (_bp, feature_id))
                            chalk_live = pipe.run('ChalkLive_%s' % feature_id,
                                lambda fid=feature_id: block_chalk_live(
                                    page, fid, ss.get('selected_pi_url') or '', ss.get('selected_pi') or '', ss.get('pi_list') or [], log=logger))
                            chalk = chalk_live['chalk']

                    # Block 4: Document Parsing
                    _uploads = uploaded_files if _fi == 1 else None
                    parsed_docs = []
                    if att_paths or _uploads:
                        logger.set('%sBlock 4: Parsing documents...' % _bp)
                        try:
                            parsed_docs = pipe.run('Docs_%s' % feature_id,
                                lambda: block_parse_docs(att_paths, _uploads, INPUTS, log=logger))
                        except Exception as _doc_err:
                            print('[V8] WARNING: Document parsing failed: %s — continuing with empty docs' % str(_doc_err)[:100], flush=True)
                            parsed_docs = []

                    # Block 4b: Deep Mine
                    # If NMNO lookup already has Business Rules, skip deep mine entirely
                    # (subtask mining is already done via Jira subtasks in dimension extractor)
                    print('', flush=True)
                    print('[V8] ═══════════════════════════════════════════════════', flush=True)
                    print('[V8] Block 4b: Deep Mining all data sources for %s' % feature_id, flush=True)
                    print('[V8] ═══════════════════════════════════════════════════', flush=True)
                    logger.set('%sBlock 4b: Deep mining %s...' % (_bp, feature_id))

                    # Pre-check: does NMNO local DB have data for this feature?
                    from modules.nmno_api_lookup import extract_api_operation_name as _ean, lookup_api_specs as _las
                    _chalk_urls_for_check = []
                    if jira and jira.acceptance_criteria:
                        import re as _re_check
                        _chalk_urls_for_check = _re_check.findall(r'https?://[^\s<>"\']+chalk[^\s<>"\']*', jira.acceptance_criteria or '')
                    _api_name_check = _ean(jira.summary if jira else '', _chalk_urls_for_check)
                    _nmno_pre_check = None
                    if _api_name_check:
                        _nmno_pre_check = _las(_api_name_check, log=lambda x: None)

                    _skip_deep_mine = _nmno_pre_check and _nmno_pre_check.business_rules
                    deep_mine_result = None

                    if _skip_deep_mine:
                        print('[V8] NMNO has %d Business Rules — SKIPPING deep mine entirely (local DB sufficient)' % len(_nmno_pre_check.business_rules), flush=True)
                        # Still create a minimal DeepMineResult with subtask mines
                        from modules.deep_miner import DeepMineResult, _mine_subtask
                        deep_mine_result = DeepMineResult(feature_id=feature_id)
                        if jira.subtasks:
                            for _st in jira.subtasks:
                                _mine = _mine_subtask(_st, log=lambda x: None)
                                if _mine.ac_items or _mine.testable_rules:
                                    deep_mine_result.subtask_mines.append(_mine)
                            print('[V8] Subtask mines: %d (from Jira cache)' % len(deep_mine_result.subtask_mines), flush=True)
                    else:
                        try:
                            dm_result = pipe.run('DeepMine_%s' % feature_id,
                                lambda: block_deep_mine(jira, chalk, page=page, log=logger))
                            deep_mine_result = dm_result['deep_mine_result']
                            print('[V8] Deep mine: %d API specs, %d subtask mines, %d testable items' % (
                                len(deep_mine_result.api_specs), len(deep_mine_result.subtask_mines),
                                len(deep_mine_result.all_testable_items)), flush=True)
                        except Exception as _dm_err:
                            print('[V8] WARNING: Deep mine failed: %s — continuing without deep data' % str(_dm_err)[:100], flush=True)
                            deep_mine_result = None

                    # Block 5: V8 Data-First Engine
                    print('', flush=True)
                    print('[V8] ═══════════════════════════════════════════════════', flush=True)
                    print('[V8] Block 5: V8.0 Data-First Engine for %s' % feature_id, flush=True)
                    print('[V8] ═══════════════════════════════════════════════════', flush=True)
                    logger.set('%sBlock 5: V8 Data-First Engine %s...' % (_bp, feature_id))
                    options = {
                        'include_positive': inc_positive,
                        'include_negative': inc_negative,
                        'include_e2e': inc_e2e,
                        'include_edge': inc_edge,
                        'include_attachments': inc_attachments,
                        'custom_instructions': custom_instructions,
                        'engine_version': '8',
                    }

                    engine_result = pipe.run('Engine_V8_%s' % feature_id,
                        lambda: block_build_suite_v8(jira, chalk, parsed_docs, options, deep_mine_result=deep_mine_result, log=logger))
                    suite = engine_result['suite']
                    total_steps = engine_result['total_steps']
                    print('[V8] Engine complete: %d TCs, %d steps' % (len(suite.test_cases), total_steps), flush=True)

                    # Store V8-specific data for display
                    if hasattr(suite, 'data_inventory') and suite.data_inventory:
                        inv = suite.data_inventory
                        ss['v8_data_sources'] = {
                            'total_testable_items': inv.total_testable_items,
                            'sources': [
                                {'source_name': s.source_name, 'source_type': s.source_type,
                                 'items_extracted': s.items_extracted, 'status': s.status,
                                 'items_detail': s.items_detail[:10] if s.items_detail else []}
                                for s in inv.sources
                            ],
                            'warnings': inv.warnings,
                            'gaps': inv.gaps,
                        }
                        print('[V8] Data sources: %d | Testable items: %d' % (
                            len(inv.sources), inv.total_testable_items), flush=True)
                        if inv.warnings:
                            for w in inv.warnings:
                                print('[V8] WARNING: %s' % w, flush=True)

                    if hasattr(suite, 'combination_plan') and suite.combination_plan:
                        plan = suite.combination_plan
                        ss['v8_combination_plan'] = {
                            'total_planned_tcs': plan.total_planned_tcs,
                            'independent_dimensions': [
                                {'name': d.name, 'values': d.values[:10]}
                                for d in plan.independent_dimensions
                            ] if plan.independent_dimensions else [],
                            'crossed_dimensions': [
                                (d1.name, d2.name) for d1, d2 in plan.crossed_dimensions
                            ] if plan.crossed_dimensions else [],
                            'reduction_notes': plan.reduction_notes or [],
                        }

                    # Store routing audit for display
                    if hasattr(suite, 'routing_audit') and suite.routing_audit:
                        ra = suite.routing_audit
                        ss['v8_routing_audit'] = {
                            'classification': ra.classification,
                            'confidence': ra.confidence,
                            'matched_keywords': ra.matched_keywords,
                            'data_sources_queried': ra.data_sources_queried,
                            'api_tcs_generated': ra.api_tcs_generated,
                            'ui_tcs_generated': ra.ui_tcs_generated,
                            'negative_tcs_generated': ra.negative_tcs_generated,
                            'total_tcs': ra.total_tcs,
                        }
                        print('[V8] Routing: %s (%.2f) | API TCs: %d | UI TCs: %d | Neg TCs: %d' % (
                            ra.classification, ra.confidence, ra.api_tcs_generated,
                            ra.ui_tcs_generated, ra.negative_tcs_generated), flush=True)

                    if not suite.test_cases:
                        print('[V8] ⚠️ Zero testable items — no TCs generated. Check Data Sources panel.', flush=True)

                    # Block 6: Excel + DB Save
                    print('[V8] ═══════════════════════════════════════════════════', flush=True)
                    print('[V8] Block 6: Generating output for %s' % feature_id, flush=True)
                    print('[V8] ═══════════════════════════════════════════════════', flush=True)
                    logger.set('%sBlock 6: Generating output %s...' % (_bp, feature_id))
                    output = pipe.run('Output_%s' % feature_id,
                        lambda: block_generate_output(suite, feature_id, ss.get('selected_pi') or 'Manual', 'Data-First', jira=jira, chalk=chalk, log=logger))

                    out_path = output['out_path']
                    _data_source_count = len(suite.data_inventory.sources) if suite.data_inventory else 0

                    # Compute grounding stats for badge display
                    try:
                        from modules.grounding_scorer import suite_grounding_pct, grounding_badge
                        suite_grounding_pct_val = suite_grounding_pct(suite.test_cases)
                        grounding_badge_val = grounding_badge(suite_grounding_pct_val)
                    except Exception:
                        suite_grounding_pct_val = -1
                        grounding_badge_val = ''

                    ss['result_path'] = str(out_path)
                    ss['suite_info'] = {
                        'tc_count': output['tc_count'],
                        'step_count': output['total_steps'],
                        'data_source_count': _data_source_count,
                        'grounding_pct': suite_grounding_pct_val,
                        'grounding_badge': grounding_badge_val,
                        'diff_report': output.get('diff_report'),
                        'scorecard_risk': getattr(output.get('scorecard'), 'overall_risk', ''),
                        'scorecard_badge': getattr(output.get('scorecard'), 'headline_badge', ''),
                    }
                    ss['last_feature_id'] = feature_id
                    ss['last_suite_id'] = output.get('suite_id', '')

                    ss['batch_results'].append({
                        'feature_id': feature_id, 'tc_count': output['tc_count'],
                        'step_count': output['total_steps'], 'file': out_path.name,
                        'file_path': str(out_path), 'title': jira.summary[:60],
                        'doc_path': str(output.get('doc_path', '')) if output.get('doc_path') else ''})
                    exit_items.append('%s%s: ✅ %d TCs | %s' % (_bp, feature_id, output['tc_count'], out_path.name))

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
                    traceback.print_exc()

                # ── END BATCH LOOP — close browser if it was opened ──
                if context:
                    try: context.close()
                    except Exception: pass
                if browser:
                    try: browser.close()
                    except Exception: pass
                if pw:
                    try: pw.stop()
                    except Exception: pass

                # ── Finalize ──
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                _feat_label = '%d features' % len(_features_to_run) if len(_features_to_run) > 1 else feature_id
                logger.set('DONE: %s in %dm %ds (V8 Data-First)' % (_feat_label, m, s))

                print('', flush=True)
                print('*' * 50, flush=True)
                print('***   V8 DATA-FIRST SUITE PREPARED SUCCESSFULLY   ***', flush=True)
                print('***   %s | %dm %ds                    ***' % (_feat_label, m, s), flush=True)
                print('*' * 50, flush=True)

                import streamlit.components.v1 as components
                components.html("<script>parent.document.getElementById('output').scrollIntoView({behavior:'smooth'});</script>", height=0)

                # Print pipeline timing
                print('\n' + '=' * 55, flush=True)
                print('  PIPELINE TIMING (V8)', flush=True)
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
                    _batch_summary = ['Batch: %d/%d features completed | %d failed' % (_completed, _batch_count, _failed)]
                    for br in ss['batch_results']:
                        _batch_summary.append('  ✅ %s: %d TCs | %s' % (br['feature_id'], br['tc_count'], br['file']))
                    if _batch_failed:
                        _batch_summary.append('')
                        _batch_summary.append('FAILED features:')
                        for _ff_id, _ff_err in _batch_failed:
                            _batch_summary.append('  ❌ %s: %s' % (_ff_id, _ff_err[:80]))
                    _title = 'V8 Batch Complete — %d/%d features' % (_completed, _batch_count)
                    if _failed:
                        _title = 'V8 Batch Partial — %d/%d completed, %d failed' % (_completed, _batch_count, _failed)
                    ss['exit_report'] = {
                        'title': _title,
                        'items': _batch_summary + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s | Engine: V8.0 Data-First' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss.get('selected_pi') or 'Manual'),
                    }
                else:
                    ss['exit_report'] = {
                        'title': 'V8 Generation Complete — %s' % feature_id,
                        'items': exit_items + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s | Engine: V8.0 Data-First' % (
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
                    'title': 'V8 Generation FAILED — %s (%d/%d completed)' % (feature_id, _completed, _total),
                    'items': _progress_items + [
                        'Pipeline block "%s" failed after %d attempts.' % (pe.block_name, pe.attempts),
                        '',
                        'Block: %s' % pe.block_name,
                        'Error: %s' % pe.error_msg[:200],
                    ] + exit_items,
                    'footer': 'Failed at %s | Duration: %dm %ds | %d/%d features completed | Engine: V8.0' % (
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
                    'title': 'V8 Generation FAILED — %s (%d/%d completed)' % (feature_id, _completed, _total),
                    'items': _progress_items + exit_items + ['', 'ERROR: %s' % str(e)[:200]],
                    'footer': 'Failed at %s | Duration: %dm %ds | %d/%d features completed | Engine: V8.0' % (
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

# ================================================================
# SIDEBAR — Generation History
# ================================================================
with st.sidebar:
    st.markdown("""<div style='padding:12px;border-radius:12px;
        background:rgba(26,35,126,0.3);border:1px solid rgba(99,102,241,0.3);margin-bottom:12px;'>
        <div style='font-weight:800;font-size:14px;color:#818cf8;'>TSG V8.0</div>
        <div style='font-size:11px;color:#94a3b8;margin-top:4px;'>Data-First Engine</div>
        <div style='font-size:10px;color:#64748b;margin-top:2px;'>Dimensions → Combinations → TCs</div>
    </div>""", unsafe_allow_html=True)

    _hist = get_history()
    if _hist:
        st.markdown("**Recent Generations**")
        for _hi, _h in enumerate(_hist[:10]):
            _h_tc = _h.get('tc_count', 0)
            _h_fid = _h.get('feature_id', '?')
            _h_ts = _h.get('timestamp', '')[:16]
            with st.expander('%s | %s | %d TCs' % (_h_ts, _h_fid, _h_tc)):
                st.caption('PI: %s' % _h.get('pi', 'N/A'))
                st.caption('Steps: %d' % _h.get('step_count', 0))
                _fp = Path(_h.get('file_path', ''))
                if _fp.exists():
                    st.download_button('📥 Download', data=_fp.read_bytes(), file_name=_fp.name,
                                       key='sidebar_hist_%d_%s' % (_hi, _h_fid))
    else:
        st.caption('No generation history yet.')

    # DB Stats
    st.markdown("---")
    _stats = get_db_stats()
    st.markdown("**Database**")
    st.caption('Features: %d' % _stats.get('feature_count', 0))
    st.caption('Chalk cached: %d' % get_chalk_cache_count())
    st.caption('DB size: %d KB' % _stats.get('db_size_kb', 0))

    # ════════════════════════════════════════════════════════════════
    # DATA SOURCE STATUS PANEL
    # ════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("**📊 Data Source Status**")

    def _get_tmo_api_chalk_stats():
        """Get TMO_API_Chalk table stats."""
        import sqlite3
        db_path = Path(__file__).parent / 'tsg_cache.db'
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            row_count = conn.execute('SELECT COUNT(*) as cnt FROM TMO_API_Chalk').fetchone()['cnt']
            last_row = conn.execute('SELECT last_fetched FROM TMO_API_Chalk ORDER BY last_fetched DESC LIMIT 1').fetchone()
            last_fetched = last_row['last_fetched'] if last_row else None
            # Count business rules
            br_count = 0
            rows = conn.execute('SELECT table_data_json FROM TMO_API_Chalk WHERE table_data_json IS NOT NULL').fetchall()
            import json
            for r in rows:
                try:
                    tables = json.loads(r['table_data_json'])
                    for t in tables:
                        if isinstance(t, dict):
                            headers = t.get('headers', [])
                            headers_lower = [h.lower() for h in headers]
                            if any('rule' in h for h in headers_lower):
                                br_count += len(t.get('rows', []))
                except:
                    pass
            conn.close()
            return {'rows': row_count, 'last_fetched': last_fetched, 'business_rules': br_count}
        except Exception:
            return {'rows': 0, 'last_fetched': None, 'business_rules': 0}

    def _is_stale(ts_str):
        """Check if timestamp is older than 24 hours."""
        if not ts_str:
            return True
        try:
            from datetime import datetime, timedelta
            fetched = datetime.fromisoformat(ts_str.replace('Z', '+00:00').split('+')[0])
            return datetime.now() - fetched > timedelta(hours=24)
        except:
            return True

    # Source 1: Jira Cache
    _jira_count = _stats.get('jira_cached', 0)
    _jira_indicator = '🔴' if _jira_count == 0 else '🟢'
    st.caption('%s Jira Cache: %d entries' % (_jira_indicator, _jira_count))

    # Source 2: Chalk PI Cache
    _chalk_count = get_chalk_cache_count()
    _chalk_indicator = '🔴' if _chalk_count == 0 else '🟢'
    st.caption('%s Chalk PI Cache: %d entries' % (_chalk_indicator, _chalk_count))

    # Source 3: TMO_API_Chalk
    _tmo_stats = _get_tmo_api_chalk_stats()
    _tmo_count = _tmo_stats['rows']
    _tmo_stale = _is_stale(_tmo_stats['last_fetched'])
    if _tmo_count == 0:
        _tmo_indicator = '🔴'
    elif _tmo_stale:
        _tmo_indicator = '🟡'
    else:
        _tmo_indicator = '🟢'
    st.caption('%s TMO_API_Chalk: %d rows' % (_tmo_indicator, _tmo_count))
    if _tmo_stats['business_rules'] > 0:
        st.caption('   📋 Business Rules: %d' % _tmo_stats['business_rules'])
    if _tmo_stats['last_fetched']:
        st.caption('   Last fetched: %s' % str(_tmo_stats['last_fetched'])[:16])

    # Source 4: NBOP UI Knowledge
    try:
        from modules.nbop_ui_knowledge import is_available
        _nbop_avail = is_available()
    except:
        _nbop_avail = False
    _nbop_indicator = '🟢' if _nbop_avail else '🔴'
    st.caption('%s NBOP UI Knowledge: %s' % (_nbop_indicator, 'loaded' if _nbop_avail else 'unavailable'))

    # ════════════════════════════════════════════════════════════════
    # RELOAD BUTTONS
    # ════════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("**🔄 Reload Data Sources**")

    _col1, _col2 = st.columns(2)
    with _col1:
        if st.button('🔄 Jira', key='reload_jira_cache', help='Refresh Jira cache'):
            st.toast('Jira cache reload triggered. Re-fetch features to update.')
    with _col2:
        if st.button('🔄 Chalk', key='reload_chalk_cache', help='Refresh Chalk PI cache'):
            st.toast('Chalk cache reload triggered. Re-fetch PI pages to update.')

    _col3, _col4 = st.columns(2)
    with _col3:
        if st.button('🔄 TMO API', key='reload_tmo_api', help='Re-crawl NMNO API Chalk pages'):
            st.toast('TMO_API_Chalk re-crawl would be triggered here.')
    with _col4:
        if st.button('🔄 NBOP', key='reload_nbop_ui', help='Re-crawl NBOP portal'):
            st.toast('NBOP UI re-crawl would be triggered here.')
