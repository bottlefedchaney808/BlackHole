# Monte Carlo American Pricer Greeks

American option pricing, Greeks, and IV across CRR, Leisen-Reimer, Newton-Raphson,
SABR, Vanna-Volga, Monte Carlo (LSM/Heston), and Barone-Adesi-Whaley, compared
against live ThetaData quotes.

## Setup

From the repo root:
```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv/bin/python -m pip install -r requirements.txt           # Linux/Mac
```

Set `THETADATA_CF_ACCESS_CLIENT_ID` and `THETADATA_CF_ACCESS_CLIENT_SECRET` in the
root `.env` (see `.env.example`) — these are required for live spot/rate/dividend
and option-chain data.

## Running

```bash
options_suite.bat   # Windows
options_suite.sh    # Linux/Mac
```

See `REPORT.md` for the CLI menu (model runs, "Run all models & compare", full
chain evaluation) and reporting output, `NOTES_chain_evaluation.md` for the
chain-evaluation/reports path, and `PROJECT_ROADMAP.md` for known issues and
session history.
