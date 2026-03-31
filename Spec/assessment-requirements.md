# Assessment Requirements — Key Additions Beyond the Spec

These are the requirements from the assessment prompt that are **not already covered** in `dental-chatbot-spec.md` and need to be addressed.

## Submission Requirements (non-code)

1. **Video demo** — recording of deploying/building + testing, explain thinking and considerations
2. **Clear commit history** — not just one big commit; show iterative progress
3. **`.env` template file** — `.env.example` with placeholder values
4. **README with run instructions** — setup, install, how to test

## Prioritization Section (added by candidate)

Must include a written section explaining:
- What you prioritized and why
- What's load-bearing for core UX vs. nice-to-have
- How you thought about risk, failure modes, and patient impact
- Framed as "shipping production for 100s of locations, 10k+ conversations/day"

## Assessment Criteria to Optimize For

| Category | What they're looking for |
|---|---|
| **Builder Mindset** | Rapid implementation, innovation, end-to-end ownership |
| **Quality** | Natural dialog, edge case handling, code quality, documentation |
| **UX Design** | Logical flow, consistent personality, accessibility |
| **Workflow** | Tool call implementation, staff notifications, escalation, feedback loop |

## Specific Scenarios That Must Work

These are explicitly listed and will be tested:

### New Patient Registration
- Collect: full name, phone, DOB, insurance name
- Schedule first appointment
- Appointment types: Cleaning, General checkup, Emergency
- Emergency: get summary of issue + notify staff

### Existing Patient
- Verify identity (name + phone OR name + DOB)
- Follow-up scheduling
- Reschedule appointment
- Cancel appointment

### Complex Family Scheduling
- Book multiple family members (kids, spouse)
- Back-to-back appointment coordination

### General Inquiries
- Insurance: "accepts all major dental insurance plans"
- No insurance: self-pay options, membership/financing
- Location and hours: "Open 8am to 6pm Mon-Sat"

### Edge Cases
- Subjective dates: "later next week", "early next month"
- Times that don't work: offer alternatives
- Sequential SMS-style messaging (short fragmented messages)
- No insurance patients

## Free Tier Constraint

Assessment says use free-tier services:
- LLM: Gemini (already chosen) ✅
- DB: SQLite local (already chosen) ✅
- No paid infrastructure required for the demo

## What Differentiates a Strong Submission

From the assessment criteria, what moves the needle:
1. **Conversation feels human** — #1 priority per the doc
2. **Edge cases handled thoughtfully** — not just happy path
3. **End-to-end ownership** — everything works together, not disconnected pieces
4. **Clear architecture decisions** — documented and explained
5. **Innovation** — "creative approaches to solving the problem"
6. **Feedback loop** — "mechanism for improvement based on interactions"
