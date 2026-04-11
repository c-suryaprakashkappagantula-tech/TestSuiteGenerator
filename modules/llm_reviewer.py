"""
llm_reviewer.py — LLM-powered QA reviewer for TestSuiteGenerator V4.
Uses the LLM to:
  1. Review generated test suite and identify gaps
  2. Generate additional test cases from natural language context
  3. Parse custom instructions with real NLU (not regex)
  4. Improve test step descriptions
Falls back to empty results if LLM is unavailable — engine still works rule-based.
"""
import json
import re
from typing import List, Dict, Optional

# Finding #8: AI prompt versioning — track prompt template versions
PROMPT_VERSION = '1.0.0'

# ================================================================
# SYSTEM PROMPTS
# ================================================================

REVIEWER_SYSTEM = """You are a senior QA engineer specializing in telecom provisioning systems (T-Mobile/Sprint MVNO).
You review test suites for completeness, correctness, and coverage gaps.

Your domain expertise includes:
- NSL (Network Service Layer) API operations
- Line provisioning: Activation, Deactivation, Suspend, Reconnect, Hotline
- Feature changes: Change BCD, Change Rateplan, Change SIM, Change Feature
- Channels: ITMBO (Internal), NBOP (External/Portal)
- Device matrix: Mobile/Tablet, eSIM/pSIM, 4G/5G, iOS/Android
- Downstream systems: MBO, Syniverse, Century Report, Genesis, MIG tables
- Transaction types and error handling (SUCC00, ERR codes)

When reviewing, think about:
- Missing negative scenarios (timeouts, invalid inputs, auth failures, rollbacks)
- Missing edge cases (concurrent operations, boundary values, state transitions)
- Missing E2E flows (full lifecycle from activation through deactivation)
- Missing data integrity checks (DB consistency, downstream sync)
- Missing regression scenarios (does this change break existing flows?)

ALWAYS respond in valid JSON format as specified in each prompt."""

GAP_ANALYSIS_PROMPT = """Review this test suite and identify GAPS — scenarios that should be tested but are missing.

Feature: {feature_id} — {feature_title}
Scope: {scope}
Acceptance Criteria:
{acceptance_criteria}

Existing Test Cases ({tc_count} total):
{existing_tcs}

Jira Description (excerpt):
{jira_desc}

Respond with a JSON array of gap test cases. Each item:
{{
  "title": "Short TC title (max 100 chars)",
  "description": "What this TC verifies and why it matters",
  "category": "Negative|Edge Case|E2E|Happy Path|Regression",
  "priority": "P1|P2|P3",
  "reasoning": "Why this gap exists and why it's important",
  "steps": [
    {{"action": "step description", "expected": "expected result"}}
  ]
}}

Rules:
- Only suggest genuinely MISSING scenarios, not duplicates of existing TCs
- Each TC must be specific and actionable, not vague
- Include 3-6 steps per TC
- Prioritize P1 gaps (critical missing coverage) over P2/P3
- Return between 3 and 10 gap TCs
- Return ONLY the JSON array, no markdown fences or extra text"""

STEP_IMPROVE_PROMPT = """Improve these test case steps to be more specific and actionable.
The current steps are generic templates. Make them specific to the feature being tested.

Feature: {feature_id} — {feature_title}
Feature Scope: {scope}

Test Case: {tc_summary}
Category: {tc_category}
Current Steps:
{current_steps}

Respond with a JSON array of improved steps:
[
  {{"step_num": 1, "action": "specific action description", "expected": "specific expected result"}}
]

Rules:
- Keep the same number of steps (or add 1-2 if needed for completeness)
- Make actions specific: include API names, field names, expected values where relevant
- Make expected results verifiable: include specific codes, states, or data to check
- Reference actual system components (NSL, MBO, Syniverse, Century Report) where appropriate
- Return ONLY the JSON array, no markdown fences or extra text"""

CUSTOM_INSTRUCTION_PROMPT = """Parse these custom test generation instructions into structured directives.

User Instructions: {instructions}

Available options:
- Channels: ITMBO, NBOP
- Devices: Mobile, Tablet, Smartwatch
- SIM Types: eSIM, pSIM
- Networks: 4G, 5G
- OS: iOS, Android
- Categories: Happy Path, Negative, Edge Case, E2E, Regression

Respond with a JSON object:
{{
  "filter_channels": ["list of channels to keep"] or null,
  "filter_devices": ["list of devices to keep"] or null,
  "filter_sim": ["list of SIM types to keep"] or null,
  "filter_networks": ["list of networks to keep"] or null,
  "filter_os": ["list of OS to keep"] or null,
  "focus_categories": ["categories to prioritize"] or null,
  "max_per_group": number or null,
  "extra_scenarios": [
    {{"title": "scenario title", "description": "what to test", "category": "category"}}
  ],
  "skip_categories": ["categories to exclude"] or null,
  "special_flags": {{
    "include_boundary": true/false,
    "include_rollback": true/false,
    "include_data_integrity": true/false,
    "include_auth_failure": true/false,
    "prioritize_e2e": true/false
  }},
  "interpretation": "One sentence summary of what the user wants"
}}

Rules:
- Only set filters that the user explicitly mentioned; leave others as null
- Extract any specific scenario requests into extra_scenarios
- Be generous in interpretation — if the user says "focus on eSIM", that means filter_sim=["eSIM"]
- Return ONLY the JSON object, no markdown fences or extra text"""


# ================================================================
# REVIEWER FUNCTIONS
# ================================================================

def review_suite_gaps(llm, suite, jira, chalk, log=print):
    """Use LLM to identify gaps in the generated test suite.
    Returns list of gap TC dicts, or empty list if LLM unavailable."""
    if not llm or not llm.available:
        log('[LLM-REVIEW] LLM not available — skipping gap analysis')
        return []

    log('[LLM-REVIEW] Analyzing test suite for gaps...')

    # Build existing TC summary for the prompt
    existing_lines = []
    for i, tc in enumerate(suite.test_cases[:40], 1):  # cap at 40 to fit context
        existing_lines.append('%d. [%s] %s' % (i, tc.category, tc.summary[:100]))
    existing_str = '\n'.join(existing_lines)

    ac_str = '\n'.join('- %s' % ac for ac in suite.acceptance_criteria[:15])

    prompt = GAP_ANALYSIS_PROMPT.format(
        feature_id=suite.feature_id,
        feature_title=suite.feature_title,
        scope=suite.scope[:500] if suite.scope else 'Not specified',
        acceptance_criteria=ac_str or 'None extracted',
        tc_count=len(suite.test_cases),
        existing_tcs=existing_str,
        jira_desc=(jira.description or '')[:800],
    )

    t0 = __import__('time').time()
    raw = llm.chat(REVIEWER_SYSTEM, prompt)
    elapsed = __import__('time').time() - t0
    log('[LLM-REVIEW] LLM responded in %.1fs (%d chars)' % (elapsed, len(raw)))

    if not raw:
        log('[LLM-REVIEW] Empty response — skipping')
        return []

    gaps = _parse_json_array(raw, log)
    if gaps:
        log('[LLM-REVIEW] Found %d gap suggestions' % len(gaps))
        for g in gaps:
            log('[LLM-REVIEW]   [%s] %s — %s' % (
                g.get('priority', '?'), g.get('category', '?'), g.get('title', '?')[:60]))
    return gaps


def improve_steps(llm, tc, suite, log=print):
    """Use LLM to improve generic test steps into specific, actionable ones.
    Returns list of improved step dicts, or empty list if LLM unavailable."""
    if not llm or not llm.available:
        return []

    current_steps_str = '\n'.join(
        '%d. Action: %s | Expected: %s' % (s.step_num, s.summary, s.expected)
        for s in tc.steps
    )

    prompt = STEP_IMPROVE_PROMPT.format(
        feature_id=suite.feature_id,
        feature_title=suite.feature_title,
        scope=suite.scope[:300] if suite.scope else '',
        tc_summary=tc.summary,
        tc_category=tc.category,
        current_steps=current_steps_str,
    )

    raw = llm.chat(REVIEWER_SYSTEM, prompt, max_tokens=2048)
    if not raw:
        return []

    return _parse_json_array(raw, log)


def parse_custom_instructions_llm(llm, instructions, log=print):
    """Use LLM to parse free-text custom instructions into structured directives.
    Returns dict of directives, or empty dict if LLM unavailable."""
    if not llm or not llm.available:
        log('[LLM-REVIEW] LLM not available — falling back to regex parser')
        return {}

    log('[LLM-REVIEW] Parsing custom instructions with LLM...')

    prompt = CUSTOM_INSTRUCTION_PROMPT.format(instructions=instructions)
    raw = llm.chat(REVIEWER_SYSTEM, prompt, max_tokens=2048)

    if not raw:
        log('[LLM-REVIEW] Empty response — falling back to regex parser')
        return {}

    result = _parse_json_object(raw, log)
    if result:
        interp = result.get('interpretation', '')
        log('[LLM-REVIEW] Interpreted: %s' % interp[:100])
    return result


# ================================================================
# HELPERS
# ================================================================

def _parse_json_array(raw, log=print):
    """Extract a JSON array from LLM response, handling markdown fences."""
    text = raw.strip()
    # Strip markdown code fences
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    # Find the array
    start = text.find('[')
    end = text.rfind(']')
    if start == -1 or end == -1:
        log('[LLM-REVIEW] No JSON array found in response')
        return []

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        log('[LLM-REVIEW] JSON parse error: %s' % e)
        # Try to fix common issues
        try:
            fixed = text[start:end + 1]
            fixed = re.sub(r',\s*([}\]])', r'\1', fixed)  # trailing commas
            return json.loads(fixed)
        except:
            return []


def _parse_json_object(raw, log=print):
    """Extract a JSON object from LLM response."""
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    text = text.strip()

    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end == -1:
        log('[LLM-REVIEW] No JSON object found in response')
        return {}

    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        log('[LLM-REVIEW] JSON parse error: %s' % e)
        try:
            fixed = text[start:end + 1]
            fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
            return json.loads(fixed)
        except:
            return {}
