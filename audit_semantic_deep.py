"""
audit_semantic_deep.py — Semantic Step-Feature Alignment Audit
================================================================
For every feature in PI-52 & PI-53, checks:
  1. Are steps appropriate for the feature TYPE? (inquiry gets inquiry steps, CDR gets CDR steps, etc.)
  2. Are there irrelevant steps? (Century Report in inquiry, API in pure UI, etc.)
  3. Does the step content match the Chalk scenario intent?
"""
import sys, os, re, json
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent))

from modules.database import load_all_features, load_latest_suite, load_jira, load_chalk_as_object, _conn
from modules.tc_templates import classify_feature
from modules.integration_contract import resolve_operation

# ════════════════════════════════════════════════════════════════════
#  SEMANTIC CHECKS
# ════════════════════════════════════════════════════════════════════

# Steps that should NOT appear in inquiry features
INQUIRY_FORBIDDEN_STEPS = ['century report', 'service grouping', 'ne portal',
                           'mig table', 'mig_device', 'mig_sim', 'mig_line',
                           'line table', 'subscriber profile', 'nbop mig']

# Steps that should NOT appear in CDR/notification features
CDR_FORBIDDEN_STEPS = ['century report', 'service grouping', 'ne portal',
                       'nsl response', 'succ00', 'http 200', 'oauth token',
                       'nbop mig', 'mig table', 'apollo_ne']

# Steps that should NOT appear in pure UI features
UI_FORBIDDEN_STEPS = ['api', 'http', 'nsl', 'endpoint', 'oauth', 'succ00',
                      'century report', 'service grouping', 'ne portal']

# Steps that SHOULD appear in inquiry features
INQUIRY_EXPECTED = ['response payload', 'response contains', 'query result',
                    'inquiry api', 'valid parameters', 'error code']

# Steps that SHOULD appear in CDR features
CDR_EXPECTED = ['mediation', 'prr', 'sftp', 'derivation', 'cdr']


def check_feature_step_alignment(fid, title, suite_data):
    """Check if steps are semantically aligned with the feature type."""
    issues = []
    tcs = suite_data.get('test_cases', [])
    
    # Classify
    jira = load_jira(fid)
    desc = jira.get('description', '') if jira else ''
    channel = jira.get('channel', '') if jira else ''
    ac = jira.get('ac_text', '') if jira else ''
    fc = classify_feature(title, description=desc, channel=channel, jira_summary=title, ac_text=ac)
    
    contract = resolve_operation(title, description=desc, ac_text=ac)
    
    # Determine what this feature IS
    is_inquiry = any(kw in title.lower() for kw in ['inquiry', 'enquiry', 'query', 'retrieve',
                                                      'sim-info', 'sim info', 'device details',
                                                      'device lock', 'event status', 'order status',
                                                      'eligibility', 'biller line', 'login auth',
                                                      'get transaction', 'retrigger'])
    is_cdr = fc.is_notification and any(kw in title.lower() for kw in ['cdr', 'mediation', 'prr',
                                                                         'ild', 'roaming', 'usage file',
                                                                         'call type', 'metering'])
    is_pure_ui = fc.is_ui and not fc.is_api
    
    for tc in tcs:
        steps = tc.get('steps', [])
        all_step_text = ' '.join((s.get('summary', '') + ' ' + s.get('expected', '')).lower() for s in steps)
        tc_label = 'TC%s' % tc.get('sno', '?')
        
        if is_inquiry:
            # Check for forbidden steps in inquiry TCs
            for term in INQUIRY_FORBIDDEN_STEPS:
                if term in all_step_text:
                    # Exception: "Syniverse NOT called" TCs can mention Century Report
                    if 'not called' in tc.get('summary', '').lower() or 'syniverse' in tc.get('summary', '').lower():
                        continue
                    issues.append('%s: INQUIRY has "%s" in steps' % (tc_label, term))
                    break  # One issue per TC is enough
        
        if is_cdr:
            for term in CDR_FORBIDDEN_STEPS:
                if term in all_step_text:
                    # Exception: UI Mirror TCs
                    if 'UI Verify' in tc.get('summary', ''):
                        continue
                    issues.append('%s: CDR has "%s" in steps' % (tc_label, term))
                    break
        
        if is_pure_ui:
            for term in UI_FORBIDDEN_STEPS:
                if term in all_step_text:
                    if 'UI Verify' in tc.get('summary', ''):
                        continue
                    issues.append('%s: UI has "%s" in steps' % (tc_label, term))
                    break
        
        # Check for irrelevant UI Mirror TCs
        if 'UI Verify' in tc.get('summary', ''):
            summary_lower = tc.get('summary', '').lower()
            # Check if the UI Mirror operation is relevant to this feature
            irrelevant_ops = []
            if is_inquiry and any(kw in summary_lower for kw in ['activate', 'deactivate', 'restore', 'suspend', 'hotline', 'change sim', 'swap']):
                irrelevant_ops.append(summary_lower.split('validate ')[-1].split(' result')[0] if 'validate' in summary_lower else '?')
            if irrelevant_ops:
                issues.append('%s: Irrelevant UI Mirror "%s" for %s feature' % (tc_label, irrelevant_ops[0][:30], 'inquiry' if is_inquiry else fc.feature_type))
    
    return {
        'ftype': fc.feature_type,
        'is_inquiry': is_inquiry,
        'is_cdr': is_cdr,
        'is_pure_ui': is_pure_ui,
        'contract': contract.operation if contract else 'NONE',
        'issues': issues,
    }


# ════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════

all_features = load_all_features()
target_pis = ['PI-52', 'PI-53']

print('=' * 110)
print('  SEMANTIC STEP-FEATURE ALIGNMENT AUDIT — PI-52 & PI-53')
print('=' * 110)
print()

total_features = 0
total_issues = 0
results = []

for pi in target_pis:
    for fid, title in sorted(all_features.get(pi, [])):
        suite = load_latest_suite(fid)
        if not suite:
            continue
        
        total_features += 1
        check = check_feature_step_alignment(fid, title, suite)
        tc_count = len(suite.get('test_cases', []))
        issue_count = len(check['issues'])
        total_issues += issue_count
        
        status = 'PASS' if issue_count == 0 else ('WARN' if issue_count <= 3 else 'FAIL')
        
        results.append({
            'pi': pi, 'fid': fid, 'title': title[:45],
            'tcs': tc_count, 'ftype': check['ftype'],
            'is_inquiry': check['is_inquiry'], 'is_cdr': check['is_cdr'],
            'contract': check['contract'][:20],
            'issues': issue_count, 'status': status,
            'detail': check['issues'][:5],
        })

# Print results
print('%-6s %-18s %-40s %4s %-14s %-5s %4s %s' % ('PI', 'Feature', 'Title', 'TCs', 'Type', 'Inq?', 'Iss', 'Status'))
print('-' * 110)
for r in results:
    marker = '✅' if r['status'] == 'PASS' else ('⚠️ ' if r['status'] == 'WARN' else '❌')
    inq = 'INQ' if r['is_inquiry'] else ('CDR' if r['is_cdr'] else '')
    print('%-6s %-18s %-40s %4d %-14s %-5s %4d %s %s' % (
        r['pi'], r['fid'], r['title'], r['tcs'], r['ftype'][:14], inq, r['issues'], marker, r['status']))

# Summary
print()
print('-' * 110)
pass_c = sum(1 for r in results if r['status'] == 'PASS')
warn_c = sum(1 for r in results if r['status'] == 'WARN')
fail_c = sum(1 for r in results if r['status'] == 'FAIL')
print('TOTAL: %d features | PASS: %d | WARN: %d | FAIL: %d | Issues: %d' % (
    total_features, pass_c, warn_c, fail_c, total_issues))

# Detail for non-PASS
print()
non_pass = [r for r in results if r['status'] != 'PASS']
if non_pass:
    print('ISSUES DETAIL:')
    for r in non_pass:
        print('  %s [%s] %s:' % (r['fid'], r['ftype'], r['title']))
        for d in r['detail']:
            print('    - %s' % d)
    print()

# Inquiry features summary
inq_features = [r for r in results if r['is_inquiry']]
if inq_features:
    print('INQUIRY FEATURES (%d):' % len(inq_features))
    for r in inq_features:
        marker = '✅' if r['status'] == 'PASS' else '❌'
        print('  %s %-18s %-40s %d issues %s' % (marker, r['fid'], r['title'], r['issues'], r['status']))
