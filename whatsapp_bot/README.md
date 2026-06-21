# 📱 WhatsApp Bot Setup

This adds two capabilities to the automation:
1. **Auto-push** — after each game, bot sends the summary to your WhatsApp group
2. **On-demand** — anyone sends "טבלה" in the group and gets the live leaderboard

---

## Architecture

```
GitHub Actions ──POST /push──► Cloudflare Worker ──► Twilio ──► WhatsApp group
                                      ▲
                                      │
                         Twilio webhook (incoming msgs)
```

---

## Step 1 — Twilio Sandbox (5 min)

1. Sign up free at [twilio.com](https://www.twilio.com)
2. Go to **Messaging → Try it out → Send a WhatsApp message**
3. You'll see a sandbox number like `+1 415 523 8886`
4. **Each group member** must activate the sandbox once:
   - Save the number in their contacts
   - Send `join <your-sandbox-keyword>` to that number on WhatsApp
   - (Twilio shows the exact message to send in their console)
5. Note your:
   - **Account SID** (starts with AC...)
   - **Auth Token**
   - **Sandbox number** (e.g. `+14155238886`)

> ⚠️ **Sandbox limitation**: Twilio sandbox can only send to numbers that have opted in.
> For a real group broadcast, each member must send that join message once.
> For production (no opt-in required), you'd apply for a WhatsApp Business number (~$5/mo).

---

## Step 2 — Deploy Cloudflare Worker (3 min)

You need Node.js installed. Then:

```bash
cd whatsapp_bot

# Install wrangler CLI
npm install -g wrangler

# Login to Cloudflare
npx wrangler login

# Deploy the worker
npx wrangler deploy

# Note the URL it gives you, e.g.:
# https://sport5-whatsapp-bot.YOUR_SUBDOMAIN.workers.dev
```

### Set secrets on the worker

```bash
npx wrangler secret put TWILIO_ACCOUNT_SID
# → paste: ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

npx wrangler secret put TWILIO_AUTH_TOKEN
# → paste: your auth token

npx wrangler secret put TWILIO_FROM
# → paste: whatsapp:+14155238886

npx wrangler secret put WHATSAPP_GROUP_ID
# → paste: whatsapp:+972XXXXXXXXX  (your number first, for testing)

npx wrangler secret put SPORT5_EMAIL
# → paste: hagaigreenfeld@gmail.com

npx wrangler secret put SPORT5_PASSWORD
# → paste: your sport5 password

npx wrangler secret put PUSH_SECRET
# → paste any random string, e.g.: wc2026secret42
```

---

## Step 3 — Connect Twilio to your Worker

1. In Twilio Console → **Messaging → Settings → WhatsApp Sandbox Settings**
2. Set **"When a message comes in"** webhook to:
   ```
   https://sport5-whatsapp-bot.YOUR_SUBDOMAIN.workers.dev/webhook
   ```
   Method: `HTTP POST`
3. Save

---

## Step 4 — Add secrets to GitHub Actions

Two new secrets to add in your repo (**Settings → Secrets**):

| Secret | Value |
|--------|-------|
| `WORKER_URL` | `https://sport5-whatsapp-bot.YOUR_SUBDOMAIN.workers.dev` |
| `PUSH_SECRET` | same random string you used above |

---

## Step 5 — Test it

**Test incoming command:**
- Send `טבלה` to the Twilio sandbox number on WhatsApp
- You should get the live leaderboard back within a few seconds

**Test push (simulate a game finishing):**
```bash
curl -X POST https://sport5-whatsapp-bot.YOUR_SUBDOMAIN.workers.dev/push \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "wc2026secret42",
    "message": "⚽ *Test Game*\n📊 תוצאה: *2:1*\n🥇 Nir mish — 53 נק'"
  }'
```

---

## Supported commands

| Command | What you get |
|---------|-------------|
| `טבלה` | Full current leaderboard |
| `עזרה` | List of available commands |
| `סטטוס` | Check if bot is alive |

---

## Sending to a real WhatsApp Group (advanced)

Twilio sandbox only supports 1-to-1 messages (to opted-in numbers).
To push to a group:
- **Option A**: Bot sends to each member's number individually (loop through a list)
- **Option B**: One designated person is in the group and forwards — more manual
- **Option C**: Apply for a WhatsApp Business API number ($5–15/mo) — supports group sending natively

For the tournament, **Option A** works great with the sandbox: just list all 20 phone numbers and the worker sends each one individually.
