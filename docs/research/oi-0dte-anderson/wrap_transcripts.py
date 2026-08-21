from pathlib import Path
import json
import textwrap

src = Path(r"C:\Users\bottl\Downloads\oi_transcripts")
out = Path(r"C:\Users\bottl\Downloads\oi_transcripts_wrap")
out.mkdir(exist_ok=True)

for p in sorted(src.glob("*.json")):
    data = json.loads(p.read_text(encoding="utf-8"))
    t = data.get("transcript") or data.get("error") or ""
    wrapped = "\n".join(textwrap.wrap(t, width=90))
    dest = out / (p.stem + ".txt")
    dest.write_text(wrapped, encoding="utf-8")
    print(p.stem, "chars", len(t), "lines", wrapped.count("\n")+1)
