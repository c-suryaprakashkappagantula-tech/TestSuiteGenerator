# -*- coding: utf-8 -*-
"""
eligibility_negatives.py — the single decision about feature-eligibility negatives.

WHY THIS EXISTS
    The decision "does this feature need eligibility negatives, and which ones" lived in
    two places:

      V7  test_engine._synthesize_eligibility_negatives   (CR/bug tickets)
      V8  data_first_engine._inject_eligibility_negatives (everything else)

    They disagreed, and the disagreement was not cosmetic. tsg-tse-hardening Requirement
    4.1 asks for exactly one implementation; this module is it. Each engine still builds
    its own TestCase objects with its own titles and step wording - unifying that text
    would change the descriptions of 13 CR features, and CR descriptions feed the cap
    score that assigns TC numbers, so it is deliberately left for a separate change with
    its own diff.

SOURCE MATERIAL ONLY
    The gate reads the ticket and Chalk. It must never read test cases the run has
    already generated.

    V7 had exactly that bug and it was only half fixed. Its outer keyword gate was moved
    onto source text, but `has_commercial`, `has_tmo` and `is_feature_gated` - which decide
    WHICH negatives get added - still read `jira_text + suite_text`. So generated content
    could still authorise more generation, one level down from where the bug was found.
    On MWTGPROV-4166 that loop gave a guaranteed-delivery defect ticket four eligibility
    negatives unrelated to its acceptance criteria, and because the V7 caller feeds the
    count into `suite._synth_neg`, it also raised that feature's CR cap by four.

WHAT EACH ENGINE CONTRIBUTED
    Kept from V7: the exclusion of notification, batch and UI feature types, and the
    mediation/CDR exclusion including 'prr'.
    Kept from V8: source-only signals, and the requirement of a strong line-eligibility
    signal rather than a bare keyword match.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

# A gating condition has to be stated outright by one of these. Note what is ABSENT:
# a bare 'feature'. Every ticket in this project is about feature provisioning, so the
# word signals nothing about eligibility gating - it fired on 48% of the cache and was the
# sole trigger for 71 features.
_ELIGIBILITY_SIGNALS = (
    'commercial line', 'subscriber', 'line on tmo', 'hotspot', 'tethering', 'roaming',
    'entitlement', 'add-on', 'addon', 'rate plan', 'rateplan', 'eligible', 'provision',
)

# Signals that this is specifically a feature-gated line capability. 'feature' is excluded
# here for the same reason as above; V7 included it, which made the flag almost always true.
_FEATURE_GATED_SIGNALS = (
    'hotspot', 'tethering', 'roaming', 'entitlement', 'add-on', 'addon',
    'international', 'ild',
)

_TMO_SIGNALS = ('tmo', 't-mobile', 'mvno')

# Mediation/CDR features own a different negative strategy (the CDR templates), so they are
# excluded here. 'prr' comes from V7; V8 omitted it and checked the title only.
_MEDIATION_SIGNALS = ('mediation', 'cdr', 'record type', 'prr', 'billing record')

_EXCLUDED_FEATURE_TYPES = ('notification', 'batch_report', 'ui_portal')


def _matcher(signals):
    """Compile signals into one word-boundary regex.

    Substring matching is wrong for short signals and this was measured, not theorised:
    plain `'ild' in text` matches BUILD, CHILD and WILD, which opened the gate on 21 extra
    features. The original V7 code worked around the same problem for one term by writing it
    as `' cdr'` with a leading space; a boundary match handles every term properly and lets
    that hack go.
    """
    # A trailing plural is allowed, otherwise the boundary is too strict in the other
    # direction: 'entitlements' and 'add-ons' are the normal way these appear in an
    # acceptance criterion, and requiring an exact boundary dropped features that genuinely
    # stated a gating condition.
    return re.compile(
        r'(?<![0-9a-z])(?:%s)(?:e?s)?(?![0-9a-z])'
        % '|'.join(re.escape(s) for s in signals),
        re.IGNORECASE)


def _found(text, signals):
    """Every signal present in `text`, matched on word boundaries."""
    return [s for s in signals if _matcher((s,)).search(text)]


def _any(text, signals) -> bool:
    return bool(_matcher(signals).search(text))


@dataclass
class EligibilitySignals:
    """The decision, plus why - so a caller can log it and a test can assert on it."""
    applicable: bool = False
    reason: str = ''
    has_commercial: bool = False
    has_tmo: bool = False
    is_feature_gated: bool = False
    matched: List[str] = field(default_factory=list)

    @property
    def any_signal(self) -> bool:
        """Whether this feature is eligibility-gated at all.

        `has_tmo` is deliberately NOT part of this. It decides WHICH negative to add (the
        non-TMO network case), not whether the feature is gated. Including it opened the gate
        on 184 extra features when measured, because 'tmo' / 't-mobile' / 'mvno' appears in
        nearly every ticket in a T-Mobile integration project - the same failure as the bare
        'feature' keyword that once fired on 48% of the cache.
        """
        return self.has_commercial or self.is_feature_gated


def source_text(jira, chalk) -> str:
    """The only text the gate may read: the ticket and Chalk, never generated test cases."""
    parts = [
        (getattr(jira, 'summary', '') or '') if jira else '',
        (getattr(jira, 'description', '') or '') if jira else '',
        (getattr(jira, 'acceptance_criteria', '') or '') if jira else '',
        (getattr(chalk, 'scope', '') or '') if chalk else '',
    ]
    for scenario in (getattr(chalk, 'scenarios', None) or []):
        parts.append(getattr(scenario, 'title', '') or '')
        parts.append(getattr(scenario, 'validation', '') or '')
    return ' '.join(p for p in parts if p).lower()


def evaluate(jira, chalk=None, feature_class=None, classification=None) -> EligibilitySignals:
    """Decide whether eligibility negatives apply, and which signals are present.

    Args:
        jira: the ticket.
        chalk: Chalk data, if any. Its scenario titles and validations count as source.
        feature_class: V7's feature classification object (``is_notification``,
            ``is_batch``, ``feature_type``). Optional.
        classification: V8's classification object or a string. ``'ui'`` excludes.
    """
    text = source_text(jira, chalk)
    title = ((getattr(jira, 'summary', '') or '') if jira else '').lower()

    # ── Exclusions, union of what both engines excluded ──
    if feature_class is not None:
        if getattr(feature_class, 'is_notification', False):
            return EligibilitySignals(reason='feature is a notification feature')
        if getattr(feature_class, 'is_batch', False):
            return EligibilitySignals(reason='feature is a batch feature')
        if getattr(feature_class, 'feature_type', '') in _EXCLUDED_FEATURE_TYPES:
            return EligibilitySignals(
                reason='feature_type=%s owns a different negative strategy'
                       % getattr(feature_class, 'feature_type', ''))

    cls = classification
    if cls is not None and not isinstance(cls, str):
        cls = getattr(cls, 'classification', '') or ''
    if (cls or '').lower() == 'ui':
        return EligibilitySignals(reason='UI feature; UI negatives are generated elsewhere')

    # Mediation is judged on the TITLE, which is V8's rule, and V8's comment explaining it was
    # right: a line-eligibility feature legitimately MENTIONS mediation in its acceptance
    # criteria without being a mediation feature. V7 checked the whole text, and taking that
    # rule wholesale excluded MWTGPROV-4373 - "International Mobile Hotspot for commercial
    # lines on TMO", the feature both implementations cite as the reason they exist. Measured:
    # the text-wide rule wrongly excluded 11 features, 4373 among them.
    hit_mediation = _found(title, _MEDIATION_SIGNALS)
    if hit_mediation:
        return EligibilitySignals(
            reason='mediation/CDR feature (%s); the CDR templates own its negatives'
                   % ', '.join(kw.strip() for kw in hit_mediation))

    # ── Signals, from source material only ──
    matched = _found(text, _ELIGIBILITY_SIGNALS)
    if not matched:
        return EligibilitySignals(
            reason='no eligibility gating condition stated in the ticket or Chalk')

    signals = EligibilitySignals(
        has_commercial=_any(text, ('commercial line', 'commercial lines')),
        has_tmo=_any(text, _TMO_SIGNALS),
        is_feature_gated=_any(text, _FEATURE_GATED_SIGNALS),
        matched=matched,
    )

    # A strong line-eligibility signal is required, not merely one of the broader keywords.
    # V8 already required this; V7 did not, so V7 could synthesize the ERR06 negative alone
    # off a bare 'subscriber' or 'provision' match - the weakest possible evidence in a
    # codebase where every ticket provisions something.
    if not signals.any_signal:
        signals.applicable = False
        signals.reason = ('eligibility keywords present (%s) but no commercial, TMO or '
                          'feature-gating signal' % ', '.join(matched[:4]))
        return signals

    signals.applicable = True
    signals.reason = 'eligibility gating evidenced by: %s' % ', '.join(matched[:6])
    return signals


def normalize_existing(test_cases) -> str:
    """Lower-cased text of existing Negative test cases, underscores flattened to spaces.

    Both engines guard against re-adding a negative by looking for a token in the text of
    the negatives already present. V8 titles are underscore-joined
    ('non-eligible_plan'), so a space-delimited guard token only matches after this
    normalisation - V7 skipped it and could double up.
    """
    joined = ' '.join(
        ((getattr(tc, 'summary', '') or '') + ' ' + (getattr(tc, 'description', '') or '')).lower()
        for tc in (test_cases or [])
        if (getattr(tc, 'category', '') or '') == 'Negative')
    return re.sub(r'[_]+', ' ', joined)


def plan_rejection_covered(existing_text: str) -> bool:
    """True if an existing negative already covers plan/line ineligibility.

    Stops the standalone non-eligible-plan negative duplicating a lumped rejection case.
    From V8; V7 had no equivalent.
    """
    return any(kw in existing_text for kw in (
        'non-eligible plan', 'non-eligible rate plan', 'not eligible', 'ineligible',
        'rejected per catalog', 'invalid retailplan', 'err07'))
