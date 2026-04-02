import re
from datetime import datetime

# Pfad zur Logdatei
logfile = "logs/trades.log"

# Muster für ENTRY und EXIT
def parse_logs(logfile):
    entry_re = re.compile(r"(?P<dt>[\d\- :,]+) - ENTRY \| Trade #(?P<id>\d+) \| (?P<symbol>\w+/\w+) \| Entry: (?P<entry_price>[\d.]+)€ \| Size: (?P<size>[\d.]+)")
    exit_re = re.compile(r"(?P<dt>[\d\- :,]+) - EXIT \| Trade #(?P<id>\d+) \| (?P<symbol>\w+/\w+) \| Exit: (?P<exit_price>[\d.]+)€ \| Entry: (?P<entry_price>[\d.]+)€ \| P&L: (?P<pl_eur>[-\d.]+)€ \((?P<pl_pct>[-\d.]+)%\)")
    trades = {}
    with open(logfile, encoding="utf-8") as f:
        for line in f:
            m = entry_re.match(line)
            if m:
                tid = int(m.group("id"))
                trades[tid] = {
                    "symbol": m.group("symbol"),
                    "entry_time": datetime.strptime(m.group("dt"), "%Y-%m-%d %H:%M:%S,%f"),
                    "entry_price": float(m.group("entry_price")),
                    "size": float(m.group("size")),
                }
            m = exit_re.match(line)
            if m:
                tid = int(m.group("id"))
                if tid in trades:
                    trades[tid].update({
                        "exit_time": datetime.strptime(m.group("dt"), "%Y-%m-%d %H:%M:%S,%f"),
                        "exit_price": float(m.group("exit_price")),
                        "pl_eur": float(m.group("pl_eur")),
                        "pl_pct": float(m.group("pl_pct")),
                    })
    return trades

# Parameter
MIN_PROFIT_TARGET = 0.02  # 2%
MIN_ABS_PROFIT = 0.25     # 0,25 EUR
ROUNDTRIP_COST = 0.0062   # 0,62%

def main():
    trades = parse_logs(logfile)
    classic, abs25 = 0, 0
    classic_sum, abs25_sum = 0, 0
    classic_trades, abs25_trades = [], []
    for t in trades.values():
        if "pl_eur" not in t:
            continue
        # Klassisch: min_profit_target + Kosten
        min_pct = MIN_PROFIT_TARGET + ROUNDTRIP_COST
        if t["pl_pct"] >= min_pct * 100 and t["pl_eur"] > 0:
            classic += 1
            classic_sum += t["pl_eur"]
            classic_trades.append(t)
        # Absolut: ab 0,25 EUR nach Kosten
        if t["pl_eur"] >= MIN_ABS_PROFIT:
            abs25 += 1
            abs25_sum += t["pl_eur"]
            abs25_trades.append(t)
    print(f"Klassisch (min_profit_target): {classic} Trades, Gesamtgewinn: {classic_sum:.2f} €")
    print(f"Absolut (ab 0,25 €):         {abs25} Trades, Gesamtgewinn: {abs25_sum:.2f} €")
    print(f"Durchschnittlicher Gewinn klassisch: {classic_sum/classic if classic else 0:.2f} €")
    print(f"Durchschnittlicher Gewinn absolut:   {abs25_sum/abs25 if abs25 else 0:.2f} €")

if __name__ == "__main__":
    main()
