import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor

from app.db import Store, now
from app.slack_chat import slack_text, split_reply
from test_slack import slack_app, signed, event, wait_for

ROOT = '1790719000.123456'


def send(client, index, text, **kw):
    payload = event(f'EvChat{index}', type='message', ts=f'17907190{index:02d}.123456', thread_ts=ROOT, text=text, **kw)
    assert client.post('/hooks/slack/events', **signed(payload)).status_code == 200
    return payload


def start(slack_app):
    app, client, runs, _ = slack_app
    assert client.post('/hooks/slack/events', **signed(event())).status_code == 200
    return app, client, runs[0]['id']


def finish(app, run_id, answer):
    message = app.state.store.claim_message(run_id)
    assert message
    app.state.store.finish_message(run_id, message['id'], answer)
    app.state.store.update_run(run_id, status='idle', summary=answer)
    app.state.slack.chat.collect()


def test_thread_followups_and_both_slack_event_types_share_one_session(slack_app):
    app, client, run_id = start(slack_app)
    mention = event('EvPaired', type='message')
    client.post('/hooks/slack/events', **signed(mention))
    finish(app, run_id, 'Remembering **blue lantern**. [Docs](https://example.com).')
    followup = send(client, 1, 'What was the phrase?')
    followup['event_id'] = 'EvSecondType'
    followup['event'].update(type='app_mention', text='<@U99999999> What was the phrase?')
    client.post('/hooks/slack/events', **signed(followup))
    assert len(app.state.store.rows('SELECT * FROM runs')) == 1
    messages = app.state.store.messages(run_id)
    assert len(messages) == 3
    assert 'What was the phrase?' in messages[-1]['content']
    assert messages[-1]['status'] == 'queued'
    finish(app, run_id, 'It was blue lantern.')
    app.state.slack.chat.collect()
    answers = app.state.store.rows("SELECT * FROM slack_outbox WHERE kind='answer'")
    assert len(answers) == 2
    assert '*blue lantern*' in answers[0]['text']
    assert '<https://example.com|Docs>' in answers[0]['text']
    assert 'It was blue lantern.' in answers[1]['text']
    assert len(app.state.store.rows('SELECT * FROM slack_receipts')) == 2


def test_parallel_duplicate_delivery_cannot_enqueue_twice(slack_app):
    app, client, run_id = start(slack_app)
    payload = event('EvParallelFollowup', type='message', ts='1790719001.111111', thread_ts=ROOT, text='Continue please')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: client.post('/hooks/slack/events', **signed(payload)).status_code, range(4)))
    assert results == [200] * 4
    assert len(app.state.store.messages(run_id)) == 2
    assert len(app.state.store.rows('SELECT * FROM slack_receipts')) == 2


def test_ignores_unrelated_threads_bots_edits_wrong_team_and_shared_channels(slack_app):
    app, client, run_id = start(slack_app)
    for index, changes in enumerate([
        {'thread_ts': '1790718990.000000'}, {'bot_id': 'B12345678'}, {'user': 'U99999999'},
        {'subtype': 'message_changed'}, {'subtype': 'message_deleted'},
    ], 1):
        payload = event(f'EvIgnore{index}', type='message', text='Please do extra work', ts=f'17907190{index:02d}.111111', thread_ts=ROOT)
        payload['event'].update(changes)
        client.post('/hooks/slack/events', **signed(payload))
    payload = event('EvWrongTeam', type='message', text='not authorized', thread_ts=ROOT, ts='1790719010.111111')
    payload['team_id'] = 'T99999999'
    client.post('/hooks/slack/events', **signed(payload))
    payload['team_id'] = 'T12345678'; payload['is_ext_shared_channel'] = True
    client.post('/hooks/slack/events', **signed(payload))
    assert len(app.state.store.messages(run_id)) == 1
    assert len(app.state.store.rows('SELECT * FROM runs')) == 1


def test_sleep_suppresses_replies_and_wake_resumes_same_saved_session(slack_app):
    app, client, run_id = start(slack_app)
    assert app.state.slack.chat.mirroring(run_id) == 'active'
    finish(app, run_id, 'First answer')
    send(client, 1, 'sleep')
    assert app.state.store.rows('SELECT paused FROM slack_threads')[0]['paused'] == 1
    assert app.state.slack.chat.mirroring(run_id) == 'paused'
    send(client, 2, 'This should be ignored while asleep')
    assert len(app.state.store.messages(run_id)) == 2
    # A late web answer must not be backfilled when the Slack thread wakes.
    app.state.store.execute("INSERT INTO messages(run_id,role,content,status,created_at) VALUES(?,'assistant','late private answer','completed',?)", (run_id, now()))
    app.state.slack.chat.collect()
    send(client, 3, 'wake')
    send(client, 4, 'Continue from where we left off')
    assert app.state.store.rows('SELECT paused FROM slack_threads')[0]['paused'] == 0
    assert app.state.slack.chat.mirroring(run_id) == 'active'
    assert len(app.state.store.rows('SELECT * FROM runs')) == 1
    assert 'Continue from where we left off' in app.state.store.messages(run_id)[-1]['content']
    assert not app.state.store.rows("SELECT * FROM slack_outbox WHERE text LIKE '%late private answer%'")


def test_wake_before_collector_does_not_replay_sleeping_answers(slack_app):
    app, client, run_id = start(slack_app)
    finish(app, run_id, 'Earlier answer')
    send(client, 1, 'sleep')
    app.state.store.execute("INSERT INTO messages(run_id,role,content,status,created_at) VALUES(?,'assistant','answer while asleep','completed',?)", (run_id, now()))
    send(client, 2, 'wake')
    app.state.slack.chat.collect()
    assert not app.state.store.rows("SELECT * FROM slack_outbox WHERE text LIKE '%answer while asleep%'")


def test_stop_revokes_active_capabilities_and_approvals_before_ack(slack_app):
    app, client, run_id = start(slack_app)
    app.state.store.claim_message(run_id)
    app.state.store.update_run(run_id, status='awaiting_approval', token_hash='capability')
    app.state.store.execute("INSERT INTO approvals VALUES('approval',?,'linear_comment','{}','pending',?,'')", (run_id, now()))
    send(client, 1, 'A queued followup')
    send(client, 2, 'stop')
    run = app.state.store.run(run_id)
    assert run['token_hash'] == '' and run['status'] in {'stopping', 'cancelled'}
    assert app.state.store.approvals(run_id)[0]['status'] == 'expired'
    assert app.state.store.messages(run_id)[-1]['status'] == 'cancelled'
    assert len(app.state.store.messages(run_id)) == 2


def test_plain_yes_never_grants_external_write_approval(slack_app):
    app, client, run_id = start(slack_app)
    app.state.store.claim_message(run_id)
    app.state.store.update_run(run_id, status='awaiting_approval')
    app.state.store.execute("INSERT INTO approvals VALUES('approval',?,'linear_comment','{}','pending',?,'')", (run_id, now()))
    app.state.slack.chat.collect()
    send(client, 1, 'yes')
    assert app.state.store.approvals(run_id)[0]['status'] == 'pending'
    rows = app.state.store.rows("SELECT * FROM slack_outbox WHERE kind='approval'")
    assert len(rows) == 1 and 'web session' in rows[0]['text']
    app.state.slack.chat.collect()
    assert len(app.state.store.rows("SELECT * FROM slack_outbox WHERE kind='approval'")) == 1


def test_no_historical_answer_backfill_and_legacy_requires_a_new_mention(slack_app):
    app, client, _, _ = slack_app
    old = app.state.store.create_slack_run('EvOld', 'Old request', [], 'C12345678', ROOT, 'U12345678')
    app.state.store.execute("UPDATE slack_events SET reply_status='sent'")
    finish(app, old['id'], 'Old answer must stay in the app')
    send(client, 1, 'An unrelated reply in an old thread')
    assert not app.state.store.rows('SELECT * FROM slack_threads')
    send(client, 2, '<@U99999999> Continue the previous conversation')
    assert len(app.state.store.rows('SELECT * FROM runs')) == 1
    app.state.slack.chat.collect()
    assert not app.state.store.rows("SELECT * FROM slack_outbox WHERE text LIKE '%Old answer%'")
    assert 'Continue the previous conversation' in app.state.store.messages(old['id'])[-1]['content']


def test_queue_limit_has_one_visible_reply_and_no_dropped_duplicate_execution(slack_app):
    app, client, run_id = start(slack_app)
    for index in range(1, 6):
        send(client, index, f'Followup {index}')
    assert len(app.state.store.messages(run_id)) == 5
    rows = app.state.store.rows("SELECT * FROM slack_outbox WHERE dedupe_key='rejected:EvChat5'")
    assert len(rows) == 1 and '5 queued messages' in rows[0]['text']
    send(client, 5, 'Followup 5')
    assert len(app.state.store.rows("SELECT * FROM slack_outbox WHERE dedupe_key='rejected:EvChat5'")) == 1


def test_results_are_only_sent_to_original_thread_and_never_ping_users(slack_app):
    app, client, run_id = start(slack_app)
    wait_for(lambda: app.state.store.rows("SELECT status FROM slack_outbox WHERE kind='ack'")[0]['status'] == 'sent')
    finish(app, run_id, 'Hello <!channel> <@U12345678>. The key is model-test-key.\n```python\nprint("hello")\n```')
    app.state.slack.chat.last_post.clear()
    asyncio.run(app.state.slack.chat.deliver_one())
    sent = slack_app[3][-1]
    assert sent['channel'] == 'C12345678' and sent['thread_ts'] == ROOT
    assert '<!channel>' not in sent['text'] and '<@U12345678>' not in sent['text']
    assert 'model-test-key' not in sent['text'] and '[redacted]' in sent['text']
    assert sent['parse'] == 'none' and sent['link_names'] is False
    assert sent['blocks'][0]['text']['verbatim'] is True


def test_uncertain_outbox_delivery_survives_restart_without_resending(slack_app, monkeypatch):
    app, client, run_id = start(slack_app)
    wait_for(lambda: app.state.store.rows("SELECT status FROM slack_outbox WHERE kind='ack'")[0]['status'] == 'sent')
    finish(app, run_id, 'An important answer')
    attempts = []
    async def fail(*args, **kwargs):
        attempts.append(1)
        raise TimeoutError('The provider response was lost')
    monkeypatch.setattr(app.state.connectors, 'request', fail)
    app.state.slack.chat.last_post.clear()
    asyncio.run(app.state.slack.chat.deliver_one())
    assert app.state.store.rows("SELECT status FROM slack_outbox WHERE kind='answer'")[0]['status'] == 'uncertain'
    # A fresh database handle observes the same durable cursor and outbox.
    reopened = Store(app.state.settings.data_dir)
    assert len(reopened.rows("SELECT * FROM slack_outbox WHERE kind='answer'")) == 1
    app.state.slack.chat.collect()
    app.state.slack.recover()
    app.state.slack.chat.last_post.clear()
    asyncio.run(app.state.slack.chat.deliver_one())
    assert attempts == [1]


def test_paused_connection_discards_pending_replies_and_cannot_send_to_new_team(slack_app):
    app, client, run_id = start(slack_app)
    finish(app, run_id, 'Do not leak this')
    app.state.store.execute("INSERT INTO connection_policies(provider,enabled) VALUES('slack',0)")
    app.state.slack.chat.collect()
    assert not app.state.store.rows("SELECT * FROM slack_outbox WHERE status='pending'")
    assert app.state.slack.chat.mirroring(run_id) is None
    send(client, 1, 'No new work')
    assert len(app.state.store.messages(run_id)) == 2


def test_long_markdown_replies_have_bounded_balanced_code_blocks():
    source = '## Example\n' + '```python\n' + 'print("hello")\n' * 900 + '```\n[Docs](https://example.com)'
    parts = split_reply(slack_text(source))
    assert len(parts) > 2
    assert all(len(part) <= 2610 for part in parts)
    assert all(part.count('```') % 2 == 0 for part in parts)
    assert '<https://example.com|Docs>' in parts[-1]
