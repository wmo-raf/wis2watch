# WIS2Watch

## Agent skills

### Issue tracker

Issues live as GitHub issues on `wmo-raf/wis2watch`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles. Three exist in this tracker, under their own names; `needs-info` and `ready-for-human` do not exist at all. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Development stack

Changes reflect on save once `COMPOSE_FILE` is set in `.env`. Rebuilding the
image for every edit is not necessary. `make` lists the shortcuts -- `make up`,
`make test`, `make logs-ingest`, `make frontend`. See
`docs/development.md`.

## Adding new features or fixing bugs

**IMPORTANT**: When you work on a new feature or bug fix, create a git branch first. Then work on changes in that
branch for the remainder of the session.
