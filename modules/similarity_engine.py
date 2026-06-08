# -*- coding: utf-8 -*-
"""
similarity_engine.py — Cross-feature reuse engine for TSG V8.0.

Detects overlapping endpoints/dimensions across the 616 cached Jira features
and suggests TC reuse to prevent duplicate work and suite bloat.

Usage:
    from modules.similarity_engine import find_similar_features, get_reuse_suggestions

    suggestions = find_similar_features('MWTGPROV-4020', top_k=5)
    # Returns list of similar features with overlap score and TC reuse candidates
"""
import re
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class SimilarFeature:
    """A feature that overlaps with the query feature."""
    feature_id: str
    feature_title: str
    similarity_score: float      # 0.0–1.0
    shared_endpoints: List[str]  # API endpoints both features share
    shared_dimensions: List[str] # dimension values in common (product, channel, etc.)
    tc_count: int                # TCs available for reuse
    file_path: str               # path to existing Excel suite
    pi: str


def find_similar_features(
    feature_id: str,
    top_k: int = 5,
    min_score: float = 0.3,
    log=print,
) -> List[SimilarFeature]:
    """Find features similar to the given one from the DB cache.

    Similarity is based on:
    - Shared API endpoints (highest weight)
    - Shared dimension values (products, channels, line states)
    - Shared Jira label keywords
    - Shared Chalk scenario keywords

    Args:
        feature_id: The feature to find similar ones for
        top_k: Max number of similar features to return
        min_score: Minimum similarity score (0.0–1.0)
        log: Logger function

    Returns:
        List of SimilarFeature sorted by similarity descending.
    """
    try:
        from .database import _conn, load_jira, load_chalk_as_object, get_suite_history
    except ImportError:
        return []

    # Load the query feature
    query_jira = load_jira(feature_id)
    if not query_jira:
        log('[SIMILARITY] Feature %s not in Jira cache — skipping' % feature_id)
        return []

    # Build query fingerprint
    query_fp = _build_feature_fingerprint(query_jira, feature_id)
    if not query_fp['keywords']:
        return []

    # Load all other features from Jira cache
    try:
        c = _conn()
        rows = c.execute(
            'SELECT feature_id, summary, labels_json FROM jira_cache WHERE feature_id != ? LIMIT 300',
            (feature_id,)
        ).fetchall()
        c.close()
    except Exception:
        return []

    results = []
    for row in rows:
        other_id = row['feature_id']
        other_jira = dict(row)
        other_fp = _build_feature_fingerprint(other_jira, other_id)

        score, shared_endpoints, shared_dims = _compute_similarity(query_fp, other_fp)

        if score >= min_score:
            # Get TC count and file path from history
            history = get_suite_history(other_id, limit=1)
            tc_count = 0
            file_path = ''
            pi = ''
            if history:
                tc_count = history[0].get('tc_count', 0)
                file_path = history[0].get('file_path', '')
                pi = history[0].get('pi', '')

            results.append(SimilarFeature(
                feature_id=other_id,
                feature_title=str(row['summary'] or '')[:80],
                similarity_score=score,
                shared_endpoints=shared_endpoints[:3],
                shared_dimensions=shared_dims[:5],
                tc_count=tc_count,
                file_path=file_path,
                pi=pi,
            ))

    # Sort by similarity descending
    results.sort(key=lambda x: x.similarity_score, reverse=True)
    similar = results[:top_k]

    if similar:
        log('[SIMILARITY] %s: found %d similar features (top: %s, score=%.2f)' % (
            feature_id, len(similar), similar[0].feature_id, similar[0].similarity_score))
    else:
        log('[SIMILARITY] %s: no similar features found (min_score=%.2f)' % (feature_id, min_score))

    return similar


def _build_feature_fingerprint(jira_row: Dict, feature_id: str) -> Dict:
    """Build a searchable fingerprint for a feature."""
    summary = str(jira_row.get('summary', '') or '').lower()
    labels = []
    try:
        labels = json.loads(jira_row.get('labels_json', '[]') or '[]')
    except Exception:
        pass
    labels_text = ' '.join(str(l).lower() for l in labels)

    # Extract keywords (4+ char words, deduped)
    all_text = '%s %s %s' % (summary, labels_text, feature_id.lower())
    keywords = set(re.findall(r'\b[a-z][a-z0-9_-]{3,}\b', all_text))

    # Extract potential API operation names from summary
    endpoints = []
    for pattern in [
        r'\b(reset.?plan|reset plan)\b',
        r'\b(activat\w+)\b',
        r'\b(deactivat\w+)\b',
        r'\b(change.?sim|change sim)\b',
        r'\b(change.?rateplan|change rate)\b',
        r'\b(hotline|enable hotline)\b',
        r'\b(suspend)\b',
        r'\b(restore|reconnect)\b',
        r'\b(swap.?mdn|swap mdn)\b',
        r'\b(port.?in|port in)\b',
        r'\b(port.?out|port out)\b',
        r'\b(change.?bcd|change bcd)\b',
        r'\b(change.?imei|change device)\b',
        r'\b(sync.?subscriber|sync sub)\b',
    ]:
        if re.search(pattern, all_text):
            m = re.search(pattern, all_text)
            if m:
                endpoints.append(m.group(1).replace(' ', '-'))

    # Extract dimension hints
    dimensions = []
    if 'tmo' in all_text:
        dimensions.append('tmo')
    if 'esim' in all_text:
        dimensions.append('esim')
    if 'psim' in all_text:
        dimensions.append('psim')
    if 'tablet' in all_text:
        dimensions.append('tablet')
    if 'wearable' in all_text or 'smartwatch' in all_text:
        dimensions.append('wearable')
    if 'nbop' in all_text:
        dimensions.append('nbop')
    if 'itmbo' in all_text:
        dimensions.append('itmbo')

    return {
        'feature_id': feature_id,
        'keywords': keywords,
        'endpoints': endpoints,
        'dimensions': dimensions,
    }


def _compute_similarity(fp1: Dict, fp2: Dict) -> Tuple[float, List[str], List[str]]:
    """Compute similarity between two feature fingerprints.

    Returns: (score, shared_endpoints, shared_dimensions)
    """
    # Shared endpoints (highest weight — 0.6)
    shared_endpoints = list(set(fp1['endpoints']) & set(fp2['endpoints']))
    endpoint_score = min(0.6, len(shared_endpoints) * 0.3)

    # Shared dimensions (weight — 0.2)
    shared_dims = list(set(fp1['dimensions']) & set(fp2['dimensions']))
    dim_score = min(0.2, len(shared_dims) * 0.05)

    # Shared keywords (weight — 0.2, Jaccard)
    kw1, kw2 = fp1['keywords'], fp2['keywords']
    # Remove very common words from scoring
    _stop = {'the', 'for', 'and', 'with', 'from', 'that', 'this', 'new', 'mvno', 'tmo',
              'nbop', 'nslnm', 'intg', 'feature', 'verify', 'validate'}
    kw1_clean = kw1 - _stop
    kw2_clean = kw2 - _stop
    if kw1_clean and kw2_clean:
        jaccard = len(kw1_clean & kw2_clean) / len(kw1_clean | kw2_clean)
        kw_score = jaccard * 0.2
    else:
        kw_score = 0.0

    total_score = endpoint_score + dim_score + kw_score
    return round(total_score, 3), shared_endpoints, shared_dims


def get_reuse_suggestions(similar_features: List[SimilarFeature]) -> List[Dict]:
    """Format similar features as reuse suggestions for the dashboard.

    Returns list of dicts ready for display.
    """
    suggestions = []
    for sf in similar_features:
        if sf.tc_count == 0:
            continue
        suggestions.append({
            'feature_id': sf.feature_id,
            'title': sf.feature_title,
            'score_pct': int(sf.similarity_score * 100),
            'shared': ', '.join(sf.shared_endpoints[:2] + sf.shared_dimensions[:3]),
            'tc_count': sf.tc_count,
            'file_path': sf.file_path,
            'pi': sf.pi,
            'reason': 'Shares %s' % (
                ', '.join(sf.shared_endpoints[:1]) if sf.shared_endpoints
                else ', '.join(sf.shared_dimensions[:2])
            ),
        })
    return suggestions
