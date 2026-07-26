# CLAUDE.md — Working agreements for the DFX_ate repo

## Plans must be saved as files

**Whenever the user asks for a plan** (implementation plan, design, refactor
strategy, "make a plan", etc.), in addition to presenting it in chat, **save the
full plan to a Markdown file** in the repo.

- Naming convention: `<TOPIC>_PLAN.md` at the repo root (e.g.
  `CRITICAL_FIXES_PLAN.md`, `ARCHITECTURE_PLAN_UI_REFACTOR.md`).
- These files are intentionally git-ignored (`.gitignore` excludes `*PLAN*.md`)
  — they are durable local working docs the user reviews and applies later.
- Always tell the user the path of the file you saved.
- Do **not** implement/apply the plan unless the user explicitly approves it
  ("a plan" means plan only).

## Documentation discipline

- The `DOC/` folder is the canonical reference set. When code changes, update the
  relevant `DOC/` file in the same change (see `DOC/DEVELOPMENT_RULES.md`).

## Project facts (quick reference)

- PySide6 desktop ATE app; entry point `src/main.py`. Source under `src/`
  (`logic/`, `drivers/`, `ui/`, `data/`).
- Roles are **Operator / Technician / Maintenance / Admin** (not "Engineer"),
  defined once in `src/logic/roles.py`. Gate behaviour on a `Capability` from
  that module, never on a role-name comparison. Maintenance may run individual
  steps out of sequence but **must not** generate reports or archive runs
  (spec Rev.1.1 §1.1.4.3).
- Secrets/config are being centralized into `.env` (see `CRITICAL_FIXES_PLAN.md`).
- Simple passwords and no forced password rotation are **intentional** (shared
  factory-floor stations) — do not "fix" that.
