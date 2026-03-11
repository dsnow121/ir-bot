"""
IR Orchestration Bot — Slack slash command that automates incident response kickoff.
/incident -> Opens a modal to collect severity, INC number, title, and summary
  -> Creates dedicated Slack channel (#inc-YYYYMMDD-title)
  -> Creates Google Doc incident template (living doc + post-mortem)
  -> Creates Google Calendar war room invite with Meet link
  -> Posts incident details to the channel
  -> Pages on-call responders (P1/P2 via DM + channel invite)
  -> Announces in #security-alerts
/incident acl -> Manage who can declare incidents (set, add, remove, clear, show)
/oncall  -> Manage on-call roster (set, add, remove, clear, show)
"""

import json
import os
import re
from datetime import datetime

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler
from flask import Flask, request

from google_integration import create_incident_doc, create_war_room_event

load_dotenv()

# ─── Slack Bolt App ──────────────────────────────────────────────────────────

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)


def make_channel_name(title: str) -> str:
    """Generate a Slack-safe channel name: inc-YYYYMMDD-slugified-title"""
    date_str = datetime.now().strftime("%Y%m%d")
    slug = re.sub(r'[^a-z0-9-]', '-', title.lower())
    slug = re.sub(r'-+', '-', slug).strip('-')[:40]
    return f"inc-{date_str}-{slug}"


# ─── On-call roster ─────────────────────────────────────────────────────────

ACL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acl.json")
ONCALL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oncall.json")


def load_oncall() -> list[str]:
    """Load on-call user IDs from disk."""
    if os.path.exists(ONCALL_FILE):
        with open(ONCALL_FILE) as f:
            return json.load(f)
    return []


def save_oncall(user_ids: list[str]):
    """Persist on-call user IDs to disk."""
    with open(ONCALL_FILE, "w") as f:
        json.dump(user_ids, f)


def load_acl() -> list[str]:
    """Load ACL user IDs from disk. Empty list = open access."""
    if os.path.exists(ACL_FILE):
        with open(ACL_FILE) as f:
            return json.load(f)
    return []


def save_acl(user_ids: list[str]):
    """Persist ACL user IDs to disk."""
    with open(ACL_FILE, "w") as f:
        json.dump(user_ids, f)


def check_acl(user_id: str) -> bool:
    """Check if user is allowed to declare incidents. Empty ACL = everyone allowed."""
    acl = load_acl()
    return not acl or user_id in acl


def parse_user_ids(text: str) -> list[str]:
    """Extract Slack user IDs from command text (e.g. '<@U12345|name>')."""
    return re.findall(r"<@(U[A-Z0-9]+)(?:\|[^>]*)?>", text)


@app.command("/oncall")
def handle_oncall(ack, command, client, logger):
    ack()

    text = (command.get("text") or "").strip()
    user_id = command["user_id"]
    channel_id = command["channel_id"]

    if not text or text == "show":
        # Show current on-call roster
        roster = load_oncall()
        if roster:
            names = ", ".join(f"<@{uid}>" for uid in roster)
            msg = f"*On-Call Roster:* {names}"
        else:
            msg = "No one is currently on-call. Use `/oncall set @user1 @user2` to set the roster."
        client.chat_postEphemeral(channel=channel_id, user=user_id, text=msg)
        return

    parts = text.split(None, 1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if action == "set":
        new_ids = parse_user_ids(rest)
        if not new_ids:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text="Usage: `/oncall set @user1 @user2`",
            )
            return
        save_oncall(new_ids)
        names = ", ".join(f"<@{uid}>" for uid in new_ids)
        client.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text=f"On-call roster set: {names}",
        )

    elif action == "add":
        new_ids = parse_user_ids(rest)
        if not new_ids:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text="Usage: `/oncall add @user`",
            )
            return
        roster = load_oncall()
        for uid in new_ids:
            if uid not in roster:
                roster.append(uid)
        save_oncall(roster)
        names = ", ".join(f"<@{uid}>" for uid in roster)
        client.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text=f"On-call roster updated: {names}",
        )

    elif action == "remove":
        rm_ids = parse_user_ids(rest)
        if not rm_ids:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text="Usage: `/oncall remove @user`",
            )
            return
        roster = [uid for uid in load_oncall() if uid not in rm_ids]
        save_oncall(roster)
        if roster:
            names = ", ".join(f"<@{uid}>" for uid in roster)
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f"On-call roster updated: {names}",
            )
        else:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text="On-call roster is now empty.",
            )

    elif action == "clear":
        save_oncall([])
        client.chat_postEphemeral(
            channel=channel_id, user=user_id,
            text="On-call roster cleared.",
        )

    else:
        # Maybe they just typed user mentions without a subcommand — treat as "set"
        all_ids = parse_user_ids(text)
        if all_ids:
            save_oncall(all_ids)
            names = ", ".join(f"<@{uid}>" for uid in all_ids)
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=f"On-call roster set: {names}",
            )
        else:
            client.chat_postEphemeral(
                channel=channel_id, user=user_id,
                text=(
                    "*On-Call Commands:*\n"
                    "`/oncall` — Show current roster\n"
                    "`/oncall set @user1 @user2` — Set roster\n"
                    "`/oncall add @user` — Add to roster\n"
                    "`/oncall remove @user` — Remove from roster\n"
                    "`/oncall clear` — Clear roster"
                ),
            )


def page_oncall(client, logger, severity, priority, title, inc_number,
                inc_channel_id, user_id):
    """Page on-call responders for P1/P2 incidents. DMs them and invites to channel."""
    if severity not in ("critical", "high"):
        return

    roster = load_oncall()
    if not roster:
        logger.info("No on-call roster configured, skipping paging")
        return

    # Build the page message
    urgency = "CRITICAL" if severity == "critical" else "HIGH"
    page_text = f"*{urgency} INCIDENT PAGE*\n*{priority} — {title}*\n"
    if inc_number:
        page_text += f"*INC #:* {inc_number}\n"
    page_text += (
        f"Declared by <@{user_id}>\n"
        f"Channel: <#{inc_channel_id}>\n\n"
        "You are being paged as an on-call responder. "
        "Please join the incident channel immediately."
    )

    for oncall_uid in roster:
        try:
            # Open a DM conversation and send the page
            dm = client.conversations_open(users=oncall_uid)
            dm_channel = dm["channel"]["id"]
            client.chat_postMessage(channel=dm_channel, text=page_text)
        except Exception as e:
            logger.error(f"Failed to DM on-call user {oncall_uid}: {e}")

        # Invite them to the incident channel
        try:
            client.conversations_invite(channel=inc_channel_id, users=oncall_uid)
        except Exception:
            pass  # Already in channel or other benign error

    # Post a notice in the incident channel about who was paged
    paged_names = ", ".join(f"<@{uid}>" for uid in roster)
    client.chat_postMessage(
        channel=inc_channel_id,
        text=f"On-call responders paged: {paged_names}",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*On-Call Responders Paged:* {paged_names}\nDirect messages sent.",
                },
            },
        ],
    )


# ─── Slash command: open the modal ──────────────────────────────────────────

@app.command("/incident")
def handle_incident(ack, command, client, logger, respond):
    ack()

    text = (command.get("text") or "").strip()
    user_id = command["user_id"]

    # --- ACL management subcommands ---
    if text.lower().startswith("acl"):
        if not check_acl(user_id):
            respond(text="You don't have permission to manage the incident ACL.", response_type="ephemeral")
            return

        acl_text = text[3:].strip()
        if not acl_text or acl_text == "show":
            acl = load_acl()
            if acl:
                names = ", ".join(f"<@{uid}>" for uid in acl)
                msg = f"*Incident ACL:* {names}"
            else:
                msg = "Incident ACL is empty (anyone can declare incidents)."
            respond(text=msg, response_type="ephemeral")
            return

        parts = acl_text.split(None, 1)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if action == "set":
            new_ids = parse_user_ids(rest)
            if not new_ids:
                respond(text="Usage: `/incident acl set @user1 @user2`", response_type="ephemeral")
                return
            save_acl(new_ids)
            names = ", ".join(f"<@{uid}>" for uid in new_ids)
            respond(text=f"Incident ACL set: {names}", response_type="ephemeral")

        elif action == "add":
            new_ids = parse_user_ids(rest)
            if not new_ids:
                respond(text="Usage: `/incident acl add @user`", response_type="ephemeral")
                return
            acl = load_acl()
            for uid in new_ids:
                if uid not in acl:
                    acl.append(uid)
            save_acl(acl)
            names = ", ".join(f"<@{uid}>" for uid in acl)
            respond(text=f"Incident ACL updated: {names}", response_type="ephemeral")

        elif action == "remove":
            rm_ids = parse_user_ids(rest)
            if not rm_ids:
                respond(text="Usage: `/incident acl remove @user`", response_type="ephemeral")
                return
            acl = [uid for uid in load_acl() if uid not in rm_ids]
            save_acl(acl)
            if acl:
                names = ", ".join(f"<@{uid}>" for uid in acl)
                respond(text=f"Incident ACL updated: {names}", response_type="ephemeral")
            else:
                respond(text="Incident ACL cleared — anyone can now declare incidents.", response_type="ephemeral")

        elif action == "clear":
            save_acl([])
            respond(text="Incident ACL cleared — anyone can now declare incidents.", response_type="ephemeral")

        else:
            respond(
                text=(
                    "*Incident ACL Commands:*\n"
                    "`/incident acl` — Show current ACL\n"
                    "`/incident acl set @user1 @user2` — Set ACL\n"
                    "`/incident acl add @user` — Add to ACL\n"
                    "`/incident acl remove @user` — Remove from ACL\n"
                    "`/incident acl clear` — Clear ACL (open access)"
                ),
                response_type="ephemeral",
            )
        return

    # --- ACL check before opening modal ---
    if not check_acl(user_id):
        respond(text="You don't have permission to declare incidents. Contact a security team member.", response_type="ephemeral")
        return

    client.views_open(
        trigger_id=command["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "incident_modal",
            "title": {"type": "plain_text", "text": "Declare Incident"},
            "submit": {"type": "plain_text", "text": "Create Incident"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "private_metadata": command["channel_id"],
            "blocks": [
                {
                    "type": "input",
                    "block_id": "severity_block",
                    "label": {"type": "plain_text", "text": "Severity"},
                    "element": {
                        "type": "static_select",
                        "action_id": "severity_select",
                        "placeholder": {"type": "plain_text", "text": "Select severity"},
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "P3 — Medium"},
                            "value": "medium",
                        },
                        "options": [
                            {"text": {"type": "plain_text", "text": "P1 — Critical"}, "value": "critical"},
                            {"text": {"type": "plain_text", "text": "P2 — High"}, "value": "high"},
                            {"text": {"type": "plain_text", "text": "P3 — Medium"}, "value": "medium"},
                            {"text": {"type": "plain_text", "text": "P4 — Low"}, "value": "low"},
                        ],
                    },
                },
                {
                    "type": "input",
                    "block_id": "inc_number_block",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "INC Number"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "inc_number_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. INC-2024-0042"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "title_block",
                    "label": {"type": "plain_text", "text": "Incident Title"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "title_input",
                        "placeholder": {"type": "plain_text", "text": "e.g. Suspicious lateral movement detected"},
                    },
                },
                {
                    "type": "input",
                    "block_id": "summary_block",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Summary"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "summary_input",
                        "multiline": True,
                        "placeholder": {"type": "plain_text", "text": "Brief description of what happened..."},
                    },
                },
                {
                    "type": "input",
                    "block_id": "visibility_block",
                    "label": {"type": "plain_text", "text": "Channel Visibility"},
                    "element": {
                        "type": "static_select",
                        "action_id": "visibility_select",
                        "initial_option": {
                            "text": {"type": "plain_text", "text": "Private"},
                            "value": "private",
                        },
                        "options": [
                            {"text": {"type": "plain_text", "text": "Private"}, "value": "private"},
                            {"text": {"type": "plain_text", "text": "Public"}, "value": "public"},
                        ],
                    },
                },
            ],
        },
    )


# ─── Modal submission: create the incident ──────────────────────────────────

@app.view("incident_modal")
def handle_modal_submission(ack, body, client, view, logger):
    ack()

    values = view["state"]["values"]
    severity = values["severity_block"]["severity_select"]["selected_option"]["value"]
    inc_number = (values["inc_number_block"]["inc_number_input"].get("value") or "").strip()
    title = values["title_block"]["title_input"]["value"].strip()
    summary = (values["summary_block"]["summary_input"].get("value") or "").strip()
    visibility = values["visibility_block"]["visibility_select"]["selected_option"]["value"]
    is_private = visibility == "private"

    user_id = body["user"]["id"]
    source_channel = view.get("private_metadata", "")
    channel_name = make_channel_name(title)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    severity_map = {"critical": "P1", "high": "P2", "medium": "P3", "low": "P4"}
    priority = severity_map.get(severity, "P3")

    # 1. Create the incident channel
    try:
        channel_resp = client.conversations_create(name=channel_name, is_private=is_private)
        inc_channel_id = channel_resp["channel"]["id"]
    except Exception as e:
        logger.error(f"Failed to create channel: {e}")
        if source_channel:
            client.chat_postMessage(
                channel=source_channel,
                text=f"Failed to create incident channel: {e}",
            )
        return

    # 2. Invite the declaring user
    try:
        client.conversations_invite(channel=inc_channel_id, users=user_id)
    except Exception:
        pass

    # 2b. Page on-call responders (P1/P2 only)
    try:
        page_oncall(client, logger, severity, priority, title, inc_number,
                    inc_channel_id, user_id)
    except Exception as e:
        logger.error(f"Failed to page on-call: {e}")

    # 3. Set channel topic
    topic_parts = [priority, title]
    if inc_number:
        topic_parts.insert(1, inc_number)
    topic_parts.append(f"Declared by <@{user_id}> at {timestamp}")
    topic = " | ".join(topic_parts)
    client.conversations_setTopic(channel=inc_channel_id, topic=topic)

    # 4. Post incident brief to the new channel
    header_text = f"Incident: {title}"
    if inc_number:
        header_text = f"[{inc_number}] {title}"

    fields = [
        {"type": "mrkdwn", "text": f"*Severity:* {severity.upper()} ({priority})"},
        {"type": "mrkdwn", "text": f"*Declared by:* <@{user_id}>"},
        {"type": "mrkdwn", "text": f"*Time:* {timestamp}"},
        {"type": "mrkdwn", "text": f"*Channel:* <#{inc_channel_id}>"},
    ]
    if inc_number:
        fields.insert(0, {"type": "mrkdwn", "text": f"*INC #:* {inc_number}"})

    brief = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text[:150]},
        },
        {
            "type": "section",
            "fields": fields,
        },
    ]

    if summary:
        brief.append({"type": "divider"})
        brief.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary}"},
        })

    brief.append({"type": "divider"})
    brief.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Incident Checklist:*\n"
                "- [ ] Assign Incident Commander\n"
                "- [ ] Identify scope and affected systems\n"
                "- [ ] Begin evidence collection\n"
                "- [ ] Notify stakeholders\n"
                "- [ ] Containment actions\n"
                "- [ ] Document timeline\n"
                "- [ ] Post-incident review scheduled"
            ),
        },
    })

    client.chat_postMessage(
        channel=inc_channel_id,
        text=f"Incident declared: {title}",
        blocks=brief,
    )

    # 5. Create Google Doc incident template
    doc_url = ""
    try:
        # Get the declaring user's display name
        user_info = client.users_info(user=user_id)
        display_name = user_info["user"]["real_name"] or user_info["user"]["name"]

        doc_result = create_incident_doc(
            title=title,
            severity=severity,
            priority=priority,
            inc_number=inc_number,
            summary=summary,
            declared_by=display_name,
            channel_name=channel_name,
            timestamp=timestamp,
        )
        doc_url = doc_result["doc_url"]

        client.chat_postMessage(
            channel=inc_channel_id,
            text=f"Incident Doc: {doc_url}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Incident Document:* <{doc_url}|Open Google Doc>\nUse this as the living doc during the incident and for the post-mortem.",
                    },
                },
            ],
        )
    except Exception as e:
        logger.error(f"Failed to create Google Doc: {e}")
        client.chat_postMessage(
            channel=inc_channel_id,
            text=f"Could not create incident doc: {e}",
        )

    # 6. Create Google Calendar war room event
    try:
        event_result = create_war_room_event(
            title=title,
            severity=severity,
            priority=priority,
            inc_number=inc_number,
            doc_url=doc_url,
        )
        meet_link = event_result.get("meet_link", "")
        event_url = event_result.get("event_url", "")

        war_room_text = "*War Room Meeting Created*\n"
        if meet_link:
            war_room_text += f"*Google Meet:* <{meet_link}|Join Meet>\n"
        if event_url:
            war_room_text += f"*Calendar Event:* <{event_url}|Open Event>\n"
        war_room_text += "Meeting starts in 5 minutes."

        client.chat_postMessage(
            channel=inc_channel_id,
            text=f"War room meeting created",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": war_room_text},
                },
            ],
        )
    except Exception as e:
        logger.error(f"Failed to create calendar event: {e}")
        client.chat_postMessage(
            channel=inc_channel_id,
            text=f"Could not create war room event: {e}",
        )

    # 7. Announce in #security-alerts (if it exists)
    try:
        channels = client.conversations_list(types="public_channel", limit=200)
        alerts_channel = None
        for ch in channels["channels"]:
            if ch["name"] == "security-alerts":
                alerts_channel = ch["id"]
                break

        if alerts_channel:
            try:
                client.conversations_join(channel=alerts_channel)
            except Exception:
                pass

            alert_text = f"*New Incident Declared*\n*{priority} — {title}*\n"
            if inc_number:
                alert_text += f"*INC #:* {inc_number}\n"
            alert_text += f"Declared by <@{user_id}>\nChannel: <#{inc_channel_id}>"

            alert_blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": alert_text},
                },
            ]
            client.chat_postMessage(
                channel=alerts_channel,
                text=f"New incident: {title}",
                blocks=alert_blocks,
            )
    except Exception as e:
        logger.warning(f"Could not post to #security-alerts: {e}")

    # 8. Respond to the user in the original channel
    if source_channel:
        confirm_text = (
            f"Incident created: *{title}* ({priority})\n"
            f"Channel: <#{inc_channel_id}>\n"
        )
        if doc_url:
            confirm_text += f"Doc: <{doc_url}|Incident Doc>\n"
        client.chat_postMessage(
            channel=source_channel,
            text=confirm_text,
        )


# ─── Flask adapter ──────────────────────────────────────────────────────────

flask_app = Flask(__name__)
handler = SlackRequestHandler(app)


@flask_app.route("/slack/incident", methods=["POST"])
def slack_incident():
    return handler.handle(request)


@flask_app.route("/slack/oncall", methods=["POST"])
def slack_oncall():
    return handler.handle(request)


@flask_app.route("/slack/interactions", methods=["POST"])
def slack_interactions():
    return handler.handle(request)


@flask_app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    flask_app.run(port=5000, debug=True)
