import subprocess, sys
from pathlib import Path
s = Path(r"C:\Users\bottl\AppData\Local\hermes\profiles\research\skills\research\grounded-citations\scripts\sources.py")
urls = [
    "https://www.intc.com/news-events/press-releases/detail/1776/intel-reports-second-quarter-2026-financial-results",
    "https://www.intc.com/news-events/press-releases/detail/1779/intel-announces-upsize-and-pricing-of-20-billion-common",
    "https://www.sec.gov/Archives/edgar/data/50863/000119312526346806/d117670d8k.htm",
    "https://www.sec.gov/Archives/edgar/data/50863/000119312526341325/d82791dfwp.htm",
    "https://www.cnbc.com/2026/08/10/intel-intc-stock-offering-ai.html",
    "https://www.reuters.com/legal/transactional/intel-launches-15-billion-share-sale-turnaround-rally-lifts-stock-2026-08-10/",
    "https://www.reuters.com/business/intel-forecasts-upbeat-quarterly-revenue-profit-strong-ai-driven-server-chip-2026-07-23/",
    "https://www.reuters.com/markets/deals/qualcomm-approached-intel-about-takeover-recent-days-wsj-reports-2024-09-20/",
    "https://www.wsj.com/business/deals/qualcomm-approached-intel-about-a-takeover-in-recent-days-fa114f9d",
    "https://www.reuters.com/technology/qualcomm-has-explored-acquiring-pieces-intel-chip-design-business-sources-say-2024-09-06/",
    "https://www.calcalistech.com/ctechnews/article/rjtxq0ajkg",
    "https://www.cnbc.com/2025/08/22/intel-goverment-equity-stake.html",
    "https://www.reuters.com/business/autos-transportation/tesla-ceo-musk-says-company-plans-use-intels-14a-process-terafab-2026-04-22/",
]
subprocess.run([sys.executable, str(s), "reset"], check=False)
cmd = [sys.executable, str(s), "add"] + urls
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout)
print(r.stderr)
r2 = subprocess.run([sys.executable, str(s), "list"], capture_output=True, text=True)
print(r2.stdout)
