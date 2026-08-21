import subprocess
from pathlib import Path

src = Path(r"C:\Users\bottl\Downloads\oi_0dte_anderson_audio.mp3")
out_dir = Path(r"C:\Users\bottl\Downloads\oi_clips")
out_dir.mkdir(exist_ok=True)

# start, duration_seconds, name
clips = [
    ("00:03:50", 160, "03_book_at_tail"),
    ("00:06:20", 280, "04_80vol"),
    ("00:13:30", 140, "05_disagree"),
    ("00:15:00", 240, "06_three_vol_factors"),
    ("00:18:55", 80, "07_slippage"),
    ("00:32:10", 180, "08_fomc"),
    ("01:01:40", 180, "09_gex"),
]

for start, dur, name in clips:
    dest = out_dir / f"{name}.mp3"
    cmd = [
        "ffmpeg", "-y", "-ss", start, "-t", str(dur), "-i", str(src),
        "-ac", "1", "-ar", "16000", "-b:a", "64k", str(dest),
    ]
    print("running", name)
    subprocess.run(cmd, check=True)
    print("wrote", dest, dest.stat().st_size)
