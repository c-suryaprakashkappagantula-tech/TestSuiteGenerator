"""
humanizer.py — Post-processing layer to make generated test suites feel human-written.
Applies: description variety, priority scoring, TC dedup/merge, smart removal, risk ordering.
No LLM needed — pure rule-based intelligence.
"""
import re
import random
from typing import List, Dict, Tuple
from collections import defaultdict


# ================================================================
# 1. DESCRIPTION HUMANIZER — vary phrasing so not every TC starts with "To validate that"
# ================================================================

_POSITIVE_PREFIXES = [
    'Confirm that %s',
    'Ensure the system correctly handles %s',
    'Verify that %s',
    'Check that %s',
    'Validate that %s',
    'Ensure %s',
    'Confirm the system %s',
]

_NEGATIVE_PREFIXES = [
    'Verify the system rejects %s',
    'Confirm that %s is properly handled',
    'Ensure the system fails gracefully when %s',
    'Check that %s results in appropriate error handling',
    'Validate that %s is blocked with clear error messaging',
]

_EDGE_PREFIXES = [
    'Verify system behavior when %s',
    'Confirm correct handling of the edge case where %s',
    'Ensure stability when %s',
    'Check system resilience when %s',
]

_E2E_PREFIXES = [
    'Verify the complete end-to-end flow for %s',
    'Confirm the full lifecycle of %s across all systems',
    'Validate the entire workflow for %s from initiation to completion',
]

_REGRESSION_PREFIXES = [
    'Confirm no regression in %s after the change',
    'Verify that %s remains unaffected',
    'Ensure existing functionality for %s is preserved',
]

def humanize_descriptions(test_cases, log=print):
    """Rewrite TC descriptions to use varied, natural phrasing."""
    _used_prefixes = set()
    changed = 0

    for tc in test_cases:
        desc = tc.description or ''
        # Only rewrite if it starts with the robotic "To validate that"
        match = re.match(r'^To validate that\s+(.+)', desc, re.IGNORECASE | re.DOTALL)
        if not match:
            continue

        core = match.group(1).strip()
        cat = (tc.category or '').lower()

        if 'negative' in cat:
            pool = _NEGATIVE_PREFIXES
        elif 'edge' in cat:
            pool = _EDGE_PREFIXES
        elif 'e2e' in cat or 'end-to-end' in cat:
            pool = _E2E_PREFIXES
        elif 'regression' in cat:
            pool = _REGRESSION_PREFIXES
        else:
            pool = _POSITIVE_PREFIXES

        # Pick a prefix we haven't used recently (avoid repetition)
        available = [p for p in pool if p not in _used_prefixes]
        if not available:
            _used_prefixes.clear()
            available = pool

        prefix = random.choice(available)
        _used_prefixes.add(prefix)

        # Rebuild description
        # Split on "Expected Result:" to preserve that part
        parts = re.split(r'\nExpected Result:', desc, maxsplit=1)
        new_desc = prefix % core.split('.')[0].strip().rstrip('.')
        if len(parts) > 1:
            new_desc += '.\nExpected Result:' + parts[1]
        else:
            new_desc += '.'

        tc.description = new_desc
        changed += 1

    log('[HUMANIZE] Rewrote %d/%d TC descriptions with varied phrasing' % (changed, len(test_cases)))
    return test_cases


# ================================================================
# 2. PRIORITY SCORING — tag each TC as P1/P2/P3
# ================================================================

_P1_KEYWORDS = ['rollback', 'data integrity', 'data corruption', 'authentication', 'auth fail',
                'timeout', 'e2e', 'end-to-end', 'api fail', 'system unavailable',
                'transaction history', 'audit trail', 'swap fail', 'activation fail']
_P2_KEYWORDS = ['negative', 'invalid', 'rejected', 'error', 'boundary', 'concurrent',
                'retry', 'duplicate', 'hotline', 'suspend', 'deactivat']
_P3_KEYWORDS = ['ui display', 'portal', 'menu visible', 'kafka', 'notification',
                'different plan', 'different manufacturer', 'wearable']


def score_priority(test_cases, log=print):
    """Assign P1/P2/P3 priority to each TC based on risk analysis."""
    counts = {'P1': 0, 'P2': 0, 'P3': 0}

    for tc in test_cases:
        text = (tc.summary + ' ' + tc.description + ' ' + tc.category).lower()

        p1_hits = sum(1 for kw in _P1_KEYWORDS if kw in text)
        p2_hits = sum(1 for kw in _P2_KEYWORDS if kw in text)
        p3_hits = sum(1 for kw in _P3_KEYWORDS if kw in text)

        # Core happy path TCs (first few) are always P1
        try:
            sno = int(tc.sno)
        except (ValueError, TypeError):
            sno = 99

        if p1_hits >= 2 or (tc.category == 'E2E') or sno <= 5:
            tc._priority = 'P1'
        elif p1_hits >= 1 or p2_hits >= 2 or tc.category == 'Negative':
            tc._priority = 'P2'
        else:
            tc._priority = 'P3'

        counts[tc._priority] = counts.get(tc._priority, 0) + 1

    log('[HUMANIZE] Priority: P1=%d | P2=%d | P3=%d' % (counts['P1'], counts['P2'], counts['P3']))
    return test_cases


# ================================================================
# 3. RISK-BASED ORDERING — sort by priority then category
# ================================================================

_CATEGORY_ORDER = {
    'Happy Path': 0, 'Positive': 0,
    'E2E': 1, 'End-to-End': 1,
    'Negative': 2,
    'Edge Case': 3,
    'Regression': 4,
}

_PRIORITY_ORDER = {'P1': 0, 'P2': 1, 'P3': 2}


def reorder_by_risk(test_cases, log=print):
    """Reorder TCs: P1 first, then by category (happy → e2e → negative → edge → regression)."""
    def sort_key(tc):
        pri = _PRIORITY_ORDER.get(getattr(tc, '_priority', 'P3'), 2)
        cat = _CATEGORY_ORDER.get(tc.category, 5)
        return (pri, cat)

    test_cases.sort(key=sort_key)
    log('[HUMANIZE] Reordered TCs by risk priority')
    return test_cases


# ================================================================
# 4. SMART DEDUP/MERGE — merge TCs with >80% step overlap
# ================================================================

def _step_fingerprint(tc) -> set:
    """Create a keyword fingerprint from TC steps."""
    text = ' '.join(s.summary.lower() + ' ' + s.expected.lower() for s in tc.steps)
    return set(re.findall(r'\b\w{4,}\b', text))


def _title_similarity(title1, title2) -> float:
    """Jaccard similarity between two TC titles."""
    words1 = set(re.findall(r'\b\w{4,}\b', title1.lower()))
    words2 = set(re.findall(r'\b\w{4,}\b', title2.lower()))
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / len(words1 | words2)


def _step_similarity(tc1, tc2) -> float:
    """Jaccard similarity between two TCs based on step content."""
    fp1 = _step_fingerprint(tc1)
    fp2 = _step_fingerprint(tc2)
    if not fp1 or not fp2:
        return 0.0
    return len(fp1 & fp2) / len(fp1 | fp2)


def dedup_and_merge(test_cases, log=print, threshold=0.85):
    """Find TCs with very high step overlap and merge them.
    Keeps the first TC and adds variant info to its description.
    Only merges if both TCs have the same category AND similar titles."""
    if len(test_cases) < 2:
        return test_cases

    # For smaller suites (<40 TCs), be more conservative — only merge near-duplicates
    if len(test_cases) < 40:
        threshold = 0.92

    merged_into = {}  # tc_index -> list of merged tc summaries
    to_remove = set()

    for i in range(len(test_cases)):
        if i in to_remove:
            continue
        for j in range(i + 1, len(test_cases)):
            if j in to_remove:
                continue
            sim = _step_similarity(test_cases[i], test_cases[j])
            if sim >= threshold:
                # Same category AND similar title? Merge.
                if test_cases[i].category == test_cases[j].category:
                    # Also check title similarity — don't merge if titles describe different scenarios
                    title_sim = _title_similarity(test_cases[i].summary, test_cases[j].summary)
                    if title_sim < 0.5:
                        continue  # Different scenarios despite similar steps — keep both
                    if i not in merged_into:
                        merged_into[i] = []
                    merged_into[i].append(test_cases[j].summary)
                    to_remove.add(j)

    # Apply merges — add variant info to the surviving TC
    for idx, merged_summaries in merged_into.items():
        tc = test_cases[idx]
        variant_names = [re.sub(r'^TC\d+_\w+-\d+_', '', s)[:60] for s in merged_summaries]
        tc.description += '\nVariants (merged): ' + '; '.join(variant_names)

    # Remove merged TCs
    result = [tc for i, tc in enumerate(test_cases) if i not in to_remove]

    if to_remove:
        log('[HUMANIZE] Merged %d redundant TCs (>%d%% step overlap) into %d parent TCs' % (
            len(to_remove), int(threshold * 100), len(merged_into)))

    return result


# ================================================================
# 5. SMART REMOVAL — flag TCs that add no unique coverage
# ================================================================

def flag_low_value(test_cases, log=print):
    """Flag TCs that are fully covered by other TCs (subset coverage).
    Doesn't remove — just adds a warning to description."""
    flagged = 0
    for i, tc in enumerate(test_cases):
        fp_i = _step_fingerprint(tc)
        if not fp_i or len(fp_i) < 5:
            continue
        # Check if this TC's content is a subset of any other TC
        for j, other in enumerate(test_cases):
            if i == j:
                continue
            fp_j = _step_fingerprint(other)
            if not fp_j:
                continue
            # If >90% of tc_i's keywords appear in tc_j, it's likely redundant
            overlap = len(fp_i & fp_j) / len(fp_i) if fp_i else 0
            if overlap > 0.90 and len(other.steps) > len(tc.steps):
                tc._low_value = True
                flagged += 1
                break

    if flagged:
        log('[HUMANIZE] Flagged %d low-value TCs (covered by other TCs)' % flagged)
    return test_cases


# ================================================================
# MAIN ENTRY — run all humanization passes
# ================================================================

def humanize_suite(test_cases, log=print):
    """Run all humanization passes on the test suite.
    Order: dedup → priority → humanize descriptions → flag low-value → reorder."""
    log('[HUMANIZE] Starting humanization pass on %d TCs...' % len(test_cases))

    # 1. Dedup/merge first (reduces TC count)
    test_cases = dedup_and_merge(test_cases, log)

    # 2. Score priorities
    test_cases = score_priority(test_cases, log)

    # 3. Humanize descriptions
    test_cases = humanize_descriptions(test_cases, log)

    # 4. Flag low-value TCs
    test_cases = flag_low_value(test_cases, log)

    # 5. Reorder by risk
    test_cases = reorder_by_risk(test_cases, log)

    log('[HUMANIZE] Done — %d TCs after humanization' % len(test_cases))
    return test_cases
