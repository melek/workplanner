# Profiles and Path-Based Resolution

Workplanner supports multiple profiles — separate state trees for
separate life contexts (e.g. `work`, `home`, `side-project`). A profile
lives at `~/.workplanner/profiles/<name>/` and owns its own
`config.json`, `backlog.json`, session directory, briefings, and
handoffs.

## How a profile gets selected

Every `wpl` invocation picks a profile before touching state. The
resolver uses a fixed precedence order:

1. **`--profile NAME` CLI flag.** Top-level argparse option. Wins over
   everything else. Use for scripts, ad-hoc inspection, or emergency
   overrides.
2. **`$WPL_PROFILE` env var.** Same effect as the flag, set in the
   environment. Useful for session-scoped overrides (e.g. a tmux pane
   pinned to a specific profile).
3. **Path-based match.** The session's `cwd` is normalized (via `~`
   expansion and `os.path.realpath`) and compared against each
   profile's declared `workspaces: [...]`. The profile with the longest
   matching workspace prefix wins. Matching is path-component aware
   (`/foo/bar` matches `/foo/bar/baz` but not `/foo/barn`).
4. **Single-profile fallback.** If exactly one profile exists and it
   has no `workspaces` declared, resolution succeeds with that profile.
   This keeps the single-setup default frictionless.
5. **Interactive first-run prompt.** If `stdin` is a TTY and
   `$WPL_CHILD != "1"`, the resolver asks whether to associate the cwd
   with an existing profile, create a new profile, or cancel.
6. **Fail with diagnostic.** Lists known profiles, their workspaces,
   and suggests concrete commands to fix.

The global `~/.workplanner/profiles/active` symlink is *not* consulted
by the resolver. It's preserved for backward compatibility with
anything that reads it directly (e.g. shell prompt integrations) and
is still updated by `wpl profile switch`, but it no longer decides
which profile a command operates against.

## Why path-based resolution

The previous scheme read the `active` symlink on every `wpl`
invocation. Two concurrent Claude sessions in different profiles could
race — either one running `wpl profile switch` flipped the symlink
globally, and the other's next command silently operated against the
wrong profile. Path-based resolution anchors each session to its cwd,
so concurrent sessions in different directories don't interfere.

## Workspaces

Each profile's `config.json` may carry a `workspaces: [...]` list of
absolute filesystem paths. A workspace declares "when a session runs
from this path (or any subdirectory), use this profile." Paths are
normalized on write.

Rules:

- **Longest-match-wins.** A profile with `/home/alice/projects/foo`
  beats a profile with `/home/alice` for any cwd inside
  `/home/alice/projects/foo/**`. This is intentional and useful: it
  lets you keep a broad "catch-all" profile alongside narrower
  project-specific profiles.
- **No identical-path overlaps.** If two profiles claim the exact
  same workspace path, the resolver can't choose. Commands that write
  workspaces (`create`, `associate`) reject this and exit non-zero.
  `wpl profile validate` surfaces any pre-existing overlaps.
- **Path-component matching.** `/foo/bar` does not falsely match
  `/foo/barn` — the resolver enforces separator boundaries.

## Commands

```
wpl profile list                              Show profiles, workspaces, and cwd match.
wpl profile create NAME [--workspace PATH]    Create a profile, optionally with workspaces.
wpl profile associate NAME PATH               Add a workspace path to an existing profile.
wpl profile disassociate NAME PATH            Remove a workspace path from a profile.
wpl profile whoami                            Which profile does cwd resolve to, and how?
wpl profile validate                          Report overlaps and missing-workspace warnings.
wpl profile migrate                           Interactively associate paths with existing profiles.
wpl profile switch NAME                       [deprecated] Flip the `active` symlink.
wpl profile active                            Print the name the `active` symlink points to.
wpl profile delete NAME                       Delete a profile.
```

## First-run flow for an unassociated cwd

When a command runs from a cwd that doesn't match any profile and
single-profile fallback doesn't apply, the resolver falls into one of
two branches:

- **Interactive (TTY).** Prompts:
  ```
  Current directory '/path/here' isn't associated with any profile.

  Existing profiles:
    - work: /Users/alice/work, /Users/alice/projects
    - home: /Users/alice/personal

  Options:
    [1] Associate this directory with an existing profile
    [2] Create a new profile here
    [3] Cancel
  ```
  Choice `1` asks for a profile name and runs `associate`. Choice `2`
  prompts for a name and creates a profile with the cwd as its first
  workspace. The resolver then returns the newly-resolved profile and
  the original command continues.

- **Non-interactive (no TTY, or `WPL_CHILD=1`).** Fails with a
  machine-readable diagnostic listing known profiles and their
  workspaces, plus the three concrete commands the user can run to
  recover (`associate`, `--profile`, `$WPL_PROFILE`). Skills catch
  this error and re-surface it to the user instead of hanging on
  stdin.

Skills that boot on session start (e.g. `/start`) should call
`wpl profile whoami` and check for a resolution failure as part of
their health check, prompting the user to associate before invoking
state-mutating commands.

## Migration from symlink-based resolution

Existing installations keep working without action:

- **Single profile.** If you have exactly one profile and haven't
  declared workspaces, the single-profile fallback resolves to it
  from any cwd. Nothing to do.
- **Multiple profiles.** `wpl profile list` warns for any profile
  without workspaces. Run `wpl profile associate <name> <path>` for
  each profile, or `wpl profile migrate` for an interactive walk
  through every profile that needs paths.

The `active` symlink stays in place. You can keep using
`wpl profile switch` if something external depends on it (e.g. a
shell prompt reading the symlink directly), but switching is no
longer part of normal workflow — profiles resolve from cwd
automatically.

## Escape hatches

- `--profile NAME` — one-shot override, e.g.
  `wpl --profile home status` to peek at another profile.
- `WPL_PROFILE=NAME` — session-scoped override, e.g. for a tmux pane
  dedicated to a specific profile regardless of cwd.
- `WPL_CHILD=1` — suppresses the interactive first-run prompt.
  Subprocesses inherit it; set manually for scripts that shouldn't
  block on stdin.
- `WPL_ROOT=/some/dir` — redirects the entire state tree. Useful for
  tests and sandbox setups.
