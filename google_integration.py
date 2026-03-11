"""
Google Docs + Calendar integration for IR Bot.
Creates incident documents and war room calendar invites.
Uses OAuth 2.0 (Desktop app) with refresh token persistence.
"""

import os
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/calendar",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "google_credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def get_credentials():
    """Load or create OAuth credentials with refresh token."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def create_incident_doc(
    title: str,
    severity: str,
    priority: str,
    inc_number: str,
    summary: str,
    declared_by: str,
    channel_name: str,
    timestamp: str,
) -> dict:
    """Create a Google Doc with the incident template. Returns {doc_id, doc_url}."""
    creds = get_credentials()
    docs_service = build("docs", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    # Build document title
    doc_title = f"Incident Report — {title}"
    if inc_number:
        doc_title = f"[{inc_number}] Incident Report — {title}"

    # Create blank doc
    doc = docs_service.documents().create(body={"title": doc_title}).execute()
    doc_id = doc["documentId"]

    # --- Phase 1: Insert all text content (no tables yet) ---
    # We use placeholders where tables will go, then replace them with real tables
    content_lines = []
    content_lines.append(doc_title)
    content_lines.append("")
    content_lines.append(f"Severity: {severity.upper()} ({priority})")
    if inc_number:
        content_lines.append(f"INC Number: {inc_number}")
    content_lines.append(f"Declared: {timestamp}")
    content_lines.append(f"Declared By: {declared_by}")
    content_lines.append(f"Slack Channel: #{channel_name}")
    content_lines.append("Status: ACTIVE")
    content_lines.append("")
    content_lines.append("INCIDENT COMMANDER")
    content_lines.append("Name: [ASSIGN IC]")
    content_lines.append("Backup IC: [ASSIGN BACKUP]")
    content_lines.append("")
    content_lines.append("SUMMARY")
    content_lines.append(summary if summary else "[Add incident summary here]")
    content_lines.append("")
    content_lines.append("TIMELINE")
    content_lines.append("<<TABLE_TIMELINE>>")
    content_lines.append("")
    content_lines.append("INDICATORS OF COMPROMISE (IOCs)")
    content_lines.append("<<TABLE_IOC>>")
    content_lines.append("")
    content_lines.append("ACTION ITEMS")
    content_lines.append("<<TABLE_ACTIONS>>")
    content_lines.append("")
    content_lines.append("POST-MORTEM")
    content_lines.append("")
    content_lines.append("Impact")
    content_lines.append("[Systems affected, data exposed, business impact]")
    content_lines.append("")
    content_lines.append("Lessons Learned")
    content_lines.append("[What went well, what could be improved]")
    content_lines.append("")
    content_lines.append("Five Whys")
    content_lines.append("1. Why?")
    content_lines.append("2. Why?")
    content_lines.append("3. Why?")
    content_lines.append("4. Why?")
    content_lines.append("5. Why?")
    content_lines.append("")
    content_lines.append("Root Cause")
    content_lines.append("[To be completed after incident resolution]")

    full_text = "\n".join(content_lines) + "\n"

    requests = []
    requests.append({
        "insertText": {"location": {"index": 1}, "text": full_text}
    })

    # Apply formatting
    def find_and_style(text, named_style):
        start = full_text.find(text)
        if start >= 0:
            requests.append({
                "updateParagraphStyle": {
                    "range": {"startIndex": start + 1, "endIndex": start + 1 + len(text) + 1},
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType",
                }
            })

    def find_and_bold(text):
        start = full_text.find(text)
        if start >= 0:
            requests.append({
                "updateTextStyle": {
                    "range": {"startIndex": start + 1, "endIndex": start + 1 + len(text)},
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            })

    find_and_style(doc_title, "HEADING_1")
    for heading in ["INCIDENT COMMANDER", "SUMMARY", "TIMELINE",
                     "INDICATORS OF COMPROMISE (IOCs)", "ACTION ITEMS", "POST-MORTEM"]:
        find_and_style(heading, "HEADING_2")
    for sub in ["Impact", "Lessons Learned", "Five Whys", "Root Cause"]:
        find_and_style(sub, "HEADING_3")
    for meta in [f"Severity: {severity.upper()} ({priority})", f"Declared: {timestamp}",
                 f"Declared By: {declared_by}", f"Slack Channel: #{channel_name}", "Status: ACTIVE"]:
        find_and_bold(meta)
    if inc_number:
        find_and_bold(f"INC Number: {inc_number}")

    docs_service.documents().batchUpdate(
        documentId=doc_id, body={"requests": requests}
    ).execute()

    # --- Phase 2: Replace placeholders with real tables ---
    # We need to find the placeholder positions, delete them, and insert tables.
    # Process in reverse order so indices don't shift.

    table_specs = [
        ("<<TABLE_ACTIONS>>", 5, ["Action", "Owner", "Status", "Ticket", "Notes"]),
        ("<<TABLE_IOC>>", 6, ["IOC", "Type", "Description", "Source", "Notes"]),
        ("<<TABLE_TIMELINE>>", 7, ["Time", "Action", "Notes"]),
    ]

    for placeholder, num_rows, headers in table_specs:
        # Re-read doc to get current content and indices
        doc_content = docs_service.documents().get(documentId=doc_id).execute()
        body_content = doc_content["body"]["content"]

        # Find the placeholder paragraph
        placeholder_start = None
        placeholder_end = None
        for element in body_content:
            if "paragraph" in element:
                para = element["paragraph"]
                para_text = ""
                for elem in para.get("elements", []):
                    if "textRun" in elem:
                        para_text += elem["textRun"]["content"]
                if placeholder in para_text:
                    placeholder_start = element["startIndex"]
                    placeholder_end = element["endIndex"]
                    break

        if placeholder_start is None:
            continue

        # Delete the placeholder text, then insert table at that position
        table_requests = [
            {"deleteContentRange": {"range": {"startIndex": placeholder_start, "endIndex": placeholder_end}}},
            {"insertTable": {"rows": num_rows, "columns": len(headers), "location": {"index": placeholder_start}}},
        ]
        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": table_requests}
        ).execute()

        # Now fill in the header row — re-read doc to find the table
        doc_content = docs_service.documents().get(documentId=doc_id).execute()
        body_content = doc_content["body"]["content"]

        # Find the table that starts near our placeholder position
        for element in body_content:
            if "table" in element and element["startIndex"] >= placeholder_start:
                table = element["table"]
                header_row = table["tableRows"][0]
                fill_requests = []
                for ci, header_text in enumerate(headers):
                    cell = header_row["tableCells"][ci]
                    # Each cell has a paragraph with at least one element
                    cell_para = cell["content"][0]
                    cell_start = cell_para["startIndex"]
                    fill_requests.append({
                        "insertText": {"location": {"index": cell_start}, "text": header_text}
                    })
                    # Bold the header text
                    fill_requests.append({
                        "updateTextStyle": {
                            "range": {"startIndex": cell_start, "endIndex": cell_start + len(header_text)},
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    })
                # Insert headers in reverse column order to avoid index shifting
                fill_requests_reversed = []
                for i in range(0, len(fill_requests), 2):
                    fill_requests_reversed = fill_requests[i:i+2] + fill_requests_reversed
                docs_service.documents().batchUpdate(
                    documentId=doc_id, body={"requests": fill_requests_reversed}
                ).execute()
                break

    # Make the doc accessible via link (anyone with link can edit)
    drive_service.permissions().create(
        fileId=doc_id,
        body={"type": "anyone", "role": "writer"},
    ).execute()

    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    return {"doc_id": doc_id, "doc_url": doc_url}


def create_war_room_event(
    title: str,
    severity: str,
    priority: str,
    inc_number: str,
    doc_url: str,
    attendee_emails: list[str] = None,
    duration_minutes: int = 60,
) -> dict:
    """Create a Google Calendar war room event. Returns {event_id, event_url}."""
    creds = get_credentials()
    cal_service = build("calendar", "v3", credentials=creds)

    now = datetime.utcnow()
    start_time = now + timedelta(minutes=5)
    end_time = start_time + timedelta(minutes=duration_minutes)

    event_title = f"WAR ROOM: {priority} — {title}"
    if inc_number:
        event_title = f"WAR ROOM: [{inc_number}] {priority} — {title}"

    description = (
        f"Incident War Room\n\n"
        f"Severity: {severity.upper()} ({priority})\n"
    )
    if inc_number:
        description += f"INC Number: {inc_number}\n"
    description += f"\nIncident Doc: {doc_url}\n"
    description += "\nJoin this meeting immediately for incident coordination."

    event_body = {
        "summary": event_title,
        "description": description,
        "start": {
            "dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 0},
            ],
        },
    }

    if attendee_emails:
        event_body["attendees"] = [{"email": e} for e in attendee_emails]

    # Add Google Meet conferencing
    event_body["conferenceData"] = {
        "createRequest": {
            "requestId": f"ir-bot-{now.strftime('%Y%m%d%H%M%S')}",
            "conferenceSolutionKey": {"type": "hangoutsMeet"},
        }
    }

    event = cal_service.events().insert(
        calendarId="primary",
        body=event_body,
        conferenceDataVersion=1,
        sendUpdates="all",
    ).execute()

    meet_link = event.get("hangoutLink", "")
    event_url = event.get("htmlLink", "")

    return {
        "event_id": event["id"],
        "event_url": event_url,
        "meet_link": meet_link,
    }
