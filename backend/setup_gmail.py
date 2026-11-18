"""One-time OAuth setup for the real Gmail connection (operator runs this once).

Steps (all local, nothing leaves the machine except calls to Google):
1. Create a Google Cloud project, enable the Gmail API and Pub/Sub.
2. Create OAuth client credentials (Desktop app) and save the JSON as
   data/google_credentials.json (gitignored).
3. Run: python3 setup_gmail.py
   A browser opens for consent; the token is written to data/gmail_token.json.
4. Set GMAIL_ENABLED=1, PUBSUB_TOPIC and PUBSUB_SUBSCRIPTION in .env,
   then run: python3 worker.py --campaign-id YOUR_CAMPAIGN_ID
"""
import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.compose"]
HERE = os.path.dirname(__file__)
CREDS = os.environ.get("GOOGLE_CREDENTIALS", os.path.join(HERE, "..", "data", "google_credentials.json"))
TOKEN = os.environ.get("GMAIL_TOKEN", os.path.join(HERE, "..", "data", "gmail_token.json"))

if not os.path.exists(CREDS):
    raise SystemExit(f"missing OAuth client file: {CREDS} (see the steps at the top of this file)")

flow = InstalledAppFlow.from_client_secrets_file(CREDS, SCOPES)
creds = flow.run_local_server(port=0)
os.makedirs(os.path.dirname(TOKEN), exist_ok=True)
with open(TOKEN, "w", encoding="utf-8") as f:
    f.write(creds.to_json())
os.chmod(TOKEN, 0o600)
print(f"token saved locally to {TOKEN}")
