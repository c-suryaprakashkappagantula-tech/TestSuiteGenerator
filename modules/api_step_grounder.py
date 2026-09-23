"""
api_step_grounder.py — Cite real NSL/NBO endpoints in API test steps (V1).

The hardcoded step generators (step_templates._change_sim_steps, _api_flow_steps,
etc.) emit steps like "Trigger Change SIM API with new ICCID ... 200 OK" — with
NO endpoint, HTTP method, or specific status. The real endpoint/method data
already lives in TMO_API_Chalk (parsed by nmno_api_lookup). This post-pass
matches each ungrounded "Trigger <op> API" step to a real spec and appends the
method + path, e.g.

    Trigger Change SIM API with new ICCID and MDN
      -> Trigger Change SIM API with new ICCID and MDN  [POST /nsl/provisioning/mno/tmo/v1/change-sim]

Matching is conservative: a spec matches a step only when EVERY meaningful token
of the spec's operation name appears in the step text, so "line-inquiry" never
grounds a "device" step. Never raises — on any error the suite is unchanged.
"""
import json
import re
from functools import lru_cache
from typing import Callable, List, Optional, Tuple

# Verbs that introduce an API action, and a test for an already-grounded step.
_ACTION_RE = re.compile(r'\b(?:trigger|invoke|submit|call|send|execute|hit)\b', re.I)
_HAS_PATH_RE = re.compile(r'/(?:nsl|nbo|nbop)/', re.I)
# Generic tokens that must NOT be the sole basis of a match.
_STOP_TOKENS = {'api', 'call', 'the', 'a', 'an', 'get', 'post', 'put', 'nsl', 'nbo',
                'tmo', 'v1', 'v2', 'mno', 'provisioning', 'request', 'with', 'valid'}


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r'[^a-z0-9]+', (text or '').lower()) if t]


def _op_tokens(api_name: str) -> set:
    """Meaningful tokens of a spec operation name (drop generic/version noise)."""
    toks = set(_tokens(api_name)) - _STOP_TOKENS
    return {t for t in toks if len(t) > 2 and not re.fullmatch(r't?\d+', t)}


@lru_cache(maxsize=1)
def build_endpoint_catalog() -> Tuple[Tuple[frozenset, str, str, str], ...]:
    """Build (op_tokens, method, endpoint, name) tuples from all TMO_API_Chalk rows.

    Cached for the process. Returns () if the DB / table is unavailable.
    """
    catalog: List[Tuple[frozenset, str, str, str]] = []
    try:
        import sqlite3
        from .nmno_api_lookup import DB_PATH, parse_api_spec_tables
        if not DB_PATH.exists():
            return tuple()
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                'SELECT section_name, section_url, table_data_json, raw_text '
                'FROM TMO_API_Chalk').fetchall()
        finally:
            conn.close()
        for r in rows:
            try:
                spec = parse_api_spec_tables(
                    r['table_data_json'] or '', r['raw_text'] or '',
                    r['section_name'] or '', r['section_url'] or '')
            except Exception:
                continue
            ep = (getattr(spec, 'endpoint', '') or '').strip()
            if not ep or '/' not in ep:
                continue
            # Prefer the parsed api_name; else derive from the section header
            # (e.g. 'T007. line-inquiry' -> 'line-inquiry').
            name = (getattr(spec, 'api_name', '') or '').strip()
            if not name:
                name = re.sub(r'^\s*T?\d+[\.\)]\s*', '', r['section_name'] or '').strip()
            toks = _op_tokens(name) or _op_tokens(ep.rsplit('/', 1)[-1])
            if not toks:
                continue
            method = (getattr(spec, 'http_method', '') or '').strip().upper()
            catalog.append((frozenset(toks), method, ep, name))
    except Exception:
        return tuple()
    return tuple(catalog)


def _best_spec(step_text: str, catalog) -> Optional[Tuple[str, str, str]]:
    """Return (method, endpoint, name) whose op tokens are ALL present in the step."""
    st = set(_tokens(step_text))
    if not st:
        return None
    best = None
    best_n = 0
    for toks, method, ep, name in catalog:
        if toks and toks <= st and len(toks) > best_n:   # every spec token present
            best = (method, ep, name)
            best_n = len(toks)
    return best


def ground_api_steps(suite, nmno_result=None, log: Callable = print) -> object:
    """Append real 'METHOD /path' to ungrounded API-trigger steps. Returns suite."""
    try:
        catalog = list(build_endpoint_catalog())
        # Add feature-specific specs from an already-fetched nmno_result, if any.
        for sp in (getattr(nmno_result, 'api_specs', None) or []):
            ep = (getattr(sp, 'endpoint', '') or '').strip()
            if not ep:
                continue
            toks = _op_tokens(getattr(sp, 'api_name', '') or '')
            if toks:
                catalog.append((frozenset(toks), (getattr(sp, 'http_method', '') or '').upper(), ep,
                                getattr(sp, 'api_name', '')))
        if not catalog:
            log('[API-GROUND] No endpoint catalog available (TMO_API_Chalk missing) — skipped')
            return suite

        grounded = 0
        for tc in getattr(suite, 'test_cases', []) or []:
            for step in getattr(tc, 'steps', []) or []:
                s = getattr(step, 'summary', '') or ''
                if not _ACTION_RE.search(s) or _HAS_PATH_RE.search(s):
                    continue
                match = _best_spec(s, catalog)
                if not match:
                    continue
                method, ep, _name = match
                tag = '[%s %s]' % (method or 'HTTPS', ep)
                step.summary = '%s  %s' % (s.rstrip(' .'), tag)
                # If the expected is a bare '200 OK', keep it; otherwise leave as-is.
                grounded += 1
        log('[API-GROUND] Endpoint-grounded %d API step(s) from %d specs'
            % (grounded, len(catalog)))
        return suite
    except Exception as exc:
        log('[API-GROUND] Failed (%s) — suite unchanged' % str(exc)[:120])
        return suite
