# Data Architect Mentor

An AI mentor that teaches data modeling and cloud-agnostic data architecture in 12 weeks. It has a
**website** (sign in with a username) and a **Telegram bot**, with the same progress in both.

## Start it

Double-click **`start_mentor.bat`**, then open **http://localhost:8000**.
**Autostart is installed**: the mentor starts hidden every time you log in to Windows (task name
`DataModelMentor`). Python and cloudflared live in `C:\Users\reddy\.data-mentor`.
- Stop it now: `Stop-ScheduledTask DataModelMentor` (or end `pythonw.exe` in Task Manager)
- Start it again: `Start-ScheduledTask DataModelMentor`
- Remove autostart: `Unregister-ScheduledTask -TaskName DataModelMentor -Confirm:$false`

People sign in with just a **username**. A new username creates a new learner, and the same username
later continues their progress on any device. The first person (or `ADMIN_USERNAME`) is **admin**. Set
`REQUIRE_INVITE=true` if new usernames should need an invite code. Telegram users can pick a website
username with `/username <name>`.

Note: there's no password, so anyone who knows a username can open that person's progress. That's fine
on this PC or among friends. Before putting it on the internet, add a PIN/password.

## What learners get

| Area | What's in it |
|------|--------------|
| **Learn** | 12 modules, 60 lessons (Mon–Fri). Each lesson: concept, business scenario, schema, SQL/PySpark, pros/cons, when NOT to use it, mistakes, Azure/AWS/GCP equivalents, and an assignment. |
| **Tests** | Placement assessment · per-lesson assignment (AI-reviewed, scored /10) · Quick check (5 auto-graded scenario MCQs) · Week test (10 MCQs) · Written weekly assessment · Architecture design challenge in weeks 4, 8, 12 · Capstone in week 12 |
| **Ask** | Chat with the mentor about anything; it remembers the conversation and your weak areas. |
| **Home** | Progress, streak, average score, focus areas, "revise my weak areas". |
| **Telegram** | Daily lesson at your reminder time, nudges if an assignment is overdue, Saturday test, Sunday revision. |

## Managing people (admin)

Website → **People**: create an invite (gives a website link, a Telegram link and a code), edit
name / reminder time / background / role / pause, or remove someone (asks to confirm; their progress
is moved to `data/removed/`, not deleted). In Telegram: `/invite`, `/users`, `/edituser`, `/removeuser`.

## Settings (.env)

- `GEMINI_API_KEY` and `GEMINI_MODEL`. The models are tried in order, and lite models are fastest on the free tier.
- `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` (optional) are used when Gemini fails. `OPENROUTER_GRADER_MODEL` is an
  optional stronger model just for reviewing answers.
- `DAILY_AI_LIMIT`: AI requests per person per day (everyone shares your free quota).
- `TELEGRAM_BOT_TOKEN`: leave empty to run the website only.

## Public link

With `TUNNEL=true` in `.env`, starting the app also opens a free Cloudflare tunnel and prints a
public https link (also saved in `data\public_url.txt`). Share that link. Notes:
- It works only while this PC is on and the app is running.
- The link changes each time the app restarts. For a fixed address, add your own domain to a free
  Cloudflare account and use a named tunnel.
- Admin usernames must also enter `ADMIN_PASSWORD` from `.env`. Learners don't need a password.
- `GLOBAL_DAILY_AI_LIMIT` and `NEW_USERS_PER_IP_PER_DAY` keep strangers from using up your free AI quota.
## App and notifications

The site is an installable app. Learners tap **Install the app** (Android, Windows, Mac) or **Share → Add to
Home Screen** (iPhone, iOS 16.4+). On the **You** page they can turn on notifications:
- **Daily reminder** at their reminder time: today's lesson, Saturday test, Sunday revision
- **Nudge** when an assignment or test is still waiting
- **New articles**, at most once a day

These need a link that never changes (otherwise installed apps break after a restart). Free setup:
1. Install ngrok: `winget install ngrok.ngrok` (or download it from ngrok.com).
2. On ngrok.com: **Domains** → claim your free static domain; **Your Authtoken** → copy it.
3. In `.env` set `NGROK_AUTHTOKEN=...` and `NGROK_DOMAIN=your-name.ngrok-free.app`, then restart the mentor.
   The link is now permanent: `https://your-name.ngrok-free.app`.

## Installer files (share privately)

- `dist\DataArchitectMentor.apk`: Android. Send it on WhatsApp or Drive. The person taps it, allows
  "install unknown apps" once, and gets the **Mentor** app (full-screen, with notifications).
- `dist\DataArchitectMentor-Setup.exe`: Windows. On first run Windows shows "Windows protected your PC";
  click **More info → Run anyway**. Installs to the Start menu and desktop; remove it in Settings → Apps.
- iPhone: open the link in Safari → Share → **Add to Home Screen** (no file possible without Apple's paid account).

Both apps open the live site, so site updates reach everyone without reinstalling.
Rebuild after changing `NGROK_DOMAIN`: `installers\build_android.ps1` and `installers\build_windows.ps1`.
For an Android update, raise `versionCode` in `installers\android\app\build.gradle` first.

**Back up `C:\Users\reddy\.data-mentor\android\`** (signing key + password). Without it, a new APK can't
update the installed app; people would have to uninstall and reinstall.

## Privacy

On free tiers, Google/OpenRouter providers may use prompts to improve their models. Tell learners not
to paste confidential company data. Logs never contain message text.

## Files

`app.py` (starts everything) · `web.py` + `static/` (website) · `bot.py` (Telegram) · `mentor.py`
(learning engine) · `ai.py` (Gemini/OpenRouter) · `curriculum.py` (edit topics) · `prompts.py`
(mentor persona) · `data/` (people and progress: back this up)
