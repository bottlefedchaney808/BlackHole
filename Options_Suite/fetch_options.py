"""Standalone yahoo option-chain -> CSV dumper. yfinance has been purged from this
project, so this utility is inert until reimplemented on PotatoHedge (or yahoo is
reinstalled as an explicit backup). Import is guarded so it never crashes anything
that happens to import this module."""
import csv
from datetime import datetime

try:
    import yfinance as yf
    _YF = True
except Exception:
    yf = None
    _YF = False

def fetch_and_save_options(symbol, expiration_date):
    if not _YF:
        raise RuntimeError(
            "fetch_options.fetch_and_save_options requires yfinance, which has been "
            "removed from this project. Reimplement on PotatoHedge or reinstall yfinance."
        )
    stock = yf.Ticker(symbol)

    if expiration_date not in stock.options:
        print(f"Invalid expiration date. Available options dates: {stock.options}")
        return

    options = stock.option_chain(expiration_date)

    calls_data = []
    puts_data = []

    for call in options.calls.itertuples():
        call_data = {
            "Contract Name": call.contractSymbol,
            "Last Trade Date (EDT)": call.lastTradeDate.strftime("%m/%d/%Y %I:%M %p") if call.lastTradeDate else "",
            "Strike": call.strike,
            "Last Price": call.lastPrice,
            "Bid": call.bid,
            "Ask": call.ask,
            "Change": call.change,
            "% Change": call.percentChange,
            "Volume": call.volume if call.volume is not None else "-",
            "Open Interest": call.openInterest if call.openInterest is not None else "-",
            "Implied Volatility": f"{call.impliedVolatility * 100:.2f}%" if call.impliedVolatility else "-"
        }
        calls_data.append(call_data)

    for put in options.puts.itertuples():
        put_data = {
            "Contract Name": put.contractSymbol,
            "Last Trade Date (EDT)": put.lastTradeDate.strftime("%m/%d/%Y %I:%M %p") if put.lastTradeDate else "",
            "Strike": put.strike,
            "Last Price": put.lastPrice,
            "Bid": put.bid,
            "Ask": put.ask,
            "Change": put.change,
            "% Change": put.percentChange,
            "Volume": put.volume if put.volume is not None else "-",
            "Open Interest": put.openInterest if put.openInterest is not None else "-",
            "Implied Volatility": f"{put.impliedVolatility * 100:.2f}%" if put.impliedVolatility else "-"
        }
        puts_data.append(put_data)

    with open(f"{symbol}_calls.csv", mode="w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=calls_data[0].keys())
        writer.writeheader()
        writer.writerows(calls_data)

    with open(f"{symbol}_puts.csv", mode="w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=puts_data[0].keys())
        writer.writeheader()
        writer.writerows(puts_data)

    print(f"Options data for {symbol} on {expiration_date} saved to CSV files.")
