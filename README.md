# Rent Receipt Bot

A private Discord bot for a landlord. You tell it a rent payment came in, and it records it, makes a PDF receipt, and emails it to the tenant. It also reminds you when rent is due, and emails the tenant a late notice if rent isn't paid by the late fee date.

It runs on a server you already have (e.g. an AWS EC2 instance), costs nothing extra, and opens no network ports.

## How you use it

In your private Discord server, type `/` to see the commands:

| Command | What it does |
|---|---|
| `/received amount:1000` | Shows a receipt preview with **Send receipt** / **Cancel** buttons. Send records the payment, emails the PDF to the tenant (with a hidden copy to you), and posts the PDF in Discord. Optional fields: `method` (Cash, Check, Zelle…), `month` (`YYYY-MM`, defaults to this month), `note` (e.g. a check number), `waive_late_fee`. |
| `/balance` | What's owed, paid and left for this month (or `month:2026-09`). |
| `/history` | The last 10 receipts. |
| `/resend number:7` | Emails receipt R-000007 again (e.g. after an email problem). |
| `/late_notice` | Emails the tenant a late notice for this month right now. |

Only you can use the bot. Anyone else gets "Sorry, this bot is private."

### Reminders (automatic)

All reminders go out at `send_time` (default 9:00 AM, your time zone), each at most once a month:

1. **Rent due day:** a Discord message to you with what's owed and what's left.
2. **The day before the late fee date:** a heads-up if anything is unpaid. If the tenant already paid you outside the bot, record it now so they don't get a late notice.
3. **Late fee date:** if a balance remains, the tenant gets a polite late notice email (rent, late fee, paid so far, balance due), and you get a Discord message saying it was sent.

A reminder is only sent on its own day. If the bot was offline that whole day, it skips that reminder instead of sending it late. That way, starting the bot in the middle of a month never sends a surprise late notice.

### Late fee rule

The late fee is added to a month once its `late_fee_day` arrives, if that month's rent wasn't fully paid **before** that day. Use `waive_late_fee` on `/received` to skip it for that month. Some states cap late fees or require a grace period, so check your local rules.

### Receipts

Receipt numbers (`R-000001`, `R-000002`, …) only go up and are never reused. Each receipt stores the rent, late fee and balance as they were when it was recorded, so re-sending an old receipt reproduces it exactly, even if you later change the rent. A copy of every PDF is kept in `/var/lib/rent-receipt-bot/receipts/`.

If an email fails, the payment and PDF are still saved. The bot tells you, and you can retry with `/resend`.

---

## Setup

You'll do three things: create the Discord bot, create a Gmail app password, and install on the server.

### 1. Discord

1. **Create a private server:** in the Discord app, click **+** (Add a Server) → **Create My Own** → **For me and my friends**, and name it something like "Rent".
2. **Create the bot:** go to <https://discord.com/developers/applications> → **New Application** (name it "Rent Receipts") → **Bot** tab → **Reset Token** → copy the token. This is a password, so keep it secret. No "Privileged Gateway Intents" are needed; leave them off.
3. **Invite the bot to your server:** **OAuth2** tab → **URL Generator** → tick the scopes `bot` and `applications.commands`, and the bot permissions **Send Messages** and **Attach Files**. Open the generated URL and pick your Rent server.
4. **Copy two IDs:** in Discord, **User Settings → Advanced → Developer Mode** on. Right-click your server icon → **Copy Server ID** (that's `guild_id`). Right-click your own name → **Copy User ID** (that's `owner_user_id`).

### 2. Gmail app password

Receipts are sent from your Gmail account through Gmail's SMTP server.

1. Turn on 2-Step Verification for the Google account, if it isn't already on.
2. Go to <https://myaccount.google.com/apppasswords>, create one named "rent bot", and copy the 16-character password.

### 3. Server (Amazon Linux 2023)

The bot is kept fully separate from anything else on the server. It has its own system user (`rentbot`), its own folder (`/opt/rent-receipt-bot`), its own Python virtual environment, its own data folder (`/var/lib/rent-receipt-bot`), and its own systemd service. It opens no ports, it's sandboxed so it can't write anywhere else, and it's capped at 256 MB of memory and 25% of one CPU.

```bash
# Check the OS version (this guide is for Amazon Linux 2023).
cat /etc/os-release

# Python 3.11 is installed side by side; the system's default python3 is left alone.
sudo dnf install -y python3.11 git

# A dedicated user that can't log in.
sudo useradd --system --home-dir /opt/rent-receipt-bot --shell /sbin/nologin rentbot

# Get the code. For a private repository, use a fine-grained GitHub token with
# read-only access to this one repository as the password when prompted.
sudo git clone https://github.com/shinjsu92/landlord.git /opt/rent-receipt-bot

# Install into the bot's own virtual environment.
sudo python3.11 -m venv /opt/rent-receipt-bot/.venv
sudo /opt/rent-receipt-bot/.venv/bin/pip install /opt/rent-receipt-bot

# Settings (no secrets in this file).
sudo mkdir -p /etc/rent-receipt-bot
sudo cp /opt/rent-receipt-bot/config.example.toml /etc/rent-receipt-bot/config.toml
sudo nano /etc/rent-receipt-bot/config.toml

# Secrets: readable by root only. systemd reads this file and passes the
# values to the bot.
sudo cp /opt/rent-receipt-bot/secrets.example.env /etc/rent-receipt-bot/secrets.env
sudo chmod 600 /etc/rent-receipt-bot/secrets.env
sudo nano /etc/rent-receipt-bot/secrets.env

# Start it now and on every boot.
sudo cp /opt/rent-receipt-bot/deploy/rent-receipt-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rent-receipt-bot

# Watch the log (Ctrl+C to stop watching). Look for "Logged in as".
sudo journalctl -u rent-receipt-bot -f
```

Then try `/balance` in your Discord server. For a first real test, put your own email in `tenant.email`, run `/received amount:1`, and check that the receipt arrives. Restart the bot after changing the config: `sudo systemctl restart rent-receipt-bot`. To remove the test payment afterwards, delete `/var/lib/rent-receipt-bot` before switching to the real tenant email (this erases all data, so only do it before real use).

### Updating

```bash
cd /opt/rent-receipt-bot && sudo git pull
sudo /opt/rent-receipt-bot/.venv/bin/pip install /opt/rent-receipt-bot
sudo systemctl restart rent-receipt-bot
```

### Backups

Everything the bot records is in `/var/lib/rent-receipt-bot` (`payments.sqlite3` plus the PDFs). Copy that folder somewhere safe now and then, or include it in an EBS snapshot.

---

## Development

Requires Python 3.11+.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest          # tests
ruff check .    # lint
ruff format .   # format
mypy src tests  # type check (strict)
```

### Code layout

| Module | Responsibility |
|---|---|
| `config.py` | Loads `config.toml` and the secrets from environment variables, and validates them. |
| `money.py`, `period.py` | Dollar amounts (stored as integer cents) and rent months. |
| `ledger.py` | Pure late fee and balance rules. |
| `storage.py` | SQLite: payments (one row per receipt) and the reminder log. |
| `receipt_pdf.py` | Draws the PDF receipt (fpdf2, pure Python). |
| `emailer.py` | Builds receipt and late notice emails, and sends them over SMTP. |
| `service.py` | Core workflow: preview → record → PDF → email. Knows nothing about Discord. |
| `reminders.py` | Decides which monthly reminders are due and sends them. |
| `bot.py` | Discord slash commands, buttons and the reminder loop. |

### Known limitations

- One tenant. The data model stores the tenant's email on each payment, so adding more tenants later doesn't need a data migration.
- The PDF uses the built-in Helvetica font, which covers English and Western European characters only. Names in other alphabets (e.g. Korean) need a bundled Unicode font.
