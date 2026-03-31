"""notify_staff tool — alert staff about emergencies, special requests, or escalations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# In-memory store for demo — production would use a webhook, email, or queue
_notifications: list[dict] = []


async def notify_staff(
    type: str,
    message: str,
    patient_id: str | None = None,
) -> dict:
    """Log a staff notification and return confirmation.

    In production this would send to Slack, email, or a staff dashboard.
    For the demo it logs and stores in memory.
    """
    notification = {
        "type": type,
        "message": message,
        "patient_id": patient_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _notifications.append(notification)

    if type == "emergency":
        logger.warning("🚨 EMERGENCY STAFF ALERT: %s (patient=%s)", message, patient_id)
    else:
        logger.info("Staff notification [%s]: %s (patient=%s)", type, message, patient_id)

    return {
        "status": "sent",
        "type": type,
        "message": f"Staff has been notified about this {type}.",
        "details": message,
    }


def get_notifications() -> list[dict]:
    """Return all stored notifications (for testing/admin)."""
    return list(_notifications)
