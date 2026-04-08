"""
TSG_Dashboard_V1.0.py -- Test Suite Generator Dashboard
Streamlit UI: Discover PIs from Chalk -> Select PI -> Enter Feature ID ->
Fetch Jira + Chalk + Attachments -> Build Test Suite -> Excel Output.

Usage:  streamlit run TSG_Dashboard_V1.0.py
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
                             CHANNELS, DEVICE_TYPES, NETWORK_TYPES, SIM_TYPES,
                             BROWSER_CHANNEL, BROWSER_HEADLESS, NAVY, ts_short)
from modules.jira_fetcher import fetch_jira_issue, download_attachments
from modules.chalk_parser import discover_pi_links, fetch_feature_from_pi, discover_features_on_pi
from modules.doc_parser import parse_file
from modules.test_engine import build_test_suite
from modules.excel_generator import generate_excel

if sys.platform.startswith('win'):
    try:
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# ================================================================
# PAGE CONFIG + CSS
# ================================================================
st.set_page_config(page_title='Test Suite Generator', page_icon='TSG', layout='wide')

st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
html, body { margin:0; padding:0; scroll-behavior: smooth; }
.stApp { background: #0e1117; color: #f0f2f6; font-family: "Inter", sans-serif; }
[data-testid="stToolbar"], header[data-testid="stHeader"], #MainMenu, footer,
header[tabindex="-1"] { display:none !important; visibility:hidden !important; height:0 !important; }
.block-container { padding-top: 0.5rem !important; max-width: 100% !important; }

.top-bar { background: #1a1d24; border: 1px solid rgba(255,255,255,0.1); border-radius: 18px;
           padding: 14px 24px; margin-bottom: 14px; display: flex; align-items: center;
           justify-content: space-between; }
.top-bar .title { font-weight: 800; font-size: 24px;
    background: linear-gradient(135deg, #fff 0%, #22d3ee 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.top-bar .ver { color: #67e8f9; font-size: 12px; font-weight: 600; }

.card { background: #1a1d24; border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
        padding: 16px 18px; margin-bottom: 10px; }
.section-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;
                 color: #94a3b8; margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.section-title::after { content: ""; flex: 1; height: 1px;
    background: linear-gradient(90deg, rgba(255,255,255,0.1), transparent); }

.cli-header { font-weight: 700; color: #fff; letter-spacing: 0.3px;
    background: rgba(6,182,212,0.15); border: 1px solid rgba(6,182,212,0.3);
    border-radius: 10px; padding: 8px 12px; margin: 0 0 8px 0;
    display: flex; align-items: center; gap: 8px; font-size: 13px; }
.cli-box { background: #141720; color: #c8d1dc; border: 1px solid rgba(255,255,255,0.08);
           border-radius: 12px; padding: 12px 14px;
           height: 420px; min-height: 420px; max-height: 420px;
           overflow-y: auto; overflow-x: auto;
           font-family: "JetBrains Mono", ui-monospace, monospace; font-size: 11.5px; line-height: 1.55; }
.cli-box pre { margin: 0; white-space: pre-wrap; word-wrap: break-word; }
.cli-box::-webkit-scrollbar { width: 8px; }
.cli-box::-webkit-scrollbar-track { background: rgba(255,255,255,0.05); border-radius: 4px; }
.cli-box::-webkit-scrollbar-thumb { background: rgba(6,182,212,0.5); border-radius: 4px; }
.cli-box::-webkit-scrollbar-thumb:hover { background: rgba(6,182,212,0.8); }

.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 10px 0; }
.stat { background: #1a1d24; border: 1px solid rgba(255,255,255,0.1); border-radius: 14px;
        padding: 12px; text-align: center; }
.stat .label { font-size: 10px; color: #94a3b8; font-weight: 600; text-transform: uppercase; }
.stat .value { font-size: 26px; font-weight: 800; color: #fff; margin-top: 2px; }

.pi-btn { display: inline-block; padding: 8px 16px; margin: 3px; border-radius: 10px;
          font-weight: 700; font-size: 13px; cursor: pointer; transition: all 0.2s;
          border: 1px solid rgba(6,182,212,0.3); color: #fff; background: rgba(6,182,212,0.1); }
.pi-btn:hover { background: rgba(6,182,212,0.3); transform: translateY(-1px); }
.pi-btn.active { background: linear-gradient(135deg, #0e7490, #22d3ee); border-color: transparent; }

.exit-report { background: rgba(6,182,212,0.08); border: 1px solid rgba(6,182,212,0.25);
               border-radius: 16px; padding: 16px 18px; margin-top: 12px; }
.exit-report h4 { margin: 0 0 8px 0; color: #22d3ee; font-weight: 700; }
.exit-report ul { margin: 0; padding-left: 20px; color: #c8d1dc; }
.exit-report li { margin: 3px 0; }

.stButton > button { background: linear-gradient(135deg, #0e7490, #22d3ee) !important;
    color: #fff !important; border: 0 !important; padding: 0.35rem 0.5rem !important;
    border-radius: 8px !important; font-weight: 700 !important; font-size: 11px !important;
    min-height: 0 !important; line-height: 1.2 !important; }
.stButton > button:hover { transform: translateY(-2px) !important;
    box-shadow: 0 8px 24px rgba(6,182,212,0.4) !important; }
.stTextInput input { color: #000 !important; background: rgba(255,255,255,0.9) !important;
    border-radius: 10px !important; font-weight: 500 !important; }
.stTextInput label, .stSelectbox label, .stMultiSelect label, .stRadio label,
.stFileUploader label, .stCheckbox label, .stCheckbox span,
[data-testid="stWidgetLabel"] p, [data-testid="stWidgetLabel"] label,
[data-testid="stMarkdownContainer"] p,
.stRadio > div > label, .stMultiSelect > label,
.stCaption, [data-testid="stCaptionContainer"] p {
    color: #e2e8f0 !important; font-weight: 700 !important; }
.stSelectbox [data-testid="stMarkdownContainer"] p { color: #e2e8f0 !important; font-weight: 700 !important; }
.stRadio > div > div > label { color: #e2e8f0 !important; font-weight: 600 !important; }
.stDownloadButton > button { background: rgba(255,255,255,0.06) !important;
    border: 1px solid rgba(255,255,255,0.1) !important; color: #fff !important;
    border-radius: 10px !important; font-weight: 600 !important; }
</style>""", unsafe_allow_html=True)

# ================================================================
# DEFAULT PI LIST (hardcoded — no Chalk fetch needed on startup)
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
    'pi_list': list(_DEFAULT_PIS),  # [(label, url), ...]
    'selected_pi': None,
    'selected_pi_url': '',
    'pi_features': [],              # [(feature_id, title), ...] from selected PI
    'feature_mode': 'dropdown',     # 'dropdown' or 'manual'
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
# HEADER
# ================================================================
st.markdown("""<div class='top-bar'>
  <div><div class='title'>TSG - Test Suite Generator</div></div>
  <div class='ver'>V1.0 | Chalk + Jira + Attachments | Any Feature ID</div>
</div>""", unsafe_allow_html=True)

# ================================================================
# LAYOUT: Left (inputs) | Right (CLI + output)
# ================================================================
left, right = st.columns([1.2, 1])

# ────────────────────────────────────────────────────────────────
# LEFT PANEL
# ────────────────────────────────────────────────────────────────
with left:
    # ── PI Selection (hardcoded, instant — no Chalk fetch needed) ──
    st.markdown("<div class='section-title'>Step 1: Select PI Iteration</div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)

    pi_list = ss['pi_list']
    # PI buttons - compact single row
    cols = st.columns(len(pi_list))
    for j, (label, url) in enumerate(pi_list):
        with cols[j]:
            is_selected = ss['selected_pi'] == label
            btn_type = 'primary' if is_selected else 'secondary'
            if st.button(label, key=f'pi_{label}', type=btn_type, use_container_width=True):
                ss['selected_pi'] = label
                ss['selected_pi_url'] = url
                ss['pi_features'] = []  # reset features when PI changes
                st.rerun()

    # Selected indicator + Refresh button
    rc1, rc2 = st.columns([3, 1])
    with rc1:
        if ss['selected_pi']:
            st.success(f'Selected: **{ss["selected_pi"]}**')
        else:
            st.caption('Select a PI iteration above')
    with rc2:
        refresh_pi_btn = st.button('Refresh from Chalk', key='refresh_pi', type='secondary', use_container_width=True)

    st.markdown("</div>", unsafe_allow_html=True)

    # ── Feature ID (dropdown from Chalk OR manual entry) ──
    st.markdown("<div class='section-title'>Step 2: Feature ID</div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)

    # Fetch features button (when PI selected but features not loaded yet)
    if ss['selected_pi'] and not ss['pi_features']:
        if st.button('Fetch Available Features from %s' % ss['selected_pi'], key='fetch_feats', use_container_width=True):
            with st.spinner('Scanning %s for features...' % ss['selected_pi']):
                try:
                    _pw = sync_playwright().start()
                    _br = _pw.chromium.launch(headless=True, channel=BROWSER_CHANNEL)
                    _cx = _br.new_context(viewport={'width': 1920, 'height': 1080})
                    _pg = _cx.new_page()
                    _feats = discover_features_on_pi(_pg, ss['selected_pi_url'], log=lambda m: None)
                    ss['pi_features'] = _feats
                    _cx.close(); _br.close(); _pw.stop()
                    st.rerun()
                except Exception as _e:
                    st.error('Failed: %s' % _e)
                    try: _cx.close()
                    except: pass
                    try: _br.close()
                    except: pass
                    try: _pw.stop()
                    except: pass

    # Toggle: dropdown vs manual
    fc1, fc2 = st.columns([5, 1])
    with fc2:
        manual_mode = st.checkbox('Manual entry', value=(ss['feature_mode'] == 'manual'), key='manual_toggle')
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

    # ── Test Matrix ──
    st.markdown("<div class='section-title'>Step 3: Test Matrix</div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    mc1, mc2 = st.columns(2)
    with mc1:
        channel = st.multiselect('Channel', CHANNELS, default=['ITMBO'])
        devices = st.multiselect('Device Types', DEVICE_TYPES, default=['Mobile'])
    with mc2:
        networks = st.multiselect('Network Types', NETWORK_TYPES, default=['4G', '5G'])
        sim_types = st.multiselect('SIM Types', SIM_TYPES, default=['eSIM', 'pSIM'])
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Options ──
    st.markdown("<div class='section-title'>Step 4: Options</div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
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

    # ── Upload ──
    st.markdown("<div class='section-title'>Step 5: Additional Docs (Optional)</div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    uploaded_files = st.file_uploader('Upload HLD/LLD/Solution docs',
                                      type=['docx', 'xlsx', 'pdf', 'txt'],
                                      accept_multiple_files=True)
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Action Buttons ──
    bc1, bc2, bc3, bc4 = st.columns([1.5, 0.6, 0.6, 0.6])
    with bc1:
        run_btn = st.button('Execute - Generate Test Suite', type='primary', use_container_width=True)
    with bc2:
        clear_btn = st.button('Clear All', use_container_width=True)
    with bc3:
        reload_btn = st.button('Reload', use_container_width=True)
    with bc4:
        cp_btn = st.button('Checkpoints', use_container_width=True)

# ────────────────────────────────────────────────────────────────
# RIGHT PANEL: CLI + Output
# ────────────────────────────────────────────────────────────────
with right:
    st.markdown("<div id='cli'></div>", unsafe_allow_html=True)
    st.markdown("<div class='section-title'>Live Terminal</div>", unsafe_allow_html=True)
    cli_header = st.empty()
    cli_log = st.empty()
    cli_tools = st.empty()

    # Show persisted logs
    if ss['logs']:
        view = '\n'.join(reversed(ss['logs'][-1200:]))
        cli_log.markdown("<div class='cli-box'><pre>%s</pre></div>" % escape(view), unsafe_allow_html=True)

    # ── Output Panel ──
    st.markdown("<div class='section-title'>Output</div>", unsafe_allow_html=True)
    output_area = st.container()

    if ss.get('result_path') and Path(ss['result_path']).exists():
        with output_area:
            info = ss.get('suite_info', {})
            st.markdown(f"""<div class='stat-row'>
                <div class='stat'><div class='label'>Test Cases</div><div class='value'>{info.get('tc_count', 0)}</div></div>
                <div class='stat'><div class='label'>Steps</div><div class='value'>{info.get('step_count', 0)}</div></div>
                <div class='stat'><div class='label'>Sheets</div><div class='value'>3</div></div>
                <div class='stat'><div class='label'>Status</div><div class='value'>OK</div></div>
            </div>""", unsafe_allow_html=True)

            dc1, dc2 = st.columns(2)
            with dc1:
                st.download_button('Download Test Suite',
                    data=Path(ss['result_path']).read_bytes(),
                    file_name=Path(ss['result_path']).name,
                    use_container_width=True, key='dl_main')

    # ── Exit Report ──
    if ss.get('exit_report'):
        rpt = ss['exit_report']
        st.markdown("<div class='exit-report'>" +
            f"<h4>{escape(rpt.get('title', 'Completed'))}</h4>" +
            "<ul>" + ''.join(f"<li>{escape(item)}</li>" for item in rpt.get('items', [])) + "</ul>" +
            f"<p style='color:#67e8f9;font-size:12px;margin-top:8px;'>{escape(rpt.get('footer', ''))}</p>" +
            "</div>", unsafe_allow_html=True)

# ================================================================
# LIVE LOG HELPER (same pattern as MDA_Jira_Dashboard_V3.0)
# ================================================================
class LiveLog:
    def __init__(self, header_ph, log_ph, tools_ph):
        self.header = header_ph
        self.log_ph = log_ph
        self.tools = tools_ph
        self.lines = list(ss.get('logs', []))
        self._ver = 0

    def set(self, text):
        self.header.markdown(f"<div class='cli-header'>>> {escape(text)}</div>", unsafe_allow_html=True)

    def write(self, s):
        parts = s.splitlines(True)
        if not parts:
            return
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

# ── Clear ──
if clear_btn:
    for k in defaults:
        ss[k] = defaults[k]
    st.rerun()

# ── Reload ──
if reload_btn:
    st.cache_resource.clear()
    st.cache_data.clear()
    for k in defaults:
        ss[k] = defaults[k]
    st.toast('Modules reloaded!')
    st.rerun()

# ── Checkpoints ──
if cp_btn:
    cps = sorted(CHECKPOINTS.glob('*.xlsx'), reverse=True)
    if cps:
        st.sidebar.title('Checkpoints')
        for cp in cps[:15]:
            st.sidebar.download_button(cp.name, data=cp.read_bytes(), file_name=cp.name, key=f'cp_{cp.stem}')
    else:
        st.sidebar.info('No checkpoints yet.')

# ── Refresh PI Links from Chalk (only when user clicks Refresh) ──
if refresh_pi_btn:
    with st.spinner('Refreshing PI iterations from Chalk...'):
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True, channel=BROWSER_CHANNEL)
            ctx = browser.new_context(viewport={'width': 1920, 'height': 1080})
            page = ctx.new_page()
            pi_links = discover_pi_links(page, log=lambda m: None)
            # Convert PILink objects to (label, url) tuples
            ss['pi_list'] = [(p.label, p.url) for p in pi_links]
            ss['selected_pi'] = None
            ss['selected_pi_url'] = ''
            ctx.close(); browser.close(); pw.stop()
            st.rerun()
        except Exception as e:
            st.error(f'Failed to refresh PIs: {e}')
            try: ctx.close()
            except: pass
            try: browser.close()
            except: pass
            try: pw.stop()
            except: pass

# ── MAIN EXECUTION ──
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
        logger.set(f'Starting: {feature_id} | {ss["selected_pi"]}')

        # Auto-scroll to CLI
        st.markdown("<script>document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>",
                    unsafe_allow_html=True)
        import streamlit.components.v1 as components
        components.html("<script>parent.document.getElementById('cli').scrollIntoView({behavior:'smooth'});</script>", height=0)

        t0 = time.time()
        exit_items = []

        with redirect_stdout(logger):
            try:
                # ── Launch Browser ──
                logger.set('Launching browser...')
                print('[INIT] Launching browser...', flush=True)
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True, channel=BROWSER_CHANNEL)
                context = browser.new_context(accept_downloads=True, viewport={'width': 1920, 'height': 1080})
                page = context.new_page()
                exit_items.append('Browser launched')

                # ── Fetch Jira ──
                logger.set(f'Fetching Jira: {feature_id}...')
                jira = fetch_jira_issue(page, feature_id, log=logger)
                exit_items.append(f'Jira fetched: {jira.summary[:50]}')

                # ── Download Attachments ──
                parsed_docs = []
                if inc_attachments and jira.attachments:
                    logger.set(f'Downloading {len(jira.attachments)} attachment(s)...')
                    att_paths = download_attachments(page, jira, log=logger)
                    for ap in att_paths:
                        logger.set(f'Parsing: {ap.name}...')
                        parsed_docs.append(parse_file(ap, log=logger))
                    exit_items.append(f'Attachments: {len(att_paths)} downloaded & parsed')
                else:
                    exit_items.append('Attachments: skipped')

                # ── Fetch Chalk (selected PI page) ──
                logger.set(f'Fetching Chalk: {ss["selected_pi"]}...')
                chalk = fetch_feature_from_pi(page, ss['selected_pi_url'], feature_id, log=logger)
                if chalk.scenarios:
                    exit_items.append(f'Chalk: {len(chalk.scenarios)} scenarios from {ss["selected_pi"]}')
                else:
                    exit_items.append(f'Chalk: Feature {feature_id} not found in {ss["selected_pi"]}')

                # ── Parse Uploaded Files ──
                if uploaded_files:
                    for uf in uploaded_files:
                        logger.set(f'Parsing upload: {uf.name}...')
                        save_path = INPUTS / uf.name
                        save_path.write_bytes(uf.getvalue())
                        parsed_docs.append(parse_file(save_path, log=logger))
                    exit_items.append(f'Uploads: {len(uploaded_files)} parsed')

                # ── Close Browser ──
                context.close(); browser.close(); pw.stop()
                exit_items.append('Browser closed')

                # ── Build Test Suite ──
                logger.set('Building test suite...')
                options = {
                    'channel': channel, 'devices': devices, 'networks': networks,
                    'sim_types': sim_types, 'include_positive': inc_positive,
                    'include_negative': inc_negative, 'include_e2e': inc_e2e,
                    'include_edge': inc_edge, 'include_attachments': inc_attachments,
                }
                suite = build_test_suite(jira, chalk, parsed_docs, options, log=logger)
                total_steps = sum(len(tc.steps) for tc in suite.test_cases)
                exit_items.append(f'Suite built: {len(suite.test_cases)} TCs | {total_steps} steps')

                # ── Generate Excel ──
                logger.set('Generating Excel...')
                out_path = generate_excel(suite, log=logger)
                exit_items.append(f'Excel: {out_path.name}')

                # Find checkpoint
                cps = sorted(CHECKPOINTS.glob(f'CHECKPOINT_{feature_id}*'), reverse=True)
                cp_path = str(cps[0]) if cps else None
                if cp_path:
                    exit_items.append(f'Checkpoint: {Path(cp_path).name}')

                # ── Done ──
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                logger.set(f'DONE in {m}m {s}s')
                print(f'\n[DONE] Total time: {m}m {s}s', flush=True)

                ss['result_path'] = str(out_path)
                ss['cp_path'] = cp_path
                ss['suite_info'] = {'tc_count': len(suite.test_cases), 'step_count': total_steps}
                ss['exit_report'] = {
                    'title': f'Generation Complete - {feature_id}',
                    'items': exit_items,
                    'footer': f'Completed at {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Duration: {m}m {s}s | PI: {ss["selected_pi"]}',
                }
                st.rerun()

            except Exception as e:
                elapsed = time.time() - t0
                m, s = divmod(int(elapsed), 60)
                logger.set(f'FAILED after {m}m {s}s')
                print(f'\n[ERROR] {e}', flush=True)
                traceback.print_exc()
                exit_items.append(f'ERROR: {str(e)[:100]}')
                ss['exit_report'] = {
                    'title': f'Generation FAILED - {feature_id}',
                    'items': exit_items,
                    'footer': f'Failed at {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | Duration: {m}m {s}s',
                }
                try: context.close()
                except: pass
                try: browser.close()
                except: pass
                try: pw.stop()
                except: pass
                st.rerun()
