"""Trace attribution uses trusted SSO email, not opaque internal IDs."""
import time

import pytest

from app.tracing import identifier
from test_tracing import setup


def google(store, sub):
    return store.identity({'method': 'google', 'identity': {'sub': sub, 'email': f'{sub}@example.com'}})


def model(tracing, processor, run):
    tracing.model(run, 'request', time.time_ns(), [], {}, 'completed')
    return processor.spans[-1].attributes


def test_followup_and_late_root_use_turn_author_not_owner_or_current_user(tmp_path):
    store, tracing, processor, _, _ = setup(tmp_path)
    alice, bob = google(store, 'alice'), google(store, 'bob')
    run = store.create_run('First', '', 'modal', [], chat_enabled=True, user_id=alice)
    first = store.claim_message(run['id'])
    snapshot = store.run(run['id'])
    assert model(tracing, processor, snapshot)['user.id'] == 'alice@example.com'
    store.finish_message(run['id'], first['id'], 'First answer')
    store.enqueue_message(run['id'], 'Follow up', 'followup', user_id=bob)
    second = store.claim_message(run['id'])
    current = store.run(run['id'])
    assert model(tracing, processor, current)['user.id'] == 'bob@example.com'
    assert model(tracing, processor, snapshot)['user.id'] == 'alice@example.com'
    tracing.finish_turn(run['id'], first['id'], 'Late replay', 'completed')
    assert processor.spans[-1].attributes['user.id'] == 'alice@example.com'
    store.finish_message(run['id'], second['id'], 'Second answer')
    assert processor.spans[-1].attributes['user.id'] == 'bob@example.com'
    assert store.run(run['id'])['owner_id'] == alice
    assert store.run(run['id'])['active_user_id'] == bob
    assert store.messages(run['id'])[0]['user_id'] == alice


@pytest.mark.parametrize('kind', ['unlinked_slack', 'shared', 'missing', 'non_google_link', 'empty_email'])
def test_no_sso_email_keeps_stable_fallback_not_owner_email(tmp_path, kind):
    store, tracing, processor, _, _ = setup(tmp_path)
    owner = google(store, 'owner')
    if kind in {'unlinked_slack', 'non_google_link'}:
        with store.connect() as conn:
            actor = store.slack_identity_in(conn, 'T12345678', 'U12345678')
            conn.execute("UPDATE users SET email='unlinked@example.com' WHERE id=?", (actor,))
        if kind == 'non_google_link':
            linked = store.identity({'method': 'local', 'role': 'admin'})
            store.execute('UPDATE users SET linked_user_id=? WHERE id=?', (linked, actor))
            store.execute("UPDATE users SET email='not-sso@example.com' WHERE id=?", (linked,))
    elif kind == 'shared':
        actor = store.identity({'method': 'password', 'role': 'admin'})
    elif kind == 'empty_email':
        actor = google(store, 'empty')
        store.execute("UPDATE users SET email='' WHERE id=?", (actor,))
    else:
        actor = 'google:missing'
    run = store.create_run('First', '', 'modal', [], chat_enabled=True, user_id=owner)
    first = store.claim_message(run['id'])
    store.finish_message(run['id'], first['id'], 'Done')
    store.enqueue_message(run['id'], 'Next', 'next', user_id=actor)
    store.claim_message(run['id'])
    attrs = model(tracing, processor, store.run(run['id']))
    assert attrs['user.id'] == format(identifier('user:' + actor, 16), '032x')
    assert attrs['traceloop.association.properties.user_id'] == attrs['user.id']
    assert 'owner@example.com' not in str(attrs)
    assert 'unlinked@example.com' not in str(attrs)


def test_owner_email_for_non_chat_and_no_identity_when_anonymous(tmp_path):
    store, tracing, processor, anonymous, _ = setup(tmp_path)
    assert 'user.id' not in model(tracing, processor, anonymous)
    actor = google(store, 'owner')
    run = store.create_run('One shot', '', 'modal', [], user_id=actor)
    assert model(tracing, processor, run)['user.id'] == 'owner@example.com'


def test_identity_lookup_uses_existing_transaction(tmp_path):
    store, tracing, processor, run, message = setup(tmp_path)
    actor = google(store, 'alice')
    with store.connect() as conn:
        conn.execute('UPDATE messages SET user_id=? WHERE id=?', (actor, message['id']))
        conn.execute("UPDATE users SET email='updated@example.com' WHERE id=?", (actor,))
        tracing.finish_turn(run['id'], message['id'], 'Done', 'completed', connection=conn)
        assert processor.spans[-1].attributes['user.id'] == 'updated@example.com'
