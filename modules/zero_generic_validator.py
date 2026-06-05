"""
zero_generic_validator.py — Zero-Generic Quality Enforcement for V8.0 Data-First Engine.

Validates that no test case contains generic/placeholder content.
Every TC must trace back to a specific data source — if it doesn't,
it should not exist.

Checks:
  1. TC titles are not in GENERIC_TITLE_PATTERNS
  2. TC steps are not in GENERIC_STEP_PATTERNS
  3. Every TC has a non-null TraceabilityRecord with non-empty fields
  4. Step expected results contain specific values (not "Verify expected result")
  5. Field references use actual names (not "the field", "the endpoint")
"""
import re
from dataclasses import dataclass, field
from typing import List, Callable

from .data_models_v8 import TestSuite, TestCase


# ─── Generic patterns that indicate template-based (not data-driven) content ──

GENERIC_TITLE_PATTERNS = [
    re.compile(r'^Verify happy path$', re.IGNORECASE),
    re.compile(r'^Verify timeout$', re.IGNORECASE),
    re.compile(r'^Verify rollback$', re.IGNORECASE),
    re.compile(r'^Verify error handling$', re.IGNORECASE),
    re.compile(r'^Verify success scenario$', re.IGNORECASE),
    re.compile(r'^Verify failure scenario$', re.IGNORECASE),
    re.compile(r'^Verify basic functionality$', re.IGNORECASE),
    re.compile(r'^Verify edge case$', re.IGNORECASE),
    re.compile(r'^Verify negative scenario$', re.IGNORECASE),
    re.compile(r'^Verify positive scenario$', re.IGNORECASE),
    re.compile(r'^Verify boundary condition$', re.IGNORECASE),
    re.compile(r'^Happy path test$', re.IGNORECASE),
    re.compile(r'^Negative test$', re.IGNORECASE),
    re.compile(r'^Error test$', re.IGNORECASE),
    re.compile(r'^Test case \d+$', re.IGNORECASE),
]

GENERIC_STEP_PATTERNS = [
    re.compile(r'^Set up preconditions$', re.IGNORECASE),
    re.compile(r'^Execute the test$', re.IGNORECASE),
    re.compile(r'^Verify expected outcome$', re.IGNORECASE),
    re.compile(r'^Validate the response$', re.IGNORECASE),
    re.compile(r'^Check the result$', re.IGNORECASE),
    re.compile(r'^Verify the result$', re.IGNORECASE),
    re.compile(r'^Perform the action$', re.IGNORECASE),
    re.compile(r'^Verify expected result', re.IGNORECASE),  # prefix match — catches "Verify expected result: ..." variants
    re.compile(r'^Execute the operation$', re.IGNORECASE),
    re.compile(r'^Validate the output$', re.IGNORECASE),
    re.compile(r'^Run the test$', re.IGNORECASE),
    re.compile(r'^Confirm the outcome$', re.IGNORECASE),
    re.compile(r'^Verify system behavior$', re.IGNORECASE),
    re.compile(r'^Check system response$', re.IGNORECASE),
    re.compile(r'^Complete the primary operation successfully$', re.IGNORECASE),
    re.compile(r'^Refer to subtask .* for details', re.IGNORECASE),
    re.compile(r'^As per .* specification \(', re.IGNORECASE),
    # Chalk section header contamination — these must never appear as step text or expected results
    re.compile(r'Scenario #\s*Test Scenario', re.IGNORECASE),
    re.compile(r'^(Negative|Positive|Edge|Regression) Scenarios\s*:', re.IGNORECASE),
]

# Patterns indicating placeholder field references
PLACEHOLDER_PATTERNS = [
    re.compile(r'\bthe field\b', re.IGNORECASE),
    re.compile(r'\bthe endpoint\b', re.IGNORECASE),
    re.compile(r'\bthe API\b', re.IGNORECASE),
    re.compile(r'\bthe service\b', re.IGNORECASE),
    re.compile(r'\bthe system\b', re.IGNORECASE),
    re.compile(r'\bthe value\b', re.IGNORECASE),
    re.compile(r'\bthe parameter\b', re.IGNORECASE),
    re.compile(r'\bsome value\b', re.IGNORECASE),
    re.compile(r'\bTBD\b'),
    re.compile(r'\bTODO\b'),
    re.compile(r'\bplaceholder\b', re.IGNORECASE),
]


# ─── Validation Result ──────────────────────────────────────────────


@dataclass
class ValidationResult:
    """Result of zero-generic validation."""
    passed: bool = True
    violations: List[str] = field(default_factory=list)
    tc_count_validated: int = 0
    warnings: List[str] = field(default_factory=list)


# ================================================================
# MAIN ENTRY POINT
# ================================================================


def validate_suite(suite: TestSuite, log: Callable = print) -> ValidationResult:
    """Validate that no test case contains generic/placeholder content.

    Checks:
      1. TC titles are not in GENERIC_TITLE_PATTERNS
      2. TC steps are not in GENERIC_STEP_PATTERNS
      3. Every TC has a non-null TraceabilityRecord with non-empty fields
      4. Step expected results contain specific values
      5. Field references use actual names (not placeholders)

    Returns ValidationResult with passed flag and list of violations.
    """
    result = ValidationResult()
    result.tc_count_validated = len(suite.test_cases)

    if not suite.test_cases:
        result.warnings.append('Suite has no test cases to validate')
        return result

    log('[VALIDATE] Validating %d test cases for zero-generic compliance...' % len(suite.test_cases))

    for tc in suite.test_cases:
        tc_id = tc.sno or '?'

        # ── Check 1: Generic title patterns ──
        title = (tc.summary or '').strip()
        # Strip common prefixes like "MDA-3941_Feature_Name_" to get the core title
        core_title = re.sub(r'^[A-Z]+-\d+_[^_]+_', '', title).strip()
        for pattern in GENERIC_TITLE_PATTERNS:
            if pattern.match(core_title) or pattern.match(title):
                violation = 'TC %s: Generic title detected: "%s"' % (tc_id, title[:80])
                result.violations.append(violation)
                result.passed = False
                break

        # ── Check 2: Generic step patterns ──
        for step in (tc.steps or []):
            step_text = (step.summary or '').strip()
            expected_text = (step.expected or '').strip()

            for pattern in GENERIC_STEP_PATTERNS:
                if pattern.match(step_text):
                    violation = 'TC %s Step %s: Generic step detected: "%s"' % (
                        tc_id, step.step_num, step_text[:60])
                    result.violations.append(violation)
                    result.passed = False
                    break

            for pattern in GENERIC_STEP_PATTERNS:
                if pattern.match(expected_text):
                    violation = 'TC %s Step %s: Generic expected result: "%s"' % (
                        tc_id, step.step_num, expected_text[:60])
                    result.violations.append(violation)
                    result.passed = False
                    break

        # ── Check 3: Traceability record present and valid ──
        if tc.traceability is None:
            violation = 'TC %s: Missing TraceabilityRecord (no data source link)' % tc_id
            result.violations.append(violation)
            result.passed = False
        else:
            if not tc.traceability.source_type:
                violation = 'TC %s: TraceabilityRecord has empty source_type' % tc_id
                result.violations.append(violation)
                result.passed = False
            if not tc.traceability.source_id:
                violation = 'TC %s: TraceabilityRecord has empty source_id' % tc_id
                result.violations.append(violation)
                result.passed = False
            if not tc.traceability.extracted_text:
                violation = 'TC %s: TraceabilityRecord has empty extracted_text' % tc_id
                result.violations.append(violation)
                result.passed = False

        # ── Check 4: Expected results contain specific values ──
        for step in (tc.steps or []):
            expected_text = (step.expected or '').strip()
            if expected_text and re.match(r'^Verify expected result', expected_text, re.IGNORECASE):
                violation = 'TC %s Step %s: Non-specific expected result: "%s"' % (
                    tc_id, step.step_num, expected_text)
                result.violations.append(violation)
                result.passed = False

        # ── Check 5: No placeholder field references ──
        all_tc_text = ' '.join([
            tc.summary or '',
            tc.description or '',
        ] + [s.summary + ' ' + s.expected for s in (tc.steps or [])])

        for pattern in PLACEHOLDER_PATTERNS:
            match = pattern.search(all_tc_text)
            if match:
                # Allow "the system" in certain contexts (it's common in valid descriptions)
                # Only flag if it's the ONLY reference (no specific name nearby)
                if pattern.pattern == r'\bthe system\b':
                    # Skip — "the system" is too common in valid test descriptions
                    continue
                if pattern.pattern == r'\bthe API\b':
                    # Skip — "the API" is acceptable when specific endpoint is also mentioned
                    if re.search(r'/api/', all_tc_text):
                        continue
                violation = 'TC %s: Placeholder reference detected: "%s"' % (
                    tc_id, match.group()[:30])
                result.violations.append(violation)
                result.passed = False
                break  # One placeholder per TC is enough to flag

    # ── Summary ──
    if result.passed:
        log('[VALIDATE] PASSED: All %d TCs comply with zero-generic rule' % result.tc_count_validated)
    else:
        log('[VALIDATE] FAILED: %d violations found in %d TCs' % (
            len(result.violations), result.tc_count_validated))
        for v in result.violations[:5]:
            log('[VALIDATE]   - %s' % v)
        if len(result.violations) > 5:
            log('[VALIDATE]   ... and %d more violations' % (len(result.violations) - 5))

    return result
