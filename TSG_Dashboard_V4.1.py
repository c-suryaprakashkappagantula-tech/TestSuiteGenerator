"""
TSG_Dashboard_V4.1.py -- Test Suite Generator Dashboard V4.1
V4.1 adds: Multi-feature batch generation, document provenance, artifact hash staleness.
Built on V4.0 (LLM layer, DB suite storage, humanizer, AI review prompt).

Usage:  streamlit run TSG_Dashboard_V4.1.py
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
st.set_page_config(page_title='TSG V4.1 - AI-Powered Test Suite Generator', page_icon='https://em-content.zobj.net/source/twitter/408/test-tube_1f9ea.png', layout='wide')
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
    <div class='badge'>V4.1</div>
    <div class='badge'>LLM-Powered</div>
    <div class='badge'>Batch Mode</div>
    <div class='badge'>%s</div>
    <div class='badge'>%s</div>
  </div>
</div>""" % (_db_badge, _ai_badge), unsafe_allow_html=True)

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
        refresh_pi_btn = st.button('🔄 Sync All from Chalk', key='refresh_pi', type='secondary', use_container_width=True)
        if refresh_pi_btn:
            ss['_sync_confirm'] = True
            st.rerun()

    # Sync confirmation dialog
    if ss.get('_sync_confirm'):
        st.warning('⚠️ This will re-fetch ALL features from Chalk pages and update the DB. This may take 5-10 minutes depending on the number of PIs and features.')
        _cf1, _cf2, _cf3 = st.columns([2, 1, 1])
        with _cf2:
            if st.button('Yes, Sync Now', key='sync_yes', type='primary', use_container_width=True):
                ss['_sync_confirm'] = False
                ss['_sync_running'] = True
                st.rerun()
        with _cf3:
            if st.button('Cancel', key='sync_cancel', use_container_width=True):
                ss['_sync_confirm'] = False
                st.rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Step 2: Feature ID ──
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

    if not ss.get('_sync_confirm'):
        _chk1, _chk2, _chk3 = st.columns([4, 2, 2])
        with _chk2:
            manual_mode = st.checkbox('Manual', value=(ss['feature_mode'] == 'manual'), key='manual_toggle')
            ss['feature_mode'] = 'manual' if manual_mode else 'dropdown'
        with _chk3:
            batch_mode = st.checkbox('Batch', value=False, key='batch_toggle')
    else:
        manual_mode = False
        batch_mode = False

    feature_id = ''
    feature_ids = []  # for batch mode
    if batch_mode and ss['pi_features']:
        # Batch mode with dropdown features
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
        # Batch mode manual — comma-separated input
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

    if feature_ids and len(feature_ids) > 1:
        st.caption('Batch mode: %d features selected' % len(feature_ids))
    elif ss['pi_features'] and ss['feature_mode'] != 'manual':
        st.caption('%d features available in %s' % (len(ss['pi_features']), ss['selected_pi']))

    # ── Step 3: Test Matrix ──
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

            st.download_button('📥 Download: %s (%d TCs)' % (
                    ss.get('last_feature_id', 'Suite'), ss.get('suite_info', {}).get('tc_count', 0)),
                data=Path(ss['result_path']).read_bytes(),
                file_name=Path(ss['result_path']).name,
                use_container_width=True, key='dl_main')

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

    # Use the CLI terminal for progress
    _sync_header = cli_header
    _sync_log = cli_log

    _sync_lines = []
    def _sync_msg(msg):
        _sync_lines.append('[%s] %s' % (ts_short(), msg))
        view = '\n'.join(reversed(_sync_lines[-200:]))
        _sync_log.markdown("<div class='cli-box'><pre>%s</pre></div>" % escape(view), unsafe_allow_html=True)

    _sync_header.markdown("<div class='cli-header'>>> Syncing all features from Chalk...</div>", unsafe_allow_html=True)

    try:
        _sync_msg('Launching browser...')
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True, channel=get_browser_channel())
        ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = ctx.new_page()
        _sync_msg('Browser launched')

        _sync_msg('Discovering PI pages...')
        pi_links = discover_pi_links(page, log=lambda m: None)
        ss['pi_list'] = [(p.label, p.url) for p in pi_links]
        save_pi_pages(ss['pi_list'])
        _sync_msg('Found %d PIs' % len(ss['pi_list']))

        _all = {}
        _chalk_count = 0
        _total_feats = 0
        for _pi_idx, (_pi_label, _pi_url) in enumerate(ss['pi_list'], 1):
            _sync_msg('[%d/%d] Scanning %s...' % (_pi_idx, len(ss['pi_list']), _pi_label))
            _feats = discover_features_on_pi(page, _pi_url, log=lambda m: None)
            _all[_pi_label] = _feats
            save_features(_pi_label, _feats)
            _total_feats += len(_feats)
            _sync_msg('[%d/%d] %s: %d features found' % (_pi_idx, len(ss['pi_list']), _pi_label, len(_feats)))

            for _fi, (_fid, _ftitle) in enumerate(_feats, 1):
                try:
                    _chalk = fetch_feature_from_pi(page, _pi_url, _fid, log=lambda m: None)
                    if _chalk and _chalk.scenarios:
                        save_chalk(_fid, _pi_label, _chalk)
                        _chalk_count += 1
                except:
                    pass
            _sync_msg('[%d/%d] %s: Chalk data cached for %d features' % (_pi_idx, len(ss['pi_list']), _pi_label, _chalk_count))

        ss['all_pi_features'] = _all
        if ss['selected_pi'] and ss['selected_pi'] in _all:
            ss['pi_features'] = _all[ss['selected_pi']]
        else:
            ss['selected_pi'] = None
            ss['selected_pi_url'] = ''
            ss['pi_features'] = []
        ctx.close(); browser.close(); pw.stop()

        _sync_msg('DONE: %d PIs | %d features | %d with Chalk data — saved to DB' % (
            len(_all), _total_feats, _chalk_count))
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
    if feature_ids and len(feature_ids) > 1:
        _features_to_run = [f.strip().upper() for f in feature_ids]
    elif feature_id.strip():
        _features_to_run = [feature_id.strip().upper()]

    if not _features_to_run:
        st.error('Please enter a Feature ID.')
    elif not ss.get('selected_pi'):
        st.error('Please select a PI iteration first.')
    else:
        feature_id = _features_to_run[0]  # primary feature (for single mode compat)
        ss['logs'] = []
        ss['result_path'] = None
        ss['cp_path'] = None
        ss['exit_report'] = None
        ss['batch_results'] = []

        logger = LiveLog(cli_header, cli_log, cli_tools)
        if len(_features_to_run) > 1:
            logger.set('Batch: %d features | %s' % (len(_features_to_run), ss['selected_pi']))
        else:
            logger.set('Starting: %s | %s' % (feature_id, ss['selected_pi']))

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
                for _fi, _current_fid in enumerate(_features_to_run, 1):
                    feature_id = _current_fid
                    _bp = '[%d/%d] ' % (_fi, _batch_count) if _batch_count > 1 else ''

                    # Block 1: Jira Fetch (with self-heal retry)
                    logger.set('%sBlock 1: Fetching Jira %s...' % (_bp, feature_id))
                    jira_result = pipe.run('Jira_%s' % feature_id,
                        lambda fid=feature_id: block_jira_fetch(page, fid, log=logger))
                    jira = jira_result['jira']
                    att_paths = jira_result['att_paths'] if inc_attachments else []
                    exit_items.append('%s%s: Jira fetched — %s' % (_bp, feature_id, jira.summary[:40]))

                    # Block 2: Chalk DB Lookup (with self-heal retry)
                    logger.set('%sBlock 2: Chalk DB lookup %s...' % (_bp, feature_id))
                    chalk_db = pipe.run('ChalkDB_%s' % feature_id,
                        lambda fid=feature_id: block_chalk_db(fid, ss['selected_pi'], log=logger))
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
                        lambda: block_generate_output(suite, feature_id, ss['selected_pi'], strategy, jira=jira, chalk=chalk, log=logger))

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
                    exit_items.append('%s%s: %d TCs | %s' % (_bp, feature_id, output['tc_count'], out_path.name))

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
                    _batch_summary = ['Batch: %d features processed' % len(ss['batch_results'])]
                    for br in ss['batch_results']:
                        _batch_summary.append('  %s: %d TCs | %s' % (br['feature_id'], br['tc_count'], br['file']))
                    ss['exit_report'] = {
                        'title': 'Batch Complete — %d features' % len(ss['batch_results']),
                        'items': _batch_summary + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss['selected_pi']),
                    }
                else:
                    ss['exit_report'] = {
                        'title': 'Generation Complete — %s' % feature_id,
                        'items': exit_items + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss['selected_pi']),
                    }

                st.rerun()

            except PipelineError as pe:
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                logger.set('PIPELINE FAILED — %s' % pe.block_name)
                print('\n[PIPELINE ERROR] %s' % pe, flush=True)
                ss['exit_report'] = {
                    'title': 'Generation FAILED — %s' % feature_id,
                    'items': [
                        'Pipeline block "%s" failed after %d attempts.' % (pe.block_name, pe.attempts),
                        '',
                        'Please Contact Dashboard Admin with below error message:',
                        'Block: %s' % pe.block_name,
                        'Error: %s' % pe.error_msg[:200],
                    ] + exit_items,
                    'footer': 'Failed at %s | Duration: %dm %ds' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s),
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
                logger.set('FAILED after %dm %ds' % (m, s))
                print('\n[ERROR] %s' % e, flush=True)
                traceback.print_exc()
                ss['exit_report'] = {
                    'title': 'Generation FAILED — %s' % feature_id,
                    'items': exit_items + ['ERROR: %s' % str(e)[:200]],
                    'footer': 'Failed at %s | Duration: %dm %ds' % (
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s),
                }
                for _obj_name in ('context', 'browser', 'pw'):
                    _obj = locals().get(_obj_name)
                    if _obj:
                        try:
                            if _obj_name == 'pw': _obj.stop()
                            else: _obj.close()
                        except: pass
                st.rerun()
