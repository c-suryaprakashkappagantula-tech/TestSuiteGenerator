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

        # Sanity check — if core is too short or starts with a preposition, skip rewrite
        _core_first = core.split('.')[0].strip().rstrip('.')
        if len(_core_first) < 15 or _core_first.lower().startswith(('for ', 'the ', 'a ', 'an ', 'in ', 'on ', 'to ', 'is ', 'it ')):
            continue

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
# 6. CONTENT CLEANER — fix common issues across all TCs
# ================================================================

# CDR/Mediation keywords — these features don't use ITMBO/NBOP channels
_CDR_KEYWORDS = ['cdr', 'mediation', 'prr', 'ild', 'international roaming', 'country code',
                 'roaming', 'call detail', 'usage record', 'call origin', 'call destination']


def clean_tc_content(test_cases, log=print):
    """Fix common content issues across all TCs."""
    fixes = 0

    # Detect if this is a CDR/Mediation feature
    all_text = ' '.join(tc.summary.lower() + ' ' + tc.description.lower() for tc in test_cases)
    is_cdr = sum(1 for kw in _CDR_KEYWORDS if kw in all_text) >= 2

    for tc in test_cases:
        # Fix 1: Replace ITMBO/NBOP with Mediation for CDR features
        if is_cdr:
            for field in ['summary', 'description', 'preconditions']:
                val = getattr(tc, field, '') or ''
                if 'ITMBO' in val or 'NBOP' in val:
                    new_val = val.replace('via ITMBO', 'via Mediation pipeline')
                    new_val = new_val.replace('via NBOP', 'via Mediation pipeline')
                    new_val = new_val.replace('channel ITMBO', 'Mediation pipeline')
                    new_val = new_val.replace('channel NBOP', 'Mediation pipeline')
                    new_val = new_val.replace('with ITMBO', 'via Mediation')
                    new_val = new_val.replace('with NBOP', 'via Mediation')
                    if new_val != val:
                        setattr(tc, field, new_val)
                        fixes += 1

        # Fix 2: Clean up step text — remove special chars, fix encoding
        for step in tc.steps:
            step.summary = step.summary.replace('→', '-').replace('←', '-')
            step.summary = re.sub(r'["""]', '', step.summary)
            step.expected = step.expected.replace('→', '-').replace('←', '-')
            step.expected = re.sub(r'["""]', '', step.expected)

        # Fix 3: Remove "Reasoning:" lines from description (internal, not for testers)
        if 'Reasoning:' in tc.description:
            tc.description = re.sub(r'\nReasoning:.*$', '', tc.description, flags=re.MULTILINE).strip()
            fixes += 1

        # Fix 4: Clean up "Variants (merged):" to be more readable
        if 'Variants (merged):' in tc.description:
            tc.description = tc.description.replace('Variants (merged):', '\nAlso covers:')

        # Fix 5: Remove empty/whitespace-only lines in description
        if tc.description:
            lines = [l for l in tc.description.split('\n') if l.strip()]
            tc.description = '\n'.join(lines)

        # Fix 6: Strip leaked Jira tags from summary (e.g., "Verify NE, INTG]: New MVNO -")
        _sum_no_prefix = re.sub(r'^TC\d+_[\w-]+_', '', tc.summary)
        if re.search(r'[A-Z]{2,6}(?:,\s*[A-Z]{2,6})+', _sum_no_prefix):
            _cleaned = re.sub(r'\[?(?:[A-Z]{2,10})(?:\s*,\s*(?:[A-Z]{2,10}))+\]?\s*:?\s*', '', _sum_no_prefix)
            _cleaned = re.sub(r'New\s+MVNO\s*[-:—ù]\s*', '', _cleaned, flags=re.IGNORECASE)
            _cleaned = re.sub(r'\d{4}-', '', _cleaned)
            _cleaned = _cleaned.strip(' -:[]')
            if _cleaned and len(_cleaned) > 10:
                _prefix_m = re.match(r'^(TC\d+_[\w-]+_)', tc.summary)
                tc.summary = (_prefix_m.group(1) if _prefix_m else '') + _cleaned
                fixes += 1

        # Fix 6: Ensure preconditions are numbered properly
        if tc.preconditions:
            pre_lines = tc.preconditions.split('\n')
            renumbered = []
            num = 1
            for line in pre_lines:
                line = line.strip()
                if not line:
                    continue
                # Strip existing numbering
                line = re.sub(r'^\d+[\.\)]\s*', '', line)
                line = re.sub(r'^\d+\.\t', '', line)
                renumbered.append('%d.\t%s' % (num, line))
                num += 1
            tc.preconditions = '\n'.join(renumbered)

    if fixes:
        log('[HUMANIZE] Content cleaner: %d fixes applied' % fixes)
    return test_cases


# ================================================================
# 7. FINAL VALIDATION — last line of defense against garbage TCs
# ================================================================

def final_validation(test_cases, log=print):
    """Final quality gate — reject or fix TCs that are clearly broken.
    This runs AFTER all other passes as the last line of defense."""
    clean = []
    rejected = 0
    fixed = 0

    for tc in test_cases:
        # Extract the name part (after TC##_FEATURE_)
        _name = re.sub(r'^TC\d+_[\w-]+_', '', tc.summary).strip()

        # REJECT: summary too short (< 15 chars) — these are garbage fragments
        if len(_name) < 15:
            rejected += 1
            continue

        # REJECT: summary is just a single word + period
        if re.match(r'^[\w]+\.$', _name) and len(_name) < 20:
            rejected += 1
            continue

        # REJECT: summary starts with preposition and is short
        if _name.lower().startswith(('for ', 'the ', 'a ', 'an ', 'in ', 'on ', 'to ', 'is ', 'it ')) and len(_name) < 30:
            rejected += 1
            continue

        # FIX: strip "..." from feature name truncation in summaries
        _name = re.sub(r'\.{2,}', '', _name).strip()
        if _name and _name != re.sub(r'^TC\d+_[\w-]+_', '', tc.summary).strip():
            _prefix_m = re.match(r'^(TC\d+_[\w-]+_)', tc.summary)
            tc.summary = (_prefix_m.group(1) if _prefix_m else '') + _name
            fixed += 1

        # FIX: strip leaked Jira tags from summary (anywhere, not just start)
        if re.search(r'[A-Z]{2,6}(?:,\s*[A-Z]{2,6})+\]', _name) or re.search(r'\[?[A-Z]{2,6},\s*[A-Z]{2,6}', _name):
            # Strip tag pattern and "New MVNO -" that follows
            _name = re.sub(r'\[?(?:[A-Z]{2,10})(?:\s*,\s*(?:[A-Z]{2,10}))+\]?\s*:?\s*', '', _name)
            _name = re.sub(r'New\s+MVNO\s*[-:—ù]\s*', '', _name, flags=re.IGNORECASE)
            _name = re.sub(r'\d{4}-', '', _name)  # strip "3641-" feature ID prefix
            _name = _name.strip(' -:[]')
            _prefix_m = re.match(r'^(TC\d+_[\w-]+_)', tc.summary)
            tc.summary = (_prefix_m.group(1) if _prefix_m else '') + _name
            fixed += 1

        # FIX: broken humanizer rewrites ("Validate that for X is blocked...")
        if 'is blocked with clear error' in _name and _name.lower().startswith(('validate that for', 'check that for')):
            rejected += 1
            continue

        # REJECT: description is also garbage
        if tc.description and len(tc.description.strip()) < 10:
            rejected += 1
            continue

        clean.append(tc)

    if rejected or fixed:
        log('[HUMANIZE] Final validation: %d passed, %d fixed, %d rejected' % (
            len(clean) - fixed, fixed, rejected))

    return clean


# ================================================================
# MAIN ENTRY — run all humanization passes
# ================================================================

def humanize_suite(test_cases, log=print):
    """Run all humanization passes on the test suite.
    Order: clean → dedup → priority → humanize descriptions → validate → flag low-value → reorder."""
    log('[HUMANIZE] Starting humanization pass on %d TCs...' % len(test_cases))

    # 0. Clean pass — fix common issues before other passes
    test_cases = clean_tc_content(test_cases, log)

    # 1. Dedup/merge first (reduces TC count)
    test_cases = dedup_and_merge(test_cases, log)

    # 2. Score priorities
    test_cases = score_priority(test_cases, log)

    # 3. Humanize descriptions
    test_cases = humanize_descriptions(test_cases, log)

    # 4. FINAL VALIDATION — reject garbage TCs that slipped through everything
    test_cases = final_validation(test_cases, log)

    # 5. Flag low-value TCs
    test_cases = flag_low_value(test_cases, log)

    # 6. Reorder by risk
    test_cases = reorder_by_risk(test_cases, log)

    log('[HUMANIZE] Done — %d TCs after humanization' % len(test_cases))
    return test_cases
