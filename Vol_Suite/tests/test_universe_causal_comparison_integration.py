import json
from pathlib import Path

from Vol_Suite.run_universe_causal_comparison import run_universe_causal_comparison


def test_missing_runner_output_is_comparison_invalid(tmp_path: Path):
    out = tmp_path / "verdict.json"
    result = run_universe_causal_comparison(
        {"intended_unique_day_denominator": 1},
        {"units": [], "artifact_registry": {}},
        output_path=out,
    )
    assert result["status"] == "CAUSAL_BLOCKED"
    assert json.loads(out.read_text())["status"] == "CAUSAL_BLOCKED"


def test_live_file_is_unchanged_after_runner_import():
    import subprocess
    live = Path(__file__).parents[1] / "dealer_positioning.py"
    base = subprocess.check_output([
        "git", "show", "1bc6fe926e05fff73f3e769f541d356ac8e361b:Vol_Suite/dealer_positioning.py",
    ], cwd=live.parents[1]).decode().replace("\\r\\n", "\\n")
    assert live.read_text(encoding="utf-8").replace("\\r\\n", "\\n") == base
