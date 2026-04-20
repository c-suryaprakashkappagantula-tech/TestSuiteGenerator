"""Scan all features for Kafka/BI event references."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from modules.database import load_all_features, load_jira, _conn

all_features = load_all_features()
target_pis = ['PI-49', 'PI-50', 'PI-51', 'PI-52', 'PI-53']

print('=' * 100)
print('  KAFKA / BI EVENT FEATURE SCAN — ALL PI-49 to PI-53')
print('=' * 100)
print()

kafka_keywords = ['kafka', 'bi kafka', 'bi event', 'event message', 'networkprovider',
                  'network provider', 'tmo indicator', 'bi topic', 'event_messages',
                  'event_notification', 'wib_events']

kafka_features = []
for pi in target_pis:
    for fid, title in all_features.get(pi, []):
        title_lower = title.lower()
        title_match = [kw for kw in kafka_keywords if kw in title_lower]

        jira_data = load_jira(fid)
        desc = (jira_data.get('description', '') or '').lower() if jira_data else ''
        ac = (jira_data.get('ac_text', '') or '').lower() if jira_data else ''
        summary = (jira_data.get('summary', '') or '').lower() if jira_data else ''
        all_jira = desc + ' ' + ac + ' ' + summary

        jira_match = [kw for kw in kafka_keywords if kw in all_jira and kw not in title_lower]

        c = _conn()
        crow = c.execute("SELECT scenarios_json FROM chalk_cache WHERE feature_id=? AND scenarios_json != '[]' LIMIT 1", (fid,)).fetchone()
        c.close()
        chalk_match = []
        if crow:
            chalk_text = crow['scenarios_json'].lower()
            chalk_match = [kw for kw in kafka_keywords if kw in chalk_text and kw not in title_lower]

        if title_match or jira_match or chalk_match:
            kafka_features.append({
                'pi': pi, 'fid': fid, 'title': title[:60],
                'title_match': title_match, 'jira_match': jira_match,
                'chalk_match': chalk_match,
            })

print('Found %d features with Kafka/BI references:' % len(kafka_features))
print()
print('%-6s %-18s %-55s %s' % ('PI', 'Feature', 'Title', 'Match Source'))
print('-' * 100)
for f in sorted(kafka_features, key=lambda x: x['fid']):
    sources = []
    if f['title_match']: sources.append('TITLE:%s' % ','.join(f['title_match']))
    if f['jira_match']: sources.append('JIRA:%s' % ','.join(f['jira_match'][:3]))
    if f['chalk_match']: sources.append('CHALK:%s' % ','.join(f['chalk_match'][:3]))
    print('%-6s %-18s %-55s %s' % (f['pi'], f['fid'], f['title'], ' | '.join(sources)))

# Now check which of these would get kafka_event_steps vs notification_steps
print()
print('=' * 100)
print('  STEP TEMPLATE ROUTING CHECK')
print('=' * 100)
print()
from modules.step_templates import _is_kafka_event, _is_notification

for f in sorted(kafka_features, key=lambda x: x['fid']):
    t = f['title'].lower()
    is_kafka = _is_kafka_event(t, t)
    is_notif = _is_notification(t, t)
    route = 'KAFKA_EVENT' if is_kafka else ('NOTIFICATION' if is_notif else 'OTHER')
    flag = '' if is_kafka else ' ← NEEDS REVIEW' if 'kafka' in t.lower() else ''
    print('  %-18s %-14s %s%s' % (f['fid'], route, f['title'][:50], flag))
