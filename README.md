# Rent Receipt Bot

A private Discord bot for a landlord. You tell it a rent payment came in, and it records it, makes a PDF receipt, and emails it to the tenant. It also reminds you when rent is due, and emails the tenant a late notice if rent isn't paid by the late fee date.

It runs in Docker on a server you already have (e.g. an AWS EC2 instance), deploys itself on every push to `main`, costs nothing extra, and opens no network ports.

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

### 3. Server: one-time setup

The bot runs as a Docker container named `rent-receipt-bot`, kept fully separate from anything else on the server:
- It runs as a non-root user inside its own container.
- It publishes no ports, since it only connects out to Discord and Gmail.
- Its filesystem is read-only except for its data folder, and it has no extra Linux capabilities.
- It's limited to 256 MB of memory and a quarter of one CPU.
- Deploys only ever replace this one container and clean up this app's own old images.

Run these once on the EC2 instance (Docker must already be installed):

```bash
# Settings (no secrets in this file).
sudo mkdir -p /etc/rent-receipt-bot
# Paste in the contents of config.example.toml from the repository, then fill it in.
sudo nano /etc/rent-receipt-bot/config.toml

# Secrets: readable by root only. Docker reads this file and passes the
# values to the bot.
sudo tee /etc/rent-receipt-bot/secrets.env > /dev/null <<'SECRETS'
DISCORD_BOT_TOKEN=paste-bot-token-here
SMTP_PASSWORD=paste-gmail-app-password-here
SECRETS
sudo chmod 600 /etc/rent-receipt-bot/secrets.env
sudo nano /etc/rent-receipt-bot/secrets.env

# Data folder (database and PDFs), owned by the container's user (ID 10001).
sudo mkdir -p /var/lib/rent-receipt-bot
sudo chown 10001 /var/lib/rent-receipt-bot
sudo chmod 700 /var/lib/rent-receipt-bot
```

The SSH user the deploy logs in as must be able to run `docker` without `sudo` (that is, be in the `docker` group). If your other app already deploys this way, it is.

### 4. GitHub: automatic deploys

Every push to `main` runs the checks (lint, type check, tests), builds the Docker image, pushes it to GitHub's container registry, and deploys it to the EC2 instance over SSH. Pull requests run the checks only. You can follow each run under the repository's **Actions** tab.

Add these in the repository under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `EC2_HOST` | The instance's public IP address or DNS name. |
| `EC2_USER` | The SSH user, e.g. `ec2-user`. |
| `EC2_SSH_KEY` | The full private key used to SSH in (the contents of the `.pem` file). |
| `EC2_KNOWN_HOSTS` | The server's SSH fingerprint, so the deploy can be sure it's talking to your server. Get it by running `ssh-keyscan -H <EC2_HOST>` on your own computer and pasting all of the output. |

The EC2 security group must allow SSH (port 22) from GitHub's servers. If your other app already deploys over SSH from GitHub Actions, it does.

After the secrets are in place, push to `main` (or open **Actions → Test, build and deploy → Run workflow**) and the bot goes live.

### Checking on it

```bash
docker ps --filter name=rent-receipt-bot     # Is it running?
docker logs -f rent-receipt-bot              # Live log (Ctrl+C to stop). Look for "Logged in as".
docker restart rent-receipt-bot              # Restart after editing config.toml or secrets.env.
```

For a first real test, put your own email in `tenant.email`, run `/received amount:1` in Discord, and check that the receipt arrives. To clear the test payment before going live, run `docker stop rent-receipt-bot`, delete the files inside `/var/lib/rent-receipt-bot`, switch to the tenant's real email, and run `docker start rent-receipt-bot`. This erases all data, so only do it before real use.

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
| `Dockerfile` | The container image (non-root, Python 3.11). |
| `.github/workflows/deploy.yml` | Checks on every push and pull request; build and deploy on `main`. |
| `deploy/deploy.sh` | Runs on the EC2 instance during a deploy to swap in the new container. |

### Known limitations

- One tenant. The data model stores the tenant's email on each payment, so adding more tenants later doesn't need a data migration.
- The PDF uses the built-in Helvetica font, which covers English and Western European characters only. Names in other alphabets (e.g. Korean) need a bundled Unicode font.
