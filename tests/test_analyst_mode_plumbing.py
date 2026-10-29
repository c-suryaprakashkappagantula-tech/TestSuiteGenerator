# -*- coding: utf-8 -*-
"""Analyst-mode plumbing: options['data_only'] reaches extract_dimensions.

FEAT-001 only adds the dashboard toggle (`analyst_mode`) and threads
``options['data_only'] = not analyst_mode`` into the single options dict passed to
``block_build_suite_v8`` → ``build_test_suite_v8``. The engine already reads
``options.get('data_only', True)`` and forwards it as ``data_only=`` to
``extract_dimensions`` (data_first_engine.py). These tests pin exactly that wire:

  * ``options={'data_only': False}`` (toggle ON)  → extract_dimensions sees data_only=False
  * default options (toggle OFF / key absent)     → extract_dimensions sees data_only=True

The engine imports ``extract_dimensions`` at module import time
(``from .dimension_extractor import extract_dimensions``), so the bound name on
``modules.data_first_engine`` is what the call site resolves. We monkeypatch that name
with a capturing stub that records ``data_only`` and raises a private sentinel to halt the
run immediately after the capture — so the assertion never depends on any downstream
engine behaviour, DB access, or network (cache-free, hermetic).

Mirrors the direct-construction style of tests/test_cr_qmetry_supplement.py; no tsg_cache.db.
"""
import os
import sys

import pytest

_TSG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TSG_ROOT not in sys.path:
    sys.path.insert(0, _TSG_ROOT)

from modules import data_first_engine as dfe
from modules.jira_fetcher import JiraIssue


class _StopAfterCapture(Exception):
    """Private sentinel: halt the engine right after extract_dimensions is reached."""


def _feature_jira():
    """A plain feature ticket (NOT a CR/bug) so the engine flows to Step 1 dimension
    extraction rather than delegating to the narrow-CR path."""
    return JiraIssue(
        key='MWTGPROV-0001',
        summary='Analyst mode plumbing feature',
        description='A feature used to verify the data_only option is threaded through.',
        issue_type='Story',
        acceptance_criteria='The option reaches dimension extraction unchanged.',
    )


def _capture_data_only(monkeypatch):
    """Patch the engine-bound extract_dimensions with a capturing stub; return a dict
    whose 'value' is filled with the data_only kwarg once the engine calls it."""
    captured = {'value': None, 'seen': False}

    def _stub(*args, **kwargs):
        captured['seen'] = True
        captured['value'] = kwargs.get('data_only')
        raise _StopAfterCapture()

    monkeypatch.setattr(dfe, 'extract_dimensions', _stub)
    return captured


def test_toggle_on_passes_data_only_false(monkeypatch):
    captured = _capture_data_only(monkeypatch)
    with pytest.raises(_StopAfterCapture):
        dfe.build_test_suite_v8(
            _feature_jira(), None, [], {'data_only': False},
            log=lambda *a, **k: None)
    assert captured['seen'], 'engine never reached extract_dimensions'
    assert captured['value'] is False, (
        'toggle ON must forward data_only=False to extract_dimensions'
    )


def test_default_passes_data_only_true(monkeypatch):
    captured = _capture_data_only(monkeypatch)
    with pytest.raises(_StopAfterCapture):
        dfe.build_test_suite_v8(
            _feature_jira(), None, [], {},
            log=lambda *a, **k: None)
    assert captured['seen'], 'engine never reached extract_dimensions'
    assert captured['value'] is True, (
        'default (toggle OFF / key absent) must forward data_only=True'
    )
