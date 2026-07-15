# vva-python-template

VVA's starter template for Python 3.13 projects — and the **golden baseline** for our Claude Code setup. It ships two stacks in one repo: a minimal, correctly-wired application skeleton, and an agent-automation layer that makes Claude Code sessions fast, guarded, and self-documenting.

New here? Do this, then read the diagrams below:

```bash
./scripts/fresh-start.sh        # once: detach from the template (drops its git history, re-inits on `dev`)
python -m venv .venv && . .venv/Scripts/activate
pip install -r requirements-dev.txt
pre-commit install
python main.py                  # run
python -m pytest                # test
claude                          # start an agent session — everything below kicks in automatically
```

Documentation lives in [`openwiki/quickstart.md`](openwiki/quickstart.md) (humans *and* agents start there) and the always-on agent rules in [`.claude/rules/`](.claude/rules). This README is the onboarding map, not the manual.

## The application stack

Small on purpose: a layered skeleton with the boring parts wired correctly, plus one example feature to copy and then delete.

![The application stack](assets/application-stack.png)

<details>
<summary>Diagram source (mermaid) — regenerate with <code>npx -y @mermaid-js/mermaid-cli -i chart.mmd -o assets/application-stack.png -b white -s 2</code></summary>

```mermaid
flowchart TD
    subgraph app["Application"]
        MAIN["main.py — entry point: configure() logging, then run"]
        EX["app/example.py — demo feature (delete when real code arrives)"]
        subgraph core["app/core/ — bottom layer, imports nothing above it"]
            CFG["config.py — env settings, one named accessor per key"]
            CST["constants.py — fixed design decisions"]
            LOG["logging_config.py — one handler, INFO/DEBUG, JSON option"]
        end
    end
    subgraph gate["Quality gate — identical in pre-commit, Claude hooks, and Bitbucket CI"]
        G["Ruff lint+format · mypy · pytest (offline) · Bandit · detect-secrets · pip-audit"]
    end
    TESTS["tests/ — offline pytest suite"]
    MAIN --> EX --> core
    TESTS -.verifies.-> app
    app -.every commit & PR.-> gate
```

</details>

Two rules carry most of the design: **settings vs constants** (varies per environment → accessor in `config.py`; fixed design choice → constant in `constants.py`; `os.environ` is never read anywhere else) and **`app/core/` never imports from the rest of `app/`** — that's what keeps the test suite offline.

## The Claude Code stack

The `.claude/` directory turns a session into a guarded feedback loop. Everything below is repo-contained — clone + Claude Code is the whole install.

![The Claude Code automation loop](assets/claude-code-stack.png)

<details>
<summary>Diagram source (mermaid) — regenerate with <code>npx -y @mermaid-js/mermaid-cli -i chart.mmd -o assets/claude-code-stack.png -b white -s 2</code></summary>

```mermaid
flowchart TD
    START(["Session starts"]) --> BRIEF["SessionStart hook<br/>injects repo brief: branch, dirty files, wiki freshness"]
    BRIEF --> WORK{{"You prompt / Claude works"}}

    WORK -- "prompt names a file" --> ROUTE1["wiki_router<br/>injects which wiki page covers it"]
    WORK -- "Claude reads/edits a file" --> ROUTE2["edit_router<br/>same routing, once per file per session"]
    WORK -- "Claude writes Python" --> FMT["ruff_format<br/>auto lint+format on write"]
    WORK -- "a command fails" --> RB["runbook_matcher<br/>injects the known fix from openwiki/troubleshooting.md"]
    WORK -- "turn ends" --> SWEEP["ruff_check_all<br/>Ruff + mypy-daemon sweep, only when code changed, capped output"]

    WORK -- "git commit" --> GUARD{"guard_commit"}
    GUARD -- "--no-verify" --> BLOCK1["blocked — gate is not optional"]
    GUARD -- "wiki stale for these changes" --> BLOCK2["blocked — update wiki or stage it along<br/>(escape: OPENWIKI_SKIP=1 + reason)"]
    GUARD -- "code change, no semver bump" --> BLOCK3["blocked — bump pyproject version<br/>(escape: VERSION_OK=1)"]
    GUARD -- "all clear" --> COMMIT(["commit lands"])

    subgraph knowledge["Self-maintaining docs (OpenWiki)"]
        WIKI["openwiki/ — pages bound to source globs with verified git heads"]
        AUTO["/openwiki autopilot — init · update stale pages · prune"]
    end
    ROUTE1 -.reads.-> WIKI
    ROUTE2 -.reads.-> WIKI
    COMMIT -.source changes stale pages.-> AUTO -.rewrites & re-verifies.-> WIKI
```

</details>

The pieces, in one table:

| Piece | Where | What it does |
|---|---|---|
| **Rules** | `.claude/rules/` | Always-on conventions: workflow discipline, settings/constants, code style, test style |
| **Skills** | `.claude/skills/` | `/openwiki` (docs autopilot), `/verify` (full gate + auto-fix), `/ship` (commit→push→PR the team way, guard-aware), `/new-setting` (config checklist), `/runbook-add` (teach the error-matcher a new fix), `/tune` (audit recent sessions for friction, propose allowlist/rules/runbook fixes) |
| **Hooks** | `.claude/hooks/` | The seven automations in the diagram — briefs, routing, formatting, runbook hints, cached sweeps, commit guards, secret-file protection |
| **OpenWiki** | `openwiki/` | Agent-oriented docs where every page declares the source files it covers; staleness is *computed*, and commits are gated on it |
| **Permissions** | `.claude/settings.json` | Pre-approved gate/git/toolkit commands (no prompt fatigue); reads of `.env` denied |
| **Headroom** (optional) | `scripts/claude-headroom.*` | Start a session behind a context-compression proxy for token-heavy work |

### What this means for you, day one

1. Run `claude` in the repo and just work — conventions are enforced, not memorized.
2. Trust the injections: the session brief, wiki routing, and runbook hints exist so neither you nor the agent rediscovers known things.
3. When a commit gets blocked, the message says exactly why and how to proceed — the escapes (`OPENWIKI_SKIP=1`, `VERSION_OK=1`) are deliberate, explained exceptions, not workarounds.
4. Ship with `/ship`: Conventional Commits, semver bump, `dev` as trunk (there is no `main`), PRs into `dev`.
5. Docs stay honest by construction: change code → the covering wiki page goes stale → `/openwiki` fixes it → the commit guard keeps everyone honest in between.
6. The setup improves itself: diagnosed a tricky error? `/runbook-add`. A session felt slow or naggy? `/tune` finds the friction and proposes the config fix.
