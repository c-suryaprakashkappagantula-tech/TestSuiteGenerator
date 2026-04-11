"""
transaction_log.py — Track all test suite generations.
Stores history in a JSON file for the History panel.
Point 12: Added file locking for concurrent Streamlit session safety.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from .config import ROOT

LOG_FILE = ROOT / 'transaction_log.json'


def _lock_file(f):
    """Acquire an exclusive lock on the file handle (Windows + Unix)."""
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX)


def _unlock_file(f):
    """Release the lock."""
    if sys.platform == 'win32':
        import msvcrt
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)


def _load():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding='utf-8'))
        except:
            return []
    return []


def _save(entries):
    try:
        with open(str(LOG_FILE), 'w', encoding='utf-8') as f:
            _lock_file(f)
            try:
                json.dump(entries, f, indent=2, ensure_ascii=False)
            finally:
                _unlock_file(f)
    except Exception:
        # Fallback: write without lock
        LOG_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding='utf-8')


def log_generation(feature_id, pi, tc_count, step_count, strategy, file_path, status='SUCCESS'):
    """Log a test suite generation."""
    entries = _load()
    entries.insert(0, {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'feature_id': feature_id,
        'pi': pi,
        'tc_count': tc_count,
        'step_count': step_count,
        'strategy': strategy,
        'file_path': str(file_path),
        'file_name': Path(file_path).name if file_path else '',
        'status': status,
    })
    # Keep last 50 entries
    entries = entries[:50]
    _save(entries)


def get_history():
    """Get all logged generations."""
    return _load()
