import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\bottl\AppData\Local\hermes\hermes-agent")

from tools.transcription_tools import transcribe_audio

clips_dir = Path(r"C:\Users\bottl\Downloads\oi_clips")
out_dir = Path(r"C:\Users\bottl\Downloads\oi_transcripts")
out_dir.mkdir(exist_ok=True)

for p in sorted(clips_dir.glob("*.mp3")):
    print("TRANSCRIBING", p.name, flush=True)
    result = transcribe_audio(str(p))
    out = out_dir / (p.stem + ".json")
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    text = result.get("transcript") or result.get("error") or str(result)
    txt = out_dir / (p.stem + ".txt")
    txt.write_text(str(text), encoding="utf-8")
    print("DONE", p.name, "ok=", result.get("success"), "chars=", len(str(text)), flush=True)
