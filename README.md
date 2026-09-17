# 🍺 Beers Counter Bot

Telegram bot that counts circle video messages (video notes) toward a shared group goal of **1,000,000 beers**.

---

## How it works

Every time a member of the group sends a **circle video** (video note), the bot:

1. Logs the message in `video_log` (using Telegram's `message_id` to prevent double-counting on restart).
2. Increments the sender's personal count in `beers`.
3. Replies with the remaining distance to the 1,000,000 goal.

At **midnight Moscow time** the bot automatically:

- Expires any streak that wasn't extended the day before (see [Streak mechanic](#streak-mechanic)).
- Sends a daily report to the main group with stats for the previous day.
- Records the day's **MVP** (the person who sent the most circles that day).
- Deletes all tracked text messages from the previous day (auto-clean).
- ~~Builds and sends a **montage**~~ — a highlight clip stitched from cuts of every circle video sent that day. **Currently disabled** while the feature is being tested via `/montage` (see [Daily montage](#daily-montage)).

---

## Module layout

| File | Responsibility |
|---|---|
| `bot.py` | Entry point — builds the `Application`, wires handlers and jobs, starts polling |
| `common.py` | Shared constants (`GOAL`, `MOSCOW`) and helpers (`escape_md`, `fmt`, `medal`, `plural_ru`, `reply_chunked`, bulk message deletion) |
| `commands.py` | User commands: `/start` `/help` `/stats` `/leaderboard` `/none` `/day` `/week` `/month` `/inactive` `/chart` |
| `admin.py` | Admin commands: `/remove` `/removelast` `/clean` |
| `video.py` | Circle-video counting + message/member tracking handlers |
| `report.py` | Daily report — `/report` command, the midnight job, and its batched data collector |
| `montage.py` | Daily montage — `/montage` command and the midnight job that stitches the day's circles into one clip with `ffmpeg` |
| `charts.py` | "Beer glass" daily-beers chart drawn with Pillow — weekly / monthly (`/chart`, `/chart m`) — plus the Sunday chart job. Fonts live in `assets/fonts/`. |
| `database.py` | All Postgres access (pooled connections via `psycopg`) |

---

## Commands

| Command | Who can use | What it does |
|---|---|---|
| `/start` | Anyone | Welcome message and command list |
| `/count` | Anyone | Your personal beer count + MVP wins |
| `/leaderboard` | Anyone | Top 100 all-time, with ⭐ per MVP win |
| `/streak` `/streak current` | Anyone | Leaderboard by all-time record streak (`current` → active streaks) |
| `/none` | Anyone | How many people from the bottom of the leaderboard, combined, equal the #1 drinker |
| `/chart` `/chart m` | Anyone | Bar chart of daily beers for the last 7 days (`m` → last 30) |
| `/report` | Admins (group) / Anyone (private) | Trigger the daily report manually |
| `/montage` | Admins (group) / Anyone (private) | Build and send a montage of today's circles so far |
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

## Streak mechanic

- `beers.current_streak` counts consecutive Moscow-calendar days with at least one circle; `beers.longest_streak` is that user's all-time record.
- Sending a circle extends the streak by comparing today's date to `last_video_at`'s date: same day keeps it, the next day extends it by one, any bigger gap resets it to 1.
- A broken streak isn't caught the moment it breaks — nobody sends a video on a day they don't drink. So every midnight (00:00 Moscow, right before the daily report), a job zeroes `current_streak` for anyone who didn't send a circle the day before. This keeps `/stats`, `/streak`, and `/leaderboard` accurate from the first read of the new day instead of showing a dead streak until that user's next video quietly resets it.

---

## Daily montage

> **Status: being tested.** The nightly job (`daily_montage_job` in `montage.py`) is implemented but deliberately **not** wired into the midnight job in `bot.py` yet — it only runs when `/montage` is called by hand. Once testing looks good, uncomment the call marked in `bot.py`'s `_midnight_job`.

- Every circle video's Telegram `file_id` is stored in `video_log` as it comes in.
- The circles that go into the montage always come from the **main group** (`settings.chat_id`) — the bot re-downloads **every** circle sent there that day (nothing is dropped, even on a very busy day), masks each into a circle over the background at `assets/images/beer_bg.png` (matching the original video-note look, instead of a plain square crop), and burns a running counter (`#1`, `#2`, …) in the corner — each as its own `ffmpeg` process, one clip at a time, so peak memory stays flat no matter how many circles the day had. The normalized clips are then stitched together with the cheap concat demuxer (a stream copy, not a re-encode).
- The total montage is capped at **30 seconds** (45s once the day has more than 100 circles) — every circle still gets a slice, but each clip's share shrinks to fit (e.g. 10 circles → 3s each, 100 circles → 0.3s each). There's a one-frame floor per clip, so an extreme day (in the thousands) could in theory push the total slightly past the cap rather than trim to nothing.
- Where the result is *sent* is the only thing that varies: `/montage` builds today's montage so far and sends it to whichever chat the command was called from — a separate test group, a private DM, whatever — without touching the main group. Admin-only in groups.
- Once re-enabled, the midnight job does the same for the previous day, sending the result to the main group itself.
- Clips whose file failed to download or failed to normalize are skipped rather than failing the whole montage; if every clip fails, or nobody sent a circle, it logs and skips silently — no message is sent.
- Requires the `ffmpeg` and `ffprobe` binaries on the host running the bot (already installed in the `Dockerfile`), plus `assets/fonts/Bitter.ttf` (counter overlay) and `assets/images/beer_bg.png` (circle background, generated/scaled with Pillow — already a dependency).

---

## Database schema

Stored in Postgres (self-hosted via the `postgres` service in `docker-compose.yml`), accessed exclusively through `database.py`.

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
id         INTEGER  GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY
user_id    BIGINT   NOT NULL          — Sender
chat_id    BIGINT   NOT NULL          — Group where the circle was sent
sent_at    TEXT     NOT NULL          — ISO timestamp (Moscow tz)
message_id BIGINT                    — Telegram message ID
                                        UNIQUE(chat_id, message_id) WHERE message_id IS NOT NULL
file_id    TEXT                      — Telegram file_id of the video note, used by the daily montage
```

The partial unique index on `(chat_id, message_id)` prevents double-counting when Telegram replays pending updates after a bot restart. Historical records restored from a chat export have `message_id = NULL` and are unaffected by the constraint (Postgres, like SQLite, treats each NULL as distinct for uniqueness purposes).

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

### `backfill_video_file_ids.py`

Backfills `video_log.file_id` for rows recorded before the montage feature existed, so those old circles can be included in a montage too. Bots can't fetch `file_id` for a message after the fact, so this matches each missing row to the corresponding video in a **media-included** Telegram chat export — exactly, by `message_id`, wherever the row has one (a chat export's message `id` is the same `message_id` the bot stores); only rows with no `message_id` (legacy data restored from an export before that was tracked) fall back to matching by sender + date, in time order. It then re-uploads each matched file to a chat you choose (to mint a fresh `file_id`) and stores it. Supports `--dry-run` to preview the match plan first.

```bash
docker cp result.json                     beers-counter:/app/result.json
docker cp backfill_video_file_ids.py      beers-counter:/app/backfill_video_file_ids.py
docker exec -it beers-counter python backfill_video_file_ids.py result.json <source_chat_id> <upload_chat_id> --dry-run
docker exec -it beers-counter python backfill_video_file_ids.py result.json <source_chat_id> <upload_chat_id>
```

`source_chat_id` is the group the export was taken from; `upload_chat_id` is where the re-uploaded videos get sent to mint `file_id`s (e.g. your own DM with the bot — DM it once first so that chat exists).

### `backfill_mvp.py`

Populates `mvp_log` from historical `video_log` data. Run once after restoring the log from an export.

```bash
docker cp backfill_mvp.py  beers-counter:/app/backfill_mvp.py
docker exec -it beers-counter python backfill_mvp.py
```

### `scripts/migrate_to_postgres.py`

One-time migration of an existing `data/beers.db` (SQLite) into Postgres. Run from the project root, after `docker-compose up -d postgres` and once the schema exists (start the bot once against it, or call `database.init_db()` directly):

```bash
python scripts/migrate_to_postgres.py
```

Prints a row-count comparison between SQLite and Postgres for every table so you can confirm the copy is complete before decommissioning the old `data/` directory.

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

Environment variables (set in `.env`, see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `POSTGRES_USER` | ✅ | Postgres username (also bootstraps the `postgres` container) |
| `POSTGRES_PASSWORD` | ✅ | Postgres password — use a long, random value; this port is internet-reachable |
| `POSTGRES_DB` | ✅ | Postgres database name |
| `DATABASE_URL` | ✅ | Full connection string the bot uses, e.g. `postgresql://$POSTGRES_USER:$POSTGRES_PASSWORD@postgres:5432/$POSTGRES_DB` |

Data is stored in Postgres, self-hosted via the `postgres` service in `docker-compose.yml` (named volume `pgdata`, persists across restarts).

### Exposing Postgres to another host

The bot's own traffic to Postgres stays inside the Docker network (`postgres:5432`), but the `postgres` service also publishes port 5432 on the host so a project on a **different** host (e.g. a companion miniapp) can connect directly with its own Postgres credentials. Since that port is then reachable from the internet, harden it:

- **Firewall it to the other host's IP only**, e.g. with `ufw`:
  ```bash
  sudo ufw allow from <other-host-ip> to any port 5432 proto tcp
  sudo ufw deny 5432/tcp
  ```
  (or the equivalent security-group rule on a cloud provider).
- **Enable TLS** on the Postgres container (mount a cert/key pair and set `ssl = on`) and require `sslmode=require` in any external connection string.
- **Use a dedicated read-only role** for external consumers instead of sharing the bot's own `POSTGRES_USER` credential.
