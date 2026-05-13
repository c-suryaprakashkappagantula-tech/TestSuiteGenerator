"""
dimension_extractor.py — Dimension Extraction for V8.0 Data-First Engine.

Scans all gathered data sources and extracts testable dimensions:
  - Products (Phone, Tablet, Smartwatch) from Chalk API pages
  - Channels (ITMBO, NBOP) from Chalk/Jira
  - Input Types (MDN, IMEI, ICCID, EID, LineID) from Chalk request fields
  - Error Codes from Business Rules tables
  - Line States (Hotlined, Suspended, Deactivated) from Jira/subtasks
  - Scenarios from Chalk scenario tables
  - Negative specs from Business Rules error codes

Each extracted item gets a TraceabilityRecord linking it to the source.
"""
import re
from typing import List, Dict, Optional, Callable, Any, Set

from .traceability import TraceabilityRecord, create_traceability
from .data_models_v8 import (
    Dimension, ExtractedScenario, NegativeSpec, DimensionSet,
    DataSourceEntry, DataInventory,
)
from .nmno_api_lookup import NMNOLookupResult, NMNOBusinessRule, NMNOAPISpec


# ─── Known dimension value patterns ────────────────────────────────

KNOWN_PRODUCTS = ['Phone', 'Tablet', 'Smartwatch', 'Wearable', 'Hotspot', 'IoT']
KNOWN_CHANNELS = ['ITMBO', 'NBOP']
KNOWN_INPUT_TYPES = ['MDN', 'IMEI', 'ICCID', 'EID', 'LineID', 'Line ID']
KNOWN_LINE_STATES = ['Hotlined', 'Suspended', 'Deactivated', 'Cancelled', 'Reserved']

# Regex for detecting input type mentions in text
INPUT_TYPE_PATTERN = re.compile(
    r'\b(MDN|IMEI|ICCID|EID|LineID|Line\s*ID)\b', re.IGNORECASE
)
PRODUCT_PATTERN = re.compile(
    r'\b(Phones?|Tablets?|Smartwatch(?:es)?|Wearables?|Hotspots?|IoT)\b', re.IGNORECASE
)
CHANNEL_PATTERN = re.compile(
    r'\b(ITMBO|NBOP)\b', re.IGNORECASE
)
LINE_STATE_PATTERN = re.compile(
    r'\b(Hotlined|Suspended|Deactivated|Cancelled|Reserved)\b', re.IGNORECASE
)


# ================================================================
# MAIN ENTRY POINT
# ================================================================


def extract_dimensions(
    jira,
    chalk,
    deep_mine_result,
    parsed_docs: List = None,
    nmno_result=None,
    nbop_data=None,
    classification: str = None,
    log: Callable = print,
) -> DimensionSet:
    """Extract all testable dimensions from gathered data.

    Scans:
      - NMNO API Lookup (TMO_API_Chalk local DB — highest priority)
      - NBOP UI Knowledge (if available)
      - Chalk API pages for Products, Channels, Input Types, Error Codes
      - Jira AC for explicit dimension mentions
      - Subtask AC for component-specific dimensions
      - Deep mine API specs for request field variations

    Returns a DimensionSet with all dimensions, scenarios, negative specs,
    and a data inventory tracking what was found from each source.
    """
    parsed_docs = parsed_docs or []
    dimensions: List[Dimension] = []
    scenarios: List[ExtractedScenario] = []
    negative_specs: List[NegativeSpec] = []
    sources_checked: List[DataSourceEntry] = []

    log('[DIM-EXTRACT] Starting dimension extraction...')

    # ── 0. Extract from NMNO API Lookup (local DB — highest priority) ──
    if nmno_result and (nmno_result.business_rules or nmno_result.api_specs):
        nmno_dims, nmno_negatives, nmno_source = _extract_dimensions_from_nmno(nmno_result, log)
        dimensions.extend(nmno_dims)
        negative_specs.extend(nmno_negatives)
        sources_checked.append(nmno_source)
    elif nmno_result:
        sources_checked.append(DataSourceEntry(
            source_name='TMO_API_Chalk (%s)' % nmno_result.api_name,
            source_type='chalk',
            items_extracted=0,
            items_detail=[],
            status='empty',
        ))
        log('[DIM-EXTRACT]   NMNO lookup returned no data for "%s"' % nmno_result.api_name)

    # ── 0b. Extract from NBOP UI Knowledge ──
    if nbop_data and isinstance(nbop_data, dict):
        nbop_dims, nbop_source = _extract_dimensions_from_nbop(nbop_data, log)
        dimensions.extend(nbop_dims)
        sources_checked.append(nbop_source)
    elif nbop_data is not None:
        sources_checked.append(DataSourceEntry(
            source_name='NBOP UI Knowledge',
            source_type='nbop',
            items_extracted=0,
            items_detail=[],
            status='empty',
        ))

    # ── 1. Extract from Chalk API specs (deep mine result) ──
    if deep_mine_result and deep_mine_result.api_specs:
        chalk_dims, chalk_scenarios, chalk_negatives, chalk_source = _extract_dimensions_from_chalk(
            deep_mine_result.api_specs, log
        )
        dimensions.extend(chalk_dims)
        scenarios.extend(chalk_scenarios)
        negative_specs.extend(chalk_negatives)
        sources_checked.append(chalk_source)
    else:
        sources_checked.append(DataSourceEntry(
            source_name='Chalk API Specs',
            source_type='chalk',
            items_extracted=0,
            items_detail=[],
            status='empty',
        ))
        log('[DIM-EXTRACT]   No Chalk API specs available')

    # ── 1b. Extract from ChalkData object (DB cache scenarios) ──
    # ONLY use Chalk DB scenarios if NMNO lookup didn't already provide Business Rules.
    # When NMNO has data, the Chalk DB scenarios are from the PI page (not the API spec page)
    # and often contain unrelated scenarios from other features on the same PI page.
    _nmno_has_data = nmno_result and nmno_result.business_rules
    # ── 1b. Extract from ChalkData object (DB cache scenarios) ──
    # ALWAYS use Chalk DB scenarios when available — they provide functional/happy path
    # scenarios that complement NMNO Business Rules (which provide negative/error TCs).
    # Dedup against NMNO-derived scenarios to avoid overlap.
    if chalk and hasattr(chalk, 'scenarios') and chalk.scenarios:
        chalk_sc_scenarios, chalk_sc_source = _extract_scenarios_from_chalk_data(chalk, log)
        existing_titles = {s.title.strip().lower() for s in scenarios}
        new_scenarios = [s for s in chalk_sc_scenarios if s.title.strip().lower() not in existing_titles]
        scenarios.extend(new_scenarios)
        sources_checked.append(chalk_sc_source)
        if _nmno_has_data:
            log('[DIM-EXTRACT]   Chalk DB scenarios: %d added (complementing NMNO Business Rules)' % len(new_scenarios))

    # ── 2. Extract from Jira AC text ──
    if jira and jira.acceptance_criteria:
        jira_dims, jira_negatives, jira_source = _extract_dimensions_from_jira(jira, log)
        # Only add dimensions not already found in Chalk
        existing_dim_names = {d.name for d in dimensions}
        for d in jira_dims:
            if d.name not in existing_dim_names:
                dimensions.append(d)
                existing_dim_names.add(d.name)
            else:
                # Merge new values into existing dimension
                for existing_d in dimensions:
                    if existing_d.name == d.name:
                        new_vals = [v for v in d.values if v not in existing_d.values]
                        existing_d.values.extend(new_vals)
                        break
        # Add Jira-extracted negatives
        negative_specs.extend(jira_negatives)
        sources_checked.append(jira_source)
    else:
        sources_checked.append(DataSourceEntry(
            source_name='Jira AC',
            source_type='jira',
            items_extracted=0,
            items_detail=[],
            status='empty',
        ))
        log('[DIM-EXTRACT]   No Jira AC text available')

    # ── 3. Extract from subtask mines ──
    if deep_mine_result and deep_mine_result.subtask_mines:
        sub_dims, sub_scenarios, sub_source = _extract_dimensions_from_subtasks(
            deep_mine_result.subtask_mines, log
        )
        existing_dim_names = {d.name for d in dimensions}
        for d in sub_dims:
            if d.name not in existing_dim_names:
                dimensions.append(d)
                existing_dim_names.add(d.name)
        scenarios.extend(sub_scenarios)
        sources_checked.append(sub_source)
    else:
        sources_checked.append(DataSourceEntry(
            source_name='Subtask Mines',
            source_type='subtask',
            items_extracted=0,
            items_detail=[],
            status='empty',
        ))
        log('[DIM-EXTRACT]   No subtask mines available')

    # ── 3b. UI Scenario Aggregation (when classification is 'ui' OR subtasks have UI work) ──
    # Enrich scenario list with subtask AC and Jira AC items via aggregation
    _has_ui_subtasks = (
        deep_mine_result and deep_mine_result.subtask_mines and
        any(
            'ui' in (getattr(m, 'component', '') or '').lower() or
            'nbop' in (getattr(m, 'component', '') or '').lower() or
            'nbop' in (getattr(m, 'summary', '') or '').lower()
            for m in deep_mine_result.subtask_mines
        )
    )
    _run_ui_aggregation = classification == 'ui' or (classification in ('api', 'hybrid') and _has_ui_subtasks)

    if _run_ui_aggregation:
        # Identify chalk-sourced scenarios from the current list
        chalk_scenarios = [s for s in scenarios if s.source and s.source.source_type == 'Chalk Scenario']
        subtask_mines = deep_mine_result.subtask_mines if deep_mine_result and deep_mine_result.subtask_mines else []
        jira_ac_text = jira.acceptance_criteria if jira and hasattr(jira, 'acceptance_criteria') and jira.acceptance_criteria else ''
        feature_id_for_agg = jira.key if jira else ''

        if subtask_mines or jira_ac_text:
            aggregated = _aggregate_ui_scenarios(
                chalk_scenarios=chalk_scenarios,
                subtask_mines=subtask_mines,
                jira_ac_text=jira_ac_text,
                feature_id=feature_id_for_agg,
                log=log,
            )
            # Replace scenarios list with aggregated result (preserves chalk + adds new)
            # Keep non-chalk scenarios that were already added (e.g., from subtask extraction)
            # but avoid duplicates — aggregation already includes subtask scenarios
            non_chalk_non_subtask = [
                s for s in scenarios
                if s.source and s.source.source_type not in ('Chalk Scenario', 'Subtask AC', 'Jira AC')
            ]
            scenarios = aggregated + non_chalk_non_subtask
            log('[DIM-EXTRACT]   UI aggregation applied: %d total scenarios' % len(scenarios))
        else:
            log('[DIM-EXTRACT]   UI classification but no subtask/Jira AC data — using Chalk-only')

    # ── 4. Extract related Chalk scenarios from deep mine ──
    # ONLY use related scenarios if NMNO lookup didn't provide data.
    # Related scenarios are from OTHER features and often irrelevant.
    if not _nmno_has_data and deep_mine_result and deep_mine_result.related_chalk_scenarios:
        related_count = 0
        existing_titles = {s.title.lower().strip() for s in scenarios}
        for rs in deep_mine_result.related_chalk_scenarios:
            title = (rs.get('title', '') or '').strip()
            if not title or len(title) < 10:
                continue
            if title.lower().strip() in existing_titles:
                continue
            tr = create_traceability(
                source_type='Related Feature',
                source_id=rs.get('_source_feature', 'related'),
                extracted_text=title[:200],
                pi_label=rs.get('_source_pi', ''),
            )
            scenarios.append(ExtractedScenario(
                title=title,
                validation=rs.get('validation', ''),
                category=rs.get('category', 'Happy Path'),
                source=tr,
            ))
            existing_titles.add(title.lower().strip())
            related_count += 1
        if related_count:
            log('[DIM-EXTRACT]   Related Chalk scenarios: %d added' % related_count)
            sources_checked.append(DataSourceEntry(
                source_name='Related Chalk Scenarios',
                source_type='chalk',
                items_extracted=related_count,
                items_detail=['%d scenarios from related features' % related_count],
                status='success',
            ))

    # ── 5. Extract line states from Jira + subtasks ──
    line_state_dim = _extract_line_states(jira, deep_mine_result, log)
    if line_state_dim:
        existing_dim_names = {d.name for d in dimensions}
        if line_state_dim.name not in existing_dim_names:
            dimensions.append(line_state_dim)

    # ── 6. Extract from parsed documents (attachments/evidence) ──
    if parsed_docs:
        doc_dims, doc_scenarios, doc_source = _extract_dimensions_from_parsed_docs(
            parsed_docs, jira, log
        )
        existing_dim_names = {d.name for d in dimensions}
        for d in doc_dims:
            if d.name not in existing_dim_names:
                dimensions.append(d)
                existing_dim_names.add(d.name)
            else:
                # Merge new values into existing dimension
                for existing_d in dimensions:
                    if existing_d.name == d.name:
                        new_vals = [v for v in d.values if v not in existing_d.values]
                        existing_d.values.extend(new_vals)
                        break
        if doc_scenarios:
            existing_titles = {s.title.lower().strip() for s in scenarios}
            for ds in doc_scenarios:
                if ds.title.lower().strip() not in existing_titles:
                    # Check if this doc scenario supersedes an existing subtask scenario
                    # (same verification type with more complete steps_hint)
                    ds_hints = getattr(ds, 'steps_hint', []) or []
                    replaced = False
                    if ds_hints and len(ds_hints) >= 3:
                        ds_title_lower = (ds.title or '').lower()
                        # Detect removal/display verification scenarios
                        ds_is_removal = 'removed' in ds_title_lower or 'not displayed' in ds_title_lower
                        ds_is_display = 'displayed' in ds_title_lower and not ds_is_removal
                        if ds_is_removal or ds_is_display:
                            for idx_s, existing_s in enumerate(scenarios):
                                ex_title_lower = (existing_s.title or '').lower()
                                ex_hints = getattr(existing_s, 'steps_hint', []) or []
                                # Match: both are removal or both are display, and doc has more hints
                                ex_is_removal = 'removed' in ex_title_lower or 'not displayed' in ex_title_lower
                                ex_is_display = 'displayed' in ex_title_lower and not ex_is_removal
                                if ((ds_is_removal and ex_is_removal) or (ds_is_display and ex_is_display)):
                                    if len(ds_hints) >= len(ex_hints):
                                        # Replace with the more complete doc scenario
                                        scenarios[idx_s] = ds
                                        replaced = True
                                        log('[DIM-EXTRACT]   Doc scenario supersedes subtask: "%s" (%d→%d hints)' % (
                                            ds.title[:50], len(ex_hints), len(ds_hints)))
                                        break
                    if not replaced:
                        scenarios.append(ds)
                        existing_titles.add(ds.title.lower().strip())
        sources_checked.append(doc_source)

    # ── 7. Build data inventory ──
    data_inventory = _build_data_inventory(sources_checked)

    log('[DIM-EXTRACT] Extraction complete: %d dimensions, %d scenarios, %d negative specs' % (
        len(dimensions), len(scenarios), len(negative_specs)))
    log('[DIM-EXTRACT]   Total testable items: %d' % data_inventory.total_testable_items)

    return DimensionSet(
        feature_id=jira.key if jira else '',
        dimensions=dimensions,
        scenarios=scenarios,
        negative_specs=negative_specs,
        data_inventory=data_inventory,
    )


# ================================================================
# NMNO API LOOKUP EXTRACTION
# ================================================================


def _extract_dimensions_from_nmno(
    nmno_result: NMNOLookupResult,
    log: Callable = print,
) -> tuple:
    """Extract dimensions, negative specs from NMNO API Lookup result.

    Converts:
      - NMNOBusinessRule items → NegativeSpec entries (error codes for negative TCs)
      - NMNOAPISpec request fields → input_type dimension values
      - NMNOAPISpec response fields → validation_point dimension values

    Returns: (dimensions, negative_specs, data_source_entry)
    """
    dimensions: List[Dimension] = []
    negative_specs: List[NegativeSpec] = []
    items_detail: List[str] = []

    # ── Convert Business Rules → NegativeSpecs ──
    for rule in nmno_result.business_rules:
        tr = create_traceability(
            source_type='Business Rule',
            source_id=rule.source_section or nmno_result.api_name,
            extracted_text='%s: %s (%s)' % (
                rule.error_code or rule.rule_name,
                rule.rule_description or rule.rule_name,
                rule.condition,
            ),
        )
        negative_specs.append(NegativeSpec(
            error_code=rule.error_code or rule.rule_name,
            error_message=rule.error_details or rule.rule_description or rule.rule_name,
            triggering_condition=rule.condition or rule.rule_description,
            source=tr,
        ))

    if negative_specs:
        items_detail.append('%d Business Rules → negative TCs' % len(negative_specs))
        log('[DIM-EXTRACT]   NMNO: %d Business Rules extracted as NegativeSpecs' % len(negative_specs))

    # ── Extract HTTP method as a dimension when multiple methods found ──
    methods_found = []
    for spec in nmno_result.api_specs:
        method = spec.http_method.upper() if spec.http_method else ''
        if method and method not in methods_found:
            methods_found.append(method)
    # Also check section names for method hints
    for section in nmno_result.sections_matched:
        if 'GET' in section.upper() and 'GET' not in methods_found:
            methods_found.append('GET')
        if 'POST' in section.upper() and 'POST' not in methods_found:
            methods_found.append('POST')
    # If we have multiple sections but only found GET, the other must be POST
    # (T003 = GET, T008 = POST — method is extracted from API Specification table)
    # No hardcoding — trust the parsed data from table_data_json
    if not methods_found:
        methods_found = ['POST']  # default when nothing found

    if len(methods_found) > 1:
        tr = create_traceability(
            source_type='Business Rule',
            source_id=nmno_result.api_name,
            extracted_text='HTTP Methods: %s' % ', '.join(methods_found),
        )
        dimensions.append(Dimension(name='http_method', values=methods_found, source=tr))
        items_detail.append('HTTP Methods: %s' % ', '.join(methods_found))
        log('[DIM-EXTRACT]   NMNO: HTTP methods → %s' % methods_found)

    # NOTE: We do NOT create dimensions from individual request/response fields.
    # Those are payload attributes, not test scenarios. Input types (IMEI, ICCID, MDN)
    # are already extracted from Jira AC as proper dimensions.
    # The NMNO lookup only contributes Business Rules → Negative TCs.

    total_items = len(negative_specs)

    source_entry = DataSourceEntry(
        source_name='TMO_API_Chalk (%s)' % nmno_result.api_name,
        source_type='chalk',
        items_extracted=total_items,
        items_detail=items_detail,
        status='success' if total_items > 0 else 'empty',
    )

    return dimensions, negative_specs, source_entry


# ================================================================
# NBOP UI KNOWLEDGE EXTRACTION
# ================================================================


def _extract_dimensions_from_nbop(
    nbop_data: Dict,
    log: Callable = print,
) -> tuple:
    """Extract dimensions from NBOP UI Knowledge data.

    Converts:
      - Navigation paths → precondition dimensions
      - Page fields → input_type dimension values with field type as metadata
      - UI elements (buttons, tabs) → action_point dimension values

    Args:
        nbop_data: Dict with keys 'nav_path', 'fields', 'ui_elements'

    Returns: (dimensions, data_source_entry)
    """
    dimensions: List[Dimension] = []
    items_detail: List[str] = []

    nav_path = nbop_data.get('nav_path', '')
    fields = nbop_data.get('fields', [])
    ui_elements = nbop_data.get('ui_elements', [])

    # ── Navigation path → precondition dimension ──
    if nav_path:
        tr = create_traceability(
            source_type='NBOP UI',
            source_id='navigation',
            extracted_text='Nav path: %s' % nav_path,
        )
        dimensions.append(Dimension(name='precondition', values=[nav_path], source=tr))
        items_detail.append('Navigation: %s' % nav_path)

    # ── Page fields → input_type dimension values ──
    if fields:
        field_values = []
        for f in fields:
            name = f.get('name', '') if isinstance(f, dict) else str(f)
            if name and name not in field_values:
                field_values.append(name)
        if field_values:
            tr = create_traceability(
                source_type='NBOP UI',
                source_id='page_fields',
                extracted_text='Fields: %s' % ', '.join(field_values[:10]),
            )
            dimensions.append(Dimension(name='input_type', values=field_values, source=tr))
            items_detail.append('Fields: %s' % ', '.join(field_values[:5]))
            log('[DIM-EXTRACT]   NBOP: %d page fields → input_type dimension' % len(field_values))

    # ── UI elements → action_point dimension values ──
    if ui_elements:
        element_values = []
        for el in ui_elements:
            name = el.get('name', el.get('text', '')) if isinstance(el, dict) else str(el)
            if name and name not in element_values:
                element_values.append(name)
        if element_values:
            tr = create_traceability(
                source_type='NBOP UI',
                source_id='ui_elements',
                extracted_text='UI elements: %s' % ', '.join(element_values[:10]),
            )
            dimensions.append(Dimension(name='action_point', values=element_values, source=tr))
            items_detail.append('UI elements: %s' % ', '.join(element_values[:5]))
            log('[DIM-EXTRACT]   NBOP: %d UI elements → action_point dimension' % len(element_values))

    total_items = sum(len(d.values) for d in dimensions)

    source_entry = DataSourceEntry(
        source_name='NBOP UI Knowledge',
        source_type='nbop',
        items_extracted=total_items,
        items_detail=items_detail,
        status='success' if total_items > 0 else 'empty',
    )

    return dimensions, source_entry


# ================================================================
# SUBTASK AC SCENARIO EXTRACTION
# ================================================================


def _extract_ui_scenarios_from_subtask_ac(
    subtask_mines: List,
    log: Callable = print,
) -> List[ExtractedScenario]:
    """Extract UI-related scenarios from subtask AC items.

    Filters subtasks by component containing "UI" or "NBOP" (case-insensitive),
    or by summary containing "NBOP" or "UI". Converts each distinct AC item
    into an ExtractedScenario with source traceability.

    Args:
        subtask_mines: List of SubtaskMine objects from DeepMineResult.
        log: Logging callable.

    Returns:
        List[ExtractedScenario] — one per distinct AC item from UI/NBOP subtasks.
    """
    scenarios: List[ExtractedScenario] = []

    if not subtask_mines:
        return scenarios

    for subtask in subtask_mines:
        # Filter: component or summary must reference UI/NBOP
        component = (getattr(subtask, 'component', '') or '').strip()
        summary = (getattr(subtask, 'summary', '') or '').strip()
        key = (getattr(subtask, 'key', '') or '').strip()

        component_lower = component.lower()
        summary_lower = summary.lower()

        is_ui_subtask = (
            'ui' in component_lower
            or 'nbop' in component_lower
            or 'nbop' in summary_lower
            or 'ui' in summary_lower
        )

        if not is_ui_subtask:
            continue

        # Extract AC items
        ac_items = getattr(subtask, 'ac_items', []) or []
        if not ac_items:
            continue

        # ── Group child items under parent verification statements ──
        # Detect pattern: a long "Verify that..." statement followed by short attribute names
        # Group them into a single scenario with steps_hint listing all attributes
        grouped_scenarios = _group_ac_items_with_children(ac_items, key)
        scenarios.extend(grouped_scenarios)

    log('[DIM-EXTRACT]   Subtask AC: %d UI scenarios extracted' % len(scenarios))
    return scenarios


def _group_ac_items_with_children(ac_items: List[str], subtask_key: str) -> List[ExtractedScenario]:
    """Group AC items: parent verification statements absorb their child bullet items.

    Pattern detected:
      - Parent: "The following total usage attributes are removed from..." (long, has verb)
      - Children: "Total MNO Usage", "Total HMNO Usage", etc. (short, no verb, < 40 chars)

    Result: One scenario with parent as title and children as steps_hint verification points.
    Items that don't fit the parent/child pattern become standalone scenarios.
    """
    results: List[ExtractedScenario] = []
    i = 0

    while i < len(ac_items):
        ac_text = (ac_items[i] or '').strip()
        if not ac_text or len(ac_text) < 5:
            i += 1
            continue

        # Check if this is a parent statement (long, contains a verb/action)
        is_parent = (
            len(ac_text) > 40 and
            any(kw in ac_text.lower() for kw in [
                'removed', 'displayed', 'should be', 'are removed', 'not visible',
                'following', 'verify that', 'ensure', 'no changes', 'keep display',
            ])
        )

        if is_parent:
            # Collect child items that follow (short, no verb, attribute-like)
            children = []
            j = i + 1
            while j < len(ac_items):
                child = (ac_items[j] or '').strip()
                if not child or len(child) < 3:
                    j += 1
                    continue
                # Child items are short (< 40 chars) and don't start with action verbs
                is_child = (
                    len(child) < 50 and
                    not any(child.lower().startswith(v) for v in [
                        'verify', 'ensure', 'no change', 'the following', 'nbop',
                        'when ', 'if ', 'login', 'navigate', 'click',
                    ])
                )
                if is_child:
                    children.append(child)
                    j += 1
                else:
                    break  # Hit another parent statement

            # Build scenario with children as steps_hint
            title = _derive_title_from_ac(ac_text)
            category = _infer_category_from_ac(ac_text)
            tr = create_traceability('Subtask AC', subtask_key, ac_text[:200])

            if children:
                # Parent + children → single scenario with verification steps
                steps_hint = []
                # Determine if removal or display verification
                is_removal = any(kw in ac_text.lower() for kw in ['removed', 'not visible', 'hidden', 'not displayed'])
                is_display = any(kw in ac_text.lower() for kw in ['displayed', 'should be displayed', 'keep display'])

                for child in children:
                    if is_removal:
                        steps_hint.append("Verify '%s' is NOT displayed" % child)
                    elif is_display:
                        steps_hint.append("Verify '%s' IS displayed" % child)
                    else:
                        steps_hint.append("Verify: %s" % child)

                results.append(ExtractedScenario(
                    title=title,
                    validation=ac_text + ' | Items: ' + ', '.join(children),
                    category=category,
                    source=tr,
                    steps_hint=steps_hint,
                ))
            else:
                # Parent with no children → standalone scenario
                results.append(ExtractedScenario(
                    title=title,
                    validation=ac_text,
                    category=category,
                    source=tr,
                ))

            i = j  # Skip past children
        else:
            # Standalone item (not a parent, not absorbed as child)
            # Only create scenario if it's meaningful (has a verb or is long enough)
            ac_lower = ac_text.lower()
            has_verb = any(v in ac_lower for v in [
                'verify', 'no change', 'ensure', 'display', 'remove', 'keep',
                'should', 'must', 'navigate', 'login', 'click',
            ])
            if has_verb or len(ac_text) > 40:
                title = _derive_title_from_ac(ac_text)
                category = _infer_category_from_ac(ac_text)
                tr = create_traceability('Subtask AC', subtask_key, ac_text[:200])
                results.append(ExtractedScenario(
                    title=title,
                    validation=ac_text,
                    category=category,
                    source=tr,
                ))
            # else: skip short attribute-like items that weren't absorbed by a parent
            i += 1

    return results


def _derive_title_from_ac(ac_text: str) -> str:
    """Derive a clean scenario title from AC text.

    Strips leading bullets/numbers, collapses whitespace, truncates to 120 chars.
    """
    # Remove leading bullet markers: "- ", "* ", "1. ", "1) ", etc.
    cleaned = re.sub(r'^[\s]*(?:[-*•]\s*|\d+[.)]\s*)', '', ac_text)
    # Collapse whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Truncate to 120 chars
    if len(cleaned) > 120:
        cleaned = cleaned[:117] + '...'
    return cleaned if cleaned else ac_text[:120]


def _infer_category_from_ac(ac_text: str) -> str:
    """Infer scenario category from AC text content.

    For UI features where the intent IS to hide/remove elements:
      - "Port Status is removed" = Happy Path (feature working correctly)
      - "All other info remains visible" = Happy Path (preservation check)
      - "No changes for VZW" = Regression (verify no side effects)

    Only classify as Negative when there's an actual error/failure condition:
      - "error", "fail", "invalid", "reject"

    Returns:
        "Negative" if describes an error/failure condition.
        "Regression" if describes no-change/preservation for other MNOs.
        "Edge Case" if contains boundary condition keywords.
        "Happy Path" otherwise (including intentional removal/hiding).
    """
    text_lower = ac_text.lower()

    # Regression indicators — verifying no side effects for other MNOs
    regression_keywords = ['no changes for', 'no change for', 'unchanged for',
                           'remains the same for', 'not affected for',
                           'there are no changes']
    for kw in regression_keywords:
        if kw in text_lower:
            return 'Regression'

    # True Negative indicators — actual error/failure conditions
    # NOT "removed" or "hidden" — those are intentional UI behavior for hide features
    negative_keywords = ['error', 'fail', 'invalid', 'reject', 'denied',
                         'unauthorized', 'forbidden', 'timeout']
    for kw in negative_keywords:
        if kw in text_lower:
            return 'Negative'

    # Edge case indicators (boundary conditions)
    edge_case_keywords = ['boundary', 'edge case', 'maximum', 'minimum', 'limit',
                          'timeout', 'empty', 'null', 'zero', 'overflow']
    for kw in edge_case_keywords:
        if kw in text_lower:
            return 'Edge Case'

    # Everything else is Happy Path — including "removed", "hidden", "not displayed"
    # because for UI hide features, that IS the expected behavior
    return 'Happy Path'


# ================================================================
# JIRA AC SCENARIO EXTRACTION
# ================================================================

# UI behavior keywords that indicate a testable UI scenario
_UI_BEHAVIOR_KEYWORDS = [
    'verify', 'should display', 'should not display',
    'is removed', 'is hidden', 'is visible',
    'navigate', 'click', 'select',
]


def _extract_ui_scenarios_from_jira_ac(
    ac_text: str,
    existing_titles: Set[str],
    feature_id: str,
    log: Callable = print,
) -> List[ExtractedScenario]:
    """Extract UI verification scenarios from Jira AC text.

    Scans for numbered/bulleted items containing UI behavior keywords:
      - "verify", "should display", "should not display"
      - "is removed", "is hidden", "is visible"
      - "navigate", "click", "select"

    Only extracts items NOT already covered by existing_titles
    (normalized comparison — lowercase, whitespace-collapsed).

    Args:
        ac_text: Raw Jira acceptance criteria text.
        existing_titles: Set of normalized titles already extracted from other sources.
        feature_id: Parent feature Jira key (e.g., "MWTGPROV-4006").
        log: Logging callable.

    Returns:
        List[ExtractedScenario] with source_type="Jira AC", source_id=feature_id.
    """
    scenarios: List[ExtractedScenario] = []

    if not ac_text or not ac_text.strip():
        return scenarios

    # Split AC text into individual items by newlines
    raw_lines = ac_text.split('\n')

    # Further split lines that contain multiple numbered/bulleted items
    items: List[str] = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        # Split on inline numbered bullets (e.g., "1. item 2. item")
        # but only if the line itself starts with a bullet/number
        items.append(line)

    # Normalize existing titles for comparison
    normalized_existing = set()
    for t in existing_titles:
        normalized_existing.add(re.sub(r'\s+', ' ', t.lower()).strip())

    for ac_item in items:
        ac_item_stripped = ac_item.strip()
        if not ac_item_stripped or len(ac_item_stripped) < 5:
            continue

        # Check if item contains any UI behavior keyword
        item_lower = ac_item_stripped.lower()
        has_ui_keyword = any(kw in item_lower for kw in _UI_BEHAVIOR_KEYWORDS)

        if not has_ui_keyword:
            continue

        # Derive title and normalize for dedup check
        title = _derive_title_from_ac(ac_item_stripped)
        normalized_title = re.sub(r'\s+', ' ', title.lower()).strip()

        if normalized_title in normalized_existing:
            continue

        # Add to normalized set to avoid extracting duplicates within this batch
        normalized_existing.add(normalized_title)

        # Infer category
        category = _infer_category_from_ac(ac_item_stripped)

        # Create traceability record
        tr = create_traceability(
            source_type='Jira AC',
            source_id=feature_id,
            extracted_text=ac_item_stripped[:200],
        )

        scenarios.append(ExtractedScenario(
            title=title,
            validation=ac_item_stripped,
            category=category,
            source=tr,
        ))

    log('[DIM-EXTRACT]   Jira AC: %d UI scenarios extracted' % len(scenarios))
    return scenarios


# ================================================================
# SCENARIO DEDUPLICATION & AGGREGATION
# ================================================================


def _normalize_title(title: str) -> str:
    """Normalize a scenario title for deduplication comparison.

    Lowercase, collapse whitespace, strip punctuation.
    """
    normalized = title.lower().strip()
    # Strip punctuation (keep alphanumeric and spaces)
    normalized = re.sub(r'[^\w\s]', '', normalized)
    # Collapse whitespace
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized


def _token_overlap_ratio(title_a: str, title_b: str) -> float:
    """Calculate token overlap ratio between two normalized titles.

    Returns the ratio of shared tokens to the smaller token set size.
    A ratio > 0.8 indicates near-match.
    """
    tokens_a = set(title_a.split())
    tokens_b = set(title_b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    # Use the smaller set as denominator to detect when one title is a subset of another
    min_size = min(len(tokens_a), len(tokens_b))
    return len(intersection) / min_size if min_size > 0 else 0.0


def _deduplicate_scenarios(
    scenarios: List[ExtractedScenario],
    log: Callable = print,
) -> List[ExtractedScenario]:
    """Deduplicate scenarios by normalized title comparison.

    Algorithm:
      1. Normalize each title: lowercase, collapse whitespace, strip punctuation
      2. Group by normalized title
      3. For each group with >1 scenario: keep the one with longest validation text
         (more specific detail)
      4. For near-matches (>80% token overlap between two titles):
         keep the longer/more specific title

    Preserves insertion order; priority: Chalk > Subtask AC > Jira AC.

    Returns: Deduplicated list preserving insertion order.
    """
    if not scenarios:
        return []

    # Phase 1: Group by exact normalized title
    # Use ordered dict to preserve insertion order
    groups: Dict[str, List[ExtractedScenario]] = {}
    for sc in scenarios:
        norm = _normalize_title(sc.title)
        if norm not in groups:
            groups[norm] = []
        groups[norm].append(sc)

    # Phase 2: For each group, keep the version with longest validation text
    deduped: List[ExtractedScenario] = []
    for norm_title, group in groups.items():
        if len(group) == 1:
            deduped.append(group[0])
        else:
            # Keep the one with longest validation (more specific detail)
            best = max(group, key=lambda s: len(s.validation or ''))
            deduped.append(best)
            log('[DIM-EXTRACT]   Dedup: merged %d scenarios with title "%s"' % (len(group), norm_title[:60]))

    # Phase 3: Near-match detection (>80% token overlap)
    # Compare all pairs and mark shorter/less-specific ones for removal
    # EXCEPTION: If the differing tokens contain MNO identifiers (TMO, VZW, MVNO),
    # these are semantically different scenarios and should NOT be merged.
    _mno_tokens = {'tmo', 'vzw', 'mvno', 'verizon', 'tmobile'}

    to_remove: Set[int] = set()
    for i in range(len(deduped)):
        if i in to_remove:
            continue
        norm_i = _normalize_title(deduped[i].title)
        for j in range(i + 1, len(deduped)):
            if j in to_remove:
                continue
            norm_j = _normalize_title(deduped[j].title)
            overlap = _token_overlap_ratio(norm_i, norm_j)
            if overlap > 0.8:
                # Check if the differing tokens are MNO-specific
                tokens_i = set(norm_i.split())
                tokens_j = set(norm_j.split())
                diff_tokens = (tokens_i - tokens_j) | (tokens_j - tokens_i)
                if diff_tokens & _mno_tokens:
                    # Different MNO scenarios — keep both
                    continue
                # Keep the longer/more specific title
                if len(norm_i) >= len(norm_j):
                    to_remove.add(j)
                    log('[DIM-EXTRACT]   Dedup: near-match removed "%s" (kept "%s")' % (
                        deduped[j].title[:50], deduped[i].title[:50]))
                else:
                    to_remove.add(i)
                    log('[DIM-EXTRACT]   Dedup: near-match removed "%s" (kept "%s")' % (
                        deduped[i].title[:50], deduped[j].title[:50]))
                    break  # i is removed, no need to compare further

    result = [sc for idx, sc in enumerate(deduped) if idx not in to_remove]
    if len(scenarios) != len(result):
        log('[DIM-EXTRACT]   Dedup: %d → %d scenarios after deduplication' % (len(scenarios), len(result)))

    return result


def _aggregate_ui_scenarios(
    chalk_scenarios: List[ExtractedScenario],
    subtask_mines: List,
    jira_ac_text: str,
    feature_id: str,
    log: Callable = print,
) -> List[ExtractedScenario]:
    """Aggregate UI scenarios from all 3 sources and deduplicate.

    Called by extract_dimensions() when classification is 'ui'.

    Sources (in priority order):
      1. Chalk scenarios (existing — highest specificity)
      2. Subtask AC items (component = UI/NBOP)
      3. Jira AC items (UI behavior patterns)

    Deduplication:
      - Normalize titles: lowercase, collapse whitespace, strip punctuation
      - Compare normalized titles for exact match
      - For near-matches (>80% token overlap): keep the more specific version

    Args:
        chalk_scenarios: Scenarios already extracted from Chalk.
        subtask_mines: SubtaskMine objects from DeepMineResult.
        jira_ac_text: Raw Jira acceptance criteria text.
        feature_id: Parent feature Jira key (e.g., "MWTGPROV-4006").
        log: Logging callable.

    Returns: Deduplicated list of ExtractedScenario with source tags.
    """
    # Source 1: Chalk scenarios (passed in, highest priority)
    all_scenarios: List[ExtractedScenario] = list(chalk_scenarios)

    # Source 2: Subtask AC scenarios
    subtask_scenarios = _extract_ui_scenarios_from_subtask_ac(subtask_mines, log)
    all_scenarios.extend(subtask_scenarios)

    # Source 3: Jira AC scenarios
    # Build existing_titles from Chalk + Subtask for dedup in Jira extraction
    existing_titles: Set[str] = set()
    for sc in all_scenarios:
        existing_titles.add(_normalize_title(sc.title))

    jira_scenarios = _extract_ui_scenarios_from_jira_ac(jira_ac_text, existing_titles, feature_id, log)
    all_scenarios.extend(jira_scenarios)

    # Deduplicate across all sources
    deduplicated = _deduplicate_scenarios(all_scenarios, log)

    log('[DIM-EXTRACT]   Aggregated UI scenarios: %d chalk + %d subtask + %d jira → %d after dedup' % (
        len(chalk_scenarios), len(subtask_scenarios), len(jira_scenarios), len(deduplicated)))

    return deduplicated


# ================================================================
# CHALK EXTRACTION
# ================================================================


def _extract_scenarios_from_chalk_data(chalk, log: Callable = print) -> tuple:
    """Extract scenarios from a ChalkData object (DB cache).

    ChalkData has .scenarios (list of ChalkScenario with title, validation, category, steps, etc.)
    Each scenario becomes an ExtractedScenario with traceability.

    Returns: (scenarios, data_source_entry)
    """
    scenarios: List[ExtractedScenario] = []
    items_detail: List[str] = []
    feature_id = chalk.feature_id or 'chalk'

    for sc in chalk.scenarios:
        title = (sc.title or '').strip()
        if not title or len(title) < 5:
            continue

        # Determine category
        category = (sc.category or 'Happy Path').strip()
        if not category:
            category = 'Happy Path'

        validation = (sc.validation or '').strip()
        steps_hint = sc.steps if hasattr(sc, 'steps') and sc.steps else []

        tr = create_traceability(
            source_type='Chalk Scenario',
            source_id=feature_id,
            extracted_text=title[:200],
        )
        scenarios.append(ExtractedScenario(
            title=title,
            validation=validation,
            category=category,
            source=tr,
            steps_hint=steps_hint,
        ))

    if scenarios:
        items_detail.append('%d scenarios from Chalk DB cache' % len(scenarios))

    log('[DIM-EXTRACT]   Chalk DB scenarios: %d extracted from %s' % (len(scenarios), feature_id))

    source_entry = DataSourceEntry(
        source_name='Chalk DB Scenarios',
        source_type='chalk',
        items_extracted=len(scenarios),
        items_detail=items_detail,
        status='success' if scenarios else 'empty',
    )

    return scenarios, source_entry


def _extract_dimensions_from_chalk(
    api_specs: List,
    log: Callable = print,
) -> tuple:
    """Extract dimensions, scenarios, and negative specs from Chalk API specs.

    Returns: (dimensions, scenarios, negative_specs, data_source_entry)
    """
    dimensions: List[Dimension] = []
    scenarios: List[ExtractedScenario] = []
    negative_specs: List[NegativeSpec] = []
    items_detail: List[str] = []

    products_found: List[str] = []
    channels_found: List[str] = []
    input_types_found: List[str] = []

    for spec in api_specs:
        spec_id = spec.api_name or 'unknown'

        # ── Products from request/response fields or scenarios ──
        all_text = ' '.join([
            ' '.join(spec.request_fields),
            ' '.join(spec.response_fields),
            spec.request_sample or '',
            spec.response_sample or '',
            ' '.join(s.get('title', '') for s in (spec.scenarios or [])),
        ])
        for product in KNOWN_PRODUCTS:
            if product.lower() in all_text.lower() and product not in products_found:
                products_found.append(product)

        # ── Channels from source/target system ──
        for channel in KNOWN_CHANNELS:
            src_tgt = (spec.source_system + ' ' + spec.target_system).upper()
            if channel in src_tgt and channel not in channels_found:
                channels_found.append(channel)

        # ── Input types from request fields ──
        for field_name in spec.request_fields:
            for input_type in KNOWN_INPUT_TYPES:
                normalized = input_type.replace(' ', '').lower()
                if normalized in field_name.replace(' ', '').lower() and input_type not in input_types_found:
                    input_types_found.append(input_type)

        # ── Scenarios from Chalk ──
        for sc in (spec.scenarios or []):
            title = sc.get('title', '').strip()
            validation = sc.get('validation', sc.get('expected', '')).strip()
            category = sc.get('category', 'Happy Path')
            if title:
                tr = create_traceability(
                    source_type='Chalk Scenario',
                    source_id=spec_id,
                    extracted_text=title,
                )
                scenarios.append(ExtractedScenario(
                    title=title,
                    validation=validation,
                    category=category,
                    source=tr,
                    api_spec=spec,
                    steps_hint=sc.get('steps', []),
                ))

        # ── Error codes → NegativeSpec ──
        for err in (spec.error_codes or []):
            code = err.get('code', err.get('error_code', '')).strip()
            message = err.get('message', err.get('error_message', '')).strip()
            condition = err.get('condition', err.get('triggering_condition', '')).strip()
            if code or message:
                tr = create_traceability(
                    source_type='Business Rule',
                    source_id=code or spec_id,
                    extracted_text='%s: %s (%s)' % (code, message, condition) if condition else '%s: %s' % (code, message),
                )
                negative_specs.append(NegativeSpec(
                    error_code=code,
                    error_message=message,
                    triggering_condition=condition,
                    source=tr,
                ))

    # ── Build dimension objects ──
    if products_found:
        tr = create_traceability('Chalk Scenario', api_specs[0].api_name or 'chalk', 'Products: ' + ', '.join(products_found))
        dimensions.append(Dimension(name='product', values=products_found, source=tr))
        items_detail.append('Products: %s' % ', '.join(products_found))

    if channels_found:
        tr = create_traceability('Chalk Scenario', api_specs[0].api_name or 'chalk', 'Channels: ' + ', '.join(channels_found))
        dimensions.append(Dimension(name='channel', values=channels_found, source=tr))
        items_detail.append('Channels: %s' % ', '.join(channels_found))

    if input_types_found:
        tr = create_traceability('Chalk Scenario', api_specs[0].api_name or 'chalk', 'Input Types: ' + ', '.join(input_types_found))
        dimensions.append(Dimension(name='input_type', values=input_types_found, source=tr))
        items_detail.append('Input Types: %s' % ', '.join(input_types_found))

    if scenarios:
        items_detail.append('%d scenarios extracted' % len(scenarios))
    if negative_specs:
        items_detail.append('%d error codes extracted' % len(negative_specs))

    total_items = len(products_found) + len(channels_found) + len(input_types_found) + len(scenarios) + len(negative_specs)

    log('[DIM-EXTRACT]   Chalk: %d products, %d channels, %d input types, %d scenarios, %d error codes' % (
        len(products_found), len(channels_found), len(input_types_found), len(scenarios), len(negative_specs)))

    source_entry = DataSourceEntry(
        source_name='Chalk API Specs',
        source_type='chalk',
        items_extracted=total_items,
        items_detail=items_detail,
        status='success' if total_items > 0 else 'empty',
    )

    return dimensions, scenarios, negative_specs, source_entry


# ================================================================
# JIRA EXTRACTION
# ================================================================


def _extract_dimensions_from_jira(
    jira,
    log: Callable = print,
) -> tuple:
    """Extract dimensions from Jira AC text.

    Scans AC text for mentions of known dimension values:
    MDN, IMEI, ICCID, EID, LineID, product names, channel names.
    Also extracts error codes mentioned in AC as NegativeSpecs.

    Returns: (dimensions, negative_specs, data_source_entry)
    """
    dimensions: List[Dimension] = []
    negative_specs: List[NegativeSpec] = []
    items_detail: List[str] = []
    ac_text = jira.acceptance_criteria or ''
    # Also scan description
    all_text = ac_text + '\n' + (jira.description or '')

    # ── Input types mentioned in AC ──
    input_matches = INPUT_TYPE_PATTERN.findall(all_text)
    if input_matches:
        # Normalize: "Line ID" → "LineID"
        normalized = []
        for m in input_matches:
            norm = m.replace(' ', '')
            if norm not in normalized:
                normalized.append(norm)
        tr = create_traceability('Jira AC', jira.key, 'Input types in AC: ' + ', '.join(normalized))
        dimensions.append(Dimension(name='input_type', values=normalized, source=tr))
        items_detail.append('Input Types: %s' % ', '.join(normalized))

    # ── Products mentioned in AC ──
    product_matches = PRODUCT_PATTERN.findall(all_text)
    if product_matches:
        unique_products = []
        for p in product_matches:
            # Normalize: map plural/variant to canonical form
            p_lower = p.lower().rstrip('s')
            if p_lower.startswith('phone'):
                cap = 'Phone'
            elif p_lower.startswith('tablet'):
                cap = 'Tablet'
            elif p_lower.startswith('smartwatch'):
                cap = 'Smartwatch'
            elif p_lower.startswith('wearable'):
                cap = 'Wearable'
            elif p_lower.startswith('hotspot'):
                cap = 'Hotspot'
            elif p_lower == 'iot':
                cap = 'IoT'
            else:
                cap = p.capitalize()
            if cap not in unique_products:
                unique_products.append(cap)
        tr = create_traceability('Jira AC', jira.key, 'Products in AC: ' + ', '.join(unique_products))
        dimensions.append(Dimension(name='product', values=unique_products, source=tr))
        items_detail.append('Products: %s' % ', '.join(unique_products))

    # ── Channels mentioned in AC ──
    channel_matches = CHANNEL_PATTERN.findall(all_text)
    if channel_matches:
        unique_channels = []
        for c in channel_matches:
            upper = c.upper()
            if upper not in unique_channels:
                unique_channels.append(upper)
        tr = create_traceability('Jira AC', jira.key, 'Channels in AC: ' + ', '.join(unique_channels))
        dimensions.append(Dimension(name='channel', values=unique_channels, source=tr))
        items_detail.append('Channels: %s' % ', '.join(unique_channels))

    # ── Error codes mentioned in AC → NegativeSpecs ──
    # Pattern: ERR06, ERR16, error code 404, etc.
    error_pattern = re.compile(
        r'(?:error\s+)?(?:code\s+)?(ERR\d+|E\d+|\d{3})\s*[:\-–—]?\s*["\']?([^"\'\n,]{5,60})["\']?',
        re.IGNORECASE
    )
    # Also match patterns like 'return error ERR06 "Device not found"'
    error_pattern2 = re.compile(
        r'(?:return|respond|send)\s+(?:error\s+)?(ERR\d+)\s+["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    # Match "When X, return error ERR06" patterns
    error_pattern3 = re.compile(
        r'[Ww]hen\s+(.{10,80}?),?\s+(?:return|respond\s+with)\s+(?:error\s+)?(ERR\d+)\s+["\']([^"\']+)["\']',
    )

    seen_codes = set()
    for pattern in [error_pattern3, error_pattern2]:
        for match in pattern.finditer(all_text):
            groups = match.groups()
            if len(groups) == 3:
                condition, code, message = groups
            elif len(groups) == 2:
                code, message = groups
                condition = ''
            else:
                continue
            code = code.strip().upper()
            if code and code not in seen_codes:
                seen_codes.add(code)
                tr = create_traceability('Jira AC', jira.key, '%s: %s' % (code, message))
                negative_specs.append(NegativeSpec(
                    error_code=code,
                    error_message=message.strip(),
                    triggering_condition=condition.strip() if condition else '',
                    source=tr,
                ))

    if negative_specs:
        items_detail.append('%d error codes from AC' % len(negative_specs))

    total_items = sum(len(d.values) for d in dimensions) + len(negative_specs)
    log('[DIM-EXTRACT]   Jira AC: %d dimension values, %d error codes found' % (
        sum(len(d.values) for d in dimensions), len(negative_specs)))

    source_entry = DataSourceEntry(
        source_name='Jira AC',
        source_type='jira',
        items_extracted=total_items,
        items_detail=items_detail,
        status='success' if total_items > 0 else 'empty',
    )

    return dimensions, negative_specs, source_entry


# ================================================================
# SUBTASK EXTRACTION
# ================================================================


def _extract_dimensions_from_subtasks(
    subtask_mines: List,
    log: Callable = print,
) -> tuple:
    """Extract dimensions and scenarios from subtask mines.

    Each subtask AC item becomes an ExtractedScenario with traceability.
    Component classification (UI/INT/API/NE) informs the category.

    Returns: (dimensions, scenarios, data_source_entry)
    """
    dimensions: List[Dimension] = []
    scenarios: List[ExtractedScenario] = []
    items_detail: List[str] = []
    components_found: List[str] = []

    for mine in subtask_mines:
        component = mine.component or 'Unknown'
        if component and component not in components_found:
            components_found.append(component)

        # Each AC item becomes a scenario — but ONLY if it's a testable behavior
        for ac_item in (mine.ac_items or []):
            ac_text = ac_item.strip()
            if not ac_text or len(ac_text) < 10:
                continue

            # ── Quality filter: reject AC items that are NOT testable scenarios ──
            ac_lower = ac_text.lower()

            # Reject: implementation notes / spec details (not testable)
            # Only reject if the item matches MULTIPLE spec-note patterns (not just one)
            _spec_note_hits = sum(1 for pattern in [
                'should be',           # "The messageHeader value should be 'MNO'"
                'same as',             # "same as Verizon"
                'default to',          # "with default to Verizon"
                'the 2 options',       # "Verizon and TMO are the 2 options"
                'has enhanced',        # "NSL has enhanced the"
                'has been added',      # "Request type has been added"
                'to capture',          # "to capture network"
                'radio button',        # "display 'MNO' radio button options"
                'messageheader',       # "The messageHeader value should be"
                'requesttype',         # "send the API request with RequestType"
                'request type in the', # "Request type in the header"
                'body has been',       # "Body has been added"
            ] if pattern in ac_lower)
            is_spec_note = _spec_note_hits >= 2  # Must match at least 2 patterns to be rejected

            # Reject: lines starting with "#" (bullet numbering artifacts)
            is_bullet_artifact = ac_text.startswith('#') or ac_text.startswith('*')

            # Reject: too short after stripping common prefixes
            stripped = re.sub(r'^(?:When\s+.{5,40},\s*)', '', ac_text)
            is_fragment = len(stripped) < 15 and 'When' in ac_text

            # Accept: lines that describe a clear testable action/behavior
            is_testable = any(kw in ac_lower for kw in [
                'verify', 'validate', 'display', 'show', 'return', 'reject',
                'error', 'send', 'trigger', 'navigate', 'search', 'click',
                'submit', 'confirm', 'check', 'ensure', 'must',
            ])

            # Decision: skip if it's a spec note (regardless of keywords)
            if is_spec_note:
                continue
            if is_bullet_artifact and len(ac_text) < 30:
                continue
            # Skip NBOP display instructions for API-only features
            if 'nbop to display' in ac_lower or 'nbop to show' in ac_lower:
                continue
            # Skip items that are clearly partial sentences or fragments
            if ac_text.startswith(('When ') ) and len(ac_text) > 100:
                # Long "When X, Y" items are usually spec descriptions, not scenarios
                # Only keep if they contain a clear testable verb after the condition
                condition_part = ac_text.split(',', 1)
                if len(condition_part) > 1:
                    action_part = condition_part[1].lower().strip()
                    if not any(v in action_part[:30] for v in ['verify', 'validate', 'display', 'show', 'return', 'reject', 'error']):
                        continue

            tr = create_traceability(
                source_type='Subtask AC',
                source_id=mine.key,
                extracted_text=ac_text,
            )
            # Determine category from component type
            category = 'Happy Path'
            if any(neg_word in ac_lower for neg_word in ['error', 'fail', 'reject', 'invalid', 'denied', 'off']):
                category = 'Negative'
            elif any(edge_word in ac_lower for edge_word in ['edge', 'boundary', 'timeout', 'concurrent']):
                category = 'Edge Case'

            scenarios.append(ExtractedScenario(
                title=ac_text[:120],
                validation=ac_text,
                category=category,
                source=tr,
                steps_hint=[],
            ))

        # Extract input types from subtask text (summary + AC + user story)
        all_subtask_text = ' '.join([mine.summary or '', mine.user_story or ''] + (mine.ac_items or []))
        input_matches = INPUT_TYPE_PATTERN.findall(all_subtask_text)
        if input_matches:
            normalized = []
            for m in input_matches:
                norm = m.replace(' ', '')
                if norm not in normalized:
                    normalized.append(norm)
            # Create input_type dimension from subtask data
            if normalized:
                tr = create_traceability('Subtask AC', mine.key, 'Input types: ' + ', '.join(normalized))
                # Check if we already have an input_type dimension
                existing_input_dim = next((d for d in dimensions if d.name == 'input_type'), None)
                if existing_input_dim:
                    for val in normalized:
                        if val not in existing_input_dim.values:
                            existing_input_dim.values.append(val)
                else:
                    dimensions.append(Dimension(name='input_type', values=normalized, source=tr))
            items_detail.append('%s: input types %s' % (mine.key, ', '.join(normalized)))

    if components_found:
        items_detail.append('Components: %s' % ', '.join(components_found))

    total_items = len(scenarios)
    log('[DIM-EXTRACT]   Subtasks: %d scenarios from %d subtasks, components: %s' % (
        total_items, len(subtask_mines), ', '.join(components_found) or 'none'))

    source_entry = DataSourceEntry(
        source_name='Subtask Mines',
        source_type='subtask',
        items_extracted=total_items,
        items_detail=items_detail,
        status='success' if total_items > 0 else 'empty',
    )

    return dimensions, scenarios, source_entry


# ================================================================
# LINE STATE EXTRACTION
# ================================================================


# ================================================================
# PARSED DOCUMENT EXTRACTION (Evidence / Attachments)
# ================================================================


def _extract_dimensions_from_parsed_docs(
    parsed_docs: List,
    jira=None,
    log: Callable = print,
) -> tuple:
    """Extract dimensions and scenarios from parsed attachment documents.

    Scans evidence documents (unit testing docs, test result docs, HLD/LLD) for:
      - Product dimensions (Phone, Tablet, Smartwatch)
      - Screen/page dimensions (Data Details, Historical Usage, etc.)
      - MNO dimensions (TMO, VZW)
      - UI elements to verify (attributes removed/kept/hidden/shown)
      - Test step patterns (numbered steps from unit testing docs)

    Returns: (dimensions, scenarios, data_source_entry)
    """
    dimensions: List[Dimension] = []
    scenarios: List[ExtractedScenario] = []
    items_detail: List[str] = []
    feature_id = jira.key if jira else ''

    # Known screen/page patterns in NBOP
    SCREEN_PATTERNS = [
        'data details', 'historical usage', 'line summary', 'subscriber profile',
        'transaction history', 'mediation details', 'voice details', 'sms/mms details',
        'notifications', 'service plan', 'port-in', 'port in',
    ]

    # Attribute removal/display patterns
    REMOVAL_PATTERNS = re.compile(
        r'(?:are removed|is removed|not visible|not displayed|hidden|removed from)',
        re.IGNORECASE,
    )
    DISPLAY_PATTERNS = re.compile(
        r'(?:should be displayed|are displayed|is displayed|should display|still display|keep display)',
        re.IGNORECASE,
    )

    products_found = set()
    screens_found = set()
    mnos_found = set()
    removed_elements = []
    kept_elements = []
    test_steps = []

    for doc in parsed_docs:
        if not doc or not hasattr(doc, 'paragraphs'):
            continue

        all_text = ' '.join(doc.paragraphs or [])
        all_lower = all_text.lower()

        # ── Extract products ──
        for match in PRODUCT_PATTERN.finditer(all_text):
            raw = match.group(1)
            # Normalize to singular form
            prod = raw
            if prod.lower().endswith('es') and prod.lower() not in ('smartwatches',):
                prod = prod[:-2]
            elif prod.lower().endswith('s') and prod.lower() not in ('smartwatches',):
                prod = prod[:-1]
            # Handle special cases
            if prod.lower() in ('smartwatche', 'smartwatches'):
                prod = 'Smartwatch'
            elif prod.lower() in ('phone', 'phones'):
                prod = 'Phone'
            elif prod.lower() in ('tablet', 'tablets'):
                prod = 'Tablet'
            elif prod.lower() in ('wearable', 'wearables'):
                prod = 'Wearable'
            elif prod.lower() in ('hotspot', 'hotspots'):
                prod = 'Hotspot'
            products_found.add(prod.capitalize() if prod[0].islower() else prod)

        # ── Extract screens ──
        for screen in SCREEN_PATTERNS:
            if screen in all_lower:
                # Capitalize properly
                screen_name = screen.title()
                screens_found.add(screen_name)

        # ── Extract MNOs ──
        if 'tmo' in all_lower or 't-mobile' in all_lower or 'tmobile' in all_lower:
            mnos_found.add('TMO')
        if 'vzw' in all_lower or 'verizon' in all_lower:
            mnos_found.add('VZW')

        # ── Extract test steps (numbered patterns) ──
        for para in (doc.paragraphs or []):
            para_stripped = para.strip()
            # Match "Step N:" patterns from unit testing docs
            step_match = re.match(r'^Step\s+(\d+)\s*[:\-]\s*(.+)', para_stripped, re.IGNORECASE)
            if step_match:
                test_steps.append({
                    'step_num': int(step_match.group(1)),
                    'text': step_match.group(2).strip(),
                    'source': doc.filename,
                })

        # ── Extract removed/kept elements ──
        _found_removal = False
        _found_display = False
        for para in (doc.paragraphs or []):
            para_lower = para.lower().strip()
            # Check if this paragraph describes removal
            if not _found_removal and REMOVAL_PATTERNS.search(para):
                # The next few paragraphs might list the elements
                idx = (doc.paragraphs or []).index(para)
                for sub_para in (doc.paragraphs or [])[idx + 1:idx + 10]:
                    sub = sub_para.strip()
                    if not sub or len(sub) < 3:
                        continue
                    # Stop if we hit another section header or "Verify" line
                    if sub.lower().startswith(('verify', 'step', 'ensure')):
                        break
                    # Element names are typically short lines (< 50 chars)
                    if len(sub) < 50 and not sub.startswith(('Step', 'Click', 'Login')):
                        removed_elements.append(sub)
                _found_removal = True
                continue  # Continue looking for display patterns in same doc

            if not _found_display and DISPLAY_PATTERNS.search(para):
                idx = (doc.paragraphs or []).index(para)
                for sub_para in (doc.paragraphs or [])[idx + 1:idx + 10]:
                    sub = sub_para.strip()
                    if not sub or len(sub) < 3:
                        continue
                    if sub.lower().startswith(('verify', 'step', 'ensure')):
                        break
                    if len(sub) < 50 and not sub.startswith(('Step', 'Click', 'Login')):
                        kept_elements.append(sub)
                _found_display = True
                continue

    # ── Build dimensions ──
    if products_found:
        tr = create_traceability('Attachment', parsed_docs[0].filename if parsed_docs else '',
                                 'Products: %s' % ', '.join(sorted(products_found)))
        dimensions.append(Dimension(name='product', values=sorted(products_found), source=tr))
        items_detail.append('Products: %s' % ', '.join(sorted(products_found)))

    if screens_found:
        tr = create_traceability('Attachment', parsed_docs[0].filename if parsed_docs else '',
                                 'Screens: %s' % ', '.join(sorted(screens_found)))
        dimensions.append(Dimension(name='screen', values=sorted(screens_found), source=tr))
        items_detail.append('Screens: %s' % ', '.join(sorted(screens_found)))

    if mnos_found and len(mnos_found) > 1:
        tr = create_traceability('Attachment', parsed_docs[0].filename if parsed_docs else '',
                                 'MNOs: %s' % ', '.join(sorted(mnos_found)))
        dimensions.append(Dimension(name='mno', values=sorted(mnos_found), source=tr))
        items_detail.append('MNOs: %s' % ', '.join(sorted(mnos_found)))

    # ── Build scenarios from test steps ──
    if test_steps:
        # Create a single scenario from the test step sequence
        steps_text = ' → '.join(s['text'][:60] for s in test_steps)
        tr = create_traceability('Attachment', test_steps[0]['source'],
                                 'Test steps: %s' % steps_text[:200])
        scenarios.append(ExtractedScenario(
            title='Verify Data Details and Historical Usage screens (TMO)',
            validation=steps_text,
            category='Happy Path',
            source=tr,
            steps_hint=[s['text'] for s in test_steps],
        ))
        items_detail.append('Test steps: %d from %s' % (len(test_steps), test_steps[0]['source']))

    # ── Build scenarios from removed/kept elements ──
    if removed_elements:
        elements_str = ', '.join(removed_elements[:6])
        tr = create_traceability('Attachment', parsed_docs[0].filename if parsed_docs else '',
                                 'Removed elements: %s' % elements_str)
        scenarios.append(ExtractedScenario(
            title='Verify attributes removed: %s' % elements_str[:80],
            validation='Verify NOT displayed: %s' % elements_str,
            category='Happy Path',
            source=tr,
            steps_hint=['Verify %s is NOT displayed' % e for e in removed_elements[:6]],
        ))
        items_detail.append('Removed elements: %d' % len(removed_elements))

    if kept_elements:
        elements_str = ', '.join(kept_elements[:6])
        tr = create_traceability('Attachment', parsed_docs[0].filename if parsed_docs else '',
                                 'Kept elements: %s' % elements_str)
        scenarios.append(ExtractedScenario(
            title='Verify attributes displayed: %s' % elements_str[:80],
            validation='Verify IS displayed: %s' % elements_str,
            category='Happy Path',
            source=tr,
            steps_hint=['Verify %s IS displayed' % e for e in kept_elements[:6]],
        ))
        items_detail.append('Kept elements: %d' % len(kept_elements))

    total_items = len(dimensions) + len(scenarios)
    if total_items > 0:
        log('[DIM-EXTRACT]   Parsed docs: %d dimensions, %d scenarios from %d documents' % (
            len(dimensions), len(scenarios), len(parsed_docs)))
        for detail in items_detail:
            log('[DIM-EXTRACT]     %s' % detail)

    source_entry = DataSourceEntry(
        source_name='Parsed Documents',
        source_type='attachment',
        items_extracted=total_items,
        items_detail=items_detail,
        status='success' if total_items > 0 else 'empty',
    )

    return dimensions, scenarios, source_entry


def _extract_line_states(
    jira,
    deep_mine_result,
    log: Callable = print,
) -> Optional[Dimension]:
    """Find line state restrictions (Hotlined, Suspended, Deactivated) from Jira/subtasks.

    Returns a Dimension if line states are found, None otherwise.
    """
    all_text_parts = []

    if jira and jira.acceptance_criteria:
        all_text_parts.append(jira.acceptance_criteria)
    if jira and jira.description:
        all_text_parts.append(jira.description)

    if deep_mine_result and deep_mine_result.subtask_mines:
        for mine in deep_mine_result.subtask_mines:
            all_text_parts.extend(mine.ac_items or [])
            if mine.user_story:
                all_text_parts.append(mine.user_story)

    all_text = ' '.join(all_text_parts)
    state_matches = LINE_STATE_PATTERN.findall(all_text)

    if state_matches:
        unique_states = []
        for s in state_matches:
            cap = s.capitalize()
            if cap not in unique_states:
                unique_states.append(cap)

        source_id = jira.key if jira else 'unknown'
        tr = create_traceability(
            source_type='Jira AC',
            source_id=source_id,
            extracted_text='Line states: ' + ', '.join(unique_states),
        )
        log('[DIM-EXTRACT]   Line states found: %s' % ', '.join(unique_states))
        return Dimension(name='line_state', values=unique_states, source=tr)

    return None


# ================================================================
# DIMENSION MERGE WITH DEDUPLICATION
# ================================================================


def merge_dimensions_dedup(existing: List[Dimension], new_dims: List[Dimension]) -> List[Dimension]:
    """Merge new dimensions into existing list with deduplication.

    When duplicate names found:
      - Keep first occurrence's source
      - Merge value lists
      - Deduplicate values

    Returns the merged list (modifies existing in place and returns it).
    """
    existing_names = {d.name.lower().strip(): d for d in existing}

    for new_d in new_dims:
        norm_name = new_d.name.lower().strip()
        if norm_name in existing_names:
            # Merge values into existing dimension
            existing_d = existing_names[norm_name]
            for val in new_d.values:
                if val not in existing_d.values:
                    existing_d.values.append(val)
        else:
            existing.append(new_d)
            existing_names[norm_name] = new_d

    return existing


# ================================================================
# DATA INVENTORY BUILDER
# ================================================================


def _build_data_inventory(sources_checked: List[DataSourceEntry]) -> DataInventory:
    """Compile DataInventory from all extraction results.

    Calculates total_testable_items and identifies gaps (sources with zero items).
    """
    total = sum(s.items_extracted for s in sources_checked)
    warnings = []
    gaps = []

    for source in sources_checked:
        if source.status == 'empty':
            gaps.append('%s returned no data' % source.source_name)
        elif source.status == 'failed':
            warnings.append('%s failed to load' % source.source_name)
        elif source.status == 'partial':
            warnings.append('%s returned partial data' % source.source_name)

    if total == 0:
        warnings.append('Zero testable items found across all sources')

    return DataInventory(
        sources=sources_checked,
        total_testable_items=total,
        warnings=warnings,
        gaps=gaps,
    )
