# HANDOFF: purge `trading_journal/` from git history before any public push

**Status:** OPEN — blocking item from the 2026-09-01 CARL public-release audit.
**Owner:** unassigned. Point an agent at this file to finish the job.
**Do not push this repo (any branch, any remote, any fork) publicly until this
is done**, or until you've made a deliberate decision to keep the repo private
instead.

## What happened

`trading_journal/` (149 files: real account balance, a masked account number,
personal names/wagers, desk notes, screenshots) was tracked in git from the
start. During the 2026-09-01 hardening pass
(`chore/repo-public-hardening`, commit `62c9307`), the directory was:

- `git rm -r --cached`'d (removed from the index / future commits)
- added to `.gitignore`
- left untouched on disk (nothing was deleted from the working tree)

That fix stops `trading_journal/` from appearing in *new* commits on that
branch. **It does nothing about commits that already contain it** — those
still exist in the repo's history and are reachable from every branch/clone
that includes them.

## Why this wasn't already finished

Rewriting git history is destructive and irreversible for anyone who has
already cloned/fetched the repo. That decision was deliberately left for you
to make explicitly rather than assumed — the original session tried to ask via
the `ask_user` tool but it failed, and you weren't available to confirm before
the session ended.

## Scope (verified 2026-09-01)

```
git log --oneline --all -- trading_journal | wc -l   # 17 commits touch this path
git log --all --format="%ad %h" --date=short -- trading_journal
# earliest: 2026-08-12 (936c05f)
# latest:   2026-09-01 (62c9307, the untracking commit itself)
```

Branches that currently contain `trading_journal/` history (check before you
start — this list can go stale):

```
git branch -a --format="%(refname:short)"
```

At last check: `master`, `chore/repo-public-hardening`,
`claude/intc-qqq-basket-analysis-7b7ccd`, `claude/reload-skills-1a66fb`,
`docs/copilot-instructions`, `feat-dealer-flows`, `feat/backtest-expansion`,
`feature/swaps_UIexp`, `fix/dashboard-slowapi-startup`, `tune/sentiment-scanner`,
`worktree-market-signals-varsuite-fixes`. Any of these that were ever pushed to
a remote (GitHub, etc.) already have the data in *that* remote's history too —
rewriting local history alone does not fix a remote that already has it.

## Two ways to close this out — pick one

### Option A (recommended if you want to keep any of this project's git history)

Rewrite history to strip `trading_journal/` from every commit on every branch,
using [`git filter-repo`](https://github.com/newren/git-filter-repo) (do NOT
use the legacy `git filter-branch` — it's much slower and has known
correctness footguns).

```bash
# 1. Work from a FRESH CLONE, never your working copy — filter-repo refuses
#    to run on a repo with existing remotes/worktrees unless you pass
#    --force, and you do not want a bad run to corrupt your only copy.
git clone --no-local /path/to/FinancialDevelopment FinancialDevelopment-purge
cd FinancialDevelopment-purge

# 2. Install filter-repo if not already available
pip install git-filter-repo   # or: pipx install git-filter-repo

# 3. Strip the path from every commit on every ref
git filter-repo --path trading_journal --invert-paths

# 4. Verify it's actually gone
git log --oneline --all -- trading_journal   # must print nothing
git rev-list --objects --all | grep trading_journal   # must print nothing

# 5. This is now your new canonical history. If a remote already exists
#    with the old history (e.g. a private GitHub repo you'd been pushing
#    to during development), you must force-push every branch to replace
#    it, and anyone else with a clone must re-clone (their old clones still
#    have the data locally even after you force-push).
git remote add origin <your-repo-url>
git push origin --force --all
git push origin --force --tags
```

After this, re-apply (or re-merge) `chore/repo-public-hardening`'s remaining
fixes on top of the rewritten history if it was created before the rewrite —
commit hashes will all change, so that branch as it exists today won't apply
cleanly to the rewritten repo without a rebase.

### Option B (simplest, no history rewrite)

Keep this repository **private** on GitHub indefinitely, or at least until
you're comfortable it doesn't matter anymore (e.g. the account is closed,
balances are irrelevant, personal notes are stale). A private repo shared only
with collaborators you trust does not need history scrubbed. This sidesteps
the entire problem at the cost of not being able to make the repo public.

If you go this route: still keep the `chore/repo-public-hardening` untracking
fix (already done) so future commits don't add MORE personal data to history
on top of what's already there, and skip Option A entirely.

## After you decide

- If Option A: delete this file as part of the same rewrite (it'll be in the
  final history anyway once merged, and won't reference stale hash lists) or
  update it to say "DONE, purged on <date>, verified no trading_journal
  blobs remain in `git rev-list --objects --all`".
- If Option B: delete this file, or edit it to say "DECIDED: repo stays
  private, no history rewrite planned" so a future agent doesn't reopen this.

## Everything else from the 2026-09-01 audit is already done

See `chore/repo-public-hardening` (commit `62c9307`) for the rest of the
public-release hardening pass: dashboard tunnel auth gate, `tvc_proxy.py`
loopback binding, sentiment schema contract fix + regression test, dead
`yfinance` dependency removed, junk/binary files untracked, root `README.md`
+ `LICENSE` added. This history-purge decision is the **only** open item.
