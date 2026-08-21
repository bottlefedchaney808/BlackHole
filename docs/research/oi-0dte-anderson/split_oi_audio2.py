import subprocess
from pathlib import Path

src = Path(r"C:\Users\bottl\Downloads\oi_0dte_anderson_audio.mp3")
out_dir = Path(r"C:\Users\bottl\Downloads\oi_clips")
clips = [
    ("00:10:30", 300, "10_riskprem_disagree"),
    ("00:18:40", 150, "11_slippage55"),
    ("00:31:00", 240, "12_fomc_full"),
    ("01:00:00", 150, "13_gex_full"),
]
for start, dur, name in clips:
    dest = out_dir / f"{name}.mp3"
    cmd = [
        "ffmpeg", "-y", "-ss", start, "-t", str(dur), "-i", str(src),
        "-ac", "1", "-ar", "16000", "-b:a", "64k", str(dest),
    ]
    print("running", name)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("wrote", dest, dest.stat().st_size)
