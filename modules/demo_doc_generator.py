# -*- coding: utf-8 -*-
"""demo_doc_generator.py - 'Test Suite Demo' Word document, generated with every suite.

Same layout as the hand-built MWTGPROV-4503 demo (30 Sep 2026):
  1. Feature at a glance        (Jira + Chalk facts)
  2. The problem in plain words (Jira description, Chalk scope, key data table)
  3. What each team must deliver ({panel:title=TEAM} blocks + acceptance criteria)
  4. Test suite overview        (sources, AC coverage, category / priority / TC tables)
  5. Test case walkthrough      (per TC: about, pre-condition, steps table)
  6. How this suite was produced
  7. Items to confirm before execution (auto-detected, e.g. duplicate Chalk IDs)

Everything is built from the suite that was just written to Excel, so the document
and the workbook always agree. Read-only on its inputs; never raises into the caller.
"""
from __future__ import annotations

import re
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .config import OUTPUTS

NAVY = '0B2A4A'
TEAL = '0E9AA7'
ZEBRA = 'EEF4F8'
GREY = '555F6D'
JIRA_BASE = 'https://jira.charter.com/browse/'

PRIORITY_MEANING = {
    'P1': 'Must run - blocks release if failing',
    'P2': 'Should run - high risk if skipped',
    'P3': 'Good to run',
}
CATEGORY_ORDER = ['Happy Path', 'Negative', 'Edge Case', 'E2E', 'Regression']


# ------------------------------------------------------------------ text helpers
def _clean(t: str) -> str:
    try:
        from .output_sanitizer import clean_text
        t = clean_text(t or '') or ''
    except Exception:
        t = t or ''
    return t.strip()


def _panels(text: str) -> "OrderedDict[str, List[str]]":
    """{panel:title=NSL} * a * b {panel} -> {'NSL': ['a', 'b']}"""
    out: "OrderedDict[str, List[str]]" = OrderedDict()
    for m in re.finditer(r'\{panel:[^}]*?title=([^|}]+)[^}]*\}(.*?)\{panel\}', text or '', re.S | re.I):
        title = m.group(1).strip()
        items = _bullets(m.group(2))
        if items:
            out[title] = items
    return out


def _bullets(text: str) -> List[str]:
    items = []
    for ln in re.split(r'\r?\n', text or ''):
        s = ln.strip()
        if not s:
            continue
        s = re.sub(r'^(?:[*#\-\u2022]+|\d+[.)])\s*', '', s)
        s = _clean(s)
        if len(s) >= 4:
            items.append(s)
    return items


def _strip_panels(text: str) -> str:
    return re.sub(r'\{panel[^}]*\}.*?\{panel\}', ' ', text or '', flags=re.S | re.I)


def _problem_paragraphs(description: str, limit: int = 2) -> List[str]:
    body = _strip_panels(description)
    body = re.split(r'\n-{4,}|\*MDA Impacts', body)[0]
    paras = [_clean(p) for p in re.split(r'\r?\n\s*\r?\n', body)]
    paras = [p for p in paras if len(p) > 20]
    return paras[:limit]


def _wiki_tables(text: str) -> List[List[List[str]]]:
    """Jira wiki tables: '||h1||h2||' header then '|a|b|' rows."""
    tables, cur = [], None
    for ln in re.split(r'\r?\n', text or ''):
        s = ln.strip()
        if s.startswith('||'):
            cur = [[_clean(c) for c in s.strip('|').split('||')]]
            tables.append(cur)
        elif s.startswith('|') and cur is not None:
            cur.append([_clean(c) for c in s.strip('|').split('|')])
        else:
            cur = None
    return [t for t in tables if len(t) >= 2 and len(t[0]) >= 3]


def _tc_label(tc, feature_id: str) -> str:
    n = getattr(tc, 'summary', '') or ''
    n = re.sub(r'^(?:TC\d+_)+', '', n)
    n = re.sub(r'^' + re.escape(feature_id) + r'_', '', n)
    n = re.sub(r'^(?:TC\d+_)+', '', n)
    n = re.sub(r'^(?:Verify|Validate|Check|Ensure)_(?:that_)?', '', n, flags=re.I)
    words = [w for w in n.split('_') if w]
    lab = ' '.join(words[:9])
    return (lab[0].upper() + lab[1:]) if lab else n


def _about(tc) -> str:
    d = _clean(getattr(tc, 'description', '') or '')
    d = re.sub(r'^\s*(?:To validate|UI verification)\s*:\s*', '', d, flags=re.I)
    return d


def _origin(tc) -> str:
    tr = getattr(tc, 'traceability', None)
    st = (getattr(tr, 'source_type', '') or '').strip().lower() if tr else ''
    if st in ('chalk', 'chalk scenario', 'business rule', 'nbop ui', 'nbop'):
        return 'Chalk'
    if st in ('jira ac', 'jira'):
        return 'Jira AC'
    if st in ('subtask ac', 'subtask'):
        return 'Subtask'
    if st == 'attachment':
        return 'Document'
    return 'Chalk' if getattr(tc, 'from_chalk', False) else (st.title() or 'Other')


_WORD_STOP = {'with', 'that', 'this', 'from', 'when', 'then', 'into', 'their', 'there',
              'which', 'should', 'would', 'verify', 'validate', 'ensure', 'successfully',
              'without', 'containing', 'remains', 'existing', 'records', 'record'}


def _words(t: str) -> set:
    return {re.sub(r'(?:ing|ed|es|s)$', '', w) for w in re.findall(r'[a-z0-9]{4,}', (t or '').lower())
            if w not in _WORD_STOP}


def _ac_coverage(acs: List[str], tcs, labels) -> List[Tuple[int, List[str]]]:
    out = []
    for i, ac in enumerate(acs, 1):
        aw = _words(ac)
        scored = []
        for n, tc in enumerate(tcs, 1):
            tw = _words((getattr(tc, 'summary', '') or '').replace('_', ' ') + ' ' + _about(tc))
            sc = len(aw & tw)
            if _origin(tc) == 'Jira AC' and sc >= max(2, len(aw) // 2):
                sc += 5
            if sc >= 3:
                scored.append((sc, n))
        scored.sort(key=lambda x: (-x[0], x[1]))
        out.append((i, ['TC%02d' % n for _, n in scored[:3]]))
    return out


# ------------------------------------------------------------------ docx helpers
def _shade(cell, hex_fill: str):
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_fill)
    tcPr.append(shd)


def _run(p, text, bold=False, size=None, color=None):
    r = p.add_run(text)
    r.bold = bold
    if size:
        r.font.size = Pt(size)
    if color:
        r.font.color.rgb = RGBColor.from_string(color)
    return r


def _heading(doc, text, level):
    h = doc.add_heading(text, level=level)
    for r in h.runs:
        r.font.color.rgb = RGBColor.from_string(NAVY if level == 1 else TEAL)
    return h


def _table(doc, header: List[str], rows: List[List[str]], head_fill=NAVY, widths=None):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = 'Table Grid'
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, h in enumerate(header):
        c = t.rows[0].cells[i]
        c.text = ''
        _run(c.paragraphs[0], str(h), bold=True, size=9, color='FFFFFF')
        _shade(c, head_fill)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, v in enumerate(row[:len(header)]):
            cells[i].text = ''
            _run(cells[i].paragraphs[0], '' if v is None else str(v), size=9)
            if ri % 2 == 1:
                _shade(cells[i], ZEBRA)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                if i < len(row.cells):
                    row.cells[i].width = Inches(w)
    doc.add_paragraph('')
    return t


def _bold_lead(doc, lead: str, text: str):
    p = doc.add_paragraph()
    _run(p, lead, bold=True)
    _run(p, text)
    return p


# ------------------------------------------------------------------ main
def generate_demo_doc(suite, jira=None, chalk=None, log: Callable = print,
                      out_dir: Optional[Path] = None) -> Optional[Path]:
    """Write <FID>_<PI>_Test_Suite_Demo_<ts>.docx. Returns the path (None on failure)."""
    try:
        return _build(suite, jira, chalk, log, out_dir)
    except Exception as e:  # a demo doc must never break suite generation
        if log:
            log('[DEMO-DOC] skipped: %s' % str(e)[:120])
        return None


def _build(suite, jira, chalk, log, out_dir):
    fid = getattr(suite, 'feature_id', '') or (getattr(jira, 'key', '') if jira else '')
    title = getattr(suite, 'feature_title', '') or (getattr(jira, 'summary', '') if jira else '')
    pi = getattr(suite, 'pi', '') or (getattr(jira, 'pi', '') if jira else '') or ''
    try:
        from .config import _pi_to_iteration
        iteration = _pi_to_iteration(pi) if pi else ''
    except Exception:
        iteration = ''
    tcs = list(getattr(suite, 'test_cases', None) or [])
    n_steps = sum(len(getattr(t, 'steps', None) or []) for t in tcs)
    labels = [_tc_label(t, fid) for t in tcs]
    desc = (getattr(jira, 'description', '') or '') if jira else ''

    doc = Document()
    sec = doc.sections[0]
    for side in ('left_margin', 'right_margin', 'top_margin', 'bottom_margin'):
        setattr(sec, side, Inches(0.79))
    doc.styles['Normal'].font.name = 'Calibri'
    doc.styles['Normal'].font.size = Pt(10)

    # ---------------- title block
    _run(doc.add_paragraph(), 'Test Suite Demo', bold=True, size=26, color=NAVY)
    _run(doc.add_paragraph(), '%s  |  %s' % (fid, pi or 'PI n/a'), bold=True, size=14, color=TEAL)
    _run(doc.add_paragraph(), title, size=12)
    try:
        from shared.mcp_gateway.toggle import legacy_enabled
        via = 'direct API' if legacy_enabled() else 'the approved Spectrum MCP gateway'
    except Exception:
        via = 'the TSG cache'
    _run(doc.add_paragraph(), 'Prepared %s  |  Source: Jira + Chalk via %s; test cases from TSG'
         % (datetime.now().strftime('%d %b %Y'), via), size=9, color=GREY)

    # ---------------- 1. at a glance
    _heading(doc, '1. Feature at a glance', 1)
    rows = [['Feature', '%s (%s) - %s%s' % (fid, (getattr(jira, 'issue_type', '') or 'Feature') if jira else 'Feature',
                                            JIRA_BASE, fid)],
            ['PI / Iteration', '%s%s' % (pi or 'n/a', ' (%s)' % iteration if iteration else '')]]
    if jira:
        rows += [['Status (Jira)', getattr(jira, 'status', '') or 'n/a'],
                 ['Priority', getattr(jira, 'priority', '') or 'n/a'],
                 ['Assignee / Reporter', '%s / %s' % (getattr(jira, 'assignee', '') or 'n/a',
                                                      getattr(jira, 'reporter', '') or 'n/a')],
                 ['Labels', ', '.join(getattr(jira, 'labels', None) or []) or 'None']]
        subs = getattr(jira, 'subtasks', None) or []
        if subs:
            rows.append(['Dev subtask(s)', '; '.join('%s - %s' % (s.get('key', ''), (s.get('summary', '') or '')[:70])
                                                   for s in subs[:4])])
        links = getattr(jira, 'linked_issues', None) or []
        if links:
            rows.append(['Linked issues', '; '.join('%s - %s' % (l.get('key', ''), (l.get('summary', '') or '')[:70])
                                                  for l in links[:4])])
    chalk_url = ''
    try:
        from .database import _conn
        c = _conn()
        r = c.execute('SELECT url FROM pi_pages WHERE label=?', (pi,)).fetchone()
        c.close()
        chalk_url = (r['url'] if r else '') or ''
    except Exception:
        pass
    if chalk is not None and getattr(chalk, 'scenarios', None):
        rows.append(['Chalk spec', '%s page%s' % (pi or 'PI', ' - %s' % chalk_url if chalk_url else '')])
    rows.append(['Suite', '%d test cases / %d steps' % (len(tcs), n_steps)])
    _table(doc, ['Field', 'Value'], rows, widths=[1.7, 5.2])

    # ---------------- 2. the problem
    _heading(doc, '2. The problem in plain words', 1)
    paras = _problem_paragraphs(desc)
    for p in paras or ['No description in Jira.']:
        doc.add_paragraph(p)
    scope = _clean(getattr(chalk, 'scope', '') or '') if chalk is not None else ''
    if scope:
        lines = [l for l in re.split(r'\r?\n', getattr(chalk, 'scope', '') or '') if l.strip()]
        keep = []
        for l in lines:
            s = _clean(l)
            if not s or s.startswith('#') or s.lower().startswith(('[pi-', 'summary:')) or s == title:
                continue
            keep.append(s.lstrip('#').strip())
            if sum(len(x) for x in keep) > 650:
                break
        if keep:
            _heading(doc, 'What Chalk says', 2)
            doc.add_paragraph(' '.join(keep)[:900])
    tables = []
    for src in [desc] + [s.get('description', '') or '' for s in (getattr(jira, 'subtasks', None) or [])] if jira else []:
        tables += _wiki_tables(src)
    if tables:
        best = sorted(tables, key=lambda t: (0 if any('field' in h.lower() for h in t[0]) else 1, -len(t)))[0]
        if len(best) <= 14:
            _heading(doc, 'Key data (from Jira)', 2)
            _table(doc, best[0], best[1:])

    # ---------------- 3. deliverables + AC
    panels = _panels(desc)
    ac_raw = (getattr(jira, 'acceptance_criteria', '') or '') if jira else ''
    acs = []
    ac_panels = _panels(ac_raw)
    for v in ac_panels.values():
        acs += v
    if not acs:
        acs = _bullets(_strip_panels(ac_raw)) or _bullets(ac_raw)
    if panels or acs:
        _heading(doc, '3. What each team must deliver', 1)
        for team, items in panels.items():
            _heading(doc, team, 2)
            for it in items:
                doc.add_paragraph(it, style='List Bullet')
        if acs:
            _heading(doc, 'Acceptance criteria (Jira)', 2)
            for i, a in enumerate(acs, 1):
                doc.add_paragraph('AC %d: %s' % (i, a), style='List Bullet')

    # ---------------- 4. overview
    _heading(doc, '4. Test suite overview', 1)
    src = Counter(_origin(t) for t in tcs)
    parts = ['%d from %s' % (n, k if k != 'Chalk' else 'the Chalk %s scenarios' % (pi or 'PI'))
             for k, n in src.most_common()]
    line = '%d test cases, %d steps. %s.' % (len(tcs), n_steps, ', '.join(parts))
    if acs:
        cov = _ac_coverage(acs, tcs, labels)
        covered = ['AC %d by %s' % (i, ', '.join(t)) for i, t in cov if t]
        gaps = ['AC %d' % i for i, t in cov if not t]
        if covered:
            line += ' Acceptance criteria: %s.' % '; '.join(covered)
        if gaps:
            line += ' No clear test found for %s - check coverage.' % ', '.join(gaps)
    doc.add_paragraph(line)
    cats = Counter(getattr(t, 'category', '') or 'Uncategorised' for t in tcs)
    ordered = [c for c in CATEGORY_ORDER if c in cats] + [c for c in cats if c not in CATEGORY_ORDER]
    _table(doc, ['Category', 'Count'], [[c, cats[c]] for c in ordered], widths=[2.5, 1.0])
    pris = Counter(getattr(t, 'priority', '') or 'P3' for t in tcs)
    _table(doc, ['Priority', 'Count', 'Meaning'],
           [[p, pris[p], PRIORITY_MEANING.get(p, '')] for p in ('P1', 'P2', 'P3') if pris.get(p)],
           widths=[0.9, 0.8, 4.4])
    _table(doc, ['TC', 'What it proves', 'Category', 'Priority'],
           [['TC%02d' % i, labels[i - 1], getattr(t, 'category', ''), getattr(t, 'priority', '')]
            for i, t in enumerate(tcs, 1)], widths=[0.6, 4.3, 1.2, 0.8])

    # ---------------- 5. walkthrough
    _heading(doc, '5. Test case walkthrough', 1)
    _run(doc.add_paragraph(), 'Each test case below explains what it is about, the pre-condition, and how it is executed.',
         color=GREY)
    for i, t in enumerate(tcs, 1):
        steps = list(getattr(t, 'steps', None) or [])
        _heading(doc, 'TC%02d - %s' % (i, labels[i - 1]), 2)
        _run(doc.add_paragraph(), '%s  |  %s  |  %d steps  |  Source: %s' % (
            getattr(t, 'category', ''), getattr(t, 'priority', ''), len(steps), _origin(t)),
            bold=True, size=9, color=TEAL)
        about = _about(t)
        if about:
            _bold_lead(doc, 'What it is about: ', about)
        pre = _clean(getattr(t, 'preconditions', '') or '')
        if pre:
            _bold_lead(doc, 'Pre-condition: ', re.sub(r'\s*\n\s*', '; ', pre))
        if steps:
            _table(doc, ['#', 'Step', 'Expected result'],
                   [[getattr(s, 'step_num', n) or n, getattr(s, 'summary', '') or '', getattr(s, 'expected', '') or '']
                    for n, s in enumerate(steps, 1)], head_fill=TEAL, widths=[0.4, 3.4, 3.1])

    # ---------------- 6. how produced
    _heading(doc, '6. How this suite was produced', 1)
    for b in [
        'TSG read the feature from Jira (description, acceptance criteria, subtasks, linked issues)'
        + (' and from the %s Chalk page (%d scenarios).' % (pi, len(getattr(chalk, 'scenarios', []) or []))
           if chalk is not None and getattr(chalk, 'scenarios', None) else '.'),
        'All Jira and Chalk data was fetched through %s.' % via,
        'Each Chalk scenario became a test case with steps, expected results and test data; Jira '
        'acceptance criteria not already covered were added as their own test cases.',
        'An output pass cleaned wiki/macro markup, made every name unique, and turned "old behaviour" '
        'lines into executable log checks. Categories and priorities are rule-based - have the feature '
        'owner confirm them.',
    ]:
        doc.add_paragraph(b, style='List Bullet')

    # ---------------- 7. items to confirm
    items = []
    if chalk is not None:
        ids = Counter((getattr(s, 'scenario_id', '') or '').strip() for s in (getattr(chalk, 'scenarios', None) or []))
        for sid, n in ids.items():
            if sid and n > 1:
                items.append('Chalk scenario ID %s is used by %d scenarios - confirm with the Chalk page author.' % (sid, n))
    for i, t in enumerate(tcs, 1):
        g = getattr(t, 'grounding_score', None)
        try:
            g = float(str(g).rstrip('%')) if g not in (None, '') else None
        except ValueError:
            g = None
        if g is not None and 0 <= g < 80:
            items.append('TC%02d grounding is %.0f%% - review its steps against the source.' % (i, g))
    for w in (getattr(suite, 'warnings', None) or [])[:8]:
        items.append(_clean(str(w))[:240])
    _heading(doc, '7. Items to confirm before execution', 1)
    for it in items or ['No open items detected automatically.']:
        doc.add_paragraph(it, style='List Bullet')

    out_dir = Path(out_dir) if out_dir else OUTPUTS
    out_dir.mkdir(parents=True, exist_ok=True)
    name = '%s_%s_Test_Suite_Demo_%s.docx' % (fid, (pi or 'PI').replace(' ', ''), datetime.now().strftime('%Y%m%d_%H%M%S'))
    path = out_dir / name
    doc.save(str(path))
    if log:
        log('[DEMO-DOC] Test Suite Demo saved: %s' % name)
    return path