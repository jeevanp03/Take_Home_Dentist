"""Tool registry — maps tool names to handlers, schemas, and Gemini declarations.

Usage::

    declarations = get_tool_declarations()   # pass to build_config(tools=...)
    result = await execute_tool("get_available_slots", {"date_start": "2026-04-01", ...}, db=db, session_id=sid)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google.genai import types
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from src.schemas import (
    BookAppointmentInput,
    CancelAppointmentInput,
    CreatePatientInput,
    GetAvailableSlotsInput,
    GetPatientAppointmentsInput,
    GetPracticeInfoInput,
    LookupPatientInput,
    NotifyStaffInput,
    RescheduleAppointmentInput,
    SearchKnowledgeBaseInput,
    SearchPastConversationsInput,
    UpdatePatientInput,
)
from src.agent.tools.knowledge import search_knowledge_base
from src.agent.tools.conversations import search_past_conversations
from src.agent.tools.patients import lookup_patient, create_patient, update_patient
from src.agent.tools.appointments import (
    get_available_slots,
    book_appointment,
    reschedule_appointment,
    cancel_appointment,
    get_patient_appointments,
)
from src.agent.tools.notifications import notify_staff
from src.agent.tools.practice_info import get_practice_info

logger = logging.getLogger(__name__)

_TOOL_TIMEOUT = 10.0  # seconds per tool execution


# ---------------------------------------------------------------------------
# Tool registry — each entry maps a tool name to its handler, schema, and
# which kwargs it needs injected (db, session_id).
# ---------------------------------------------------------------------------

_ToolEntry = dict[str, Any]

TOOL_REGISTRY: dict[str, _ToolEntry] = {
    "search_knowledge_base": {
        "handler": search_knowledge_base,
        "schema": SearchKnowledgeBaseInput,
        "inject": [],
        "description": "Search the dental knowledge base for practice info, procedures, insurance, or clinical dental information.",
    },
    "search_past_conversations": {
        "handler": search_past_conversations,
        "schema": SearchPastConversationsInput,
        "inject": [],
        "description": "Search past conversation history for a specific patient.",
    },
    "lookup_patient": {
        "handler": lookup_patient,
        "schema": LookupPatientInput,
        "inject": ["db", "session_id"],
        "description": "Look up an existing patient by name and phone number or date of birth.",
    },
    "create_patient": {
        "handler": create_patient,
        "schema": CreatePatientInput,
        "inject": ["db", "session_id"],
        "description": "Register a new patient. Requires full name and phone number. DOB and insurance are optional.",
    },
    "update_patient": {
        "handler": update_patient,
        "schema": UpdatePatientInput,
        "inject": ["db"],
        "description": "Update a patient's date of birth or insurance information.",
    },
    "get_available_slots": {
        "handler": get_available_slots,
        "schema": GetAvailableSlotsInput,
        "inject": ["db"],
        "description": "Get available appointment time slots for a date range. Can filter by morning or afternoon.",
    },
    "book_appointment": {
        "handler": book_appointment,
        "schema": BookAppointmentInput,
        "inject": ["db", "session_id"],
        "description": "Book an appointment for a patient at a specific time slot. Requires patient_id, slot_id, and appointment type.",
    },
    "reschedule_appointment": {
        "handler": reschedule_appointment,
        "schema": RescheduleAppointmentInput,
        "inject": ["db"],
        "description": "Reschedule an existing appointment to a new time slot.",
    },
    "cancel_appointment": {
        "handler": cancel_appointment,
        "schema": CancelAppointmentInput,
        "inject": ["db"],
        "description": "Cancel a scheduled appointment and free the time slot.",
    },
    "get_patient_appointments": {
        "handler": get_patient_appointments,
        "schema": GetPatientAppointmentsInput,
        "inject": ["db"],
        "description": "Get a patient's upcoming scheduled appointments.",
    },
    "notify_staff": {
        "handler": notify_staff,
        "schema": NotifyStaffInput,
        "inject": [],
        "description": "Send a notification to dental office staff. Use for emergencies, special requests, or escalations.",
    },
    "get_practice_info": {
        "handler": get_practice_info,
        "schema": GetPracticeInfoInput,
        "inject": [],
        "description": "Get practice details: hours, location, phone, providers, insurance accepted, self-pay options. No parameters needed.",
    },
}


# ---------------------------------------------------------------------------
# Gemini function declarations (derived from registry)
# ---------------------------------------------------------------------------

def _extract_enum_values(annotation) -> list[str] | None:
    """If ``annotation`` is a Literal or Optional[Literal], return enum values."""
    import typing

    origin = getattr(annotation, "__origin__", None)

    # Plain Literal["a", "b", "c"]
    if origin is typing.Literal:
        args = getattr(annotation, "__args__", ())
        if args and all(isinstance(a, str) for a in args):
            return list(args)
        return None

    # Union type (Optional[X] = X | None) — unwrap and check inner
    args = getattr(annotation, "__args__", None)
    if args:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _extract_enum_values(non_none[0])

    return None


def _schema_to_gemini_params(schema_cls: type[BaseModel]) -> types.Schema | None:
    """Convert a Pydantic model to a Gemini Schema for function parameters."""
    fields = schema_cls.model_fields
    if not fields:
        return None

    properties: dict[str, types.Schema] = {}
    required: list[str] = []

    for name, field_info in fields.items():
        description = field_info.description or ""

        # Check for Literal (enum) types first
        enum_values = _extract_enum_values(field_info.annotation)
        if enum_values is not None:
            properties[name] = types.Schema(
                type="STRING",
                description=description,
                enum=enum_values,
            )
        else:
            properties[name] = types.Schema(
                type="STRING",
                description=description,
            )

        # Required check applies to ALL fields (enum or not)
        if field_info.is_required():
            required.append(name)

    return types.Schema(
        type="OBJECT",
        properties=properties,
        required=required if required else None,
    )


def get_tool_declarations() -> list[types.Tool]:
    """Return Gemini Tool declarations for all registered tools.

    Pass the result to ``build_config(tools=...)``.
    """
    declarations: list[types.FunctionDeclaration] = []

    for name, entry in TOOL_REGISTRY.items():
        params = _schema_to_gemini_params(entry["schema"])
        declarations.append(
            types.FunctionDeclaration(
                name=name,
                description=entry["description"],
                parameters=params,
            )
        )

    return [types.Tool(function_declarations=declarations)]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

async def execute_tool(
    name: str,
    args: dict[str, Any],
    *,
    db: Session | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Validate args via Pydantic, call the handler, return a JSON dict.

    Injected kwargs (db, session_id) are passed through to handlers that
    need them. Per-tool timeout prevents hangs.
    """
    if name not in TOOL_REGISTRY:
        logger.error("Unknown tool: %s", name)
        return {"error": f"Unknown tool: {name}"}

    entry = TOOL_REGISTRY[name]
    schema_cls: type[BaseModel] = entry["schema"]
    handler = entry["handler"]
    inject: list[str] = entry["inject"]

    # --- Validate args via Pydantic ---
    try:
        validated = schema_cls(**args)
    except ValidationError as exc:
        error_msg = "; ".join(
            f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors()
        )
        logger.warning("Validation error for %s: %s", name, error_msg)
        return {"error": f"Invalid arguments for {name}: {error_msg}"}

    # --- Build kwargs: validated fields + injected deps ---
    kwargs = validated.model_dump()
    if "db" in inject:
        if db is None:
            return {"error": f"Tool {name} requires a database session."}
        kwargs["db"] = db
    if "session_id" in inject:
        if session_id is None:
            return {"error": f"Tool {name} requires a session_id."}
        kwargs["session_id"] = session_id

    # --- Execute with timeout ---
    try:
        result = await asyncio.wait_for(
            handler(**kwargs),
            timeout=_TOOL_TIMEOUT,
        )
        logger.info("Tool %s executed successfully.", name)
        return result if isinstance(result, dict) else {"result": str(result)}

    except asyncio.TimeoutError:
        logger.error("Tool %s timed out after %.0fs.", name, _TOOL_TIMEOUT)
        return {"error": f"Tool {name} timed out. Please try again."}
    except Exception as exc:
        logger.error("Tool %s raised %s: %s", name, type(exc).__name__, exc)
        return {"error": f"Tool {name} encountered an error: {exc}"}
