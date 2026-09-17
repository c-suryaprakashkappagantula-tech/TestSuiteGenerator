# -*- coding: utf-8 -*-
"""
retrieval.py — Local, LLM-free retrieval over TSG's own historical corpus (tsg_cache.db).

This is the "retrieval" half of RAG, with ZERO external dependencies and ZERO network/LLM
calls: a pure-Python BM25 ranker over the 20k+ historical test cases TSG has already
generated. Given a feature title / description, it returns the most similar past test
cases — evidence that a generated suite is grounded in real prior work, not invented.

Design guarantees (so it can never break generation):
  * No third-party imports — only Python stdlib + sqlite3.
  * Every public call is wrapped by the CALLER in try/except; this module also fails soft
    (returns [] on any error) so a missing/locked DB never raises into the UI.
  * Read-only: opens the cache DB read-only; never writes.

Typical use (display-only, in the dashboard):
    from modules.retrieval import retrieve_similar_testcases
    hits = retrieve_similar_testcases("Change BCD / DPFO reset day for line", top_k=5)
    # hits = [{'score', 'feature_id', 'summary', 'suite_id', 'sno'}, ...]
"""
from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Cache DB lives at the TSG root (parent of this modules/ dir).
_DEFAULT_DB = Path(__file__).resolve().parent.parent / 'tsg_cache.db'

# Lightweight English stopwords — enough to stop BM25 rewarding filler.
_STOP = frozenset("""
a an the of to for in on at by and or is are be with from as into via per this that these those
it its user users when then should must will shall via using use used check verify validate ensure
test case step steps line lines number value values response request api nbop tmo
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    toks = _TOKEN_RE.findall((text or '').lower())
    return [t for t in toks if len(t) > 1 and t not in _STOP]


class _BM25:
    """Minimal BM25 (Okapi) over a list of token-lists. Pure Python, deterministic."""

    def __init__(self, corpus_tokens: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = corpus_tokens
        self.N = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.N) if self.N else 0.0
        # document frequency
        self.df: Dict[str, int] = {}
        self.tf: List[Dict[str, int]] = []
        for d in corpus_tokens:
            seen = {}
            for t in d:
                seen[t] = seen.get(t, 0) + 1
            self.tf.append(seen)
            for t in seen:
                self.df[t] = self.df.get(t, 0) + 1
        # idf
        self.idf: Dict[str, float] = {}
        for t, df in self.df.items():
            # BM25 idf with +0.5 smoothing; floor at a small positive so common terms
            # still contribute a little rather than going negative.
            self.idf[t] = max(0.01, math.log((self.N - df + 0.5) / (df + 0.5) + 1.0))

    def score(self, query_tokens: List[str], idx: int) -> float:
        if self.N == 0 or self.avgdl == 0:
            return 0.0
        tf = self.tf[idx]
        dl = self.doc_len[idx]
        s = 0.0
        for t in query_tokens:
            f = tf.get(t, 0)
            if not f:
                continue
            idf = self.idf.get(t, 0.0)
            denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            s += idf * (f * (self.k1 + 1)) / denom
        return s

    def top_k(self, query_tokens: List[str], k: int) -> List[Tuple[int, float]]:
        scored = [(i, self.score(query_tokens, i)) for i in range(self.N)]
        scored = [x for x in scored if x[1] > 0]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]


def _connect_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.exists():
        return None
    try:
        # read-only URI so we never lock/modify the live cache.
        return sqlite3.connect('file:%s?mode=ro' % db_path.as_posix(), uri=True, timeout=3)
    except Exception:
        try:
            return sqlite3.connect(str(db_path), timeout=3)
        except Exception:
            return None


def _load_testcases(conn: sqlite3.Connection, limit: int) -> List[Dict[str, Any]]:
    """Pull historical test cases (most recent suites first, bounded for speed)."""
    rows: List[Dict[str, Any]] = []
    try:
        cur = conn.execute(
            "SELECT tc.summary, tc.description, tc.suite_id, tc.sno, s.feature_id, s.feature_title "
            "FROM test_cases tc LEFT JOIN test_suites s ON tc.suite_id = s.suite_id "
            "WHERE tc.summary IS NOT NULL AND tc.summary != '' "
            "ORDER BY tc.rowid DESC LIMIT ?", (int(limit),))
        for summ, desc, suite_id, sno, fid, ftitle in cur.fetchall():
            rows.append({
                'summary': summ or '',
                'description': desc or '',
                'suite_id': suite_id or '',
                'sno': sno or '',
                'feature_id': fid or '',
                'feature_title': ftitle or '',
            })
    except Exception:
        pass
    return rows


def retrieve_similar_testcases(query: str, top_k: int = 5, *,
                               db_path: Optional[Path] = None,
                               corpus_limit: int = 4000,
                               min_score: float = 5.0) -> List[Dict[str, Any]]:
    """Return up to `top_k` historical test cases most similar to `query`.

    Never raises — returns [] on any problem (missing DB, empty corpus, bad query), so a
    caller can wire it into the UI without risking the generation flow.

    Each hit: {'score', 'summary', 'feature_id', 'feature_title', 'suite_id', 'sno'}.
    """
    try:
        qtok = _tokenize(query)
        if not qtok:
            return []
        conn = _connect_ro(Path(db_path) if db_path else _DEFAULT_DB)
        if conn is None:
            return []
        try:
            rows = _load_testcases(conn, corpus_limit)
        finally:
            conn.close()
        if not rows:
            return []
        corpus = [_tokenize(r['summary'] + ' ' + r['description']) for r in rows]
        bm = _BM25(corpus)
        hits = []
        for idx, score in bm.top_k(qtok, top_k):
            if score < min_score:      # drop weak/noise matches
                continue
            r = rows[idx]
            hits.append({
                'score': round(float(score), 3),
                'summary': r['summary'][:160],
                'feature_id': r['feature_id'],
                'feature_title': (r['feature_title'] or '')[:80],
                'suite_id': r['suite_id'],
                'sno': r['sno'],
            })
        return hits
    except Exception:
        return []


def retrieve_similar_chalk(query: str, top_k: int = 3, *,
                           db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return up to `top_k` Chalk sections whose raw_text best matches `query`.

    Grounds a feature against the real spec corpus (chalk_cache + *_Chalk tables).
    Fails soft (returns []).
    """
    try:
        qtok = _tokenize(query)
        if not qtok:
            return []
        conn = _connect_ro(Path(db_path) if db_path else _DEFAULT_DB)
        if conn is None:
            return []
        docs: List[Dict[str, Any]] = []
        try:
            # chalk_cache: feature-scoped spec text
            try:
                for fid, scope, raw in conn.execute(
                        "SELECT feature_id, scope, raw_text FROM chalk_cache "
                        "WHERE raw_text IS NOT NULL AND raw_text != '' LIMIT 400"):
                    docs.append({'src': 'chalk_cache', 'ref': fid or (scope or ''),
                                 'text': raw or ''})
            except Exception:
                pass
            # section-based chalk tables
            for tbl in ('NSL_VZ_Chalk', 'TMO_API_Chalk'):
                try:
                    for name, raw in conn.execute(
                            "SELECT section_name, raw_text FROM %s "
                            "WHERE raw_text IS NOT NULL AND raw_text != '' LIMIT 300" % tbl):
                        docs.append({'src': tbl, 'ref': name or '', 'text': raw or ''})
                except Exception:
                    pass
        finally:
            conn.close()
        if not docs:
            return []
        corpus = [_tokenize(d['text']) for d in docs]
        bm = _BM25(corpus)
        out = []
        for idx, score in bm.top_k(qtok, top_k):
            d = docs[idx]
            out.append({'score': round(float(score), 3), 'source': d['src'],
                        'ref': str(d['ref'])[:80], 'snippet': d['text'][:200]})
        return out
    except Exception:
        return []
