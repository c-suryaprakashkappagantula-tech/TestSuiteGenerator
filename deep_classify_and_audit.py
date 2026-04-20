"""
deep_classify_and_audit.py — Deep Classification + Spillage Audit
=================================================================
For EVERY feature in DB (PI-49 to PI-53):
  1. Classify: API / UI / UI+API / Notification / CDR / Batch
  2. Check contract match
  3. Run through pipeline (silent)
  4. Audit every TC field for cross-contamination (spillage)
  5. Report findings
"""

import sys, os, json, re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_all_features, load_chalk_as_object, load_jira, _conn
from modules.tc_templates import classify_feature, FeatureClassification
from modules.integration_contract import resolve_operation, get_syniverse_assertion, OPERATION_CONTRACTS
from modules.test_engine import build_test_suite, TestStep, TestCase
from modules.jira_fetcher import JiraIssue


# ════════════════════════════════════════════════════════════════════
#  CONTAMINATION RULES — what should NOT appear in each feature type
# ════════════════════════════════════════════════════════════════════

API_TERMS = ['nsl ', 'nsl,', 'succ00', 'http 200', 'http 202', 'oauth token',
             'century report', 'service grouping', 'ne portal', 'apollo_ne',
             'mig table', 'mig_device', 'mig_sim', 'mig_line', 'mig_feature',
             'nbop mig', 'transaction id generated']

UI_TERMS = ['nbop', 'portal', 'screen', 'menu', 'navigation', 'display',
            'login to nbop', 'launch nbop', 'nbop portal']

CDR_TERMS = ['mediation', 'prr', 'sftp', 'filezilla', 'derivation rule',
             'cdr file', 'batch job', 'prr output']

NOTIFICATION_TERMS = ['kafka', 'dpfo', 'threshold', 'speed reduction',
                      'notification payload', 'suppress']

# What's FORBIDDEN per feature type
FORBIDDEN = {
    'notification': API_TERMS + UI_TERMS,  # CDR/notification should NOT have API or UI
    'ui_portal': API_TERMS + CDR_TERMS + NOTIFICATION_TERMS,  # Pure UI should NOT have API/CDR
    'api_crud': UI_TERMS + CDR_TERMS,  # API should NOT have UI or CDR
    'hybrid': CDR_TERMS + NOTIFICATION_TERMS,  # Hybrid can have API+UI but NOT CDR
    'batch_report': API_TERMS + UI_TERMS,  # Batch should NOT have API or UI
    'async_workflow': UI_TERMS + CDR_TERMS,  # Async API should NOT have UI or CDR
}


def check_spillage(tc, feature_type):
    """Check a TC for cross-contamination based on feature type."""
    forbidden = FORBIDDEN.get(feature_type, [])
    if not forbidden:
        return []

    issues = []
    fields = {
        'summary': tc.summary or '',
        'description': tc.description or '',
        'preconditions': tc.preconditions or '',
    }
    # Add steps
    for step in tc.steps:
        fields['step_%s_summary' % step.step_num] = step.summary or ''
        fields['step_%s_expected' % step.step_num] = step.expected or ''

    for field_name, field_val in fields.items():
        val_lower = field_val.lower()
        for term in forbidden:
            if term in val_lower:
                issues.append({
                    'field': field_name,
                    'term': term,
                    'context': field_val[:100],
                })
    return issues


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

def main():
    print('=' * 120)
    print('  DEEP CLASSIFICATION + SPILLAGE AUDIT — ALL PI-49 to PI-53 FEATURES')
    print('  %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 120)
    print()

    all_features = load_all_features()
    target_pis = ['PI-49', 'PI-50', 'PI-51', 'PI-52', 'PI-53']

    features = []
    for pi in target_pis:
        for fid, title in all_features.get(pi, []):
            features.append((pi, fid, title))

    print('Total features: %d' % len(features))
    print()

    # ── Phase 1: Classify every feature ──
    print('━' * 120)
    print('  PHASE 1: FEATURE CLASSIFICATION')
    print('━' * 120)

    type_buckets = defaultdict(list)
    classifications = {}

    for pi, fid, title in sorted(features, key=lambda x: x[1]):
        # Get Jira data for richer classification
        jira_data = load_jira(fid)
        desc = jira_data.get('description', '') if jira_data else ''
        channel = jira_data.get('channel', '') if jira_data else ''
        ac = jira_data.get('ac_text', '') if jira_data else ''
        jira_summary = jira_data.get('summary', title) if jira_data else title

        # Get Chalk scope
        c = _conn()
        crow = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
        c.close()
        scope = ''
        if crow:
            chalk = load_chalk_as_object(fid, crow['pi_label'])
            scope = chalk.scope if chalk else ''

        fc = classify_feature(
            feature_name=title, description=desc, channel=channel,
            jira_summary=jira_summary, ac_text=ac, scope=scope)

        # Contract match
        contract = resolve_operation(title, description=desc, ac_text=ac, scope=scope)
        contract_name = contract.operation if contract else 'NONE'
        syn_action = get_syniverse_assertion(contract)['action'] if contract else 'N/A'

        classifications[fid] = {
            'pi': pi, 'title': title[:50], 'ftype': fc.feature_type,
            'is_ui': fc.is_ui, 'is_api': fc.is_api, 'is_notif': fc.is_notification,
            'channel': channel, 'contract': contract_name, 'syniverse': syn_action,
        }
        type_buckets[fc.feature_type].append(fid)

    # Print classification summary
    print()
    print('  TYPE DISTRIBUTION:')
    for ftype in ['api_crud', 'ui_portal', 'hybrid', 'notification', 'batch_report', 'async_workflow']:
        fids = type_buckets.get(ftype, [])
        print('    %-18s %3d features' % (ftype, len(fids)))
    print()

    # Print full classification table
    print('  %-6s %-18s %-45s %-14s %-20s %-12s' % ('PI', 'Feature ID', 'Title', 'Type', 'Contract', 'Syniverse'))
    print('  ' + '-' * 115)
    for fid in sorted(classifications.keys()):
        cl = classifications[fid]
        print('  %-6s %-18s %-45s %-14s %-20s %-12s' % (
            cl['pi'], fid, cl['title'][:45], cl['ftype'], cl['contract'][:20], cl['syniverse']))

    # ── Phase 2: Build suites and check spillage ──
    print()
    print('━' * 120)
    print('  PHASE 2: SPILLAGE AUDIT (building suites from DB cache)')
    print('━' * 120)
    print()

    total_spillage = 0
    spillage_by_type = defaultdict(int)
    spillage_details = []
    build_errors = []
    features_audited = 0

    for pi, fid, title in sorted(features, key=lambda x: x[1]):
        cl = classifications[fid]
        ftype = cl['ftype']

        # Load Chalk
        c = _conn()
        crow = c.execute("SELECT pi_label FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
        c.close()

        if not crow:
            continue

        chalk = load_chalk_as_object(fid, crow['pi_label'])
        jira_data = load_jira(fid)
        if not jira_data:
            continue

        try:
            jira = JiraIssue(
                key=fid, summary=jira_data.get('summary', ''),
                description=jira_data.get('description', ''),
                status=jira_data.get('status', ''), priority=jira_data.get('priority', ''),
                issue_type='Story', assignee=jira_data.get('assignee', ''),
                reporter=jira_data.get('reporter', ''),
                labels=json.loads(jira_data.get('labels_json', '[]')),
                pi=jira_data.get('pi', pi), channel=jira_data.get('channel', 'ITMBO'),
                acceptance_criteria=jira_data.get('ac_text', ''),
                attachments=[], linked_issues=json.loads(jira_data.get('links_json', '[]')),
                subtasks=json.loads(jira_data.get('subtasks_json', '[]')),
                comments=json.loads(jira_data.get('comments_json', '[]')),
            )

            options = {
                'channel': [jira.channel] if jira.channel else ['ITMBO'],
                'devices': ['Mobile'], 'networks': ['4G', '5G'],
                'sim_types': ['eSIM', 'pSIM'], 'os_platforms': ['iOS', 'Android'],
                'include_positive': True, 'include_negative': True, 'include_e2e': True,
                'include_edge': True, 'include_attachments': False,
                'strategy': 'Smart Suite (Recommended)', 'custom_instructions': '',
            }

            suite = build_test_suite(jira, chalk, [], options, log=lambda x: None)
            features_audited += 1

            # Check every TC for spillage
            feature_spillage = []
            for tc in suite.test_cases:
                spills = check_spillage(tc, ftype)
                if spills:
                    for sp in spills:
                        feature_spillage.append({
                            'fid': fid, 'tc': tc.sno, 'ftype': ftype,
                            **sp
                        })

            if feature_spillage:
                total_spillage += len(feature_spillage)
                spillage_by_type[ftype] += len(feature_spillage)
                spillage_details.extend(feature_spillage)
                # Print summary for this feature
                unique_terms = set(sp['term'] for sp in feature_spillage)
                affected_tcs = set(sp['tc'] for sp in feature_spillage)
                print('  [SPILL] %-18s %-14s %d spills in %d TCs: %s' % (
                    fid, ftype, len(feature_spillage), len(affected_tcs),
                    ', '.join(sorted(unique_terms)[:5])))

        except Exception as e:
            build_errors.append((fid, str(e)[:80]))

    # ── Phase 3: Summary ──
    print()
    print('━' * 120)
    print('  PHASE 3: SUMMARY')
    print('━' * 120)
    print()
    print('  Features audited: %d' % features_audited)
    print('  Build errors: %d' % len(build_errors))
    print('  Total spillage instances: %d' % total_spillage)
    print()

    if spillage_by_type:
        print('  SPILLAGE BY FEATURE TYPE:')
        for ftype, count in sorted(spillage_by_type.items(), key=lambda x: -x[1]):
            print('    %-18s %d spills' % (ftype, count))
        print()

    # Top spillage terms
    if spillage_details:
        term_counts = defaultdict(int)
        for sp in spillage_details:
            term_counts[sp['term']] += 1
        print('  TOP SPILLAGE TERMS:')
        for term, count in sorted(term_counts.items(), key=lambda x: -x[1])[:15]:
            print('    %4d  "%s"' % (count, term))
        print()

        # Top spillage features
        feat_counts = defaultdict(int)
        for sp in spillage_details:
            feat_counts[sp['fid']] += 1
        print('  TOP SPILLAGE FEATURES:')
        for fid, count in sorted(feat_counts.items(), key=lambda x: -x[1])[:10]:
            cl = classifications[fid]
            print('    %4d  %-18s %-14s %s' % (count, fid, cl['ftype'], cl['title'][:40]))
        print()

    if build_errors:
        print('  BUILD ERRORS:')
        for fid, err in build_errors:
            print('    %-18s %s' % (fid, err))
        print()

    # Final verdict
    print('=' * 120)
    if total_spillage == 0 and not build_errors:
        print('  VERDICT: ZERO SPILLAGE — ALL FEATURES CLEAN')
    elif total_spillage <= 10:
        print('  VERDICT: MINOR SPILLAGE (%d instances) — review recommended' % total_spillage)
    else:
        print('  VERDICT: SIGNIFICANT SPILLAGE (%d instances) — fix required' % total_spillage)
    print('=' * 120)


if __name__ == '__main__':
    main()
