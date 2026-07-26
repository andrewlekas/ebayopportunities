#!/bin/bash
# One-time helper: finds your Telegram chat id and saves it to config.yaml.
# Before running: (1) paste your bot token into config.yaml under
# alerts -> telegram -> bot_token, (2) send your bot any message in Telegram.
cd "$(dirname "$0")"
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3

"$PY" - << 'EOF'
import json, sys, urllib.request
import yaml

cfg = yaml.safe_load(open("config.yaml"))
tg = ((cfg.get("alerts") or {}).get("telegram") or {})
token = (tg.get("bot_token") or "").strip()
if not token:
    print("No bot token found. Open config.yaml and paste the token from")
    print("@BotFather under alerts -> telegram -> bot_token, then rerun this.")
    sys.exit(1)

try:
    with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getUpdates", timeout=20) as r:
        data = json.load(r)
except Exception as e:
    print(f"Could not reach Telegram ({e}). Check the token and try again.")
    sys.exit(1)

chats = {}
for u in data.get("result", []):
    msg = u.get("message") or u.get("edited_message") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        name = f'{chat.get("first_name","")} {chat.get("last_name","")}'.strip()
        chats[chat["id"]] = name or chat.get("username", "?")

if not chats:
    print("No messages found. Open Telegram, send your bot any message")
    print("(find it by the username you gave BotFather), then rerun this.")
    sys.exit(1)

chat_id = list(chats)[-1]
cfg["alerts"]["telegram"]["chat_id"] = str(chat_id)
yaml.safe_dump(cfg, open("config.yaml", "w"), sort_keys=False,
               allow_unicode=True, width=100)
print(f"Found chat: {chats[chat_id]} (id {chat_id}) - saved to config.yaml")

# send a confirmation message
body = json.dumps({"chat_id": chat_id,
                   "text": "Card scanner alerts are live. You'll get a "
                           "message here whenever a hot deal appears."}).encode()
req = urllib.request.Request(
    f"https://api.telegram.org/bot{token}/sendMessage", data=body,
    headers={"Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=20)
print("Sent a test message - check your Telegram.")
EOF
read -r -p "Press Enter to close..."
