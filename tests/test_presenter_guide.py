# -*- coding: utf-8 -*-
"""Tests for the Test Case Presenter Guide (modules/demo_doc_generator.py).

Fully offline and deterministic: builds a small synthetic suite (no cache, no Jira,
no Chalk, no LLM) and asserts the generated Word doc has every required section,
one TC-block per test case, plain-English narration, and zero banned phrases.
"""
import re

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from modules.client_text import _HLS_BANNED
# Aliased so pytest does not try to collect these dataclasses as test classes.
from modules.data_models_v8 import TestCase as _TestCase
from modules.data_models_v8 import TestStep as _TestStep
from modules.data_models_v8 import TestSuite as _TestSuite
from modules.demo_doc_generator import generate_demo_doc

LQUO, RQUO = '\u201c', '\u201d'


def _tc(summary, description='', category='Happy Path', steps=None):
    tc = _TestCase(summary=summary, description=description, category=category)
    tc.steps = [_TestStep(step_num=i + 1, summary=s[0], expected=s[1])
                for i, s in enumerate(steps or [])]
    return tc


@pytest.fixture
def synthetic_suite():
    suite = _TestSuite(
        feature_id='MWTGPROV-9999',
        feature_title='Enable intake of the weekly reconciliation file',
        feature_desc='The system receives a weekly file, validates each record, and '
                     'corrects any mismatched values so the data stays in sync.',
        scope='In scope: file intake, validation and correction.',
        pi='PI-60',
    )
    suite.acceptance_criteria = ['The file is received and parsed.',
                                 'Mismatched records are corrected.']
    suite.test_cases = [
        _tc('TC01_MWTGPROV-9999_Verify_the_file_is_received',
            description='To validate: the weekly file is received and parsed',
            category='Happy Path',
            steps=[('Receive the weekly file', 'File is read successfully')]),
        _tc('TC02_MWTGPROV-9999_Verify_mismatched_records_are_corrected',
            description='To validate: mismatched records are corrected',
            category='Happy Path',
            steps=[('Compare each record', 'Mismatch is corrected')]),
        _tc('TC03_MWTGPROV-9999_Verify_a_blank_file_does_not_fail',
            description='To validate: the process does not fail when the file is blank',
            category='Negative',
            steps=[('Provide a blank file', 'Process completes without failure')]),
    ]
    return suite


def _blocks(doc):
    for c in doc.element.body.iterchildren():
        if c.tag == qn('w:p'):
            yield Paragraph(c, doc.part)
        elif c.tag == qn('w:tbl'):
            yield Table(c, doc.part)


def _cell_fill(tbl):
    tcPr = tbl.rows[0].cells[0]._tc.find(qn('w:tcPr'))
    if tcPr is None:
        return ''
    shd = tcPr.find(qn('w:shd'))
    return shd.get(qn('w:fill')).upper() if shd is not None else ''


def _full_text(doc):
    out = []
    for b in _blocks(doc):
        if isinstance(b, Paragraph):
            out.append(b.text)
        else:
            for row in b.rows:
                for cell in row.cells:
                    out.append(cell.text)
    return '\n'.join(out)


@pytest.fixture
def built_doc(synthetic_suite, tmp_path):
    path = generate_demo_doc(synthetic_suite, None, None, log=lambda *a: None,
                             out_dir=tmp_path)
    assert path is not None and path.exists()
    assert 'Test_Case_Presenter_Guide' in path.name
    return Document(str(path))


def test_all_required_sections_present(built_doc):
    """Title block, How-to, Opening narrative, Closing summary, Quick mental model."""
    paras = [b for b in _blocks(built_doc) if isinstance(b, Paragraph)]
    texts = [p.text for p in paras]

    assert paras[0].style.name == 'Title'
    assert 'Test Case Presenter Guide' in paras[0].text
    assert 'Plain-English narration for screen-share walkthroughs' in texts
    assert any(re.match(r'^MWTGPROV-9999 \| PI', t) for t in texts)

    h1 = [p.text for p in paras if p.style.name == 'Heading 1']
    h2 = [p.text for p in paras if p.style.name == 'Heading 2']
    assert 'How to use this guide' in h1
    assert 'Closing summary' in h1
    assert 'Quick mental model' in h2

    bullets = [p for p in paras if p.style.name == 'List Bullet']
    assert len(bullets) >= 3

    # Opening narrative shaded table (EEF3F8) + per-TC Key message tables (FFF4D6)
    tables = [b for b in _blocks(built_doc) if isinstance(b, Table)]
    opening = [t for t in tables if t.rows[0].cells[0].text.startswith('Opening narrative')]
    assert len(opening) == 1 and _cell_fill(opening[0]) == 'EEF3F8'


def test_one_tc_block_per_test_case(built_doc, synthetic_suite):
    paras = [b for b in _blocks(built_doc) if isinstance(b, Paragraph)]
    tc_heads = [p for p in paras if p.style.name == 'Heading 2' and re.match(r'^TC\d{2} \| .+', p.text)]
    what_to_say = [p for p in paras if p.text.startswith('What to say')]

    tables = [b for b in _blocks(built_doc) if isinstance(b, Table)]
    key_tables = [t for t in tables if t.rows[0].cells[0].text.startswith('Key message:')]

    n = len(synthetic_suite.test_cases)
    assert len(tc_heads) == n
    assert len(what_to_say) == n
    assert len(key_tables) == n
    for kt in key_tables:
        assert _cell_fill(kt) == 'FFF4D6'


def test_narration_is_quoted_plain_english(built_doc):
    paras = [b for b in _blocks(built_doc) if isinstance(b, Paragraph)]
    for p in paras:
        if p.text.startswith('What to say'):
            # "What to say" lead run must be bold; narration wrapped in curly quotes.
            assert p.runs[0].bold and p.runs[0].text == 'What to say'
            assert LQUO in p.text and RQUO in p.text
            # No raw snake_case / ALL-CAPS identifiers / file extensions in narration.
            narration = p.text.split('\n', 1)[-1]
            assert not re.search(r'\b\w+_\w+\b', narration), narration
            assert '.csv' not in narration.lower()


def test_no_banned_phrases_anywhere(built_doc):
    """Narration contains no banned phrases (whole-document scan)."""
    full = _full_text(built_doc).lower()
    hits = [b for b in _HLS_BANNED if b in full]
    assert hits == [], 'banned phrases leaked: %s' % hits


def test_banned_step_text_is_scrubbed(tmp_path):
    """A TC whose own one-liner carries a banned phrase must not surface it."""
    suite = _TestSuite(feature_id='TEST-BAN', feature_title='Banned safety net check',
                      feature_desc='A feature description.', pi='PI-60')
    tc = _tc('TC01_TEST-BAN_Verify_service_grouping_is_applied',
             description='To validate: service grouping is applied to each record',
             category='Happy Path',
             steps=[('Apply service grouping', 'Service grouping applied')])
    suite.test_cases = [tc]
    path = generate_demo_doc(suite, None, None, log=lambda *a: None, out_dir=tmp_path)
    full = _full_text(Document(str(path))).lower()
    assert 'service grouping' not in full


def test_template_works_with_no_llm(synthetic_suite, tmp_path, monkeypatch):
    """Generation must not import or call any LLM client. Block common LLM modules."""
    import builtins
    real_import = builtins.__import__
    banned = ('openai', 'anthropic', 'boto3', 'ollama', 'azure.ai')

    def guarded_import(name, *args, **kwargs):
        if any(name == b or name.startswith(b + '.') for b in banned):
            raise AssertionError('LLM module import attempted: %s' % name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    path = generate_demo_doc(synthetic_suite, None, None, log=lambda *a: None, out_dir=tmp_path)
    assert path is not None and path.exists()


def test_feature_id_title_fallback(tmp_path):
    """CHALK-only features with no Jira title fall back to the feature id."""
    suite = _TestSuite(feature_id='MWTGPROV-4576', feature_title='', feature_desc='', pi='PI-60')
    suite.test_cases = [_tc('TC01_MWTGPROV-4576_Verify_something',
                            description='To validate: something happens', category='Happy Path')]
    doc = Document(str(generate_demo_doc(suite, None, None, log=lambda *a: None, out_dir=tmp_path)))
    title = [b for b in _blocks(doc) if isinstance(b, Paragraph)][0].text
    assert 'MWTGPROV-4576' in title
