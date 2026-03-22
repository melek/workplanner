# Future Work

Features that are designed but deferred until prerequisites are met or usage patterns emerge.

## Cross-Profile Task References

**What:** Tasks in one profile's session could reference work from another profile's backlog. A workday task like "call plumber at lunch" originated from the home backlog would carry a `profile: "home"` tag so artifacts file back to the right place.

**Why deferred:** Cross-profile tasks require atomic writes across two profile directories, introduce ambiguous artifact ownership, and complicate undo semantics. The simpler "switch profiles" model should be validated first.

**Prerequisites:**
- Profile system in active use with multiple profiles
- Clear user demand for cross-profile visibility
- Design for cross-profile undo and artifact routing

## Pattern Reckoning from Archived Sessions

**What:** The decision log enables pattern-based config suggestions. The system analyzes archived sessions to notice patterns like "you've been over-budget 4 of the last 5 days" and suggests adjustments: "Want to lower your task cap from 10 to 8?"

**Why deferred:** Requires historical data from archived sessions that won't exist until the tool has been used for a while. The decision log CRUD is useful on its own immediately; pattern detection layers on top of accumulated data.

**Prerequisites:**
- Archived session data from at least 1-2 weeks of use
- Metrics extraction from archived sessions (completion rate, over/under budget, deferral frequency)
- A suggestion engine that maps patterns to config recommendations

## Pre-Plan Auto-Integration into /start

**What:** Running `/pre-plan` automatically as part of `/start`, either always or when time allows. Currently, pre-planning is a separate manual step.

**Why deferred:** This is a UX decision that should be informed by actual usage patterns. Some users may find auto-pre-planning too slow (it launches parallel research agents). Others may want it every day.

**Prerequisites:**
- Usage data on how often `/pre-plan` is used manually
- Timing data on pre-plan duration vs morning assembly duration
- Opt-in config flag (e.g., `auto_preplan: true`)
