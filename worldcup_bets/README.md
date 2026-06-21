# ⚽ World Cup 2026 — Bets Automation

Automatically scrapes all 20 players' bets from your **Sport5 Hevre** group after each game kickoff, analyzes winners/losers, and writes everything to **Google Sheets**.

---

## How it works

```
GitHub Actions cron (fires at kickoff)
    ↓
scripts/resolve_game_id.py   ← figures out which game is starting
    ↓
scraper.py                   ← logs into Sport5, fetches all 20 players' bets via API
    ↓
analyzer.py                  ← computes winners, losers, exact scores, leaderboard
    ↓
sheets.py                    ← writes 3 tabs to Google Sheets
```

**Google Sheets output (3 tabs):**
| Tab | Contents |
|-----|---------|
| `Leaderboard` | Live rankings with Δ points and Δ rank after each game |
| `All Bets` | Every player's bet for every game (appends after each game) |
| `Game Summary` | One row per game: who picked what, top earner, exact scores count |

---

## Setup (one-time, ~20 minutes)

### 1. Fork / clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/worldcup-bets.git
cd worldcup-bets
```

### 2. Create a Google Sheet

1. Go to [Google Sheets](https://sheets.google.com) → create a new blank sheet
2. Name it e.g. **"World Cup 2026 Bets"**
3. Copy the Sheet ID from the URL:  
   `https://docs.google.com/spreadsheets/d/`**`THIS_IS_YOUR_SHEET_ID`**`/edit`

### 3. Create a Google Service Account

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable **Google Sheets API** and **Google Drive API**
4. Go to **IAM & Admin → Service Accounts** → Create service account
5. Download the JSON key file
6. **Share your Google Sheet** with the service account email  
   (looks like `xxx@your-project.iam.gserviceaccount.com`) — give it **Editor** access

### 4. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret name | Value |
|-------------|-------|
| `SPORT5_EMAIL` | Your Sport5 login email (hagaigreenfeld@gmail.com) |
| `SPORT5_PASSWORD` | Your Sport5 password |
| `GOOGLE_SHEETS_KEY_JSON` | The **full contents** of your service account JSON key file |
| `GOOGLE_SHEET_ID` | Your Google Sheet ID (from step 2) |
| `FOOTBALL_DATA_API_KEY` | *(optional)* Free key from [football-data.org](https://www.football-data.org/client/register) |

### 5. Generate the cron schedule

```bash
pip install requests
FOOTBALL_DATA_API_KEY=your_key python3 scripts/generate_crons.py
```

Copy the output and replace the `schedule:` section in `.github/workflows/worldcup.yml`.

### 6. Test it manually

In your GitHub repo → **Actions → ⚽ World Cup Bets Scraper → Run workflow**

Enter:
- `game_id`: a Sport5 game ID (see note below)
- `game_label`: e.g. `Argentina vs Norway (17/06 01:00)`

> **Finding a game_id**: Open the site, click a player, open browser DevTools → Network tab, look for the `getFriendGuesses` POST request, check the request body for the `gid` field inside the games array.

---

## Local testing

```bash
pip install -r requirements.txt

export SPORT5_EMAIL=your@email.com
export SPORT5_PASSWORD=yourpassword
export GOOGLE_SHEET_ID=your_sheet_id
export GOOGLE_SHEETS_KEY_JSON='{"type":"service_account",...}'

# Dry run (no Sheets write):
python3 main.py --game-id GAME_ID_HERE --game-label "Test Game" --dry-run

# Full run:
python3 main.py --game-id GAME_ID_HERE --game-label "Argentina vs Norway"
```

---

## Project structure

```
worldcup-bets/
├── main.py                    # Orchestrator
├── scraper.py                 # Sport5 API client
├── analyzer.py                # Winners/losers logic
├── sheets.py                  # Google Sheets writer
├── schedule.py                # Football schedule helpers
├── requirements.txt
├── scripts/
│   ├── resolve_game_id.py     # Auto-detect current game (used by Actions)
│   └── generate_crons.py      # Print cron lines for all WC games
└── .github/workflows/
    └── worldcup.yml           # GitHub Actions workflow
```

---

## Notes

- **No login needed for the group page** — the group leaderboard is public. But fetching individual bets requires your Sport5 JWT token, which is obtained by logging in.
- **Rate limiting**: the scraper adds a 0.3s delay between player fetches to be polite to Sport5's servers.
- **Token expiry**: Sport5 JWT tokens appear to last ~48 hours. The script logs in fresh every run so this is not an issue.
- **Group ID**: hardcoded as `6a202c81f6f70af684071fd4` (your group).
