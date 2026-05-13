"""
data_models_v8.py — Core data models for the V8.0 Data-First Engine.

All dataclasses used by the V8.0 pipeline:
  - Dimension, ExtractedScenario, NegativeSpec, DimensionSet
  - DataSourceEntry, DataInventory
  - CombinationPlan
  - TestStep, TestCase, TestSuite (V8.0 versions)
  - WarningReport (zero-items case)

These models enforce the Data-First principle: every test case traces back
to a specific data source via TraceabilityRecord.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

from .traceability import TraceabilityRecord


# ─── Dimension Models ───────────────────────────────────────────────


@dataclass
class Dimension:
    """A single testable axis extracted from data.

    Examples:
      - Dimension(name="input_type", values=["IMEI", "ICCID", "MDN"], ...)
      - Dimension(name="product", values=["Phone", "Tablet", "Smartwatch"], ...)
      - Dimension(name="channel", values=["ITMBO", "NBOP"], ...)
    """
    name: str                                    # e.g., "input_type", "product", "channel"
    values: List[str]                            # e.g., ["IMEI", "ICCID", "MDN"]
    source: TraceabilityRecord                   # where this dimension was found
    cross_with: List[str] = field(default_factory=list)  # explicit cross-dimension names


@dataclass
class ExtractedScenario:
    """A test scenario extracted from Chalk tables or subtask data.

    Each scenario maps 1:1 to a planned test case — no multiplication.
    """
    title: str                                   # scenario title from source
    validation: str                              # what to validate / expected outcome
    category: str                                # "Happy Path" | "Negative" | "Edge Case"
    source: TraceabilityRecord                   # traceability back to data source
    api_spec: Optional[Any] = None               # APISpec if from Chalk API page
    steps_hint: List[str] = field(default_factory=list)  # hint steps from source


@dataclass
class NegativeSpec:
    """A negative test specification from Business Rules table or line states.

    Each NegativeSpec maps 1:1 to a negative test case.
    """
    error_code: str                              # e.g., "ERR06"
    error_message: str                           # e.g., "IMEI not found in inventory"
    triggering_condition: str                    # e.g., "When IMEI is not registered"
    source: TraceabilityRecord                   # traceability back to data source


# ─── Data Inventory ─────────────────────────────────────────────────


@dataclass
class DataSourceEntry:
    """Record of a single data source consulted during gathering.

    Tracks what was found (or not found) from each source.
    """
    source_name: str                             # e.g., "Jira AC", "Chalk T008", "Subtask MDA-3942"
    source_type: str                             # "jira" | "chalk" | "subtask" | "attachment" | "nbop"
    items_extracted: int                         # count of testable items found
    items_detail: List[str]                      # brief descriptions of what was found
    status: str                                  # "success" | "empty" | "partial" | "failed"
    cache_hit: bool = False                      # whether data came from cache
    cache_age_hours: float = 0.0                 # age of cached data in hours


@dataclass
class DataInventory:
    """Complete inventory of all data sources consulted for a feature.

    The total_testable_items is the sum of items_extracted across all sources.
    """
    sources: List[DataSourceEntry] = field(default_factory=list)
    total_testable_items: int = 0
    warnings: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)  # sources that returned nothing


# ─── Dimension Set ──────────────────────────────────────────────────


@dataclass
class DimensionSet:
    """All dimensions extracted for a feature — input to the Combination Engine."""
    feature_id: str
    dimensions: List[Dimension] = field(default_factory=list)
    scenarios: List[ExtractedScenario] = field(default_factory=list)
    negative_specs: List[NegativeSpec] = field(default_factory=list)
    data_inventory: DataInventory = field(default_factory=DataInventory)


# ─── Combination Plan ───────────────────────────────────────────────


@dataclass
class CombinationPlan:
    """The plan for how dimensions will be combined into test cases.

    Produced by the Combination Engine, consumed by the TC Builder.
    """
    independent_dimensions: List[Dimension] = field(default_factory=list)
    crossed_dimensions: List[Tuple[Dimension, Dimension]] = field(default_factory=list)
    scenario_tcs: List[ExtractedScenario] = field(default_factory=list)
    negative_tcs: List[NegativeSpec] = field(default_factory=list)
    total_planned_tcs: int = 0
    reduction_notes: List[str] = field(default_factory=list)  # why crosses were NOT applied


# ─── Test Case Models (V8.0) ───────────────────────────────────────


@dataclass
class TestStep:
    """A single step in a test case with data reference."""
    step_num: int = 0
    summary: str = ''
    expected: str = ''
    data_reference: str = ''                     # specific field/value from data source


@dataclass
class TestCase:
    """A complete test case with traceability (V8.0).

    Every TC must have a non-null traceability record linking it
    to the data source that justifies its existence.
    """
    sno: str = ''
    summary: str = ''
    description: str = ''
    preconditions: str = ''
    steps: List[TestStep] = field(default_factory=list)
    story_linkage: str = ''
    label: str = ''
    category: str = 'Happy Path'                 # "Happy Path" | "Negative" | "Edge Case"
    priority: str = 'P2'                         # "P1" | "P2" | "P3" — assigned by engine
    traceability: Optional[TraceabilityRecord] = None  # V8.0: links TC to data source
    dimension_values: Dict[str, str] = field(default_factory=dict)  # e.g., {"input_type": "IMEI"}


# ─── Test Suite (V8.0) ─────────────────────────────────────────────


@dataclass
class TestSuite:
    """Complete test suite output from the V8.0 Data-First Engine.

    Includes data inventory, combination plan, and engine version metadata.
    Legacy fields are preserved for dashboard compatibility with V7.0.
    """
    feature_id: str = ''
    feature_title: str = ''
    feature_desc: str = ''
    test_cases: List[TestCase] = field(default_factory=list)
    data_inventory: DataInventory = field(default_factory=DataInventory)       # V8.0
    combination_plan: CombinationPlan = field(default_factory=CombinationPlan) # V8.0
    warnings: List[str] = field(default_factory=list)
    engine_version: str = '8.0.0'                # V8.0 identifier

    # Legacy fields preserved for dashboard compatibility
    acceptance_criteria: List[str] = field(default_factory=list)
    scope: str = ''
    rules: str = ''
    channel: str = ''
    pi: str = ''
    groups: Dict[str, List] = field(default_factory=dict)
    # Jira metadata fields (needed by excel_generator Summary sheet)
    jira_status: str = ''
    jira_priority: str = ''
    jira_assignee: str = ''
    jira_reporter: str = ''
    jira_labels: List[str] = field(default_factory=list)
    jira_links: List[Dict] = field(default_factory=list)
    attachment_names: List[str] = field(default_factory=list)
    data_sources: List[str] = field(default_factory=list)
    ac_traceability: Dict[str, List[str]] = field(default_factory=dict)
    combinations: List[Dict] = field(default_factory=list)
    devices: List[str] = field(default_factory=list)
    networks: List[str] = field(default_factory=list)
    sim_types: List[str] = field(default_factory=list)
    open_items: List[str] = field(default_factory=list)
    open_item_coverage: Dict[str, str] = field(default_factory=dict)
    # V8.0 Routing Audit (populated by engine after TC generation)
    routing_audit: Optional['RoutingAudit'] = None


# ─── Warning Report (zero-items case) ──────────────────────────────


@dataclass
class WarningReport:
    """Produced when the engine finds zero testable items across all sources.

    Instead of fabricating test cases, the engine produces this report
    with guidance on what manual action is needed.
    """
    feature_id: str
    sources_checked: List[DataSourceEntry] = field(default_factory=list)
    reason: str = 'No testable data found across all sources'
    guidance: List[str] = field(default_factory=list)
    # Example guidance:
    # - "Jira AC is empty — add acceptance criteria to the ticket"
    # - "No Chalk URLs found — link the API spec page in the AC"
    # - "Subtasks have no AC — add acceptance criteria to subtasks"


# ─── Routing Audit ──────────────────────────────────────────────────


@dataclass
class RoutingAudit:
    """Records the feature routing decision and its outcomes.

    Produced by the TC builder after generation to track which path
    was taken and how many TCs were generated per path.
    """
    classification: str = 'api'              # "api" | "ui" | "hybrid"
    confidence: float = 0.0                  # Classification confidence (0.0–1.0)
    matched_components: List[str] = field(default_factory=list)  # e.g., ["NSLNM"] or ["NBOP"]
    matched_keywords: List[str] = field(default_factory=list)    # Keywords/markers that drove classification
    data_sources_queried: List[str] = field(default_factory=list)  # ["TMO_API_Chalk", "NBOP_UI_Knowledge"]
    api_tcs_generated: int = 0               # Count of API-path TCs
    ui_tcs_generated: int = 0                # Count of UI-path TCs
    negative_tcs_generated: int = 0          # Count of negative TCs
    total_tcs: int = 0                       # Total TC count
