"""Hermetic test: authored/imperative step summaries survive polish verbatim.

The splitter/humanizer artifact was prefixing authored QMetry lines with 'Verify that ' and
lowercasing the first word ('Verify that let Mediation run...'). polish_suite must leave an
authored step (authored=True) and an already-imperative step byte-for-byte unchanged.
"""
from modules.data_models_v8 import TestStep, TestCase
from modules.traceability import create_traceability
from modules.output_sanitizer import polish_suite


class _Suite:
    def __init__(self, test_cases):
        self.test_cases = test_cases


def _tc():
    tr = create_traceability('QMetry Test Case', 'MWTGPROV-4628', 'authored steps')
    steps = [
        TestStep(step_num=1,
                 summary='Let Mediation process the PRR file',
                 expected='Mediation processes the PRR file successfully.',
                 authored=True),
        TestStep(step_num=2,
                 summary='Retrieve the processed Amdocs TMO billing output record',
                 expected='The Amdocs TMO billing output record is available.',
                 authored=True),
        TestStep(step_num=3,
                 summary='Inspect the call_direction_ind field in the output record',
                 expected='call_direction_ind equals I in the produced record.',
                 authored=False),  # not authored, but already imperative
    ]
    return TestCase(summary='MWTGPROV-4628_Authored_Mediation_Steps', steps=steps,
                    traceability=tr, category='Happy Path',
                    description='Authored QMetry mediation steps.')


def test_authored_steps_not_verify_prefixed_or_recased():
    suite = _Suite([_tc()])
    polish_suite(suite, log=lambda *a: None)
    tc = suite.test_cases[0]
    s1 = tc.steps[0].summary
    assert s1 == 'Let Mediation process the PRR file', s1
    assert not s1.lower().startswith('verify that')
    # 'Let' keeps its capital L — no re-casing.
    assert s1.startswith('Let ')

    s2 = tc.steps[1].summary
    assert s2.startswith('Retrieve ')
    assert not s2.lower().startswith('verify that')


def test_imperative_non_authored_step_not_verify_prefixed():
    suite = _Suite([_tc()])
    polish_suite(suite, log=lambda *a: None)
    s3 = suite.test_cases[0].steps[2].summary
    assert s3.startswith('Inspect '), s3
    assert not s3.lower().startswith('verify that')
