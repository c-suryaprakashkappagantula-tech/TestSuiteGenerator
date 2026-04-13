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
        refresh_pi_btn = st.button('Refresh from Chalk', key='refresh_pi', type='secondary', use_container_width=True)

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

    _chk1, _chk2, _chk3 = st.columns([4, 2, 2])
    with _chk2:
        manual_mode = st.checkbox('Manual', value=(ss['feature_mode'] == 'manual'), key='manual_toggle')
        ss['feature_mode'] = 'manual' if manual_mode else 'dropdown'
    with _chk3:
        batch_mode = st.checkbox('Batch', value=False, key='batch_toggle')

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

            st.download_button('Download: %s — %s' % (
                    ss.get('last_feature_id', ''), Path(ss['result_path']).stem[:50]),
                data=Path(ss['result_path']).read_bytes(),
                file_name=Path(ss['result_path']).name,
                use_container_width=True, key='dl_main')

            # Batch mode: show download buttons for ALL generated suites
            if ss.get('batch_results') and len(ss['batch_results']) > 1:
                st.markdown("**All generated suites:**")
                for _bi, _br in enumerate(ss['batch_results']):
                    _bp = Path(_br.get('file_path', _br.get('path', '')))
                    if _bp.exists():
                        st.download_button(
                            'Download: %s — %d TCs | %s' % (_br['feature_id'], _br['tc_count'], _br.get('title', '')[:40]),
                            data=_bp.read_bytes(),
                            file_name=_bp.name,
                            use_container_width=True,
                            key='dl_batch_%d' % _bi)

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

                # ── BATCH LOOP: process each feature ──
                _batch_count = len(_features_to_run)
                for _fi, _current_fid in enumerate(_features_to_run, 1):
                    feature_id = _current_fid
                    _batch_prefix = '[%d/%d] ' % (_fi, _batch_count) if _batch_count > 1 else ''

                    _tick('Jira Fetch')
                    logger.set('%s[2/8] Fetching Jira: %s...' % (_batch_prefix, feature_id))
                    jira = fetch_jira_issue(page, feature_id, log=logger)
                    exit_items.append('%sJira fetched: %s' % (_batch_prefix, jira.summary[:50]))

                    parsed_docs = []
                    if inc_attachments and jira.attachments:
                        _tick('Attachments_%s' % feature_id)
                        att_paths = download_attachments(page, jira, log=logger)
                        for ap in att_paths:
                            parsed_docs.append(parse_file(ap, log=logger, source='Jira Attachment'))

                    _tick('Chalk_%s' % feature_id)
                    logger.set('%sFetching Chalk: %s...' % (_batch_prefix, feature_id))
                    chalk = load_chalk_as_object(feature_id, ss['selected_pi'])
                    if not (chalk and chalk.scenarios):
                        from modules.database import _conn as _db_conn
                        _c = _db_conn()
                        _row = _c.execute('SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != "[]" LIMIT 1',
                                          (feature_id,)).fetchone()
                        _c.close()
                        if _row:
                            chalk = load_chalk_as_object(feature_id, _row['pi_label'])
                        if not (chalk and chalk.scenarios):
                            chalk = fetch_feature_from_pi(page, ss['selected_pi_url'], feature_id, log=logger)
                            if chalk and chalk.scenarios:
                                save_chalk(feature_id, ss['selected_pi'], chalk)
                            else:
                                for _sl, _su in ss['pi_list']:
                                    if _sl == ss['selected_pi']: continue
                                    try:
                                        _sc = fetch_feature_from_pi(page, _su, feature_id, log=lambda m: None)
                                        if _sc and _sc.scenarios:
                                            chalk = _sc; save_chalk(feature_id, _sl, chalk); break
                                    except: pass
                                if not chalk or not chalk.scenarios:
                                    from modules.chalk_parser import ChalkData
                                    chalk = ChalkData(feature_id=feature_id)

                    if uploaded_files and _fi == 1:
                        for uf in uploaded_files:
                            save_path = INPUTS / uf.name
                            save_path.write_bytes(uf.getvalue())
                            parsed_docs.append(parse_file(save_path, log=logger))

                    _tick('Engine_%s' % feature_id)
                    logger.set('%sBuilding suite: %s...' % (_batch_prefix, feature_id))
                    options = {
                        'channel': channel, 'devices': devices, 'networks': networks,
                        'sim_types': sim_types, 'os_platforms': os_platforms,
                        'include_positive': inc_positive,
                        'include_negative': inc_negative, 'include_e2e': inc_e2e,
                        'include_edge': inc_edge, 'include_attachments': inc_attachments,
                        'strategy': strategy, 'custom_instructions': custom_instructions,
                    }

                    suite = build_test_suite(jira, chalk, parsed_docs, options, log=logger)
                    total_steps = sum(len(tc.steps) for tc in suite.test_cases)

                    _tick('Excel_%s' % feature_id)
                    logger.set('%sGenerating Excel: %s...' % (_batch_prefix, feature_id))
                    out_path = generate_excel(suite, log=logger)

                    sheet_count = len(suite.groups) + 2 if len(suite.groups) > 1 else 3
                    if hasattr(suite, 'combinations') and suite.combinations and len(suite.combinations) > 1:
                        sheet_count += 1
                    ss['result_path'] = str(out_path)
                    ss['suite_info'] = {'tc_count': len(suite.test_cases), 'step_count': total_steps, 'sheet_count': sheet_count}
                    ss['last_feature_id'] = feature_id

                    try:
                        _suite_id = save_test_suite(suite, file_path=str(out_path))
                        ss['last_suite_id'] = _suite_id
                    except: pass

                    ss['batch_results'].append({
                        'feature_id': feature_id, 'tc_count': len(suite.test_cases),
                        'step_count': total_steps, 'file': out_path.name, 'file_path': str(out_path),
                        'title': jira.summary[:60]})
                    exit_items.append('%s%s: %d TCs | %s' % (_batch_prefix, feature_id, len(suite.test_cases), out_path.name))

                    log_generation(feature_id, ss['selected_pi'], len(suite.test_cases), total_steps, strategy, str(out_path))
                    log_generation_db(feature_id, ss['selected_pi'], len(suite.test_cases), total_steps, strategy, str(out_path))

                # ── END BATCH LOOP — close browser ──
                _tick('Browser Close')
                context.close(); browser.close(); pw.stop()

                # Finalize timing
                _tick('_end')
                timings.pop()

                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                if len(_features_to_run) > 1:
                    logger.set('[BATCH] DONE — %d features in %dm %ds' % (len(_features_to_run), m, s))
                else:
                    logger.set('[10/10] DONE in %dm %ds' % (m, s))

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
                ss['cp_path'] = None
                ss['suite_info'] = {'tc_count': len(suite.test_cases), 'step_count': total_steps, 'sheet_count': sheet_count}

                # Build exit report
                if len(_features_to_run) > 1:
                    _batch_summary = ['Batch: %d features processed' % len(ss['batch_results'])]
                    for br in ss['batch_results']:
                        _batch_summary.append('  %s: %d TCs | %s' % (br['feature_id'], br['tc_count'], br['file']))
                    timing_items = ['⏱ %s: %.1fs' % (name, dur) for name, dur in timings]
                    ss['exit_report'] = {
                        'title': 'Batch Generation Complete — %d features' % len(ss['batch_results']),
                        'items': _batch_summary + [''] + exit_items[-5:] + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss['selected_pi']),
                    }
                else:
                    timing_items = ['⏱ %s: %.1fs' % (name, dur) for name, dur in timings]
                    ss['exit_report'] = {
                        'title': 'Generation Complete - %s' % feature_id,
                        'items': exit_items + [''] + timing_items,
                        'footer': 'Completed at %s | Duration: %dm %ds | PI: %s' % (
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S'), m, s, ss['selected_pi']),
                    }

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
