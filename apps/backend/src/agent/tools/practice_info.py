"""get_practice_info tool — static practice details, no vector search.

This is the fastest tool — returns a pre-built dict for common questions
like "what are your hours?" or "where are you located?". No ChromaDB
query, no LLM reasoning needed.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Static practice data — sourced from data/knowledge/office_info.md
_PRACTICE_INFO = {
    "name": "Bright Smile Dental",
    "address": "123 Dental Way, Suite 100, Springfield, IL 62701",
    "phone": "(555) 123-4567",
    "email": "info@brightsmile-dental.com",
    "website": "www.brightsmile-dental.com",
    "parking": "Free parking available in the rear lot",
    "hours": {
        "Monday": "8:00 AM - 6:00 PM",
        "Tuesday": "8:00 AM - 6:00 PM",
        "Wednesday": "8:00 AM - 6:00 PM",
        "Thursday": "8:00 AM - 6:00 PM",
        "Friday": "8:00 AM - 6:00 PM",
        "Saturday": "8:00 AM - 6:00 PM",
        "Sunday": "Closed",
    },
    "providers": [
        {
            "name": "Dr. Sarah Smith",
            "title": "General Dentist",
            "experience": "Over 15 years of clinical experience in general and restorative dentistry",
            "availability": "Monday through Saturday",
            "services": "Cleanings, exams, fillings, crowns, root canals, extractions, whitening",
        },
        {
            "name": "Dr. Michael Chen",
            "title": "Orthodontics Consultant",
            "experience": "Board-certified orthodontist specializing in braces and aligners",
            "availability": "Tuesdays only",
            "services": "Orthodontic consultations and follow-ups",
        },
    ],
    "insurance_accepted": [
        "Delta Dental",
        "Aetna",
        "Cigna",
        "Blue Cross Blue Shield",
        "MetLife",
        "Guardian",
        "United Healthcare",
    ],
    "self_pay_options": {
        "discount": "15% discount for uninsured patients paying at time of service",
        "financing": "CareCredit financing available",
        "membership": "Bright Smile Membership Plan: $299/year — includes 2 cleanings, 1 exam, X-rays, 20% off other procedures",
    },
    "payment_methods": [
        "Cash",
        "Checks",
        "All major credit cards",
        "HSA/FSA cards",
    ],
    "billing_notes": "Payment is due at time of service. We file insurance claims on your behalf — you only pay your estimated co-pay or deductible at checkout.",
    "accessibility": [
        "Wheelchair-accessible office and restrooms",
        "Bilingual staff: English and Spanish",
        "Comfort amenities for anxious patients (noise-canceling headphones, TV during procedures)",
        "Complimentary Wi-Fi in the waiting area",
    ],
    "cancellation_policy": "24 hours notice required; sick-day waiver for genuine illness",
}


async def get_practice_info() -> dict:
    """Return static practice information — instant, no DB or vector query."""
    logger.debug("get_practice_info called")
    return _PRACTICE_INFO
