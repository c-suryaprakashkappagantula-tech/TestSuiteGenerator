# -*- coding: utf-8 -*-
"""
grounding_scorer.py — Grounding Score for V8.0 Test Cases.

Every TC gets a 0–100 Grounding Score representing how much of its content
traces back to real data sources (Chalk / NMNO / Jira / NBOP) versus
template filler.

Scoring algorithm:
  +35  Steps come from steps_hint or Chalk API spec (real authored steps)
  +25  Expected result is distinct from title and not a header/filler
  +20  source_type is authoritative: Chalk Scenario / Business Rule / Jira AC / NBOP UI
  +10  confidence >= 0.8 on the TraceabilityRecord
  +10  TC has ≥ 3 steps (thin 1-step TCs are likely filler)
  ─────────────────────────────────────────────────────────────────
  Max: 100

Gate threshold (default): 40
  TCs below this are dropped from the suite entirely.
  Configurable via GROUNDING_GATE_THRESHOLD env var or passed directly.

Usage:
    from modules.grounding_scorer import score_tc, gate_suite, GATE_THRESHOLD
    score = score_tc(tc)
    clean_tcs = gate_suite(test_cases, threshold=GATE_THRESHOLD, log=print)
"""

import os
import re
from typing import List, Callable, Optional

# Default gate threshold — TCs below this are dropped
GATE_THRESHOLD = int(os.getenv('GROUNDING_GATE_THRESHOLD', '40'))

# Source types that indicate real data extraction (not template/inferred)
_AUTHORITATIVE_SOURCES = frozenset([
    'Chalk Scenario',
    'Business Rule',
    'Jira AC',
    'NBOP UI',
    'Attachment',
    'Subtask AC',
])

# Step text patterns that indicate template filler (0 grounding value)
_FILLER_STEP_PATTERNS = [
    re.compile(r'^(obtain|get|fetch|retrieve) oauth token', re.IGNORECASE),
    re.compile(r'^obtain.*token.*from.*oauth', re.IGNORECASE),
    re.compile(r'^complete the primary operation successfully$', re.IGNORECASE),
    re.compile(r'^execute the (test|operation)$', re.IGNORECASE),
    re.compile(r'^verify expected result$', re.IGNORECASE),
    re.compile(r'^verify expected result:', re.IGNORECASE),
    re.compile(r'^set up preconditions$', re.IGNORECASE),
    re.compile(r'^validate the (response|output)$', re.IGNORECASE),
    re.compile(r'^refer to subtask .* for details', re.IGNORECASE),
    re.compile(r'^as per .* specification \(', re.IGNORECASE),
    re.compile(r'Scenario #\s*Test Scenario', re.IGNORECASE),
    re.compile(r'^(Negative|Positive|Edge|Regression) Scenarios\s*:', re.IGNORECASE),
    re.compile(r'^download century report', re.IGNORECASE),
    re.compile(r'^step completed successfully$', re.IGNORECASE),
]

# Expected result patterns that indicate filler
_FILLER_EXPECTED_PATTERNS = [
    re.compile(r'^(step|operation|request) (completed|accepted|processed) successfully$', re.IGNORECASE),
    re.compile(r'^API (responds|returns) with HTTP 200', re.IGNORECASE),
    re.compile(r'^verification passes as expected$', re.IGNORECASE),
    re.compile(r'^Scenario #\s*Test Scenario', re.IGNORECASE),
    re.compile(r'^(Negative|Positive|Edge|Regression) Scenarios\s*:', re.IGNORECASE),
]


def _is_filler_step(text: str) -> bool:
    """Return True if step text is template filler, not grounded content."""
    return any(p.search(text or '') for p in _FILLER_STEP_PATTERNS)


def _is_filler_expected(text: str) -> bool:
    """Return True if expected result is template filler."""
    return any(p.search(text or '') for p in _FILLER_EXPECTED_PATTERNS)


def score_tc(tc) -> int:
    """Score a TestCase on a 0–100 grounding scale.

    Args:
        tc: A TestCase dataclass instance with traceability, steps, summary, etc.

    Returns:
        Integer 0–100. Higher = more grounded in real data.
    """
    score = 0

    # ── +35: Steps have real content (from steps_hint or API spec) ──
    steps = tc.steps or []
    if steps:
        non_filler_steps = [
            s for s in steps
            if s.summary and not _is_filler_step(s.summary)
        ]
        filler_ratio = 1.0 - (len(non_filler_steps) / len(steps)) if steps else 1.0
        # Full 35 pts if all steps are non-filler, scaled down proportionally
        score += int(35 * (1.0 - filler_ratio))

    # ── +25: Expected results are distinct and non-filler ──
    if steps:
        good_expected = [
            s for s in steps
            if s.expected
            and not _is_filler_expected(s.expected)
            and s.expected.strip().lower() != (tc.summary or '').strip().lower()
            and len(s.expected.strip()) > 15
        ]
        expected_ratio = len(good_expected) / len(steps) if steps else 0.0
        score += int(25 * expected_ratio)

    # ── +20: Authoritative source type ──
    tr = getattr(tc, 'traceability', None)
    if tr:
        source_type = getattr(tr, 'source_type', '') or ''
        if source_type in _AUTHORITATIVE_SOURCES:
            score += 20
        elif source_type:
            # Partial credit for known-but-lower sources
            score += 8

    # ── +10: TraceabilityRecord has high confidence (>= 0.8) ──
    if tr:
        confidence = getattr(tr, 'confidence', 0.0) or 0.0
        if confidence >= 0.8:
            score += 10
        elif confidence >= 0.5:
            score += 5

    # ── +10: TC has enough steps to be meaningful (>= 3) ──
    if len(steps) >= 3:
        score += 10
    elif len(steps) == 2:
        score += 5

    return min(score, 100)


def score_suite(test_cases: List) -> List[int]:
    """Score all TCs in a suite. Returns list of scores in same order."""
    return [score_tc(tc) for tc in test_cases]


def gate_suite(
    test_cases: List,
    threshold: int = GATE_THRESHOLD,
    log: Callable = print,
) -> List:
    """Apply the grounding gate: drop TCs below threshold.

    Also attaches `grounding_score` attribute to each TC for downstream use.

    Args:
        test_cases: List of TestCase objects
        threshold: Minimum grounding score to keep (default GATE_THRESHOLD)
        log: Logging function

    Returns:
        Filtered list of TestCase objects with grounding_score attached.
    """
    if not test_cases:
        return []

    kept = []
    dropped = []

    for tc in test_cases:
        score = score_tc(tc)
        # Attach score to TC for Excel export and UI display
        tc.grounding_score = score

        if score >= threshold:
            kept.append(tc)
        else:
            dropped.append((tc, score))

    if dropped:
        log('[GROUND] Gate threshold=%d — dropped %d TC(s):' % (threshold, len(dropped)))
        for tc, s in dropped:
            log('[GROUND]   DROPPED (score=%d): %s' % (s, (tc.summary or '')[:80]))
    else:
        log('[GROUND] Gate threshold=%d — all %d TCs passed' % (threshold, len(kept)))

    return kept


def suite_grounding_pct(test_cases: List) -> float:
    """Return the mean grounding score as a 0–100 percentage."""
    if not test_cases:
        return 0.0
    scores = [getattr(tc, 'grounding_score', score_tc(tc)) for tc in test_cases]
    return round(sum(scores) / len(scores), 1)


def grounding_badge(pct: float) -> str:
    """Return an emoji badge based on grounding percentage."""
    if pct >= 80:
        return '🟢'
    elif pct >= 60:
        return '🟡'
    else:
        return '🔴'
