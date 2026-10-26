"""Safe migration/status CLI. It never sends mail or prints configuration values."""

import argparse
import json

from services.apex_email import EmailServiceNotConfigured, create_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "status"))
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    try:
        service = create_service()
        service.queue.initialize()
        result = service.queue.status(max(1, min(args.limit, 500))) if args.command == "status" else {"ok": True, "schema_version": 1}
    except EmailServiceNotConfigured:
        result = {"ok": False, "error": "email_service_not_configured"}
    except Exception:
        result = {"ok": False, "error": "queue_unavailable"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
