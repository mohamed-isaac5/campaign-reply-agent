"""Real Gmail live path: watch + Pub/Sub pull + history.list reconciliation.

STATUS: implemented but NOT yet validated against a live account. It only runs
when GMAIL_ENABLED=1 and the operator's own Google Cloud project credentials are
present in the environment. Demo mode never touches it.

Security notes baked into the design:
- OAuth tokens stay in a local token file on the operator's machine, never in the repo.
- The app filters its own work to the campaign label; Google's scopes are account-wide,
  so that restriction is enforced by this app, not by Google.
- Draft-only vs send is an app-level policy: gmail.compose allows sending, so this
  app simply never calls send unless auto-replies are explicitly enabled.
"""
import base64
import os
from email.message import EmailMessage
from email.utils import parseaddr
from html.parser import HTMLParser

ENABLED = os.environ.get("GMAIL_ENABLED") == "1"
CAMPAIGN_LABEL = os.environ.get("GMAIL_LABEL", "campaigns")
TOKEN_PATH = os.environ.get("GMAIL_TOKEN", os.path.join(os.path.dirname(__file__), "..", "data", "gmail_token.json"))
PUBSUB_SUBSCRIPTION = os.environ.get("PUBSUB_SUBSCRIPTION", "")


def available():
    if not ENABLED:
        return False, "GMAIL_ENABLED is off"
    try:
        import google.oauth2  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
    except ImportError:
        return False, "Google libraries are not installed"
    if not os.path.exists(TOKEN_PATH):
        return False, "No local OAuth token"
    return True, "ready"


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(
        TOKEN_PATH, scopes=["https://www.googleapis.com/auth/gmail.modify",
                            "https://www.googleapis.com/auth/gmail.compose"])
    return build("gmail", "v1", credentials=creds)


def renew_watch(topic):
    """Gmail watch expires (max 7 days); Google recommends renewing daily."""
    svc = _service()
    return svc.users().watch(userId="me", body={
        "topicName": topic, "labelIds": [campaign_label_id(svc)], "labelFilterBehavior": "INCLUDE"}).execute()


def pull_pubsub(max_messages=10):
    """Pull subscription: the app initiates, so no public webhook endpoint is needed.
    Returns (payload, ack_id) pairs; the caller acknowledges via ack_pubsub only
    after the notification's history has been processed, so a crash redelivers."""
    from google.cloud import pubsub_v1

    from google.api_core.exceptions import DeadlineExceeded
    subscriber = pubsub_v1.SubscriberClient()
    try:
        try:
            resp = subscriber.pull(subscription=PUBSUB_SUBSCRIPTION, max_messages=max_messages, timeout=10, retry=None)
        except DeadlineExceeded:
            return []  # an idle long-poll is not a failed worker cycle
        return [(m.message.data, m.ack_id) for m in resp.received_messages]
    finally:
        subscriber.close()


def ack_pubsub(ack_ids):
    if not ack_ids:
        return
    from google.cloud import pubsub_v1

    subscriber = pubsub_v1.SubscriberClient()
    try:
        subscriber.acknowledge(subscription=PUBSUB_SUBSCRIPTION, ack_ids=ack_ids, retry=None, timeout=10)
    finally:
        subscriber.close()


class HistoryExpired(Exception):
    """The stored historyId is older than Gmail keeps history for (404).
    The only safe recovery is a full label sync, never skipping ahead silently."""


def current_history_id():
    svc = _service()
    return svc.users().getProfile(userId="me").execute().get("historyId")


def campaign_label_id(svc=None):
    svc = svc or _service()
    for label in svc.users().labels().list(userId="me").execute().get("labels", []):
        if CAMPAIGN_LABEL in (label.get("id"), label.get("name")):
            return label["id"]
    raise ValueError("Configured campaign label not found. Create or select it before starting.")


def full_sync_list(max_results=100):
    """Read every recovery page, then order by source timestamp, oldest first."""
    svc = _service()
    label = campaign_label_id(svc)
    out, token = [], None
    while True:
        args = {"userId": "me", "labelIds": [label], "maxResults": max_results}
        if token:
            args["pageToken"] = token
        resp = svc.users().messages().list(**args).execute()
        for stub in resp.get("messages", []):
            out.append(svc.users().messages().get(userId="me", id=stub["id"], format="full").execute())
        token = resp.get("nextPageToken")
        if not token:
            return sorted(out, key=lambda m: int(m.get("internalDate", 0)))


def fetch_new_messages_since(history_id):
    """Reconciliation safety net via history.list, as Google recommends.
    Returns (messages, latest_history_id): the caller stores the checkpoint only
    after every returned message has been processed, so a crash never loses mail."""
    svc = _service()
    out, latest = [], history_id
    request = svc.users().history().list(userId="me", startHistoryId=history_id,
                                         labelId=campaign_label_id(svc), historyTypes=["messageAdded"])
    try:
        results = request.execute()
    except Exception as e:
        if getattr(e, "resp", None) is not None and getattr(e.resp, "status", None) == 404:
            raise HistoryExpired(str(history_id)) from e
        raise
    while True:
        for h in results.get("history", []):
            latest = h.get("id", latest)
            for added in h.get("messagesAdded", []):
                full = svc.users().messages().get(userId="me", id=added["message"]["id"], format="full").execute()
                out.append(full)
        request = svc.users().history().list_next(request, results)
        if request is None:
            break
        results = request.execute()
    if "historyId" in (results or {}):
        latest = results["historyId"]
    return out, latest


def _raw_reply(to, subject, body, message_id=None, in_reply_to=None):
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject if subject.lower().startswith("re:") else "Re: " + subject
    if message_id:
        message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def create_draft(thread_id, to, subject, body):
    svc = _service()
    return svc.users().drafts().create(userId="me", body={"message": {"raw": _raw_reply(to, subject, body), "threadId": thread_id}}).execute()


def send_message(thread_id, to, subject, body, message_id=None, in_reply_to=None):
    """Persisted RFC Message-ID gives reconciliation a durable exact identity."""
    if not message_id or not in_reply_to:
        raise ValueError("A durable outbound identity and an original Message-ID are required")
    return _service().users().messages().send(userId="me", body={"raw": _raw_reply(to, subject, body, message_id, in_reply_to), "threadId": thread_id}).execute()


def fetch_thread_messages(gmail_thread_id):
    """Full Gmail thread, both directions, mapped to pipeline fields.
    Outbound context comes from the source of truth (Gmail), never inferred."""
    svc = _service()
    thread = svc.users().threads().get(userId="me", id=gmail_thread_id, format="full").execute()
    me = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
    out = []
    for msg in thread.get("messages", []):
        parsed = parse_message(msg)
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        parsed["direction"] = "out" if "SENT" in msg.get("labelIds", []) or (me and parseaddr(headers.get("from", ""))[1].lower() == me.lower()) else "in"
        out.append(parsed)
    return sorted(out, key=lambda m: m["created_at"])


class _HTMLText(HTMLParser):
    """Readable complete HTML text, retaining quote boundaries for consent parsing."""
    def __init__(self):
        super().__init__()
        self.parts, self.skip, self.quoted, self.stack = [], 0, 0, []
        self.history_started = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        marker = (attrs.get("class", "") + " " + attrs.get("id", "")).lower()
        if "divrplyfwdmsg" in marker:
            self.history_started = True
        quoted = tag == "blockquote" or any(k in marker for k in ("gmail_quote", "yahoo_quoted", "moz-cite-prefix", "divrplyfwdmsg"))
        if tag not in ("br", "hr", "img", "input", "meta", "link"):
            self.stack.append((tag, quoted, tag in ("script", "style")))
            self.quoted += int(quoted)
            self.skip += int(tag in ("script", "style"))
        if tag in ("br", "p", "div", "li", "blockquote"):
            self.parts.append("\n")
    def handle_endtag(self, tag):
        if any(t[0] == tag for t in self.stack):
            while self.stack:
                name, quote, skip = self.stack.pop()
                self.quoted -= int(quote)
                self.skip -= int(skip)
                if name == tag:
                    break
        if tag in ("p", "div", "li", "blockquote"):
            self.parts.append("\n")
    def handle_data(self, data):
        if not self.skip:
            self.parts.append("\n" + "\n".join("> " + line for line in data.splitlines()) + "\n" if (self.quoted or self.history_started) else data)


def full_body(full):
    """Decode MIME recursively. Prefer text/plain; HTML is a readable fallback.
    A Gmail snippet is never a substitute for the whole customer question.
    """
    def walk(part):
        if part.get("filename"):
            return [], []  # ordinary attachments are not the message body
        plain, html = [], []
        mime = part.get("mimeType", "text/plain")
        body = part.get("body", {})
        data = body.get("data")
        if not data and body.get("attachmentId"):
            data = _service().users().messages().attachments().get(userId="me", messageId=full["id"], id=body["attachmentId"]).execute().get("data")
        if data and mime in ("text/plain", "text/html"):
            raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
            headers = {h["name"].lower(): h["value"] for h in part.get("headers", [])}
            import re
            match = re.search(r"charset=[\"']?([^;\"' ]+)", headers.get("content-type", ""), re.I)
            charset = match.group(1) if match else "utf-8"
            text = raw.decode(charset, errors="replace")
            (plain if mime == "text/plain" else html).append(text)
        for child in part.get("parts", []):
            p, h = walk(child)
            plain.extend(p); html.extend(h)
        return plain, html
    plain, html = walk(full.get("payload", {}))
    if plain:
        return "\n".join(plain), True
    if html:
        parser = _HTMLText(); parser.feed("\n".join(html))
        return "".join(parser.parts).strip(), True
    return "", False


def parse_message(full):
    headers = {h["name"].lower(): h["value"] for h in full.get("payload", {}).get("headers", [])}
    name, addr = parseaddr(headers.get("from", ""))
    if not addr or "@" not in addr:
        raise ValueError("Message has no valid sender address")
    body, complete = full_body(full)
    return {"external_id": full["id"], "gmail_thread_id": full.get("threadId"),
            "subject": headers.get("subject", ""), "contact_email": addr.lower(), "contact_name": name or addr,
            "body": body, "body_complete": complete,
            "reply_to_id": headers.get("message-id"), "created_at": int(full.get("internalDate", "0"))/1000,
            "auto": headers.get("auto-submitted", "no").lower() != "no" or "list" in headers.get("precedence", "").lower()}


def find_landed_reply(gmail_thread_id, body=None, since_ts=None, message_id=None):
    """Require exact Message-ID and exact sender binding. Prefix/body similarity
    cannot prove an uncertain send landed and cannot authorize a retry.
    """
    if not message_id:
        return False
    svc = _service()
    thread = svc.users().threads().get(userId="me", id=gmail_thread_id, format="full").execute()
    me = svc.users().getProfile(userId="me").execute().get("emailAddress", "").lower()
    for msg in thread.get("messages", []):
        h = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        own = "SENT" in msg.get("labelIds", []) or (me and parseaddr(h.get("from", ""))[1].lower() == me)
        if own and h.get("message-id") == message_id:
            return True
    return False
