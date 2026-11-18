"""Independent-review regression cases. Local DB and fake clients only."""
import asyncio
import os
import sys
import time
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import app
import db
import engine
import llm
import mcp_server
from email_text import newest_authored_text

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'test.db'))
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.delenv('LLM_API_KEY', raising=False)
    db.init()
    db.run('INSERT INTO campaigns (name,status,facts,autonomy,created_at) VALUES (?,?,?,?,?)',
           ('Campaign A', 'active', db.jdump({'price':'49.99 USD'}), db.jdump({'version':2,'auto_price':True}), time.time()))

@pytest.mark.parametrize('provider', ['anthropc', 'unknown', ''])
def test_invalid_provider_creates_no_http_client(provider, monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', provider)
    monkeypatch.setenv('LLM_API_KEY', 'fake-test-not-a-key')
    def forbidden(*a, **kw):
        raise AssertionError('HTTP client must not be created')
    monkeypatch.setattr(llm.httpx, 'AsyncClient', forbidden)
    assert llm.mode()['mode'] == 'invalid'
    with pytest.raises(ValueError):
        asyncio.run(llm._json_completion('test'))
    assert not asyncio.run(llm.test_connection())['ok']
    out = asyncio.run(llm.classify('How much?', {}, ''))
    assert out['intent'] == 'unclear' and out['confidence'] == 0
    assert app.status()['llm']['mode'] == 'invalid'

QUOTED = [
    "I'm interested, what is the price?\n\nOn Tue, Example wrote:\nTo unsubscribe reply stop.",
    "I'm interested, what is the price?\n> To unsubscribe reply stop.",
    "I'm interested, what is the price?\n-----Original Message-----\nUnsubscribe now",
    "I'm interested, what is the price?\nFrom: campaign@example.com\nSent: Monday\nSubject: Offer\nStop to unsubscribe",
    '<div>I am interested, what is the price?</div><div class="gmail_quote"><blockquote>Stop to unsubscribe</blockquote></div>',
]

@pytest.mark.parametrize('body', QUOTED)
def test_positive_reply_quoted_footer_does_not_suppress(body):
    assert not llm.has_optout(body)
    asyncio.run(engine.process_reply(1, 'positive', 'Offer', 'lead@example.com', 'Lead', body))
    assert not db.q('SELECT * FROM suppressions')
    assert db.q('SELECT classification FROM messages WHERE direction="in"', one=True)['classification'] == 'interested'
    assert db.q('SELECT body FROM messages WHERE direction="in"', one=True)['body'] == body

@pytest.mark.parametrize('body', ['Stop, even with a discount.\nOn Tue, Example wrote:\nOur price is 49.99 USD', 'Please unsubscribe\n> Interested in the price?', '<p>Remove me</p><blockquote>Our price</blockquote>'])
def test_current_stop_still_immediate_address_wide(body):
    asyncio.run(engine.process_reply(1, 'stop', 'Offer', 'lead@example.com', 'Lead', body))
    assert db.q('SELECT * FROM suppressions', one=True)['email'] == 'lead@example.com'


def test_empty_quoted_only_text_is_review_not_optout():
    asyncio.run(engine.process_reply(1, 'quote-only', 'Offer', 'lead@example.com', 'Lead', '> Stop to unsubscribe'))
    assert not db.q('SELECT * FROM suppressions')
    assert db.q('SELECT classification FROM messages', one=True)['classification'] == 'unclear'


def test_onboarding_resume_exact_question_and_saved_answers():
    engine.onboarding_answer('We sell sales software')
    state = app.onboarding_get()
    assert state['answers'] == {'business':'We sell sales software'}
    assert state['next_question'][0] == 'facts'
    engine.onboarding_answer('The price is 49.99 USD')
    assert app.onboarding_get()['next_question'][0] == 'price'


def test_edit_campaign_identity_goal_plan_together():
    answers = dict(business='Software', facts='49.99 USD', price='49.99 USD', campaign='Original campaign, goal: renewals, Reminder then Final call', tone='Friendly', autonomy='Draft only')
    db.run('UPDATE onboarding SET answers=?', (db.jdump(answers),))
    engine.onboarding_confirm()
    new = 'New campaign, goal: upsell, Introduction then Demo invitation'
    assert engine.onboarding_edit('campaign', new)['ok']
    camp = engine.get_campaign(2)
    assert camp['name'] == 'New campaign' and camp['goal'] == new
    assert [s['name'] for s in camp['steps']['plan']] == ['Introduction', 'Demo invitation']
    assert app.onboarding_get()['summary']['campaign'] == new


def test_global_dnc_distinct_and_sample_filter():
    db.run('INSERT INTO campaigns(name,status) VALUES (?,?)', ('Campaign B','active'))
    db.run('INSERT INTO campaigns(name,status) VALUES (?,?)', ('Sample: C','active'))
    for cid, email in [(1,'one@example.com'), (2,'ONE@example.com'), (3,'sample@example.com')]:
        engine.ensure_thread(cid, email, 'Person', 'Subject')
        db.run('INSERT OR IGNORE INTO suppressions VALUES (?,?)', (email.lower(),time.time()))
    assert app.campaign_overview_stats(False)['do_not_contact'] == 1
    assert app.campaign_overview_stats(True)['do_not_contact'] == 2


def test_mcp_queue_and_thread_preserve_all_unresolved_states():
    t = engine.ensure_thread(1,'lead@example.com','Lead','Subject')
    for i, status in enumerate(['pending','sending','uncertain']):
        db.run('INSERT INTO actions(thread_id,kind,status,reason,created_at) VALUES (?,?,?,?,?)', (t['id'],'draft',status,'Review',time.time()+i))
    assert {a['status'] for a in mcp_server.call('get_review_queue',{})} == {'pending','sending','uncertain'}
    thread = mcp_server.call('get_thread', {'thread_id':t['id']})
    assert len(thread['actions']) == 3 and 'messages' in thread


def test_activity_exposes_fifteen_records_and_missing_campaign():
    t = engine.ensure_thread(1,'lead@example.com','Lead','Subject')
    for i in range(15):
        db.run('INSERT INTO actions(thread_id,kind,status,reason,created_at) VALUES (?,?,?,?,?)', (t['id'],'note','done','Completed',time.time()+i))
    assert len(app.activity()) == 15
    assert app.campaign_detail(999)['error'] == 'not found'


def test_html_mime_retains_history_without_treating_it_as_authored():
    import base64
    import gmail_client
    html = '<p>I am interested, what is the price?</p><div class="gmail_quote"><blockquote>Reply stop to unsubscribe</blockquote></div>'
    payload = {'id':'html-test','payload':{'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(html.encode()).decode()}}}
    text, complete = gmail_client.full_body(payload)
    assert complete and 'unsubscribe' in text
    assert not llm.has_optout(text)
    assert 'price' in newest_authored_text(text)


def test_worker_context_quoted_footer_not_a_new_stop(monkeypatch):
    import gmail_client
    import worker
    thread = engine.ensure_thread(1,'lead@example.com','Lead','Subject')
    monkeypatch.setattr(gmail_client, 'fetch_thread_messages', lambda _: [{'direction':'in','body':QUOTED[0], 'external_id':'history-test','contact_email':'lead@example.com','created_at':time.time(),'body_complete':True}])
    worker._sync_thread_context(thread['id'],'fake-thread')
    assert not db.q('SELECT * FROM suppressions')


def test_unmarked_footer_ambiguity_is_review_not_confirmed_stop():
    body = "Yes, interested in the price.\nClick here to unsubscribe."
    asyncio.run(engine.process_reply(1, 'ambiguous', 'Offer', 'lead@example.com', 'Lead', body))
    assert not db.q('SELECT * FROM suppressions')
    assert db.q('SELECT classification FROM messages', one=True)['classification'] == 'unclear'


def test_genuine_stop_not_weakened_by_footer_words_later_in_line():
    assert llm.has_optout('Please stop. I do not want to click here to unsubscribe.')

OUTLOOK = '<div>I am interested, what is the price?</div><div id="divRplyFwdMsg"><b>From:</b> campaign@example.com<br><b>Sent:</b> Tuesday<br><b>Subject:</b> Offer</div><div>Our campaign offer. Please reply stop to unsubscribe.</div>'


def test_outlook_header_starts_persistent_history_boundary():
    assert newest_authored_text(OUTLOOK) == 'I am interested, what is the price?'
    assert not llm.has_optout(OUTLOOK)
    asyncio.run(engine.process_reply(1,'outlook-direct','Subject','lead@example.com','Lead',OUTLOOK))
    assert not db.q('SELECT * FROM suppressions')
    assert db.q('SELECT classification FROM messages',one=True)['classification'] == 'interested'


def test_outlook_html_mime_and_worker_context_preserve_boundary(monkeypatch):
    import base64
    import gmail_client
    import worker
    full = {'id':'outlook-mime','payload':{'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(OUTLOOK.encode()).decode()}}}
    body, complete = gmail_client.full_body(full)
    assert complete and 'unsubscribe' in body
    assert not llm.has_optout(body)
    asyncio.run(engine.process_reply(1,'outlook-mime','Subject','lead@example.com','Lead',body))
    thread = db.q('SELECT * FROM threads',one=True)
    monkeypatch.setattr(gmail_client,'fetch_thread_messages',lambda _: [{'direction':'in','body':body,'external_id':'outlook-history','contact_email':'lead@example.com','created_at':time.time(),'body_complete':True}])
    worker._sync_thread_context(thread['id'],'fake-outlook-thread')
    assert not db.q('SELECT * FROM suppressions')


@pytest.mark.parametrize('body', [
    'Click here to unsubscribe. Please stop emailing me.',
    'To unsubscribe reply stop. I want you to stop now.',
    'Reply stop to unsubscribe, please stop contacting me.',
    'Unsubscribe here. Remove me from this list.',
    'Click here to unsubscribe please stop emailing me',
    'Click here to unsubscribe, مش عايز رسائل تاني',
])
def test_footer_like_prefix_never_hides_current_stop(body):
    assert llm.has_optout(body)
    asyncio.run(engine.process_reply(1,'prefixed-stop','Subject','lead@example.com','Lead',body))
    assert db.q('SELECT * FROM suppressions',one=True)['email'] == 'lead@example.com'

MOZ_BOTTOM = '<div class="moz-cite-prefix">On Tuesday, Campaign wrote:</div><blockquote type="cite"><p>Our price is 49.99 USD. Reply stop to unsubscribe.</p></blockquote><p>Please stop emailing me.</p>'
MOZ_INLINE = '<div class="moz-cite-prefix">On Tuesday, Campaign wrote:</div><blockquote type="cite"><p>Our first offer</p></blockquote><p>Remove me from this list.</p><blockquote type="cite"><p>Our price is 49.99 USD.</p></blockquote>'


@pytest.mark.parametrize('html', [MOZ_BOTTOM, MOZ_INLINE])
def test_mozilla_bottom_and_inline_authored_stops_survive_mime(html):
    import base64
    import gmail_client
    assert llm.has_optout(html)
    full = {'id':'moz-mime','payload':{'mimeType':'text/html','body':{'data':base64.urlsafe_b64encode(html.encode()).decode()}}}
    body, complete = gmail_client.full_body(full)
    assert complete and llm.has_optout(body)
    assert '49.99 USD' not in newest_authored_text(body)
    asyncio.run(engine.process_reply(1,'moz-stop','Subject','lead@example.com','Lead',body))
    assert db.q('SELECT * FROM suppressions',one=True)['email'] == 'lead@example.com'
    db.run('INSERT INTO campaigns (name,status,facts,autonomy,created_at) VALUES (?,?,?,?,?)', ('Other campaign','active',db.jdump({'price':'49.99 USD'}),db.jdump({'version':2,'auto_price':True}),time.time()))
    asyncio.run(engine.process_reply(2,'later-price','Subject','lead@example.com','Lead','How much does it cost?'))
    assert not db.q("SELECT * FROM actions WHERE status IN ('sent','sent_simulated')")
    assert not db.q("SELECT * FROM messages WHERE direction='out'")


def test_mozilla_positive_reply_after_quote_is_not_suppressed():
    html = '<div class="moz-cite-prefix">On Tue, Campaign wrote:</div><blockquote>Reply stop to unsubscribe.</blockquote><p>I am interested, what is the price?</p>'
    assert not llm.has_optout(html)
    assert newest_authored_text(html) == 'I am interested, what is the price?'

@pytest.mark.parametrize('header', [
    'On Tuesday, November 17, Campaign Sender <sender@example.com> wrote:',
    'On Tuesday, November 17, Campaign Sender <sender@example.com>\nwrote:',
    'On Tuesday, November 17,\nCampaign Sender\n<sender@example.com>\nwrote:',
])
@pytest.mark.parametrize('footer', ['Our offer. To opt out, reply STOP.', 'Our offer. Click here to unsubscribe.'])
def test_single_and_wrapped_plain_attribution_keeps_old_footer_historical(header, footer):
    current = 'Yes, interested. What is the price?'
    body = current + '\n\n' + header + '\n' + footer
    assert newest_authored_text(body) == current
    asyncio.run(engine.process_reply(1,'wrapped-positive','Subject','lead@example.com','Lead',body))
    assert not db.q('SELECT * FROM suppressions')
    assert db.q('SELECT classification FROM messages',one=True)['classification'] == 'interested'
    assert db.q('SELECT body FROM messages',one=True)['body'] == body


@pytest.mark.parametrize('current', [
    'Please stop emailing me.',
    'I read your offer.\nPlease remove me from this list.',
    'شكرا على العرض\nمش عايز رسائل تاني',
    'Please stop.\nOn my side we have made a decision.',
])
def test_wrapped_header_preserves_current_multiline_stop(current):
    body = current + '\n\nOn Tuesday, Campaign Sender <sender@example.com>\nwrote:\nOur price is 49.99 USD.'
    assert newest_authored_text(body) == current
    asyncio.run(engine.process_reply(1,'wrapped-stop','Subject','lead@example.com','Lead',body))
    assert db.q('SELECT * FROM suppressions',one=True)['email'] == 'lead@example.com'


def test_multiline_current_prose_not_mistaken_for_header():
    body = 'On our side we are interested.\nPlease tell me the price.\n\nOur accountant wrote:\nBudget approved.'
    assert newest_authored_text(body) == body

@pytest.mark.parametrize('header', ['On Tuesday, Sender wrote:', 'On Tuesday, Sender <sender@example.com>\nwrote:'])
@pytest.mark.parametrize('current', ['Please stop emailing me.', 'مش عايز رسائل تاني، اوقف الارسال.'])
def test_plain_bottom_stop_after_attribution_and_explicit_quotes(header,current):
    body = header + '\n> Our offer is 49.99 USD.\n\n' + current
    assert newest_authored_text(body) == current
    asyncio.run(engine.process_reply(1,'plain-bottom-stop','Subject','lead@example.com','Lead',body))
    assert db.q('SELECT * FROM suppressions',one=True)['email'] == 'lead@example.com'
    db.run('INSERT INTO campaigns (name,status,facts,autonomy,created_at) VALUES (?,?,?,?,?)', ('Other campaign','active',db.jdump({'price':'49.99 USD'}),db.jdump({'version':2,'auto_price':True}),time.time()))
    asyncio.run(engine.process_reply(2,'plain-bottom-price','Subject','lead@example.com','Lead','How much does it cost?'))
    assert not db.q("SELECT * FROM messages WHERE direction='out'")
    assert not db.q("SELECT * FROM actions WHERE status IN ('sent','sent_simulated')")


@pytest.mark.parametrize('header', ['On Tuesday, Sender wrote:', 'On Tuesday, Sender <sender@example.com>\nwrote:'])
def test_plain_explicit_quoted_stop_does_not_suppress_positive_bottom_reply(header):
    body = header + '\n> Stop. To opt out, reply STOP.\n\nYes, interested. What is the price?'
    assert newest_authored_text(body) == 'Yes, interested. What is the price?'
    assert not llm.has_optout(body)
    asyncio.run(engine.process_reply(1,'plain-positive-bottom','Subject','lead@example.com','Lead',body))
    assert not db.q('SELECT * FROM suppressions')
    assert db.q('SELECT classification FROM messages',one=True)['classification'] == 'interested'


def test_plain_stop_between_multiple_explicit_quote_blocks():
    body = 'On Tuesday, Sender wrote:\n> Our offer\nPlease stop emailing me.\n> Our price\nThank you.'
    assert llm.has_optout(body)
    assert 'Our offer' not in newest_authored_text(body)
