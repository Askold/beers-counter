# 🍺 Beers Counter Bot

Telegram bot that counts circle video messages (video notes) toward a shared group goal of **1,000,000 beers**.

---

## How it works

Every time a member of the group sends a **circle video** (video note), the bot:

1. Logs the message in `video_log` (using Telegram's `message_id` to prevent double-counting on restart).
2. Increments the sender's personal count in `beers`.
3. Replies with the remaining distance to the 1,000,000 goal.

At **midnight Moscow time** the bot automatically:

- Sends a daily report to the main group with stats for the previous day.
- Records the day's **MVP** (the person who sent the most circles that day).
- Deletes all tracked text messages from the previous day (auto-clean).

---

## Module layout

| File | Responsibility |
|---|---|
| `bot.py` | Entry point — builds the `Application`, wires handlers and jobs, starts polling |
| `common.py` | Shared constants (`GOAL`, `MOSCOW`) and helpers (`escape_md`, `fmt`, `medal`, `plural_ru`, `reply_chunked`, bulk message deletion) |
| `commands.py` | User commands: `/start` `/help` `/stats` `/leaderboard` `/none` `/day` `/week` `/month` `/inactive` `/chart` `/topchart` |
| `admin.py` | Admin commands: `/remove` `/removelast` `/clean` |
| `video.py` | Circle-video counting + message/member tracking handlers |
| `report.py` | Daily report — `/report` command, the midnight job, and its batched data collector |
| `charts.py` | "Beer glass" charts drawn with Pillow — daily beers weekly / monthly (`/chart`, `/chart m`) and the above-median leaderboard (`/topchart`) — plus the Sunday chart job. Fonts live in `assets/fonts/`. |
| `database.py` | All SQLite access (WAL mode; connection-per-call) |

---

## Commands

| Command | Who can use | What it does |
|---|---|---|
| `/start` | Anyone | Welcome message and command list |
| `/count` | Anyone | Your personal beer count + MVP wins |
| `/leaderboard` | Anyone | Top 100 all-time, with ⭐ per MVP win |
| `/none` | Anyone | How many people from the bottom of the leaderboard, combined, equal the #1 drinker |
| `/chart` `/chart m` | Anyone | Bar chart of daily beers for the last 7 days (`m` → last 30) |
| `/topchart` | Anyone | Horizontal bar chart of the leaderboard, users above the median count only |
| `/report` | Admins (group) / Anyone (private) | Trigger the daily report manually |
| `/clean` | Admins, main group only | Delete yesterday's text messages |
| `/reset` | Anyone | Reset your own count to 0 |

---

## Daily report contents

- Beers drunk today / yesterday
- Remaining to goal
- Pace estimate: days left at the average of the last 5 days
- Top 3 drinkers of the day 🌟
- MVP streak line if someone has won multiple days in a row 🔥
- All-time top 3 🏆
- Risk zone: users inactive for 20+ days ⚠️

---

## MVP mechanic

- Every midnight the top drinker of the previous day is recorded as **MVP** in `mvp_log`.
- Each MVP win adds a ⭐ next to the user's name in `/leaderboard` and `/count`.
- If the same person wins multiple days in a row, the report shows a 🔥 streak line.

---

## Database schema

### `beers`

Stores each user's all-time count.

```
user_id       INTEGER  PRIMARY KEY   — Telegram user ID
username      TEXT                   — @handle (updated on each circle)
full_name     TEXT                   — Display name
count         INTEGER  DEFAULT 0     — Total circles sent
last_video_at TEXT                   — ISO timestamp of last circle (Moscow tz)
```

### `video_log`

One row per circle video received by the bot. Used for daily stats and pace calculation.

```
id         INTEGER  PRIMARY KEY AUTOINCREMENT
user_id    INTEGER  NOT NULL          — Sender
chat_id    INTEGER  NOT NULL          — Group where the circle was sent
sent_at    TEXT     NOT NULL          — ISO timestamp (Moscow tz)
message_id INTEGER                   — Telegram message ID
                                        UNIQUE(chat_id, message_id) WHERE message_id IS NOT NULL
```

The partial unique index on `(chat_id, message_id)` prevents double-counting when Telegram replays pending updates after a bot restart. Historical records restored from a chat export have `message_id = NULL` and are unaffected by the constraint (SQLite treats each NULL as distinct).

### `mvp_log`

One row per day recording who was MVP (top drinker).

```
date    TEXT  PRIMARY KEY   — YYYY-MM-DD (Moscow date)
user_id INTEGER NOT NULL    — Winner's Telegram user ID
chat_id INTEGER NOT NULL    — Group the win was recorded in
```

### `settings`

Key-value store for bot configuration.

```
key   TEXT  PRIMARY KEY
value TEXT  NOT NULL
```

Currently used keys:

| key | value |
|---|---|
| `chat_id` | Main group's Telegram chat ID (auto-saved on first circle video) |

### `text_messages`

Tracks text message IDs so `/clean` and auto-clean can delete them later.

```
chat_id    INTEGER  NOT NULL
message_id INTEGER  NOT NULL
sent_at    TEXT               — ISO timestamp (Moscow tz)
PRIMARY KEY (chat_id, message_id)
```

---

## Two-group setup

The bot supports a **debug group** and a **main group**:

- The main group is whichever chat first received a circle video (saved in `settings.chat_id`).
- All data queries (counts, leaderboard, MVP) always use the main group.
- `/report` sends the report to whichever chat the command was called in, but the data always comes from the main group.
- `/clean` only works in the main group.

---

## Utility scripts

### `resync_from_export.py`

Wipes `beers` and `video_log` and reseeds them from a Telegram chat export (`result.json`). Use when starting fresh from an export.

```bash
docker cp result.json            beers-counter:/app/result.json
docker cp resync_from_export.py  beers-counter:/app/resync_from_export.py
docker exec -it beers-counter python resync_from_export.py result.json
```

### `restore_video_log.py`

Inserts `video_log` records from a Telegram chat export without wiping any existing data. Skips dates already covered in the log to avoid duplicates.

```bash
docker cp result.json             beers-counter:/app/result.json
docker cp restore_video_log.py    beers-counter:/app/restore_video_log.py
docker exec -it beers-counter python restore_video_log.py result.json
```

### `backfill_mvp.py`

Populates `mvp_log` from historical `video_log` data. Run once after restoring the log from an export.

```bash
docker cp backfill_mvp.py  beers-counter:/app/backfill_mvp.py
docker exec -it beers-counter python backfill_mvp.py
```

---

## Deployment

```bash
# First run
docker-compose up -d

# After updating bot.py / database.py
docker-compose down && docker-compose up -d

# View logs
docker-compose logs -f
```

Environment variables (set in `docker-compose.yml` or `.env`):

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |

Data is stored in `./data/beers.db` (mounted volume, persists across restarts).
