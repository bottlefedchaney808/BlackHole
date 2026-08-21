import json
import sys
from pathlib import Path
import textwrap

sys.path.insert(0, r"C:\Users\bottl\AppData\Local\hermes\hermes-agent")
from tools.transcription_tools import transcribe_audio

clips_dir = Path(r"C:\Users\bottl\Downloads\oi_clips")
out_dir = Path(r"C:\Users\bottl\Downloads\oi_transcripts")
wrap_dir = Path(r"C:\Users\bottl\Downloads\oi_transcripts_wrap")
wrap_dir.mkdir(exist_ok=True)

for name in ["10_riskprem_disagree", "11_slippage55", "12_fomc_full", "13_gex_full"]:
    p = clips_dir / f"{name}.mp3"
    print("TRANSCRIBING", p.name, flush=True)
    result = transcribe_audio(str(p))
    (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    t = result.get("transcript") or result.get("error") or str(result)
    wrapped = "\n".join(textwrap.wrap(t, width=90))
    (wrap_dir / f"{name}.txt").write_text(wrapped, encoding="utf-8")
    print("DONE", name, "ok=", result.get("success"), "chars=", len(t), flush=True)
