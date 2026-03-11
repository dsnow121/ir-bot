# IR Bot — Incident Response Orchestration for Slack

Slack bot that automates incident response kickoff. One slash command spins up an entire incident workspace: dedicated channel, formatted Google Doc, war room calendar invite with Meet link, on-call paging, and cross-channel announcements.

## What It Does

`/incident` opens a modal to collect incident details, then automatically:

1. **Creates a Slack channel** — `#inc-YYYYMMDD-title` (public or private)
2. **Creates a Google Doc** — Formatted incident template with timeline table, IOC table, action items, and post-mortem sections
3. **Creates a Google Calendar event** — War room invite with Google Meet link, starts in 5 minutes
4. **Posts incident brief** — Severity, INC number, summary, and response checklist to the new channel
5. **Pages on-call responders** — DMs on-call users and auto-invites them to the channel (P1/P2 only)
6. **Announces in #security-alerts** — Cross-posts incident notification

### Additional Commands

- `/oncall set|add|remove|clear|show` — Manage the on-call roster
- `/incident acl set|add|remove|clear|show` — Control who can declare incidents

## Stack

- Python (Flask + slack-bolt)
- Google Docs API, Calendar API, Drive API (OAuth 2.0)
- Deployed on AWS EC2 with nginx reverse proxy and Let's Encrypt SSL

## Setup

### 1. Slack App

Create a Slack app at [api.slack.com/apps](https://api.slack.com/apps) with these bot token scopes:

- `channels:read`, `channels:join`, `channels:manage`
- `groups:write`, `groups:read`
- `chat:write`, `im:write`
- `users:read`
- `commands`

Create two slash commands:
- `/incident` — Request URL: `https://your-domain/slack/incident`
- `/oncall` — Request URL: `https://your-domain/slack/oncall`

Set the Interactivity Request URL to: `https://your-domain/slack/interactions`

### 2. Google Cloud

Create a Google Cloud project with these APIs enabled:
- Google Docs API
- Google Drive API
- Google Calendar API

Create OAuth 2.0 credentials (Desktop app type), download as `google_credentials.json` and place in the project root.

On first run, a browser window will open for OAuth consent. The refresh token is saved to `token.json` for subsequent runs.

### 3. Environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your Slack credentials:

```bash
cp .env.example .env
```

### 4. Run

```bash
python app.py
```

The app runs on port 5000. In production, put it behind nginx with SSL.

## Incident Doc Template

The generated Google Doc includes:

- Metadata block (severity, INC number, timestamps, channel link)
- Incident Commander assignment section
- Summary
- Timeline table (Time / Action / Notes)
- IOC table (IOC / Type / Description / Source / Notes)
- Action Items table (Action / Owner / Status / Ticket / Notes)
- Post-mortem: Impact, Lessons Learned, Five Whys, Root Cause

## License

MIT
