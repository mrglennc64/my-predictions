"""Contest Edge CLI.

  python scan.py negrisk              # negative-risk arb monitor
  python scan.py wallets              # smart-wallet consensus basket
  python scan.py divergence [sport]   # book line vs Polymarket (free ESPN odds)
  python scan.py screener             # poly-maker-style market quality screen
  python scan.py crypto               # BTC/ETH up-or-down window divergence
"""
import sys


def run_negrisk():
    from src.scanners import negrisk
    print("Scanning negRisk events for over/under-round...")
    opps = negrisk.scan(threshold=0.02)
    if not opps:
        print("No neg-risk arbitrage above 2% right now (normal — they close fast).")
        return
    for o in opps:
        print(f"\n[{o.edge_pct}% edge] {o.event_title}  ({o.direction}, "
              f"{o.n_outcomes} outcomes, liq ${o.liquidity:,})")
        print(f"  YES ask sum: {o.ask_sum}  |  YES bid sum: {o.bid_sum}")
        print(f"  https://polymarket.com/event/{o.slug}")


def run_wallets():
    from src.scanners import wallets
    print("Building smart-wallet basket from monthly PnL leaderboard...")
    consensus = wallets.scan(top_n_wallets=30, min_wallets=2, min_position_usd=200)
    if not consensus:
        print("No 3+-wallet consensus positions found among top traders.")
        return
    for c in consensus[:20]:
        print(f"\n{c.n_wallets} sharp wallets | ${c.total_value:,.0f} combined")
        print(f"  {c.market_title} -> {c.outcome}")
        print(f"  avg entry {c.avg_price}  now {c.cur_price}")


def run_divergence(sport: str = "mlb"):
    from src.scanners import divergence
    print(f"Scanning {sport.upper()}: book line (ESPN, de-vigged) vs Polymarket...")
    results = divergence.scan(sport=sport, threshold=0.04)
    if not results:
        print("No divergences >= 4% found (or no games matched).")
        return
    for d in results:
        print(f"\n[{d.edge_pct}% edge] {d.game} — {d.outcome}")
        print(f"  book prob {d.book_prob}  vs  PM price {d.pm_price}")
        print(f"  https://polymarket.com/market/{d.pm_slug}")


def run_screener():
    from src.scanners import screener
    print("Screening markets (reward/volume/calm score, poly-maker style)...")
    for s in screener.scan(top_n=20):
        flag = "R" if s.incentivized else " "
        print(f"  [{s.score:>7.1f}]{flag} {s.price:.2f}  vol24 ${s.volume24h:>10,}  "
              f"spread {s.spread:.3f}  {s.question[:70]}")


def run_crypto():
    from src.scanners import crypto
    print("Scanning live up-or-down windows vs Coinbase spot...")
    signals = crypto.scan(threshold=0.05)
    if not signals:
        print("No active-window divergences >= 5% right now.")
        return
    for s in signals:
        print(f"\n[{s.edge:+.3f}] {s.title}  ({s.seconds_left}s left)")
        print(f"  spot lead {s.lead_pct:+.3f}%  model P(Up) {s.model_p_up}  "
              f"PM P(Up) {s.pm_p_up}")
        print(f"  https://polymarket.com/event/{s.slug}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "negrisk"
    if cmd == "negrisk":
        run_negrisk()
    elif cmd == "wallets":
        run_wallets()
    elif cmd == "divergence":
        run_divergence(sys.argv[2] if len(sys.argv) > 2 else "mlb")
    elif cmd == "screener":
        run_screener()
    elif cmd == "crypto":
        run_crypto()
    elif cmd == "crypto-watch":
        from src.scanners import crypto_watch
        crypto_watch.run(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        print(__doc__)
