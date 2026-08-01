"""Tools -- expandable framework of standalone quant-finance tools.

Each tool under Tools/tools/ is a small, independent plugin that can pull a
validated suite_context.json handoff object from ANY of the four sibling
suites' run outputs (Vol_Suite, Options_Suite, VaR_Tools_Simulations,
sentiment-scanner) and do something useful with it, without being locked
into whichever suite happened to produce that context.

See Tools/README.md for the plugin pattern and a non-technical walkthrough,
Tools/context_loader.py for how contexts are discovered/loaded, and
Tools/registry.py for the list of installed tools.

This __init__ also makes sure the repo root (FinancialDevelopment/) is on
sys.path, the same way orchestrator.py bootstraps its own imports -- so
`import Tools` works whether the caller launched from the repo root, from
inside Tools/, or from a dashboard process with its own cwd.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
