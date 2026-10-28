# -*- coding: utf-8 -*-
"""
qmetry_session_guard.py — dashboard-side QMetry session guard (TSG).

Pure helpers (no Streamlit import) used by ``TSG_Dashboard_V8.0.py`` to:

  * probe QMetry auth before a run, cached briefly in ``st.session_state``;
  * decide whether Execute is allowed (blocked unless the user acknowledges
    "Generate without QMetry" when the session is down);
  * extract "QMetry supplement skipped" messages after a run (both engine paths);
  * launch the headed MDA SSO login in a SEPARATE process and fan the refreshed
    ``storage_state.json`` out to the TSG search locations.

QMetry carve-out: QMetry never goes through MCP; nothing here touches Chalk/Jira MCP.
No engine logic lives here — this is dashboard UX only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

STATUS_TTL_SECS = 300
LOGIN_WAIT_SECS = 240

SS_STATUS = '_qmetry_auth_status'        # cached dict from qmetry_session_status
SS_ACK = 'qmetry_ack_without'            # checkbox key "Generate without QMetry"
SS_GATE = '_qmetry_gate_last'            # 'allow' | 'allow_ack' | 'block'
SS_LOGIN_RESULT = '_qmetry_login_result'  # last refresh result dict (popped after display)
SS_SKIPS = '_qmetry_skip_by_feature'     # {feature_id: [msg, ...]} for the current run

EXPIRED_MSG = ('QMetry session expired — QMetry test cases will NOT be included. '
               'Refresh login before generating.')
POSTRUN_MSG = 'Suite generated WITHOUT QMetry test cases'
ACK_WARN_MSG = 'Generating WITHOUT QMetry (user acknowledged expired session)'

REPO_ROOT = Path(r'C:\Users\P3314665\Documents\mcp-jira-server')
MDA_DIR = REPO_ROOT / 'MDA-Jira-Dashboard'
TSG_ROOT = Path(__file__).resolve().parent.parent
WORKER = TSG_ROOT / 'tools' / 'qmetry_refresh_login.py'


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS CACHE
# ═══════════════════════════════════════════════════════════════════════════════

def _default_probe():
    # Looked up at call time so tests can monkeypatch the QMetry module.
    from modules import qmetry_steps_source as _qss
    return _qss.qmetry_session_status()


def get_cached_status(ss, now: Optional[float] = None, force: bool = False,
                      probe=None) -> Dict[str, Any]:
    """Return the cached QMetry status, re-probing when missing, older than
    ``STATUS_TTL_SECS``, or ``force``. ``ss`` is any dict-like (session_state)."""
    now = time.time() if now is None else now
    cached = ss.get(SS_STATUS) if hasattr(ss, 'get') else None
    if (not force and isinstance(cached, dict)
            and (now - float(cached.get('_cached_at', 0))) < STATUS_TTL_SECS):
        return cached
    try:
        status = dict((probe or _default_probe)())
    except Exception as exc:  # probe helper never raises, but never crash the UI
        status = {'ok': False, 'reason': 'probe error: %s' % exc, 'source': None,
                  'checked_at': now}
    status['_cached_at'] = now
    ss[SS_STATUS] = status
    return status


def invalidate_status(ss) -> None:
    """Drop the cached status so the next render re-probes."""
    try:
        ss.pop(SS_STATUS, None)
    except Exception:
        try:
            del ss[SS_STATUS]
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# GATE + POST-RUN MESSAGES
# ═══════════════════════════════════════════════════════════════════════════════

def gate_decision(status: Dict[str, Any], acknowledged: bool) -> Tuple[str, str]:
    """('allow', '') when QMetry is authenticated; otherwise 'allow_ack' if the user
    ticked "Generate without QMetry", else 'block' with the banner message."""
    if status and status.get('ok'):
        return 'allow', ''
    if acknowledged:
        return 'allow_ack', ACK_WARN_MSG
    return 'block', EXPIRED_MSG + ' Tick "Generate without QMetry" to proceed anyway.'


def qmetry_skip_messages(suite, options) -> List[str]:
    """Union of ``options['_qmetry_skip_warning']`` (both engine paths) and
    ``suite.warnings`` entries starting with ``[QMETRY]`` (main path), de-duplicated.
    Never raises."""
    out: List[str] = []
    try:
        w = (options or {}).get('_qmetry_skip_warning') if isinstance(options, dict) else None
        if w:
            out.append(str(w))
    except Exception:
        pass
    try:
        for w in (getattr(suite, 'warnings', None) or []):
            if isinstance(w, str) and w.startswith('[QMETRY]'):
                out.append(w)
    except Exception:
        pass
    seen, uniq = set(), []
    for m in out:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq


# ═══════════════════════════════════════════════════════════════════════════════
# STORAGE-STATE DISTRIBUTION
# ═══════════════════════════════════════════════════════════════════════════════

def _norm(p: Path) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except Exception:
        return os.path.normcase(str(p))


def refresh_copy_targets() -> List[Path]:
    """Where the refreshed MDA storage_state is copied: repo root, TestSuiteGenerator,
    and the running TSG root (differs only when running from a worktree)."""
    targets = [REPO_ROOT / 'storage_state.json',
               REPO_ROOT / 'TestSuiteGenerator' / 'storage_state.json']
    try:
        from modules import qmetry_steps_source as _qss
        targets.append(_qss._candidate_storage_states()[0])
    except Exception:
        targets.append(TSG_ROOT / 'storage_state.json')
    seen, out = set(), []
    for t in targets:
        k = _norm(t)
        if k not in seen:
            seen.add(k)
            out.append(Path(t))
    return out


def distribute_storage_state(src: Path, targets: List[Path]) -> List[str]:
    """Atomically copy ``src`` to each target (temp file + ``os.replace``). Skips
    duplicates, the source itself, and targets whose parent dir is missing.
    Returns the list of paths written."""
    src = Path(src)
    data = src.read_bytes()
    src_key = _norm(src)
    copied, seen = [], set()
    for t in targets:
        t = Path(t)
        k = _norm(t)
        if k in seen or k == src_key:
            continue
        seen.add(k)
        if not t.parent.is_dir():
            continue
        fd, tmp = tempfile.mkstemp(prefix='.storage_state.', suffix='.tmp', dir=str(t.parent))
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(data)
            os.replace(tmp, str(t))
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
        copied.append(str(t))
    return copied


# ═══════════════════════════════════════════════════════════════════════════════
# LOGIN SUBPROCESS
# ═══════════════════════════════════════════════════════════════════════════════

def build_login_command(result_path: Path, python_exe: Optional[str] = None,
                        targets: Optional[List[Path]] = None) -> List[str]:
    """argv for the headed-SSO worker."""
    tgts = targets if targets is not None else refresh_copy_targets()
    return [python_exe or sys.executable, str(WORKER),
            '--result', str(result_path),
            '--mda-dir', str(MDA_DIR),
            '--targets', ';'.join(str(t) for t in tgts)]


def start_login_subprocess(work_dir: Optional[Path] = None, python_exe: Optional[str] = None,
                           popen=None) -> Tuple[Any, Path, Path]:
    """Launch ``tools/qmetry_refresh_login.py`` in a separate process (a Playwright
    crash/hang cannot take down the Streamlit server). Returns
    ``(proc, result_json_path, log_path)``."""
    if work_dir is None:
        work_dir = Path(tempfile.gettempdir()) / 'tsg_qmetry_login' / datetime.now().strftime('%Y%m%d_%H%M%S')
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = work_dir / 'result.json'
    log_path = work_dir / 'login.log'
    cmd = build_login_command(result_path, python_exe=python_exe)
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    log_fh = open(log_path, 'w', encoding='utf-8')
    try:
        proc = (popen or subprocess.Popen)(cmd, cwd=str(MDA_DIR), stdout=log_fh,
                                           stderr=subprocess.STDOUT, env=env)
    finally:
        log_fh.close()  # child holds its own handle
    return proc, result_path, log_path


def read_login_result(result_path: Path) -> Dict[str, Any]:
    """Parse the worker's result JSON; ``{'ok': False, 'error': ...}`` if missing/bad."""
    try:
        with open(result_path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {'ok': False, 'error': 'bad result file'}
        return data
    except FileNotFoundError:
        return {'ok': False, 'error': 'no result file'}
    except Exception as exc:
        return {'ok': False, 'error': 'bad result file: %s' % exc}


def tail_log(log_path: Path, n: int = 5) -> List[str]:
    """Last ``n`` non-blank lines of the worker log (for progress display)."""
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = [ln.rstrip() for ln in fh if ln.strip()]
        return lines[-n:]
    except Exception:
        return []
