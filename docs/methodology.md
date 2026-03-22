# Workplanner Methodology

## What This Document Is

This document describes the productivity philosophy behind workplanner — the principles that guide its design and behavior. The methodology is opinionated by default but fully customizable. It's the "reference runbook" — the starting point that works well for busy and neurodivergent knowledge workers.

The engine (CLI, state machine) is methodology-agnostic. This methodology is one way to use it. Users can adjust every aspect through conversational config changes, recorded in the decision log.

## Core Principles

### 1. Capture Exhaustively, Decide Once

Sweep all inboxes automatically. The human never manually checks sources — the system does it and presents a unified view. This eliminates the "did I miss something?" anxiety loop that drains executive function before real work begins.

**Lineage:** GTD's capture/collect phase. The key insight is that *incomplete capture* is more stressful than *a long list* — the brain keeps cycling on what it might have missed.

### 2. Two Bookends, Nothing Between

`/start` and `/eod` are the only user-facing ceremonies. During the day, task transitions happen through atomic CLI commands invoked by the LLM. The user's cognitive overhead is: look at the agenda, do the work, say "done" or "next."

**Lineage:** Time-blocking's boundary rituals, with mid-day friction eliminated. The LLM handles task pickup transitions, dashboard updates, and state management transparently.

### 3. Timebox, Don't Estimate

"30 minutes" means "spend 30 minutes advancing this," not "finish in 30 minutes." This reframe eliminates estimation anxiety and the paralysis of "this is too big to start." Any task can be timeboxed to 15-30 minutes regardless of total scope.

**Lineage:** Pomodoro's fixed-interval philosophy applied to task estimation. The psychological barrier to starting drops dramatically when the commitment is bounded.

### 4. Carryover Earns Its Place

Deferred tasks return at medium priority, not top-of-list. They compete for the agenda on merit alongside fresh items. Data shows carryover tasks complete at ~41% vs ~81% for manually-added tasks — forcing them first doesn't improve this. They were deferred for a reason.

**Lineage:** Bullet journal's migration concept — the act of re-evaluating whether to carry something forward is itself valuable signal.

### 5. Force the Reckoning

After N deferrals (configurable, default 3), the system demands a decision: break it down, delegate, drop, timebox to backlog, or consciously keep deferring. No silent accumulation. This prevents zombie tasks from haunting the agenda indefinitely.

**Lineage:** GTD's "someday/maybe" review, made automatic and threshold-triggered rather than relying on weekly review discipline.

### 6. Deterministic Plumbing, Flexible Policy

The engine (CLI, state machine, atomic writes) enforces structural rules mechanically. The LLM handles judgment calls (triage, context gathering, briefings). Neither does the other's job.

This is the **dual-ergonomics principle**: the LLM doesn't drift on state management (no hallucinated task IDs, no invalid transitions), and the CLI doesn't try to reason about what work means. Verbose CLI errors guide the LLM back on track. The result is reliable for the LLM *and* trustworthy for the human.

**Lineage:** Unix philosophy (do one thing well) applied to human-AI collaboration. The constraint makes both parties better.

### 7. Graceful Degradation Everywhere

Any data source can fail. Any MCP can be absent. The system always produces *something* — a shorter agenda, a manual fallback, a note about what's missing. Assembly never blocks on a single source failure.

**Lineage:** Resilience engineering. For a daily planning tool, "no plan" is worse than "partial plan." The system is designed to be useful even with only a calendar and manual task entry.

## Neurodivergent Design Rationale

The methodology specifically addresses executive function challenges:

- **Decision fatigue:** Automated triage and prioritization reduce the number of decisions before work starts. The agenda is presented as "here's what to do," not "here's everything — you decide."
- **Task initiation:** Pre-plan briefings reduce the activation energy to start a task. Context is pre-gathered, approach is pre-drafted. The user's job is to validate and begin, not to figure out where to start.
- **Working memory:** The dashboard externalizes the full work state. No need to remember what's pending, what's blocked, or how much time is left.
- **Time blindness:** Budget calculations and EOD targets make time visible. Protected blocks prevent over-scheduling.
- **Completion momentum:** Small tasks (5-15m) are included early in the agenda to build momentum. The timebox philosophy means even large tasks have a "completable" increment.
- **Shame-free deferral:** Deferring is a first-class action, not a failure. The reckoning mechanism is constructive ("let's figure out what to do with this") not punitive.

## Customization

The methodology is modified conversationally through the workplanner. The user says what they want; the workplanner changes config and records the decision in the decision log.

Common adjustments and their config surface:

| Want | Config change |
|------|--------------|
| Shorter days | `triage.filter.task_cap` (default: 10) |
| Different priority weights | `triage.source_priority` mapping |
| Longer/shorter timeboxes | `triage.estimates` mapping |
| More/fewer deferrals before reckoning | `triage.deferrals.reckoning_threshold` |
| Strict time-blocking | Add protected blocks, reduce task cap |
| No carryover priority penalty | Set `triage.source_priority.carryover` to `"high"` |

All changes are recorded in `decision-log.json` with rationale. The system can explain any deviation from defaults and suggest reversions when patterns change.

## Framework Lineage

| Framework | What we borrow | How we adapt it |
|-----------|----------------|----------------|
| GTD | Capture everything, process to zero, weekly review | Automated capture via MCP sweeps; forced reckoning replaces manual review |
| Pomodoro | Fixed time intervals reduce resistance | Applied to task estimation (timeboxes) not work sessions |
| Bullet Journal | Migration as triage; deliberate carry-forward | Carryover at medium priority; deferral reckoning at threshold |
| Time Blocking | Boundaries make time visible | Two bookends only; mid-day is fluid with budget tracking |
| Resilience Engineering | Graceful degradation under failure | Every data source is optional; assembly never blocks on one failure |
