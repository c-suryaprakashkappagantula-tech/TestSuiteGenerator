"""
combination_engine.py — Smart Combination Logic for V8.0 Data-First Engine.

Determines how extracted dimensions are combined into test cases:
  - Independent dimensions: one TC per value (additive, NOT multiplicative)
  - Crossed dimensions: only when data explicitly says to cross
  - Scenarios: 1:1 mapping (one TC per scenario)
  - Negative specs: 1:1 mapping (one TC per error code/state)
  - Deduplication: merge TCs with identical test logic

The key insight: dimensions are tested INDEPENDENTLY unless the data
explicitly requires crossing. This avoids cartesian explosion.

Example: Products=[Phone, Tablet, Smartwatch], Input Types=[IMEI, ICCID]
  → 5 TCs (3 + 2), NOT 6 TCs (3 × 2)
  UNLESS Chalk says "For Smartwatch, only IMEI is valid" → then cross those two.
"""
from typing import List, Callable

from .data_models_v8 import (
    Dimension, ExtractedScenario, NegativeSpec, DimensionSet, CombinationPlan,
)


# ================================================================
# MAIN ENTRY POINT
# ================================================================


def plan_combinations(
    dimension_set: DimensionSet,
    log: Callable = print,
) -> CombinationPlan:
    """Determine smart multiplication strategy for dimensions.

    Rules:
      1. Each dimension generates TCs independently (one per value)
      2. Cross-dimension only when Dimension.cross_with explicitly names another
      3. Error codes → one negative TC per error (from negative_specs)
      4. Scenarios from Chalk → one TC per scenario (no multiplication)
      5. Deduplication: if two planned TCs would have identical test logic, merge

    Returns a CombinationPlan consumed by the TC Builder.
    """
    log('[COMBINE] Planning combinations...')

    independent_dimensions: List[Dimension] = []
    crossed_dimensions: List[tuple] = []
    reduction_notes: List[str] = []

    # Track which dimensions are involved in crosses (they won't be independent)
    crossed_dim_names = set()

    # ── Step 1: Identify explicit crosses ──
    dim_by_name = {d.name: d for d in dimension_set.dimensions}

    for dim in dimension_set.dimensions:
        if dim.cross_with:
            for target_name in dim.cross_with:
                if target_name in dim_by_name:
                    target_dim = dim_by_name[target_name]
                    # Avoid duplicate crosses (A×B and B×A)
                    pair_key = tuple(sorted([dim.name, target_name]))
                    already_crossed = any(
                        tuple(sorted([d1.name, d2.name])) == pair_key
                        for d1, d2 in crossed_dimensions
                    )
                    if not already_crossed:
                        crossed_dimensions.append((dim, target_dim))
                        crossed_dim_names.add(dim.name)
                        crossed_dim_names.add(target_name)
                        log('[COMBINE]   Explicit cross: %s × %s (%d × %d = %d TCs)' % (
                            dim.name, target_name,
                            len(dim.values), len(target_dim.values),
                            len(dim.values) * len(target_dim.values),
                        ))

    # ── Step 2: Remaining dimensions are independent ──
    for dim in dimension_set.dimensions:
        if dim.name not in crossed_dim_names:
            independent_dimensions.append(dim)
            if len(dim.values) > 1:
                reduction_notes.append(
                    '%s: %d values tested independently (not crossed with other dimensions)' % (
                        dim.name, len(dim.values))
                )

    # ── Step 3: Calculate total planned TCs ──
    independent_tc_count = sum(len(d.values) for d in independent_dimensions)
    crossed_tc_count = sum(len(d1.values) * len(d2.values) for d1, d2 in crossed_dimensions)
    scenario_tc_count = len(dimension_set.scenarios)
    negative_tc_count = len(dimension_set.negative_specs)

    total_before_dedup = independent_tc_count + crossed_tc_count + scenario_tc_count + negative_tc_count

    # ── Step 4: Deduplication ──
    # Actually deduplicate scenarios and negatives (not just estimate)
    deduped_scenarios, scenario_dedup_count = _deduplicate_scenarios(dimension_set.scenarios)
    deduped_negatives, negative_dedup_count = _deduplicate_negatives(dimension_set.negative_specs)
    dedup_count = scenario_dedup_count + negative_dedup_count

    scenario_tc_count = len(deduped_scenarios)
    negative_tc_count = len(deduped_negatives)
    total_planned = independent_tc_count + crossed_tc_count + scenario_tc_count + negative_tc_count

    if dedup_count > 0:
        reduction_notes.append('Deduplication removed %d planned TCs with identical test logic' % dedup_count)

    # ── Summary notes ──
    if not crossed_dimensions and len(dimension_set.dimensions) > 1:
        dim_names = [d.name for d in dimension_set.dimensions]
        reduction_notes.append(
            'No cartesian product applied across %s — dimensions tested independently' % (
                ' × '.join(dim_names))
        )

    log('[COMBINE]   Independent: %d TCs from %d dimensions' % (independent_tc_count, len(independent_dimensions)))
    log('[COMBINE]   Crossed: %d TCs from %d crosses' % (crossed_tc_count, len(crossed_dimensions)))
    log('[COMBINE]   Scenarios: %d TCs (1:1 mapping)' % scenario_tc_count)
    log('[COMBINE]   Negatives: %d TCs (1:1 mapping)' % negative_tc_count)
    log('[COMBINE]   Dedup removed: %d' % dedup_count)
    log('[COMBINE]   Total planned: %d TCs' % total_planned)

    return CombinationPlan(
        independent_dimensions=independent_dimensions,
        crossed_dimensions=crossed_dimensions,
        scenario_tcs=deduped_scenarios,
        negative_tcs=deduped_negatives,
        total_planned_tcs=total_planned,
        reduction_notes=reduction_notes,
    )


# ================================================================
# DEDUPLICATION
# ================================================================


def _deduplicate_scenarios(scenarios: List[ExtractedScenario]) -> tuple:
    """Deduplicate scenarios — only remove EXACT title duplicates.

    Chalk scenarios are authored by test architects and are meaningful.
    Only remove if two scenarios have the EXACT same normalized title.
    Near-matches are kept — they likely test different things.

    Returns: (deduped_list, count_removed)
    """
    seen_titles = set()
    deduped = []
    for sc in scenarios:
        normalized = _normalize_title(sc.title)
        if not normalized or len(normalized) < 5:
            continue  # Skip empty/tiny titles
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            deduped.append(sc)
    return deduped, len(scenarios) - len(deduped)


def _deduplicate_negatives(negative_specs: List[NegativeSpec]) -> tuple:
    """Actually deduplicate negative specs by error code + condition.

    Returns: (deduped_list, count_removed)
    """
    seen_keys = set()
    deduped = []
    for neg in negative_specs:
        key = (neg.error_code.strip().lower(), neg.triggering_condition.strip().lower())
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(neg)
    return deduped, len(negative_specs) - len(deduped)


def _estimate_deduplication(
    independent_dimensions: List[Dimension],
    scenarios: List[ExtractedScenario],
    negative_specs: List[NegativeSpec],
) -> int:
    """Estimate how many planned TCs would be duplicates.

    Detects:
      - Dimension values that appear as scenario titles (already covered)
      - Negative specs whose error codes match dimension values (already covered)
      - Scenarios with identical normalized titles

    Returns the count of TCs that should be removed.
    """
    dedup_count = 0

    # ── Check if any scenario title duplicates a dimension value ──
    # (e.g., scenario "Retrieve device by IMEI" when input_type dimension has "IMEI")
    # These are NOT duplicates — the scenario has specific steps while the dimension TC
    # would be a generic per-value TC. So we don't dedup these.

    # ── Check for duplicate scenario titles ──
    seen_titles = set()
    for sc in scenarios:
        normalized = _normalize_title(sc.title)
        if normalized in seen_titles:
            dedup_count += 1
        else:
            seen_titles.add(normalized)

    # ── Check for duplicate negative specs ──
    seen_errors = set()
    for neg in negative_specs:
        key = (neg.error_code.strip().lower(), neg.triggering_condition.strip().lower())
        if key in seen_errors:
            dedup_count += 1
        else:
            seen_errors.add(key)

    return dedup_count


def _normalize_title(title: str) -> str:
    """Normalize a title for deduplication comparison.

    Lowercases, collapses whitespace, strips common prefixes.
    """
    import re
    t = title.strip().lower()
    t = re.sub(r'\s+', ' ', t)
    # Strip common prefixes
    for prefix in ['verify ', 'validate ', 'test ', 'check ']:
        if t.startswith(prefix):
            t = t[len(prefix):]
    return t
