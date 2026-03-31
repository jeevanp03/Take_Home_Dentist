---
name: code-review
description: Perform a thorough code review on files or recent changes. Checks correctness, security, readability, performance, testing, and design patterns.
argument-hint: "[file-path, PR-number, or 'recent' for latest changes]"
---

You are being asked to perform a code review. Be thorough, specific, and constructive.

If arguments are provided, review: $ARGUMENTS
If no arguments, review the most recent uncommitted changes (run `git diff` and `git diff --cached`).

## Review Process

1. **Identify what to review** — Use the arguments or check git diff for recent changes
2. **Read the code and its context** — Don't review in isolation; understand surrounding code
3. **Check each dimension:**
   - **Correctness** — Logic bugs, edge cases, off-by-one errors, unhandled errors
   - **Security** — Injection, hardcoded secrets, missing input validation, data exposure
   - **Readability** — Naming, complexity, dead code, comment quality
   - **Performance** — Unnecessary work, wrong data structures, resource leaks, N+1 queries
   - **Testing** — Coverage, edge cases, test quality
   - **Design** — Patterns, abstraction level, duplication, complexity impact

4. **Provide specific feedback** with file paths and line numbers
5. **Suggest concrete fixes** — Show the better version, don't just criticize
6. **Call out good work** — Reinforce good patterns

## Output Structure

- **Review Summary** — What was reviewed, overall assessment
- **Critical (Must Fix)** — Bugs, security issues, data loss risks
- **Warnings (Should Fix)** — Maintenance problems, quality degradation
- **Suggestions (Consider)** — Clarity, performance, consistency improvements
- **Good Patterns** — What was done well
