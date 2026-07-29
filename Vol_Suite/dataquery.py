"""dataquery.py -- scratch/one-off query helper.

Originally a 6-line yfinance snippet pulling a single GME put quote. yahoo has
been purged from this suite, so this is rewritten against PotatoHedge/ThetaData.
Run directly: `python dataquery.py`.
"""
from thetadata_client import ThetaDataController, strike_to_theta


def main():
    td = ThetaDataController()
    try:
        root, exp, strike, right = "GME", "20261016", 10.0, "P"
        q = td.option_snapshot_quote(root, exp, strike, right)
        print(f"{root} {exp} {strike:.1f}{right}: "
              f"bid={q.get('bid')} ask={q.get('ask')} "
              f"iv={q.get('implied_vol')}")
    finally:
        td.close()


if __name__ == "__main__":
    main()
