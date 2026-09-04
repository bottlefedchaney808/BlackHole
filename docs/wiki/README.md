# FinDev Wiki

How the method works, and how the repo runs it.

This wiki is organized like a method book: the idea first, the standing conventions next, then the objects, the engine, how to read the outputs, and only then the file paths and knobs.

## Contents

1. [The Idea](./01-the-idea.md) — what FinDev is trying to see
2. [Standing Conventions](./02-standing-conventions.md) — locked decisions and their consequences
3. [Objects and Hygiene](./03-objects-and-hygiene.md) — the named measurements and what not to fuse
4. [The Engine](./04-the-engine.md) — the analysis pipeline and its stages
5. [How to Read](./05-how-to-read.md) — agreement, divergence, and the clock
6. [Architecture and Module Map](./06-architecture-and-module-map.md) — repo layout and key file paths
7. [Workflows](./07-workflows.md) — morning scan, dealer-book load, unified run, archive
8. [Knobs and Config](./08-knobs-and-config.md) — `.env`, flags, registry checkboxes
9. [Failure and What to Ignore](./09-failure-and-what-to-ignore.md) — loud gaps, vendor parity, stale data
10. [Extending and Debugging](./10-extending-and-debugging.md) — adding a module, reading a failed run
11. [Glossary](./11-glossary.md) — FinDev terms defined by question, quantity, and sign
12. [The Build](./12-the-build.md) — commit history and artifact discipline

---

Start with [The Idea](./01-the-idea.md) if this is your first pass. If you already know the method and need the file path for a suite, jump to [Architecture and Module Map](./06-architecture-and-module-map.md). For the exact command sequence of a run, see the [Analysis Pipeline Runbook](../guides/FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md).
