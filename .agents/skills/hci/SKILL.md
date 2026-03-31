---
name: hci
description: HCI researcher evaluating chat UI and conversational UX — human-likeness, accessibility, cognitive load, emotional intelligence, and interaction design. Use when reviewing conversation flows, UI, or chatbot personality.
---

Evaluate the dental chatbot from an HCI research perspective, covering both surfaces:

**Conversational UX** (how the bot talks):
1. Naturalness — does it sound like a human receptionist, not a corporate bot?
2. Emotional intelligence — does it detect and respond to anxiety, frustration, urgency?
3. Turn-taking — one question at a time? Acknowledges before moving on?
4. Repair — graceful recovery from misunderstandings and errors?
5. Persona consistency — is "Mia" the same character throughout?

**Visual UI** (how the chat looks):
1. Layout and hierarchy — is the chat window the focal point?
2. Message design — scannable, structured data presented well?
3. Accessibility — WCAG 2.1 AA, older demographic friendly?
4. Error states — network failure, timeout, invalid input?

Read `.agents/hci.md` for the full evaluation framework. Read the system prompt and frontend code before assessing.

For every issue found, provide a specific fix — not "make it better" but the exact rewrite or code change.
