"""
PI53_Feature_Dashboard.py — Jira Features Dashboard for PI-53 (from Chalk)
Interactive Web UI showing all PI-53 features with Jira + Chalk data,
test generation history, and coverage metrics.

Usage:  streamlit run PI53_Feature_Dashboard.py
"""
import sys, os, json, time
from pathlib import Path
from datetime import datetime
from html import escape

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from modules.database import (
    init_db, load_features, load_all_features, load_jira, load_chalk,
    load_chalk_as_object, get_db_stats, get_chalk_cache_count,
    get_all_suite_history, load_latest_suite,
)
from modules.config import ROOT, OUTPUTS
from modules.theme_v2 import CSS

# ================================================================
# PAGE CONFIG
# ================================================================
st.set_page_config(
    page_title='PI-53 Feature Dashboard',
    page_icon='🧬',
    layout='wide',
)
st.markdown(CSS, unsafe_allow_html=True)

# ================================================================
# EXTRA CSS — Feature cards, tables, filters
# ================================================================
st.markdown("""<style>
/* Feature card grid */
.feat-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 14px; margin: 14px 0; }
.feat-card {
    background: rgba(15,15,25,0.65);
    backdrop-filter: blur(18px);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 18px; padding: 16px 18px;
    transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
    cursor: pointer; position: relative; overflow: hidden;
}
.feat-card::before {
    content: ""; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: var(--accent, linear-gradient(90deg, #8b5cf6, #3b82f6));
    opacity: 0.7;
}
.feat-card:hover {
    transform: translateY(-4px);
    border-color: rgba(139,92,246,0.35);
    box-shadow: 0 12px 35px rgba(0,0,0,0.35), 0 0 20px rgba(139,92,246,0.1);
}
.feat-card .fid {
    font-family: "JetBrains Mono", monospace; font-size: 12px; font-weight: 700;
    color: #c084fc; letter-spacing: 0.5px;
}
.feat-card .ftitle {
    font-size: 13px; font-weight: 600; color: #e2e8f0; margin: 6px 0 8px 0;
    line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2;
    -webkit-box-orient: vertical; overflow: hidden;
}
.feat-card .fmeta {
    display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px;
}
.feat-card .ftag {
    font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 8px;
    text-transform: uppercase; letter-spacing: 0.3px;
}
.ftag.status-dev { background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid rgba(251,191,36,0.25); }
.ftag.status-done { background: rgba(34,197,94,0.15); color: #22c55e; border: 1px solid rgba(34,197,94,0.25); }
.ftag.status-todo { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid rgba(100,116,139,0.25); }
.ftag.status-test { background: rgba(59,130,246,0.15); color: #60a5fa; border: 1px solid rgba(59,130,246,0.25); }
.ftag.channel { background: rgba(236,72,153,0.12); color: #f472b6; border: 1px solid rgba(236,72,153,0.2); }
.ftag.priority-high { background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(239,68,68,0.25); }
.ftag.priority-med { background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid rgba(251,191,36,0.25); }
.ftag.priority-low { background: rgba(34,197,94,0.15); color: #22c55e; border: 1px solid rgba(34,197,94,0.25); }
.ftag.chalk-yes { background: rgba(139,92,246,0.15); color: #a78bfa; border: 1px solid rgba(139,92,246,0.25); }
.ftag.chalk-no { background: rgba(100,116,139,0.1); color: #64748b; border: 1px solid rgba(100,116,139,0.15); }
.ftag.gen { background: rgba(6,182,212,0.15); color: #22d3ee; border: 1px solid rgba(6,182,212,0.25); }

/* Detail panel */
.detail-panel {
    background: rgba(12,12,20,0.7);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(139,92,246,0.15);
    border-radius: 20px; padding: 22px 24px; margin: 14px 0;
}
.detail-panel h3 {
    margin: 0 0 4px 0; font-weight: 800;
    background: linear-gradient(135deg, #c084fc, #60a5fa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.detail-section {
    background: rgba(15,15,25,0.5);
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px; padding: 14px 16px; margin: 10px 0;
}
.detail-section h4 {
    font-size: 12px; font-weight: 800; text-transform: uppercase;
    letter-spacing: 1px; color: #8b5cf6; margin: 0 0 8px 0;
}
.detail-section p, .detail-section li {
    font-size: 13px; color: #cbd5e1; line-height: 1.6;
}
.scenario-card {
    background: rgba(20,20,35,0.6);
    border: 1px solid rgba(139,92,246,0.1);
    border-radius: 12px; padding: 12px 14px; margin: 8px 0;
}
.scenario-card .sid {
    font-family: "JetBrains Mono", monospace; font-size: 11px;
    color: #a78bfa; font-weight: 700;
}
.scenario-card .stitle { font-size: 13px; color: #e2e8f0; font-weight: 600; margin: 4px 0; }
.scenario-card .smeta { font-size: 11px; color: #64748b; }

/* Summary table */
.sum-table { width: 100%; border-collapse: collapse; margin: 10px 0; }
.sum-table th {
    background: rgba(139,92,246,0.12); color: #c084fc; font-size: 11px;
    font-weight: 800; text-transform: uppercase; letter-spacing: 0.5px;
    padding: 10px 12px; text-align: left;
    border-bottom: 1px solid rgba(139,92,246,0.15);
}
.sum-table td {
    padding: 8px 12px; font-size: 12px; color: #cbd5e1;
    border-bottom: 1px solid rgba(255,255,255,0.04);
}
.sum-table tr:hover td { background: rgba(139,92,246,0.05); }

/* Donut chart placeholder */
.donut-wrap { display: flex; gap: 20px; flex-wrap: wrap; justify-content: center; margin: 14px 0; }
</style>""", unsafe_allow_html=True)

# ================================================================
# INIT DB
# ================================================================
init_db()

# ================================================================
# HELPERS
# ================================================================
PI_LABEL = 'PI-53'

def _status_class(status):
    s = (status or '').lower()
    if 'complete' in s or 'done' in s or 'closed' in s:
        return 'status-done'
    if 'progress' in s or 'dev' in s or 'review' in s:
        return 'status-dev'
    if 'test' in s or 'qa' in s or 'uat' in s:
        return 'status-test'
    return 'status-todo'

def _priority_class(priority):
    p = (priority or '').lower()
    if 'critical' in p or 'blocker' in p or 'highest' in p:
        return 'priority-high'
    if 'major' in p or 'high' in p or 'medium' in p:
        return 'priority-med'
    return 'priority-low'

def _safe(text, maxlen=200):
    if not text:
        return ''
    return escape(str(text)[:maxlen])

# ================================================================
# LOAD DATA
# ================================================================
@st.cache_data(ttl=300)
def load_pi53_data():
    """Load all PI-53 features with Jira + Chalk enrichment."""
    features = load_features(PI_LABEL)
    enriched = []
    for fid, title in features:
        jira = load_jira(fid)
        chalk = load_chalk(fid, PI_LABEL)
        suite = load_latest_suite(fid) if hasattr(sys.modules.get('modules.database', None), 'load_latest_suite') else None
        try:
            suite = load_latest_suite(fid)
        except Exception:
            suite = None

        scenarios_count = 0
        if chalk and chalk.get('scenarios_json'):
            try:
                scenarios_count = len(json.loads(chalk['scenarios_json']))
            except Exception:
                pass

        enriched.append({
            'feature_id': fid,
            'title': (jira or {}).get('summary', '') or title,
            'status': (jira or {}).get('status', ''),
            'priority': (jira or {}).get('priority', ''),
            'assignee': (jira or {}).get('assignee', ''),
            'channel': (jira or {}).get('channel', ''),
            'labels': json.loads((jira or {}).get('labels_json', '[]')) if jira else [],
            'ac_text': (jira or {}).get('ac_text', ''),
            'description': (jira or {}).get('description', ''),
            'has_jira': jira is not None,
            'has_chalk': chalk is not None,
            'chalk_scenarios': scenarios_count,
            'chalk_scope': (chalk or {}).get('scope', ''),
            'chalk_rules': (chalk or {}).get('rules', ''),
            'subtasks': json.loads((jira or {}).get('subtasks_json', '[]')) if jira else [],
            'links': json.loads((jira or {}).get('links_json', '[]')) if jira else [],
            'comments': json.loads((jira or {}).get('comments_json', '[]')) if jira else [],
            'suite_tc_count': (suite or {}).get('tc_count', 0) if suite else 0,
            'suite_step_count': (suite or {}).get('step_count', 0) if suite else 0,
        })
    return enriched

features_data = load_pi53_data()

# ================================================================
# COMPUTE STATS
# ================================================================
total_features = len(features_data)
with_jira = sum(1 for f in features_data if f['has_jira'])
with_chalk = sum(1 for f in features_data if f['has_chalk'])
total_scenarios = sum(f['chalk_scenarios'] for f in features_data)
total_tcs = sum(f['suite_tc_count'] for f in features_data)
generated = sum(1 for f in features_data if f['suite_tc_count'] > 0)

status_dist = {}
for f in features_data:
    s = f['status'] or 'Unknown'
    status_dist[s] = status_dist.get(s, 0) + 1

channel_dist = {}
for f in features_data:
    ch = f['channel'] or 'Unknown'
    channel_dist[ch] = channel_dist.get(ch, 0) + 1

assignee_dist = {}
for f in features_data:
    a = f['assignee'] or 'Unassigned'
    assignee_dist[a] = assignee_dist.get(a, 0) + 1

# ================================================================
# BANNER
# ================================================================
st.markdown(f"""<div class='banner'>
  <div>
    <div class='title'>PI-53 &mdash; Feature Dashboard</div>
    <div class='sub'>Jira + Chalk &rarr; {total_features} Features &bull; {total_scenarios} Chalk Scenarios &bull; {total_tcs} Generated TCs</div>
  </div>
  <div style='display:flex; gap:8px; align-items:center; flex-wrap:wrap;'>
    <div class='badge'>PI-53</div>
    <div class='badge'>🧬 {total_features} Features</div>
    <div class='badge'>📋 {with_chalk}/{total_features} Chalk</div>
    <div class='badge'>🎯 {generated}/{total_features} Generated</div>
  </div>
</div>""", unsafe_allow_html=True)

# ================================================================
# STATS ROW
# ================================================================
st.markdown(f"""<div class='stats-row'>
  <div class='stat-card' style='--accent: #8b5cf6;'>
    <div class='icon'>🧬</div>
    <div class='label'>Total Features</div>
    <div class='value'>{total_features}</div>
  </div>
  <div class='stat-card' style='--accent: #3b82f6;'>
    <div class='icon'>📋</div>
    <div class='label'>Chalk Scenarios</div>
    <div class='value'>{total_scenarios}</div>
  </div>
  <div class='stat-card' style='--accent: #22c55e;'>
    <div class='icon'>✅</div>
    <div class='label'>Generated TCs</div>
    <div class='value'>{total_tcs}</div>
  </div>
  <div class='stat-card' style='--accent: #f59e0b;'>
    <div class='icon'>🔗</div>
    <div class='label'>Jira Cached</div>
    <div class='value'>{with_jira}</div>
  </div>
</div>""", unsafe_allow_html=True)

# ================================================================
# TABS
# ================================================================
tab_overview, tab_detail, tab_coverage, tab_matrix = st.tabs([
    '📊 Overview', '🔍 Feature Detail', '📈 Coverage Analysis', '🗂 Full Matrix'
])

# ================================================================
# TAB 1: OVERVIEW — Feature Cards with Filters
# ================================================================
with tab_overview:
    # Filters
    fcol1, fcol2, fcol3, fcol4 = st.columns(4)
    with fcol1:
        filter_status = st.multiselect('Status', sorted(status_dist.keys()), default=[], key='f_status')
    with fcol2:
        filter_channel = st.multiselect('Channel', sorted(channel_dist.keys()), default=[], key='f_channel')
    with fcol3:
        filter_chalk = st.selectbox('Chalk Data', ['All', 'Has Chalk', 'No Chalk'], key='f_chalk')
    with fcol4:
        filter_search = st.text_input('🔍 Search', '', key='f_search')

    # Apply filters
    filtered = features_data
    if filter_status:
        filtered = [f for f in filtered if f['status'] in filter_status]
    if filter_channel:
        filtered = [f for f in filtered if f['channel'] in filter_channel]
    if filter_chalk == 'Has Chalk':
        filtered = [f for f in filtered if f['has_chalk']]
    elif filter_chalk == 'No Chalk':
        filtered = [f for f in filtered if not f['has_chalk']]
    if filter_search:
        q = filter_search.lower()
        filtered = [f for f in filtered if q in f['feature_id'].lower() or q in f['title'].lower()]

    st.markdown(f"<div class='sec-title'><span class='icon'>🧬</span> {len(filtered)} Features</div>", unsafe_allow_html=True)

    # Build feature cards HTML
    cards_html = "<div class='feat-grid'>"
    for f in filtered:
        status_cls = _status_class(f['status'])
        priority_cls = _priority_class(f['priority'])
        chalk_cls = 'chalk-yes' if f['has_chalk'] else 'chalk-no'
        chalk_label = f"Chalk: {f['chalk_scenarios']} scenarios" if f['has_chalk'] else 'No Chalk'
        gen_label = f"TCs: {f['suite_tc_count']}" if f['suite_tc_count'] > 0 else ''

        cards_html += f"""<div class='feat-card'>
            <div class='fid'>{escape(f['feature_id'])}</div>
            <div class='ftitle'>{_safe(f['title'], 120)}</div>
            <div class='fmeta'>
                <span class='ftag {status_cls}'>{_safe(f['status'] or 'Unknown', 20)}</span>
                <span class='ftag {priority_cls}'>{_safe(f['priority'] or '—', 15)}</span>
                <span class='ftag channel'>{_safe(f['channel'] or '—', 10)}</span>
                <span class='ftag {chalk_cls}'>{chalk_label}</span>
                {"<span class='ftag gen'>" + gen_label + "</span>" if gen_label else ""}
            </div>
            <div style='font-size:11px; color:#64748b; margin-top:6px;'>
                👤 {_safe(f['assignee'] or 'Unassigned', 30)}
            </div>
        </div>"""
    cards_html += "</div>"
    st.markdown(cards_html, unsafe_allow_html=True)

# ================================================================
# TAB 2: FEATURE DETAIL — Deep dive into a single feature
# ================================================================
with tab_detail:
    feature_options = [f"{f['feature_id']} — {f['title'][:60]}" for f in features_data]
    if not feature_options:
        st.warning('No PI-53 features found in DB. Run `python preload_db.py --pi PI-53` first.')
    else:
        selected_idx = st.selectbox('Select Feature', range(len(feature_options)),
                                     format_func=lambda i: feature_options[i], key='detail_select')
        feat = features_data[selected_idx]
        fid = feat['feature_id']

        # Header
        st.markdown(f"""<div class='detail-panel'>
            <h3>{escape(fid)} — {_safe(feat['title'], 150)}</h3>
            <div style='display:flex; gap:8px; margin-top:8px; flex-wrap:wrap;'>
                <span class='ftag {_status_class(feat["status"])}'>{_safe(feat['status'], 20)}</span>
                <span class='ftag {_priority_class(feat["priority"])}'>{_safe(feat['priority'], 15)}</span>
                <span class='ftag channel'>{_safe(feat['channel'], 10)}</span>
                <span class='ftag chalk-yes'>👤 {_safe(feat['assignee'], 30)}</span>
            </div>
        </div>""", unsafe_allow_html=True)

        # Two columns: Jira | Chalk
        jcol, ccol = st.columns(2)

        with jcol:
            st.markdown("<div class='sec-title'><span class='icon'>🎫</span> Jira Data</div>", unsafe_allow_html=True)

            if feat['has_jira']:
                # Description
                st.markdown(f"""<div class='detail-section'>
                    <h4>📝 Description</h4>
                    <p>{_safe(feat['description'], 800) or '<em>No description</em>'}</p>
                </div>""", unsafe_allow_html=True)

                # Acceptance Criteria
                ac = feat.get('ac_text', '')
                st.markdown(f"""<div class='detail-section'>
                    <h4>✅ Acceptance Criteria</h4>
                    <p>{_safe(ac, 1000) or '<em>No AC found</em>'}</p>
                </div>""", unsafe_allow_html=True)

                # Labels
                labels = feat.get('labels', [])
                if labels:
                    labels_html = ' '.join(f"<span class='ftag channel'>{escape(l)}</span>" for l in labels[:15])
                    st.markdown(f"""<div class='detail-section'>
                        <h4>🏷 Labels</h4>
                        <div style='display:flex; gap:6px; flex-wrap:wrap;'>{labels_html}</div>
                    </div>""", unsafe_allow_html=True)

                # Subtasks
                subtasks = feat.get('subtasks', [])
                if subtasks:
                    st_html = '<ul>'
                    for sub in subtasks[:10]:
                        sk = sub.get('key', '')
                        ss_text = sub.get('summary', sub.get('fields', {}).get('summary', ''))
                        st_html += f"<li><strong>{escape(sk)}</strong> — {_safe(ss_text, 100)}</li>"
                    st_html += '</ul>'
                    st.markdown(f"""<div class='detail-section'>
                        <h4>📌 Subtasks ({len(subtasks)})</h4>
                        {st_html}
                    </div>""", unsafe_allow_html=True)

                # Linked Issues
                links = feat.get('links', [])
                if links:
                    lk_html = '<ul>'
                    for lk in links[:10]:
                        lk_key = lk.get('key', '')
                        lk_sum = lk.get('summary', '')
                        lk_type = lk.get('type', '')
                        lk_html += f"<li><strong>{escape(lk_key)}</strong> ({escape(lk_type)}) — {_safe(lk_sum, 80)}</li>"
                    lk_html += '</ul>'
                    st.markdown(f"""<div class='detail-section'>
                        <h4>🔗 Linked Issues ({len(links)})</h4>
                        {lk_html}
                    </div>""", unsafe_allow_html=True)

                # Comments
                comments = feat.get('comments', [])
                if comments:
                    cm_html = ''
                    for cm in comments[:5]:
                        author = cm.get('author', {}).get('displayName', cm.get('author', ''))
                        body = cm.get('body', '')[:200]
                        cm_html += f"<div style='margin:6px 0; padding:8px; background:rgba(139,92,246,0.05); border-radius:8px;'>"
                        cm_html += f"<div style='font-size:11px; color:#a78bfa; font-weight:700;'>💬 {escape(str(author))}</div>"
                        cm_html += f"<div style='font-size:12px; color:#94a3b8; margin-top:4px;'>{_safe(body, 200)}</div></div>"
                    st.markdown(f"""<div class='detail-section'>
                        <h4>💬 Comments ({len(comments)})</h4>
                        {cm_html}
                    </div>""", unsafe_allow_html=True)
            else:
                st.info('No Jira data cached. Run the TSG Dashboard to fetch this feature.')

        with ccol:
            st.markdown("<div class='sec-title'><span class='icon'>📋</span> Chalk Data</div>", unsafe_allow_html=True)

            if feat['has_chalk']:
                chalk_raw = load_chalk(fid, PI_LABEL)

                # Scope
                scope = chalk_raw.get('scope', '')
                if scope:
                    st.markdown(f"""<div class='detail-section'>
                        <h4>🎯 Scope</h4>
                        <p>{_safe(scope, 600)}</p>
                    </div>""", unsafe_allow_html=True)

                # Rules
                rules = chalk_raw.get('rules', '')
                if rules:
                    st.markdown(f"""<div class='detail-section'>
                        <h4>📏 Business Rules</h4>
                        <p>{_safe(rules, 600)}</p>
                    </div>""", unsafe_allow_html=True)

                # Scenarios
                scenarios = []
                try:
                    scenarios = json.loads(chalk_raw.get('scenarios_json', '[]'))
                except Exception:
                    pass

                if scenarios:
                    sc_html = ''
                    for sc in scenarios:
                        sid = sc.get('scenario_id', '')
                        stitle = sc.get('title', '')
                        cat = sc.get('category', '')
                        prereq = sc.get('prereq', '')
                        steps = sc.get('steps', [])
                        validation = sc.get('validation', '')

                        sc_html += f"""<div class='scenario-card'>
                            <div class='sid'>{escape(sid)}</div>
                            <div class='stitle'>{_safe(stitle, 120)}</div>
                            <div class='smeta'>
                                {"Category: " + escape(cat) + " &bull; " if cat else ""}
                                {str(len(steps)) + " steps" if steps else "No steps"}
                                {" &bull; Prereq: " + _safe(prereq, 60) if prereq else ""}
                            </div>
                        </div>"""

                    st.markdown(f"""<div class='detail-section'>
                        <h4>🧪 Chalk Scenarios ({len(scenarios)})</h4>
                        {sc_html}
                    </div>""", unsafe_allow_html=True)

                # Open Items
                open_items = []
                try:
                    open_items = json.loads(chalk_raw.get('open_items_json', '[]'))
                except Exception:
                    pass
                if open_items:
                    oi_html = '<ul>' + ''.join(f"<li>{_safe(oi, 150)}</li>" for oi in open_items[:10]) + '</ul>'
                    st.markdown(f"""<div class='detail-section'>
                        <h4>⚠️ Open Items ({len(open_items)})</h4>
                        {oi_html}
                    </div>""", unsafe_allow_html=True)
            else:
                st.info('No Chalk data cached. Run `python preload_db.py --pi PI-53` to fetch.')

        # Generation History
        st.markdown("<div class='sec-title'><span class='icon'>📦</span> Generation History</div>", unsafe_allow_html=True)
        if feat['suite_tc_count'] > 0:
            st.success(f"Latest suite: {feat['suite_tc_count']} TCs, {feat['suite_step_count']} steps")
        else:
            st.info('No test suite generated yet for this feature.')

# ================================================================
# TAB 3: COVERAGE ANALYSIS
# ================================================================
with tab_coverage:
    st.markdown("<div class='sec-title'><span class='icon'>📈</span> PI-53 Coverage Analysis</div>", unsafe_allow_html=True)

    # Status distribution
    cov1, cov2, cov3 = st.columns(3)

    with cov1:
        st.markdown("<div class='detail-section'><h4>📊 Status Distribution</h4>", unsafe_allow_html=True)
        status_html = '<table class="sum-table"><tr><th>Status</th><th>Count</th><th>%</th></tr>'
        for s, cnt in sorted(status_dist.items(), key=lambda x: -x[1]):
            pct = cnt / max(total_features, 1) * 100
            status_html += f"<tr><td>{escape(s)}</td><td>{cnt}</td><td>{pct:.0f}%</td></tr>"
        status_html += '</table></div>'
        st.markdown(status_html, unsafe_allow_html=True)

    with cov2:
        st.markdown("<div class='detail-section'><h4>📡 Channel Distribution</h4>", unsafe_allow_html=True)
        ch_html = '<table class="sum-table"><tr><th>Channel</th><th>Count</th><th>%</th></tr>'
        for ch, cnt in sorted(channel_dist.items(), key=lambda x: -x[1]):
            pct = cnt / max(total_features, 1) * 100
            ch_html += f"<tr><td>{escape(ch)}</td><td>{cnt}</td><td>{pct:.0f}%</td></tr>"
        ch_html += '</table></div>'
        st.markdown(ch_html, unsafe_allow_html=True)

    with cov3:
        st.markdown("<div class='detail-section'><h4>👤 Assignee Distribution</h4>", unsafe_allow_html=True)
        as_html = '<table class="sum-table"><tr><th>Assignee</th><th>Count</th></tr>'
        for a, cnt in sorted(assignee_dist.items(), key=lambda x: -x[1])[:10]:
            as_html += f"<tr><td>{_safe(a, 30)}</td><td>{cnt}</td></tr>"
        as_html += '</table></div>'
        st.markdown(as_html, unsafe_allow_html=True)

    # Coverage gaps
    st.markdown("<div class='sec-title'><span class='icon'>⚠️</span> Coverage Gaps</div>", unsafe_allow_html=True)

    gap1, gap2 = st.columns(2)
    with gap1:
        no_chalk = [f for f in features_data if not f['has_chalk']]
        if no_chalk:
            st.markdown("<div class='detail-section'><h4>❌ No Chalk Data</h4>", unsafe_allow_html=True)
            for f in no_chalk:
                st.markdown(f"- **{f['feature_id']}** — {_safe(f['title'], 60)}")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.success('All features have Chalk data cached!')

    with gap2:
        no_gen = [f for f in features_data if f['suite_tc_count'] == 0 and f['has_chalk']]
        if no_gen:
            st.markdown("<div class='detail-section'><h4>🔄 Chalk Available but Not Generated</h4>", unsafe_allow_html=True)
            for f in no_gen[:15]:
                st.markdown(f"- **{f['feature_id']}** — {_safe(f['title'], 60)} ({f['chalk_scenarios']} scenarios)")
            if len(no_gen) > 15:
                st.markdown(f"*... and {len(no_gen) - 15} more*")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.success('All features with Chalk data have been generated!')

    # Scenario richness
    st.markdown("<div class='sec-title'><span class='icon'>🧪</span> Scenario Richness</div>", unsafe_allow_html=True)
    rich_data = sorted([f for f in features_data if f['has_chalk']], key=lambda x: -x['chalk_scenarios'])
    if rich_data:
        rich_html = '<table class="sum-table"><tr><th>Feature</th><th>Title</th><th>Chalk Scenarios</th><th>Generated TCs</th><th>Ratio</th></tr>'
        for f in rich_data[:20]:
            ratio = f['suite_tc_count'] / max(f['chalk_scenarios'], 1)
            ratio_str = f"{ratio:.1f}x" if f['suite_tc_count'] > 0 else '—'
            rich_html += f"""<tr>
                <td style='font-family:JetBrains Mono; font-weight:700; color:#c084fc;'>{escape(f['feature_id'])}</td>
                <td>{_safe(f['title'], 50)}</td>
                <td style='text-align:center;'>{f['chalk_scenarios']}</td>
                <td style='text-align:center;'>{f['suite_tc_count'] or '—'}</td>
                <td style='text-align:center;'>{ratio_str}</td>
            </tr>"""
        rich_html += '</table>'
        st.markdown(rich_html, unsafe_allow_html=True)

# ================================================================
# TAB 4: FULL MATRIX — Sortable table of all features
# ================================================================
with tab_matrix:
    st.markdown("<div class='sec-title'><span class='icon'>🗂</span> Full Feature Matrix</div>", unsafe_allow_html=True)

    # Build full table
    matrix_html = """<table class='sum-table'>
    <tr>
        <th>#</th><th>Feature ID</th><th>Title</th><th>Status</th>
        <th>Priority</th><th>Channel</th><th>Assignee</th>
        <th>Chalk</th><th>Scenarios</th><th>TCs</th><th>Steps</th>
    </tr>"""

    for i, f in enumerate(features_data, 1):
        chalk_icon = '✅' if f['has_chalk'] else '❌'
        tc_val = str(f['suite_tc_count']) if f['suite_tc_count'] > 0 else '—'
        step_val = str(f['suite_step_count']) if f['suite_step_count'] > 0 else '—'
        sc_val = str(f['chalk_scenarios']) if f['chalk_scenarios'] > 0 else '—'

        matrix_html += f"""<tr>
            <td>{i}</td>
            <td style='font-family:JetBrains Mono; font-weight:700; color:#c084fc;'>{escape(f['feature_id'])}</td>
            <td>{_safe(f['title'], 55)}</td>
            <td><span class='ftag {_status_class(f["status"])}'>{_safe(f['status'], 15)}</span></td>
            <td><span class='ftag {_priority_class(f["priority"])}'>{_safe(f['priority'], 10)}</span></td>
            <td>{_safe(f['channel'], 8)}</td>
            <td>{_safe(f['assignee'], 20)}</td>
            <td style='text-align:center;'>{chalk_icon}</td>
            <td style='text-align:center;'>{sc_val}</td>
            <td style='text-align:center;'>{tc_val}</td>
            <td style='text-align:center;'>{step_val}</td>
        </tr>"""

    matrix_html += '</table>'
    st.markdown(matrix_html, unsafe_allow_html=True)

    # Export button
    st.markdown("---")
    import pandas as pd
    df_export = pd.DataFrame([{
        'Feature ID': f['feature_id'],
        'Title': f['title'],
        'Status': f['status'],
        'Priority': f['priority'],
        'Channel': f['channel'],
        'Assignee': f['assignee'],
        'Has Chalk': 'Yes' if f['has_chalk'] else 'No',
        'Chalk Scenarios': f['chalk_scenarios'],
        'Generated TCs': f['suite_tc_count'],
        'Generated Steps': f['suite_step_count'],
    } for f in features_data])

    csv_data = df_export.to_csv(index=False).encode('utf-8')
    st.download_button(
        '📥 Export PI-53 Matrix as CSV',
        csv_data,
        file_name=f'PI53_Feature_Matrix_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv',
        mime='text/csv',
    )
