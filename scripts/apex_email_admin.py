"""Safe migration/status CLI. It never sends mail or prints configuration values."""

import argparse
import json

from services.apex_email import EmailServiceNotConfigured, create_service
from services.apex_email_queue import queue_error_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("migrate", "status", "doctor"))
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    try:
        service = create_service()
        service.queue.initialize()
        if args.command == "status":
            result = service.queue.status(max(1, min(args.limit, 500)))
        elif args.command == "doctor":
            result = {"ok": True, "configuration": "ready", "queue": "ready", "smtp": "not_tested"}
        else:
            result = {"ok": True, "schema_version": 1}
    except EmailServiceNotConfigured as exc:
        result = {"ok": False, "error": "email_service_not_configured", "issues": exc.diagnostics()}
    except Exception as exc:
        result = {"ok": False, "error": "queue_unavailable", "reason": queue_error_code(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
