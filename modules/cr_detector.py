"""
cr_detector.py — Single source of truth for CR/bug fix detection.
Used by test_engine.py, tc_templates.py, and TSG_Dashboard_V7.0.py.
"""

# Keywords in Jira summary that indicate a CR/bug fix
_CR_SUMMARY_KEYWORDS = ['cr -', 'cr:', 'cr ', 'bug', 'defect', 'not working', 'hotfix', 'fix -', 'fix:']

# Jira issue types that indicate a CR/bug fix
_CR_ISSUE_TYPES = {'bug', 'defect', 'cr', 'incident', 'problem'}

# Keywords in description/context that indicate a CR/bug fix
_CR_CONTEXT_KEYWORDS = ['not working', 'broken', 'failing', 'regression', 'defect from']


def is_cr_or_bug(summary='', issue_type='', description='', labels=None):
    """Detect if a Jira ticket is a CR/bug fix.
    
    Args:
        summary: Jira summary/title
        issue_type: Jira issue type (Bug, Epic, Story, etc.)
        description: Jira description text (optional, for deeper check)
        labels: Jira labels list (optional)
    
    Returns:
        True if the ticket is a CR/bug fix, False otherwise.
    """
    _summary_lower = (summary or '').lower()
    _type_lower = (issue_type or '').lower()
    
    # Check summary keywords
    if any(kw in _summary_lower for kw in _CR_SUMMARY_KEYWORDS):
        return True
    
    # Check issue type
    if _type_lower in _CR_ISSUE_TYPES:
        return True
    
    # Check description for defect indicators (only if summary didn't match)
    if description:
        _desc_lower = (description or '').lower()[:500]  # Only check first 500 chars
        if any(kw in _desc_lower for kw in _CR_CONTEXT_KEYWORDS):
            return True
    
    return False
