"""Hermetic tests for feature-class-aware classification (Mediation/PRR/billing).

Pure unit tests: no tsg_cache.db, no `shared` package, no network. They assert that a
Mediation/PRR/billing feature classifies as the batch_file class (NOT api) so its TCs get
PRR-flow steps, while a pure NSL-provisioning feature with no mediation signal stays api.
"""
from modules.tc_builder import classify_feature
from modules.qmetry_pattern_library import is_mediation_feature

# Reuse the real 4628 AC captured in the assertion-grounding fixtures.
from tests.test_assertion_grounding import FIXTURE_4628_AC


def test_4628_classifies_as_batch_file_not_api():
    summary = ('[NSLNM, INTG]: TMO PRR Mediation - MMS_TYPE_INDICATOR maps to '
               'call_direction_ind in the Amdocs TMO billing record')
    fc = classify_feature(summary, FIXTURE_4628_AC)
    assert fc.classification == 'batch_file', fc
    # The old wrong behaviour recorded api_kw=['NSLNM']; the mediation class carries none.
    assert fc.api_keywords_found == []


def test_synthetic_wpamed_mediation_classifies_batch_file():
    summary = '[WPAMED]: Mediation PRR processing for TMO biller SFTP input files'
    fc = classify_feature(summary, 'Process the PRR file through TMO_PRR_Processing_01.')
    assert fc.classification == 'batch_file', fc


def test_pure_nsl_provisioning_stays_api():
    # No mediation/PRR/billing signal anywhere — must remain the api/NSL class.
    summary = '[NSLNM]: New MVNO - Retrieve device (GET/POST)'
    fc = classify_feature(summary, 'NSL retrieves the device for the subscriber line.')
    assert fc.classification == 'api', fc
    assert not is_mediation_feature(summary, 'NSL retrieves the device for the subscriber line.')


def test_detector_requires_real_signal_not_nslnm_alone():
    assert not is_mediation_feature('[NSLNM]: Change rate plan POST')
    assert is_mediation_feature('[NSLNM, INTG]: ... Amdocs TMO billing record ...')
    assert is_mediation_feature('feature about call_direction_ind field')
    assert is_mediation_feature('', '', 'MMS_TYPE_INDICATOR value MT')
