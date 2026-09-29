# -*- coding: utf-8 -*-
"""
rateplan_rule_local.py - Vendored, self-contained copy of the rate-plan family rule.

WHY THIS EXISTS: the rule is owned elsewhere. The authoritative rule DOCUMENT is
`.kiro/steering/rateplan-rules.md`; the canonical IMPLEMENTATION is
`shared/lifecycle_validator/orchestrator.py::LifecycleOrchestrator._compute_new_rateplan`,
reached at runtime via a sibling-folder path. When TSG is deployed ALONE to a UAT/VDI/
Docker box (cloned without the parent `shared/` folder), that import isn't available.
This file is a VENDORED MIRROR of that implementation so plan resolution keeps working
with ZERO external dependency.

This is a mirror, not a fork. Do not "improve" the logic here: it must stay behaviourally
identical to the shared implementation. Any change to the rule belongs in
`.kiro/steering/rateplan-rules.md` and the shared orchestrator first, then is copied here.
A property test asserts the two implementations agree on every generated input, so drift
is caught rather than assumed away.

API-compatible with the shared classmethod (minus the `cls` parameter) so a facade can try
the shared module first and transparently fall back to this one:
    compute_new_rateplan(current, device_type='Phone') -> str

THE RULE (see `.kiro/steering/rateplan-rules.md` for the full statement):
  Mobile/Phone  ->  CMUNL  <->  CMUNLP     (CM* plans)
  Tablet        ->  CTUNL  <->  CTUNLP     (CT* plans)
The LINE's current plan family is authoritative; device type is only the fallback.
"""

__all__ = ['compute_new_rateplan']


# -- Deterministic rate-plan rule (PERMANENT, per user directive) --
#   Mobile/Phone  ->  CMUNL  <->  CMUNLP     (always CM* plans)
#   Tablet        ->  CTUNL  <->  CTUNLP     (always CT* plans)
# The plan family is chosen by DEVICE TYPE (CM for Mobile, CT for Tablet) only as a
# fallback. Sending a CT plan to a Mobile line (or vice-versa) is rejected by NSL as
# "Invalid RetailPlan".
def compute_new_rateplan(current, device_type='Phone'):
    """Target retail plan for a line, given its current plan and device type.

    Verbatim mirror of
    shared/lifecycle_validator/orchestrator.py::LifecycleOrchestrator._compute_new_rateplan
    """
    cur = (current or '').upper().strip()
    # The LINE's CURRENT plan family (from NBOP) is AUTHORITATIVE for what NSL will accept:
    # a CM line only accepts CM* plans, a CT line only accepts CT*. Sending the wrong family
    # is rejected as ERR07 "Invalid RetailPlan for a line" -- even when the physical device
    # looks like a Tablet but the line was provisioned on a CM plan (a real, observed case:
    # CMUNLP on an eSIM Tablet, where sending CTUNLP was rejected).
    # So when the current plan is a recognized CM*/CT* plan we stay in THAT family and only
    # toggle standard <-> Plus. device_type is the FALLBACK, used only when NBOP has no
    # recognized plan yet (fresh line / COMMERCIAL / unknown).
    if cur.startswith('CT'):
        base = 'CT'
    elif cur.startswith('CM'):
        base = 'CM'
    else:
        dt = (device_type or '').upper()
        base = 'CT' if dt.startswith('TAB') else 'CM'   # Tablet -> CT, Mobile/Phone -> CM
    std, plus = base + 'UNL', base + 'UNLP'
    if cur == plus:            # already Plus -> downgrade to standard
        return std
    if cur == std:             # standard -> upgrade to Plus
        return plus
    # recognized family but non-standard variant, or unknown -> Plus plan for the family
    return plus
