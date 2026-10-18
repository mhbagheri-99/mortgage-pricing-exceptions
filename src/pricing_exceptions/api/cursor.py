"""The list's cursor (design doc 5.6). Clients treat it as opaque and pass it back unchanged. Inside, it is
the last row's (created_at, id) as base64url JSON, so its format can change without breaking a client."""

import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi.exceptions import RequestValidationError

from pricing_exceptions.service.queries import Position


def write_cursor(position: Position) -> str:
    created_at, request_id = position
    raw = json.dumps({"created_at": created_at.isoformat(), "id": str(request_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def read_cursor(cursor: str) -> Position:
    """The position a cursor names, or a 422 on the `cursor` parameter for anything this service did not
    issue: not base64url, not JSON, other keys, a time without a time zone, an id that is not a UUID."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if not isinstance(data, dict) or set(data) != {"created_at", "id"}:
            raise ValueError("not a cursor")
        created_at = datetime.fromisoformat(data["created_at"])
        if created_at.tzinfo is None:
            raise ValueError("no time zone")
        return created_at, UUID(data["id"])
    except (ValueError, TypeError, AttributeError) as bad:
        problem = "is not a cursor this service issued; pass next_cursor back unchanged"
        raise RequestValidationError(
            [{"type": "value_error", "loc": ("query", "cursor"), "msg": problem, "input": None}]
        ) from bad
