"""Check TC07 and TC08 steps specifically."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import init_db, _conn
from modules.jira_fetcher import JiraIssue
from modules.deep_miner import DeepMineResult, _mine_subtask
from modules.data_first_engine import build_test_suite_v8
from modules.doc_parser import ParsedDoc

init_db()
c = _conn()
jira_row = c.execute("SELECT * FROM jira_cache WHERE feature_id LIKE '%4230%'").fetchone()
d = dict(jira_row)
jira = JiraIssue(key=d['feature_id'], summary=d.get('summary',''), description=d.get('description',''),
    acceptance_criteria=d.get('ac_text',''), channel=d.get('channel','NBOP'), pi=d.get('pi',''),
    status=d.get('status',''), priority=d.get('priority',''),
    labels=json.loads(d.get('labels_json','[]')), subtasks=json.loads(d.get('subtasks_json','[]')))
c.close()

subtask_mines = [_mine_subtask(st, log=lambda x: None) for st in jira.subtasks]
deep_mine = DeepMineResult(feature_id=jira.key, subtask_mines=subtask_mines, data_sources_used=['Jira subtasks'])

parsed_docs = [
    ParsedDoc(filename='Unit testing document.docx', file_type='.docx', doc_type='Test Reference',
        paragraphs=[
            'Step 1: Log in to NBOP application and search for an existing Phone, Tablet or Smartwatch TMO lines so that we can able to see line-summary screen.',
            'Step 2: Click on the Data Details menu, so that the page will navigate to Data Details Screen. Ensure the cards and parameters are not visible according to the TMO requirements.',
            'Step 3: Click on the View Historical Usage, so that the page will navigate to Historical Usage Grid. Ensure the cards and parameters are not visible according to the TMO requirements.',
        ]),
    ParsedDoc(filename='Test Result Phone.docx', file_type='.docx', doc_type='Test Reference',
        paragraphs=[
            'Verify that the following total usage attributes are removed from the Data Details and Historical Usage screens for TMO',
            'Total MNO Usage', 'Total HMNO Usage', 'Total Promo Usage', 'Total Usage', 'Threshold', 'Percentage Used',
            'Verify that the following fields should be displayed under Total Usage (Current Billing Period)',
            'MNO Data Usage', 'MNO MHS Data Usage', 'HMNO Data Usage', 'HMNO MHS Usage', 'Promo Data Usage', 'Promo MHS Usage',
            'No changes for Verizon subscribers, keep displaying the Total Usage attributes for them on both screens.',
        ]),
]

suite = build_test_suite_v8(jira, None, parsed_docs, {'channel': ['NBOP'], 'engine_version': '8'}, deep_mine, log=lambda x: None)

print('8 TCs, %d steps\n' % sum(len(tc.steps) for tc in suite.test_cases))
for tc in suite.test_cases[6:]:  # TC07 and TC08
    print('=' * 70)
    print('TC%s: %s [%s]' % (tc.sno, tc.summary[:60], tc.category))
    print('Steps: %d' % len(tc.steps))
    for s in tc.steps:
        print('  %d. %s' % (s.step_num, s.summary[:90]))
        print('     -> %s' % s.expected[:90])
    print()
