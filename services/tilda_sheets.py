"""Write-only Google Sheets disaster copies. No order values are ever read back."""

import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from services.tilda_payload import order_number, webhook_key

HEADERS = ["backup_id", "backed_up_at", "database_target", "id", "site", "created_at", "payload_type",
           "im_number", "webhook_key", "error_type", "part", "parts", "submission_json", "payload_json",
           "cookies_json", "client_json", "headers_json", "queue_state_json"]
API = "https://sheets.googleapis.com/v4/spreadsheets"


class SheetsBackupError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def backup_document(target, submission, state, error):
    record = {key: submission.get(key, {}) for key in ("payload", "cookies", "client", "headers")}
    record.update({key: submission[key] for key in ("id", "site", "created_at", "payload_type")})
    record["im_number"] = state.get("im_number", submission.get("im_number", order_number(record["payload"])))
    record["webhook_key"] = webhook_key(record["payload"])
    return {"backup_id": hashlib.sha256(f'{target}/{record["site"]}/{record["webhook_key"]}'.encode()).hexdigest(),
            "backed_up_at": datetime.now(timezone.utc).isoformat(), "database_target": target,
            "submission": record, "queue_state": state, "error_type": error}


def backup_rows(document):
    record = document["submission"]
    values = [json.dumps(value, ensure_ascii=False, separators=(",", ":")) for value in (
        record, record["payload"], record["cookies"], record["client"], record["headers"], document["queue_state"])]
    # 20k Unicode codepoints are <=40k UTF-16 units, below Google's 50k cell limit.
    chunks = [[value[i:i + 20000] for i in range(0, len(value), 20000)] for value in values]
    parts = max(map(len, chunks))
    prefix = [document["backup_id"], document["backed_up_at"], document["database_target"],
              *[str(record[key])[:20000] for key in ("id", "site", "created_at", "payload_type", "im_number", "webhook_key")],
              document["error_type"]]
    return [prefix + [str(index + 1), str(parts)] + [pieces[index] if index < len(pieces) else "" for pieces in chunks]
            for index in range(parts)]


class GoogleSheetsBackup:
    def __init__(self, spreadsheet_id, account_json, tab="Webhook backup"):
        self.spreadsheet_id = spreadsheet_id.strip()
        self.account_json = account_json
        self.tab = tab

    @property
    def url(self):
        if re.fullmatch(r"[A-Za-z0-9_-]{10,200}", self.spreadsheet_id):
            return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/edit"
        return ""

    def session(self, create=False):
        try:
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            if create:
                scopes.append("https://www.googleapis.com/auth/drive.file")
            credentials = Credentials.from_service_account_info(json.loads(self.account_json), scopes=scopes)
            return AuthorizedSession(credentials, refresh_timeout=10)
        except Exception:
            raise SheetsBackupError("sheets_credentials_invalid") from None

    @staticmethod
    def request(session, method, url, **kwargs):
        try:
            response = session.request(method, url, timeout=15, **kwargs)
            if not response.ok:
                code = {400: "sheets_invalid_request_or_tab", 401: "sheets_authentication_failed",
                        403: "sheets_permission_or_quota_denied", 404: "sheets_not_found",
                        429: "sheets_rate_limited"}.get(response.status_code, "sheets_unavailable")
                raise SheetsBackupError(code)
            return response.json()
        except SheetsBackupError:
            raise
        except Exception:
            raise SheetsBackupError("sheets_connection_error") from None

    def range_url(self, suffix="", cells="A:R"):
        if not self.url or not self.tab.strip() or len(self.tab) > 100:
            raise SheetsBackupError("sheets_not_configured")
        cell_range = "'" + self.tab.replace("'", "''") + "'!" + cells
        return f"{API}/{self.spreadsheet_id}/values/{quote(cell_range, safe='')}{suffix}"

    def initialize(self, session):
        body = {"values": [HEADERS]}
        try:
            self.request(session, "PUT", self.range_url(cells="A1:R1"),
                         params={"valueInputOption": "RAW"}, json=body)
        except SheetsBackupError as exc:
            if exc.code != "sheets_invalid_request_or_tab":
                raise
            self.request(session, "POST", f"{API}/{self.spreadsheet_id}:batchUpdate",
                         json={"requests": [{"addSheet": {"properties": {"title": self.tab}}}]})
            self.request(session, "PUT", self.range_url(cells="A1:R1"),
                         params={"valueInputOption": "RAW"}, json=body)

    def send(self, document):
        with self.session() as session:
            self.initialize(session)
            batch = []
            for row in backup_rows(document):
                candidate = batch + [row]
                if batch and len(json.dumps(candidate, ensure_ascii=False).encode()) > 1024 * 1024:
                    self._append(session, batch)
                    batch = []
                batch.append(row)
            if batch:
                self._append(session, batch)
        return {"status": "saved", "url": self.url}

    def _append(self, session, rows):
        result = self.request(session, "POST", self.range_url(":append"),
                              params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"}, json={"values": rows})
        if result.get("updates", {}).get("updatedRows") != len(rows):
            raise SheetsBackupError("sheets_unconfirmed")
