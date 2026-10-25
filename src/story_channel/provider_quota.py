"""Read-only account quota queries. Never infer balances from project costs."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx


@dataclass(frozen=True)
class Quota:
    remaining: Decimal | None
    unit: str
    checked_at: datetime
    used: Decimal | None = None
    limit: Decimal | None = None
    resets_at: datetime | None = None


def _number(data, name):
    value = data[name]
    if isinstance(value, bool):
        raise ValueError("Invalid quota")
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError("Invalid quota")
    return number


def _read(client, url, headers, *, params=None):
    response = client.get(url, headers=headers, **({"params": params} if params is not None else {}))
    if response.status_code in (401, 403):
        raise ValueError("Quota unavailable: check the saved key and its account/subscription read permissions.")
    if response.status_code == 429:
        raise ValueError("Quota check rate limited. Try again later.")
    if not response.is_success:
        raise ValueError(f"Quota unavailable (HTTP {response.status_code}). Try again later.")
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid quota")
    return data


def _openai_spending(client, headers):
    base = "https://api.openai.com/v1/organization"
    now = datetime.now(timezone.utc)
    params = {"start_time": int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()),
              "end_time": int(now.timestamp()), "bucket_width": "1d", "limit": 180}
    total, seen = Decimal(0), set()
    while True:
        data = _read(client, base + "/costs", headers, params=params)
        if not isinstance(data["data"], list) or not isinstance(data["has_more"], bool):
            raise ValueError("Invalid quota")
        for bucket in data["data"]:
            for result in bucket["results"]:
                amount = result["amount"]
                if amount["currency"].upper() != "USD":
                    raise ValueError("Invalid quota")
                total += _number(amount, "value")
        if not data["has_more"]:
            break
        cursor = data.get("next_page")
        if not isinstance(cursor, str) or not cursor or cursor in seen or len(seen) >= 100:
            raise ValueError("Invalid quota")
        seen.add(cursor)
        params["page"] = cursor
    return Quota(None, "USD", datetime.now(timezone.utc), used=total)


def fetch_quota(key_name, api_key):
    """Use saved credentials only; sanitize every provider/network error."""
    if not api_key:
        raise ValueError("Save an API key before checking quota.")
    if key_name == "OPENAI_ADMIN_KEY":
        url = None
        headers = {"Authorization": "Bearer " + api_key}
    elif key_name == "RUNWAYML_API_SECRET":
        url = "https://api.dev.runwayml.com/v1/organization"
        headers = {"Authorization": "Bearer " + api_key, "X-Runway-Version": "2024-11-06"}
    elif key_name == "ELEVENLABS_API_KEY":
        url = "https://api.elevenlabs.io/v1/user/subscription"
        headers = {"xi-api-key": api_key}
    else:
        raise ValueError("Remaining balance is not available through a supported API.")
    try:
        # Do not forward credentials through redirects or use generation endpoints.
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            if key_name == "OPENAI_ADMIN_KEY":
                return _openai_spending(client, headers)
            data = _read(client, url, headers)
        checked = datetime.now(timezone.utc)
        if key_name == "RUNWAYML_API_SECRET":
            return Quota(_number(data, "creditBalance"), "credits", checked)
        used, limit = _number(data, "character_count"), _number(data, "character_limit")
        if used < 0 or limit < 0:
            raise ValueError("Invalid quota")
        reset = data.get("next_character_count_reset_unix")
        resets_at = datetime.fromtimestamp(reset, timezone.utc) if reset is not None else None
        return Quota(max(Decimal(0), limit - used), "characters", checked, used, limit, resets_at)
    except httpx.RequestError:
        raise ValueError("Could not reach the quota API. Check your connection and try again.") from None
    except (KeyError, TypeError, InvalidOperation, OverflowError, OSError):
        raise ValueError("The provider returned an unreadable quota response.") from None
    except ValueError as exc:
        # JSON/number/date parsing can embed raw response values; show only our messages.
        if str(exc).startswith(("Quota unavailable", "Quota check")):
            raise
        raise ValueError("The provider returned an unreadable quota response.") from None
