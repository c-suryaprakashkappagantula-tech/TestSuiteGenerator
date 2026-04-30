"""
TSG_Dashboard_V5.0.py -- Test Suite Generator Dashboard V5.0
V5.0 adds: [YOUR NEW FEATURES HERE]
Built on V4.1 (batch generation, document provenance, artifact hash staleness,
LLM layer, DB suite storage, humanizer, AI review prompt).

Usage:  streamlit run TSG_Dashboard_V5.0.py
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
from modules.llm_engine import (LLMClient, create_llm_from_env,
                                 PROVIDER_OPENAI, PROVIDER_AZURE, PROVIDER_BEDROCK,
                                 PROVIDER_OLLAMA, PROVIDER_NONE, DEFAULT_MODELS)
from modules.llm_reviewer import review_suite_gaps, improve_steps, parse_custom_instructions_llm
from modules.pipeline import Pipeline, PipelineError, block_jira_fetch, block_chalk_db, block_chalk_live, block_parse_docs, block_build_suite, block_generate_output
from modules.database import (init_db, save_pi_pages, load_pi_pages, save_features,
                               load_features, load_all_features, get_features_count,
                               save_jira, load_jira, is_jira_stale, save_chalk, load_chalk,
                               load_chalk_as_object, get_chalk_cache_count,
                               log_generation_db, get_history_db, get_db_stats, is_data_stale,
                               save_test_suite, load_latest_suite, build_ai_review_prompt,
                               get_all_suite_history, save_artifact_hash, check_staleness)

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
st.set_page_config(page_title='TSG V5.0 - AI-Powered Test Suite Generator', page_icon='https://em-content.zobj.net/source/twitter/408/test-tube_1f9ea.png', layout='wide')
st.markdown(CSS, unsafe_allow_html=True)

# ================================================================
# LLM CONFIG DEFAULTS (populated in Step 4 expander)
# ================================================================
_provider_map = {
    'None (Rule-Based)': PROVIDER_NONE,
    'OpenAI': PROVIDER_OPENAI,
    'Azure OpenAI': PROVIDER_AZURE,
    'AWS Bedrock': PROVIDER_BEDROCK,
    'Ollama (Local)': PROVIDER_OLLAMA,
}

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
_ai_provider_label = ss.get('llm_provider_select', 'None (Rule-Based)')
_ai_badge = 'AI: %s' % _ai_provider_label if _ai_provider_label != 'None (Rule-Based)' else 'AI: Off'
st.markdown("""<div class='banner'>
  <div>
    <div class='title'>TSG &mdash; Test Suite Generator</div>
    <div class='sub'>Chalk + Jira + Attachments + AI &rarr; Production-Ready Test Suites</div>
  </div>
  <div style='display:flex; gap:8px; align-items:center; flex-wrap:wrap;'>
    <div class='badge'>V5.0</div>
    <div class='badge'>LLM-Powered</div>
    <div class='badge'>Batch Mode</div>
    <div class='badge'>Manual Links</div>
    <div class='badge'>%s</div>
    <div class='badge'>%s</div>
  </div>
</div>""" % (_db_badge, _ai_badge), unsafe_allow_html=True)

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
            '🔗 Manual mode — provide Jira &amp; Chalk URLs directly</div>',
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
            'Paste the Jira feature URL and the Chalk page URL containing the feature scope. '
            'The generator will fetch data from these links directly.</div>',
            unsafe_allow_html=True)

        _manual_jira = st.text_input(
            '🔗 Jira Feature URL',
            value=ss.get('manual_jira_url', ''),
            placeholder='https://jira.charter.com/browse/MWTGPROV-3985',
            key='manual_jira_input',
            help='Full Jira URL — feature ID will be extracted automatically')

        _manual_chalk = st.text_input(
            '🔗 Chalk Page URL',
            value=ss.get('manual_chalk_url', ''),
            placeholder='https://chalk.charter.com/spaces/MDA/pages/3281127794/PI-53',
            key='manual_chalk_input',
            help='Chalk page URL containing the feature scope/scenarios')

        ss['manual_jira_url'] = _manual_jira
        ss['manual_chalk_url'] = _manual_chalk

        # Extract feature ID from Jira URL
        import re as _re_manual
        _manual_fid = ''
        if _manual_jira:
            # Support any Jira project key (e.g., MWTGPROV-3985, MOBIT2-62, ABC-123)
            _jira_match = _re_manual.search(r'([A-Z][A-Z0-9]+-\d+)', _manual_jira.upper())
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
            if _manual_chalk and 'chalk.charter.com' in _manual_chalk.lower():
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(52,211,153,0.08);border:1px solid rgba(52,211,153,0.2);'
                    'font-size:12px;color:#34D399;">✅ Chalk URL valid</div>',
                    unsafe_allow_html=True)
            elif _manual_chalk:
                st.markdown(
                    '<div style="padding:6px 12px;border-radius:8px;margin:4px 0;'
                    'background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);'
                    'font-size:12px;color:#FBBF24;">⚠️ URL does not look like a Chalk page</div>',
                    unsafe_allow_html=True)

        st.markdown("</div>", unsafe_allow_html=True)

        # Set variables for downstream compatibility
        feature_id = _manual_fid
        feature_ids = [_manual_fid] if _manual_fid else []
        manual_mode = False
        batch_mode = False
        _sync_in_progress = False

    # ================================================================
    # PI SCOPE MODE (existing Steps 1 & 2)
    # ================================================================
    if not _is_manual_mode:
        # ── Step 1: PI Selection ──
        st.markdown("<div class='sec-title'><span class='icon'>&#127919;</span> Step 1: Select PI Iteration</div>", unsafe_allow_html=True)
        st.markdown("<div class='glass'>", unsafe_allow_html=True)

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
                    # Clear previous execution state
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
            st.markdown("<div class='sec-title'><span class='icon'>&#128269;</span> Step 2: Feature ID</div>", unsafe_allow_html=True)

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
                'Select Features (%d available in %s)' % (len(ss['pi_features']), ss['selected_pi']),
                options=options, default=_default, key='feature_multiselect')
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
            options = ['-- Select a Feature --'] + [
                '%s - %s' % (fid, title) for fid, title in ss['pi_features']
            ]
            selected = st.selectbox(
                'Available Features (%d found in %s)' % (len(ss['pi_features']), ss['selected_pi']),
                options=options, key='feature_dropdown')
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

    # ── Step 3: Test Matrix ──
    if _sync_in_progress:
        st.markdown("<div class='sec-title' style='opacity:0.4'><span class='icon'>&#9881;</span> Step 3: Test Matrix & Strategy (blocked during sync)</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='sec-title'><span class='icon'>&#9881;</span> Step 3: Test Matrix & Strategy</div>", unsafe_allow_html=True)

    # Suite Strategy — checkboxes (user can select multiple)
    st.markdown("Suite Strategy:")
    _sc1, _sc2, _sc3 = st.columns(3)
    with _sc1:
        use_smart = st.checkbox('Smart Suite', value=True, key='strat_smart', help='Representative combos (ITMBO + NBOP)')
    with _sc2:
        use_full = st.checkbox('Full Matrix', value=False, key='strat_full', help='Every combination — customize below')
    with _sc3:
        use_custom = st.checkbox('Custom Instructions', key='custom_instructions_toggle')

    # Determine active strategy for the engine
    strategy = 'Smart Suite (Recommended)'
    if use_full:
        strategy = 'Full Matrix'

    # Default values — Smart Suite includes both channels
    channel = ['ITMBO', 'NBOP']
    devices = ['Mobile']
    networks = ['4G', '5G']
    sim_types = ['eSIM', 'pSIM']
    os_platforms = ['iOS', 'Android']

    if use_smart and not use_full:
        st.caption('Smart Suite: ITMBO + NBOP | Mobile | eSIM+pSIM | iOS+Android | 4G+5G')
    if use_full:
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

    # ── AI Engine (compact inline config) ──
    with st.expander('🤖 AI Engine', expanded=False):
        _ai_c1, _ai_c2 = st.columns([1, 2])
        with _ai_c1:
            llm_provider = st.selectbox('Provider', list(_provider_map.keys()), key='llm_provider_select')
        _selected_provider = _provider_map[llm_provider]

        # Provider-specific config defaults
        _llm_api_key = ''
        _llm_model = DEFAULT_MODELS.get(_selected_provider, '')
        _llm_base_url = ''
        _azure_endpoint = ''
        _azure_deployment = ''
        _bedrock_region = 'us-east-1'
        _llm_temp = 0.3

        if _selected_provider == PROVIDER_OPENAI:
            with _ai_c2:
                _llm_api_key = st.text_input('API Key', type='password', key='oai_key',
                                              value=os.environ.get('OPENAI_API_KEY', ''))
            _oc1, _oc2 = st.columns(2)
            with _oc1:
                _llm_model = st.text_input('Model', value=os.environ.get('OPENAI_MODEL', 'gpt-4o'), key='oai_model')
            with _oc2:
                _llm_base_url = st.text_input('Base URL (optional)', value='', key='oai_url')

        elif _selected_provider == PROVIDER_AZURE:
            with _ai_c2:
                _llm_api_key = st.text_input('API Key', type='password', key='az_key',
                                              value=os.environ.get('AZURE_OPENAI_API_KEY', ''))
            _ac1, _ac2 = st.columns(2)
            with _ac1:
                _azure_endpoint = st.text_input('Endpoint', key='az_endpoint',
                                                 value=os.environ.get('AZURE_OPENAI_ENDPOINT', ''))
            with _ac2:
                _azure_deployment = st.text_input('Deployment', key='az_deploy',
                                                   value=os.environ.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o'))

        elif _selected_provider == PROVIDER_BEDROCK:
            with _ai_c2:
                _bedrock_region = st.text_input('AWS Region', value=os.environ.get('AWS_DEFAULT_REGION', 'us-east-1'), key='br_region')
            _bc1, _bc2 = st.columns(2)
            with _bc1:
                _llm_model = st.text_input('Model ID', value=DEFAULT_MODELS[PROVIDER_BEDROCK], key='br_model')
            with _bc2:
                st.caption('Uses ~/.aws/credentials or env vars')

        elif _selected_provider == PROVIDER_OLLAMA:
            with _ai_c2:
                _llm_base_url = st.text_input('Ollama URL', value='http://localhost:11434/v1', key='ol_url')
            _lc1, _lc2 = st.columns(2)
            with _lc1:
                _llm_model = st.text_input('Model', value='llama3.1', key='ol_model')

        if _selected_provider != PROVIDER_NONE:
            _tc1, _tc2 = st.columns([2, 1])
            with _tc1:
                _llm_temp = st.slider('Temperature', 0.0, 1.0, 0.3, 0.1, key='llm_temp')
            with _tc2:
                if st.button('Test Connection', key='test_llm', use_container_width=True):
                    with st.spinner('Testing...'):
                        _test_llm = LLMClient(
                            provider=_selected_provider, model=_llm_model, api_key=_llm_api_key,
                            base_url=_llm_base_url, azure_endpoint=_azure_endpoint,
                            azure_deployment=_azure_deployment, region=_bedrock_region,
                            log=lambda m: None)
                        if _test_llm.available:
                            _resp = _test_llm.chat('You are a test assistant.', 'Reply with exactly: OK')
                            if _resp:
                                st.success('Connected!')
                            else:
                                st.error('Empty response — check model name')
                        else:
                            st.error('Init failed — check credentials')

    # AI feature toggles (only when LLM is configured)
    if _selected_provider != PROVIDER_NONE:
        ai_c1, ai_c2, ai_c3 = st.columns(3)
        with ai_c1:
            ai_gap_analysis = st.checkbox('AI Gap Analysis', value=True, key='ai_gap',
                                           help='LLM reviews suite and suggests missing scenarios')
        with ai_c2:
            ai_step_improve = st.checkbox('AI Step Improvement', value=True, key='ai_steps',
                                           help='LLM improves generic steps to be feature-specific')
        with ai_c3:
            ai_custom_parse = st.checkbox('AI Custom Instructions', value=True, key='ai_custom',
                                           help='LLM parses custom instructions with NLU instead of regex')
    else:
        ai_gap_analysis = False
        ai_step_improve = False
        ai_custom_parse = False
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

            _dl_c1, _dl_c2 = st.columns([2, 1])
            with _dl_c1:
                st.download_button('📥 Download: %s (%d TCs)' % (
                        ss.get('last_feature_id', 'Suite'), ss.get('suite_info', {}).get('tc_count', 0)),
                    data=Path(ss['result_path']).read_bytes(),
                    file_name=Path(ss['result_path']).name,
                    use_container_width=True, key='dl_main')
            with _dl_c2:
                # Feature Summary doc download
                if ss.get('batch_results'):
                    _last_br = ss['batch_results'][-1]
                    _doc_p = Path(_last_br.get('doc_path', '')) if _last_br.get('doc_path') else None
                    if _doc_p and _doc_p.exists():
                        st.download_button('📄 Feature Summary',
                            data=_doc_p.read_bytes(),
                            file_name=_doc_p.name,
                            use_container_width=True, key='dl_doc_main')
                else:
                    # Single mode: show CLI log download in the second column
                    if ss.get('logs'):
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
                st.markdown("**All generated suites:**")
                for _bi, _br in enumerate(ss['batch_results']):
                    _bp = Path(_br.get('file_path', _br.get('path', '')))
                    _dp = Path(_br.get('doc_path', '')) if _br.get('doc_path') else None
                    if _bp.exists():
                        _dc1, _dc2 = st.columns([2, 1])
                        with _dc1:
                            st.download_button(
                                '📥 %s — %d TCs' % (_br['feature_id'], _br['tc_count']),
                                data=_bp.read_bytes(),
                                file_name=_bp.name,
                                use_container_width=True,
                                key='dl_batch_%d' % _bi)
                        with _dc2:
                            if _dp and _dp.exists():
                                st.download_button(
                                    '📄 Summary',
                                    data=_dp.read_bytes(),
                                    file_name=_dp.name,
                                    use_container_width=True,
                                    key='dl_doc_%d' % _bi)

            # ── CLI Log Download Button ──
            if ss.get('logs'):
                _cli_text = '\n'.join(ss['logs'])
                _log_filename = 'TSG_CLI_Log_%s_%s.txt' % (
                    ss.get('selected_pi', 'Manual').replace(' ', '_'),
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
    """Smart CLI logger — shows only high-level progress to user.
    Detailed logs are stored internally but not displayed unless it's an error."""

    # Only these prefixes show in the CLI window
    _SHOW_PREFIXES = ['[PIPELINE]', '[HUMANIZE]', '[ERROR]', '[WARN]', 'Block ', 'DONE', 'FAILED',
                       'Batch:', '[ENGINE] [OK]', '[ENGINE] Step 8d', '[EXCEL]',
                       '[JIRA] Found', '[JIRA]   Epic child', '[JIRA] 🔍 Fetching epic']
    # These are always hidden (too noisy)
    _HIDE_PREFIXES = ['[JIRA]   ', '[CHALK] Step', '[ENGINE] Step 5', '[ENGINE] Step 1',
                       '[ENGINE] Step 2', '[ENGINE] Step 3', '[ENGINE] Step 4',
                       '[ENGINE] Step 5a', '[ENGINE] Step 5b', '[ENGINE] Step 5c',
                       '[ENGINE] Step 5d', '[ENGINE] Step 6', '[ENGINE] Step 7',
                       '[ENGINE] Step 8:', '[ENGINE] Step 8b', '[ENGINE] Step 8c',
                       '[ENGINE] Step 9', '[ENGINE] Step 10', '[ENGINE] Step 11',
                       '[ENGINE]   ', '[AUDIT]   ', '[TIME]', '[INIT]',
                       '[DOC]', '[CUSTOM]', 'DEBUG']

    def __init__(self, header_ph, log_ph, tools_ph):
        self.header = header_ph
        self.log_ph = log_ph
        self.tools = tools_ph
        self.lines = list(ss.get('logs', []))  # visible lines
        self._all_lines = []  # all lines (for debug)
        self._ver = 0

    def set(self, text):
        self.header.markdown("<div class='cli-header'>>> %s</div>" % escape(text), unsafe_allow_html=True)

    def _should_show(self, text):
        """Decide if a log line should be shown in the CLI window."""
        t = text.strip()
        if not t:
            return False
        # Always show errors
        if 'ERROR' in t or 'FAIL' in t or 'error' in t.lower()[:20]:
            return True
        # Always show pipeline block messages
        if any(t.startswith(p) or p in t for p in self._SHOW_PREFIXES):
            return True
        # Hide noisy internal logs
        if any(t.startswith(p) for p in self._HIDE_PREFIXES):
            return False
        # Show OK/success summaries
        if '✅' in t or '✓' in t:
            return True
        # Hide everything else by default
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

if ss.get('_sync_running'):
    ss['_sync_running'] = False
    ss['logs'] = []
    ss['result_path'] = None
    ss['exit_report'] = None

    _sync_scope = ss.get('_sync_scope', 'All Iterations')
    _specific_pis = ss.get('_sync_specific_pis', [])

    # Use the CLI terminal for progress
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

        # Always discover PI pages first — this auto-detects new PIs (e.g., PI-56)
        _sync_msg('Discovering PI pages (auto-detecting new PIs)...')
        pi_links = discover_pi_links(page, log=lambda m: None)
        _new_pi_list = [(p.label, p.url) for p in pi_links]

        # Check for new PIs
        _old_labels = set(label for label, url in ss.get('pi_list', []))
        _new_labels = set(label for label, url in _new_pi_list)
        _added_pis = _new_labels - _old_labels
        if _added_pis:
            _sync_msg('NEW PIs detected: %s' % ', '.join(sorted(_added_pis)))

        ss['pi_list'] = _new_pi_list
        save_pi_pages(ss['pi_list'])
        _sync_msg('Found %d PIs%s' % (len(ss['pi_list']),
            ' (NEW: %s)' % ', '.join(sorted(_added_pis)) if _added_pis else ''))

        # Determine which PIs to sync
        if _sync_scope == 'Specific Iteration(s)' and _specific_pis:
            _pis_to_sync = [(label, url) for label, url in ss['pi_list'] if label in _specific_pis]
            _sync_msg('Syncing %d specific PI(s): %s' % (len(_pis_to_sync), ', '.join(_specific_pis)))
        else:
            _pis_to_sync = ss['pi_list']
            _sync_msg('Syncing ALL %d PIs' % len(_pis_to_sync))

        _all = ss.get('all_pi_features', {})  # Preserve existing data for non-synced PIs
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
# MAIN EXECUTION
# ================================================================
if run_btn:
    # Build the list of features to process
    _features_to_run = []
    if feature_ids and len(feature_ids) >= 1:
        _features_to_run = [f.strip().upper() for f in feature_ids if f.strip()]
    elif feature_id and feature_id.strip():
        _features_to_run = [feature_id.strip().upper()]

    # Fallback: if batch mode with _batch_default but multiselect didn't populate
    if not _features_to_run and ss.get('_batch_default'):
        _features_to_run = [s.split(' - ')[0].strip().upper() for s in ss['_batch_default'] if s.strip()]

    if not _features_to_run:
        st.error('Please enter a Feature ID.' if not _is_manual_mode else 'Please provide a valid Jira URL with a MWTGPROV-XXXX feature ID.')
    elif not _is_manual_mode and not ss.get('selected_pi'):
        st.error('Please select a PI iteration first.')
    elif _is_manual_mode and not ss.get('manual_chalk_url'):
        st.error('Please provide a Chalk page URL.')
    else:
        feature_id = _features_to_run[0]  # primary feature (for single mode compat)
        ss['logs'] = []
        ss['result_path'] = None
        ss['cp_path'] = None
        ss['exit_report'] = None
        ss['batch_results'] = []

        logger = LiveLog(cli_header, cli_log, cli_tools)
        if len(_features_to_run) > 1:
            logger.set('Batch: %d features | %s' % (len(_features_to_run), ss.get('selected_pi') or 'Manual Links'))
        else:
            logger.set('Starting: %s | %s' % (feature_id, ss.get('selected_pi') or 'Manual Links'))

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
                _batch_failed = []  # [(feature_id, error_msg), ...]
                _batch_skipped = []
                for _fi, _current_fid in enumerate(_features_to_run, 1):
                  try:
                    feature_id = _current_fid
                    _bp = '[%d/%d] ' % (_fi, _batch_count) if _batch_count > 1 else ''

                    # Block 1: Jira Fetch (with self-heal retry)
                    logger.set('%sBlock 1: Fetching Jira %s...' % (_bp, feature_id))
                    jira_result = pipe.run('Jira_%s' % feature_id,
                        lambda fid=feature_id: block_jira_fetch(page, fid, log=logger))
                    jira = jira_result['jira']
                    att_paths = jira_result['att_paths'] if inc_attachments else []
                    exit_items.append('%s%s: Jira fetched — %s' % (_bp, feature_id, jira.summary[:40]))

                    # Block 2 & 3: Chalk Data
                    if _is_manual_mode:
                        # Manual mode: skip DB, go straight to live fetch using provided Chalk URL
                        logger.set('%sBlock 2: Chalk live fetch from manual URL %s...' % (_bp, feature_id))
                        _manual_chalk_url = ss.get('manual_chalk_url', '')
                        chalk_live = pipe.run('ChalkLive_%s' % feature_id,
                            lambda fid=feature_id, curl=_manual_chalk_url: block_chalk_live(
                                page, fid, curl, 'Manual', [(ss.get('selected_pi', 'Manual'), curl)], log=logger))
                        chalk = chalk_live['chalk']
                    else:
                        # PI Scope mode: DB first, live fallback
                        # Block 2: Chalk DB Lookup (with self-heal retry)
                        logger.set('%sBlock 2: Chalk DB lookup %s...' % (_bp, feature_id))
                        chalk_db = pipe.run('ChalkDB_%s' % feature_id,
                            lambda fid=feature_id: block_chalk_db(fid, ss.get('selected_pi', ''), log=logger))
                        chalk = chalk_db['chalk']

                        # Block 3: Chalk Live Fetch (only if DB missed, with self-heal retry)
                        if not chalk or not chalk.scenarios:
                            logger.set('%sBlock 3: Chalk live fetch %s...' % (_bp, feature_id))
                            chalk_live = pipe.run('ChalkLive_%s' % feature_id,
                                lambda fid=feature_id: block_chalk_live(
                                    page, fid, ss['selected_pi_url'], ss['selected_pi'], ss['pi_list'], log=logger))
                            chalk = chalk_live['chalk']

                    # Block 4: Document Parsing (with self-heal retry)
                    _uploads = uploaded_files if _fi == 1 else None
                    if att_paths or _uploads:
                        logger.set('%sBlock 4: Parsing documents...' % _bp)
                        parsed_docs = pipe.run('Docs_%s' % feature_id,
                            lambda: block_parse_docs(att_paths, _uploads, INPUTS, log=logger))
                    else:
                        parsed_docs = []

                    # Block 5: Test Engine (with self-heal retry)
                    logger.set('%sBlock 5: Building test suite %s...' % (_bp, feature_id))
                    options = {
                        'channel': channel, 'devices': devices, 'networks': networks,
                        'sim_types': sim_types, 'os_platforms': os_platforms,
                        'include_positive': inc_positive,
                        'include_negative': inc_negative, 'include_e2e': inc_e2e,
                        'include_edge': inc_edge, 'include_attachments': inc_attachments,
                        'strategy': strategy, 'custom_instructions': custom_instructions,
                    }
                    engine_result = pipe.run('Engine_%s' % feature_id,
                        lambda: block_build_suite(jira, chalk, parsed_docs, options, log=logger))
                    suite = engine_result['suite']
                    total_steps = engine_result['total_steps']

                    # Block 6: Excel + DB Save (with self-heal retry)
                    logger.set('%sBlock 6: Generating output %s...' % (_bp, feature_id))
                    output = pipe.run('Output_%s' % feature_id,
                        lambda: block_generate_output(suite, feature_id, ss.get('selected_pi') or 'Manual', strategy, jira=jira, chalk=chalk, log=logger))

                    out_path = output['out_path']
                    sheet_count = len(suite.groups) + 2 if len(suite.groups) > 1 else 3
                    if hasattr(suite, 'combinations') and suite.combinations and len(suite.combinations) > 1:
                        sheet_count += 1

                    ss['result_path'] = str(out_path)
                    ss['suite_info'] = {'tc_count': output['tc_count'], 'step_count': output['total_steps'], 'sheet_count': sheet_count}
                    ss['last_feature_id'] = feature_id
                    ss['last_suite_id'] = output['suite_id']

                    ss['batch_results'].append({
                        'feature_id': feature_id, 'tc_count': output['tc_count'],
                        'step_count': output['total_steps'], 'file': out_path.name,
                        'file_path': str(out_path), 'title': jira.summary[:60],
                        'doc_path': str(output.get('doc_path', '')) if output.get('doc_path') else ''})
                    exit_items.append('%s%s: ✅ %d TCs | %s' % (_bp, feature_id, output['tc_count'], out_path.name))

                  except PipelineError as _pe:
                    # Single feature failed — log it and continue to next
                    _err_msg = '%s block "%s" failed: %s' % (feature_id, _pe.block_name, str(_pe.error_msg)[:100])
                    _batch_failed.append((feature_id, _err_msg))
                    exit_items.append('%s%s: ❌ FAILED — %s' % (_bp, feature_id, _pe.block_name))
                    logger.set('%s%s FAILED at %s — skipping to next' % (_bp, feature_id, _pe.block_name))
                    print('[BATCH] %s' % _err_msg, flush=True)

                  except Exception as _ex:
                    # Unexpected error — log and continue
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

                # Show completion banner in CLI
                print('', flush=True)
                print('*' * 50, flush=True)
                print('***   TEST SUITE PREPARED SUCCESSFULLY   ***', flush=True)
                print('***   %s | %dm %ds                    ***' % (_feat_label, m, s), flush=True)
                print('*' * 50, flush=True)

                # Auto-scroll to output/download section
                import streamlit.components.v1 as components
                components.html("<script>parent.document.getElementById('output').scrollIntoView({behavior:'smooth'});</script>", height=0)

                # Print pipeline timing
                print('\n' + '=' * 55, flush=True)
                print('  PIPELINE TIMING', flush=True)
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
                    _title = 'Batch Complete — %d/%d features' % (_completed, _batch_count)
                    if _failed:
                        _title = 'Batch Partial — %d/%d completed, %d failed' % (_completed, _batch_count, _failed)
                    ss['exit_report'] = {
                        'title': _title,
                        'items': _batch_summary + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss.get('selected_pi') or 'Manual'),
                    }
                else:
                    ss['exit_report'] = {
                        'title': 'Generation Complete — %s' % feature_id,
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
                    'title': 'Generation FAILED — %s (%d/%d completed)' % (feature_id, _completed, _total),
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
                    'title': 'Generation FAILED — %s (%d/%d completed)' % (feature_id, _completed, _total),
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
