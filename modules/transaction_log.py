"""
transaction_log.py — Track all test suite generations.
Stores history in a JSON file for the History panel.
"""
import json
from datetime import datetime
from pathlib import Path
from .config import ROOT

LOG_FILE = ROOT / 'transaction_log.json'


def _load():
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text(encoding='utf-8'))
        except:
            return []
    return []


def _save(entries):
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
