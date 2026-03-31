---
name: checklist
description: Create and manage task checklists for project milestones, deliverables, and demo preparation. Use when user wants to track progress, plan work, or organize TODO items.
---

# Checklist & Task Tracker

## Usage

When invoked, create or update a checklist file at `CHECKLIST.md` in the project root. Track tasks with markdown checkboxes that the user can check off manually or ask Claude to update.

## Format

```markdown
# Project Checklist

## [Milestone Name] — Due: [date]

### [Category]
- [ ] Task description
- [x] Completed task
- [ ] ~Cancelled task~ (strikethrough if no longer needed)

**Status**: X/Y complete
```

## Checklist Categories for This Project

### Analysis
- Data loading & cleaning
- EDA & outlier detection
- Feature engineering
- Global LightGBM + SHAP
- Per-physician analysis
- Additional analyses (learning curve, complexity, scheduling)
- Results export (CSV, JSON, markdown)

### Demo Preparation
- Key findings summarized
- Plots finalized (publication quality)
- Narrative/talking points written
- Demo format chosen (notebook / dashboard / slides / live run)
- Demo built and tested
- Dry run completed

### Code Quality
- Sanity check passed (`/sanity-check`)
- Numbers consistent across outputs
- Plots render correctly
- Script runs end-to-end without errors

### Submission
- Report written
- Code committed and pushed
- README up to date
- All output files generated

## Workflow

1. **Create**: `/checklist` — generates initial checklist from project state
2. **Update**: "mark X as done" or "add task Y to the checklist"
3. **Review**: "show checklist status" — prints progress summary
4. **Archive**: Move completed milestones to bottom with completion date
