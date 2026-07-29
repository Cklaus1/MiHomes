# MiHomes

AI-first multi-home estate management system.

## Tech Stack
- Python 3.11+, SQLite, Typer + Rich (CLI), Claude API (AI features)
- Node.js + Baileys (@whiskeysockets/baileys) for WhatsApp bridge
- Local-first, single-user, CLI interface

## Key Documents
- PRD.md — Full product requirements document
- tasks/todo.md — Current task plan with checkable items
- tasks/lessons.md — Patterns, corrections, and self-improvement rules

## Conventions
- CLI command pattern: `mihomes <entity> <action> [options]`
- AI prioritization uses the SPACE framework (Safety, Presence, Asset Protection, Compliance, Economy)
- Architecture: CLI → Service → Model (thin CLI, business logic in services, persistence in models)
- All entities use slug-based identification (auto-generated from name, user-overridable)

## Workflow Rules

### Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, stop and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity
- Write plan to `tasks/todo.md` with checkable items before starting implementation

### Subagents Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review `tasks/lessons.md` at session start for relevant patterns

### Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

### Worktree Workflow
**Golden rule: nothing is deleted until the primary branch is pushed.** Deletion is
irreversible; a merge that exists only locally is not durable. Follow the order exactly.

- When starting a task that uses a Git worktree:
  1. Create the worktree. If it branched from a stale base, rebase onto my primary branch
     **before editing** — otherwise the merge silently reverts newer work on files I touch.
     Check with `git diff --stat <primary> -- <files I plan to edit>`.
  2. Work inside the worktree; complete the requested tasks and verify the changes.
  3. **Commit ALL work in the worktree, including untracked files.** Run
     `git -C <worktree> status --porcelain` and confirm it prints nothing before moving on.
     Untracked files are invisible to `git log`, `git diff`, and merges — `worktree remove`
     deletes them permanently, with no reflog entry and no recovery.
  4. Switch back to my primary active branch.
  5. Merge the worktree branch into my primary branch using `--no-ff`.
  6. **Push the primary branch.** The work is only durable once this succeeds. If the push
     fails or is denied, stop here and report it — do not proceed to deletion.
  7. Remove the worktree (`git worktree unlock <path>` first if locked), then delete the
     branch with `git branch -d` — never `-D`. `-d` refuses to delete an unmerged branch,
     so it doubles as proof step 5 landed. If `-d` objects, stop and investigate.
  8. Confirm the merge is complete and cleanup is clean.

- Delete the remote branch too (`git push origin --delete <branch>`), but only after step 6
  succeeds — before that, the remote may hold the only copy of the work.
- Verify with `git worktree list` and `git branch` that no `worktree-*` remnants survive.
- On Windows, `git worktree remove` can fail with "Permission denied" / "Device or resource
  busy" when another process holds a handle on the directory. Check `git worktree list` — if
  the worktree is already deregistered, only an empty directory shell remains. That is
  cosmetic, not a failure: report it, and never kill processes or force-delete to clear it.
- Never create a worktree to fix a worktree-cleanup problem — it recreates the mess. If an
  isolation guard blocks the edit, report it and let me apply it.

## Task Management
- **Plan First**: Write plan to `tasks/todo.md` with checkable items before starting
- **Verify Plan**: Check in with user before starting implementation on non-trivial work
- **Track Progress**: Mark items complete as you go
- **Explain Changes**: High-level summary at each step — what changed and why
- **Document Results**: Add review notes to `tasks/todo.md` when done
- **Capture Lessons**: Update `tasks/lessons.md` after any correction

## Core Principles
- **Simplicity First**: Make every change as simple as possible. Minimal code impact.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Only touch what's necessary. Avoid introducing unrelated changes or bugs.

## Phase Tracking
- Phase 1a (Core MVP) — current focus
- See PRD.md Section 10 for full phase breakdown
