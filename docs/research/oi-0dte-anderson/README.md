# OI 0DTE — Mark Anderson / Lakha Video Research

Research data from a video on 0DTE options / open-interest (OI) dynamics, featuring
Mark Anderson (Lakha). Content the trading journal references as
`knowledge_0dte_anderson_lakha_20260820.md`.

## What this is

A YouTube video was downloaded (yt-dlp) and split into topical audio clips that were
then transcribed. The full video download was **incomplete** (a broken 492MB `.part`
file — yt-dlp `.ytdl` metadata confirms the download never finished), so **only the
audio track and the derived clips/transcripts survive**. The video itself was never
usable and has been trashed.

## What survived

- **Transcripts (raw JSON + plain text)** — `oi_transcripts/`
  - 13 clips covering: book-at-tail behavior, 80-vol reporting, disagreement with
    risk-premium framing, three volume factors, slippage, FOMC positioning, GEX
    (gamma exposure), risk-premium disagreement
- **Transcripts (re-wrapped, 90-col)** — `oi_transcripts_wrap/` — same content, wrapped
  for easy reading
- **Audio clips (11 mp3, ~15MB total)** — outside the repo, see below
- **Full audio track (34MB mp3)** — outside the repo, see below
- **image.png** — screenshot/thumbnail from the video page

## Where things live

| Item | Location |
|------|----------|
| Transcripts + scripts + README | `docs/research/oi-0dte-anderson/` (this dir, in repo) |
| Audio clips (`oi_clips/`) | `C:\Users\bottl\Videos\oi-0dte-anderson\oi_clips\` (outside repo) |
| Full audio (`oi_0dte_anderson_audio.mp3`) | `C:\Users\bottl\Videos\oi-0dte-anderson\` (outside repo) |
| Broken `.mp4.part` download junk | `_trash/2026-08-21/` (in repo, gitignored trash) |

## Helper scripts (what each does)

- `register_intc_sources.py` — registers a list of Intel (INTC) news/press-release
  URLs into the `grounded-citations` skill's `sources.py` for the research profile
  (background context for the INTC-related trading research).
- `split_oi_audio.py` — splits the full audio mp3 into the first 7 topical clips
  (`03_book_at_tail` … `09_gex`) with ffmpeg (mono 16kHz, 64kbps).
- `split_oi_audio2.py` — splits 4 more clips (`10_riskprem_disagree`,
  `11_slippage55`, `12_fomc_full`, `13_gex_full`).
- `transcribe_oi.py` — transcribes every mp3 in `oi_clips/` via Hermes
  transcription tools; writes JSON + txt per clip.
- `transcribe_oi2.py` — transcribes the second batch of 4 clips; writes JSON + a
  90-column wrapped txt into `oi_transcripts_wrap/`.
- `wrap_transcripts.py` — re-wraps all JSON transcripts into 90-column txt files in
  `oi_transcripts_wrap/`.

## Notes

- Original download was incomplete: 492MB `.part` + fragment + `.ytdl` metadata were
  unusable garbage and were moved to `_trash/2026-08-21/` (never `rm`'d, per repo
  hygiene rules). Only audio + clips + transcripts survive.
- Scripts hardcode original paths (`C:\Users\bottl\Downloads\...`); they are kept for
  provenance/reproducibility, not for re-running as-is.
