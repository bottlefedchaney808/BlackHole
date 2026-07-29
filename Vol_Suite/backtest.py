from thetadata_client import ThetaDataController
td = ThetaDataController()
rows = td.option_bulk_hist_greeks("SPY", "20261016", "20260210", "20260210")
print(len(rows), rows[0] if rows else "no rows")
td.close()