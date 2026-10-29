"""Create/share or initialize the backup sheet; never reads order values."""

import argparse
import json

from services.tilda_sheets import API, SheetsBackupError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "init", "account"))
    parser.add_argument("--title", default="Резервные заказы 2p2p")
    parser.add_argument("--share-with", help="Your Google account email, required for create")
    args = parser.parse_args()
    if args.command == "create" and not args.share_with:
        parser.error("create requires --share-with YOUR_GOOGLE_EMAIL")
    try:
        from routers.tilda_apex_webhook import _sheets_backup_client
        client = _sheets_backup_client()
        if args.command == "account":
            email = json.loads(client.account_json).get("client_email")
            if not email:
                raise SheetsBackupError("sheets_credentials_invalid")
            result = {"ok": True, "service_account_email": email}
        else:
            with client.session(create=args.command == "create") as session:
                if args.command == "create":
                    response = client.request(session, "POST", API, json={"properties": {"title": args.title},
                        "sheets": [{"properties": {"title": client.tab}}]})
                    client.spreadsheet_id = response["spreadsheetId"]
                    try:
                        client.request(session, "POST", f"https://www.googleapis.com/drive/v3/files/{client.spreadsheet_id}/permissions",
                                       params={"sendNotificationEmail": "false"},
                                       json={"type": "user", "role": "writer", "emailAddress": args.share_with})
                    except SheetsBackupError:
                        print(json.dumps({"ok": False, "error": "sheet_created_but_sharing_failed", "spreadsheet_url": client.url}))
                        return 1
                client.initialize(session)
                result = {"ok": True, "spreadsheet_id": client.spreadsheet_id, "spreadsheet_url": client.url}
    except SheetsBackupError as exc:
        result = {"ok": False, "error": exc.code}
    except Exception:
        result = {"ok": False, "error": "sheets_configuration_or_creation_failed"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
