"""Weather near-resolution lag-logger (paper-mode, strictly read-only).

Hardens the original src/lanes/weather_watch.py to the v1 measurement spec:
  * settlement source PARSED from each market's own rules, not hardcoded
    (the hardcoded map silently dropped Beijing — the #1-volume market)
  * order-book depth + fillable EDGE-DOLLARS at every lock, not just a flag
  * whole thing testable: pure lock / METAR-max / local-day modules

No orders, no wallet, no keys. It logs the lag between a bucket becoming
mechanically decided by the observed station max and the market repricing.
"""
