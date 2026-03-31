---
name: architect
description: Analyze system architecture, project structure, and best practices. Use when you need architectural guidance, want to evaluate project organization, or plan structural changes.
argument-hint: "[focus-area or question]"
---

You are being asked to perform an architectural analysis. The user wants guidance on system architecture, project structure, and/or best practices.

If arguments are provided, focus on: $ARGUMENTS

## What to Do

1. **Explore the codebase** — Understand the project structure, entry points, dependencies, and data flow before forming opinions
2. **Identify the architectural pattern** — What pattern is in use? Is it appropriate for this project?
3. **Evaluate structure** — Is the directory layout clean? Are concerns separated? Does it scale?
4. **Check best practices** — SOLID, DRY, KISS, YAGNI. Are they followed? Where are they violated?
5. **Assess dependencies** — Are they minimal, up-to-date, and well-abstracted?
6. **Provide actionable recommendations** — Be specific about what to change, why, and in what order

## Output Structure

Organize findings into:
- **Architecture Overview** — What exists today
- **Strengths** — What's working well (keep these)
- **Critical Issues** — Must-fix problems
- **Important Improvements** — Should-fix items
- **Suggestions** — Nice-to-have refinements
- **Action Plan** — Prioritized steps to implement changes

Every recommendation must include a concrete reason. No vague "best practice" justifications.
