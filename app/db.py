"""Ledger database — six tables per the v1 spec, SQLAlchemy Core over SQLite.

The predictions table is APPEND-ONLY: no UPDATE statement for it exists
anywhere in this codebase. That constraint is what makes the ledger auditable.
"""
import os

from sqlalchemy import (Column, Float, ForeignKey, Integer, MetaData, Table,
                        Text, create_engine)

DB_URL = os.environ.get("CONTEST_EDGE_DB", "sqlite:///ledger.sqlite")

metadata = MetaData()

teams = Table(
    "teams", metadata,
    Column("team_id", Text, primary_key=True),        # 'mlb_NYY'
    Column("sport", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("elo", Float, nullable=False, default=1500.0),
    Column("elo_updated", Text),
)

games = Table(
    "games", metadata,
    Column("game_id", Text, primary_key=True),        # source-native id
    Column("sport", Text, nullable=False),
    Column("start_time", Text, nullable=False),       # UTC ISO
    Column("home_team", Text, ForeignKey("teams.team_id")),
    Column("away_team", Text, ForeignKey("teams.team_id")),
    Column("status", Text, nullable=False, default="scheduled"),
    Column("home_score", Integer),
    Column("away_score", Integer),
    Column("meta", Text),                             # JSON: probables, park
)

odds_snapshots = Table(
    "odds_snapshots", metadata,
    Column("snapshot_id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", Text, ForeignKey("games.game_id")),
    Column("book", Text, nullable=False),
    Column("market", Text, nullable=False),
    Column("home_odds", Float, nullable=False),       # decimal odds
    Column("away_odds", Float, nullable=False),
    Column("fetched_at", Text, nullable=False),
)

model_versions = Table(
    "model_versions", metadata,
    Column("model_id", Text, primary_key=True),       # 'elo_mlb_v1.0'
    Column("sport", Text, nullable=False),
    Column("description", Text),
    Column("params", Text),                           # JSON
    Column("created_at", Text, nullable=False),
)

predictions = Table(                                   # APPEND-ONLY
    "predictions", metadata,
    Column("prediction_id", Integer, primary_key=True, autoincrement=True),
    Column("game_id", Text, ForeignKey("games.game_id")),
    Column("model_id", Text, ForeignKey("model_versions.model_id")),
    Column("p_home", Float, nullable=False),
    Column("market_p_home", Float),                   # de-vigged, at freeze time
    Column("frozen_at", Text, nullable=False),        # must be < start_time
)

grades = Table(
    "grades", metadata,
    Column("prediction_id", Integer, ForeignKey("predictions.prediction_id"),
           primary_key=True),
    Column("outcome", Integer, nullable=False),       # 1 home won, 0 away won
    Column("brier", Float, nullable=False),
    Column("market_brier", Float),
    Column("clv", Float),
    Column("graded_at", Text, nullable=False),
)


crypto_signals = Table(                                # APPEND-ONLY signals; outcome
    "crypto_signals", metadata,                        # filled once, then immutable
    Column("signal_id", Integer, primary_key=True, autoincrement=True),
    Column("slug", Text, nullable=False, unique=True),  # one signal per window
    Column("symbol", Text, nullable=False),
    Column("captured_at", Text, nullable=False),       # UTC ISO
    Column("seconds_left", Integer, nullable=False),
    Column("end_ts", Integer, nullable=False),
    Column("lead_pct", Float, nullable=False),
    Column("model_p_up", Float, nullable=False),
    Column("pm_p_up", Float, nullable=False),
    Column("outcome", Integer),                        # 1 up, 0 down; NULL pending
    Column("model_brier", Float),
    Column("pm_brier", Float),
    Column("graded_at", Text),
    # paper trading: filled at capture when |edge| clears the trade threshold
    Column("trade_side", Text),                        # 'up' | 'down' | NULL no trade
    Column("trade_price", Float),                      # actual CLOB ask paid
    Column("trade_pnl", Float),                        # settled at resolution
)


tennis_ratings = Table(
    "tennis_ratings", metadata,
    Column("player", Text, primary_key=True),          # accent-folded lowercase
    Column("display", Text, nullable=False),
    Column("tour", Text, nullable=False),              # 'atp' | 'wta'
    Column("rating", Float, nullable=False),
    Column("rd", Float, nullable=False),
    Column("vol", Float, nullable=False),
    Column("last_ts", Integer, nullable=False),
    Column("matches", Integer, nullable=False),
)

tennis_predictions = Table(                            # APPEND-ONLY like the rest
    "tennis_predictions", metadata,
    Column("pred_id", Integer, primary_key=True, autoincrement=True),
    Column("slug", Text, nullable=False, unique=True), # PM event slug
    Column("title", Text, nullable=False),
    Column("p1", Text, nullable=False),                # first-listed player
    Column("p2", Text, nullable=False),
    Column("model_p1", Float),                         # NULL if a player unrated
    Column("market_p1", Float, nullable=False),        # displayed price, side 1
    Column("market_p2", Float),                         # displayed price, side 2 —
    #                              real quote (NOT 1-market_p1); for edge backtest
    Column("frozen_at", Text, nullable=False),
    Column("event_start", Text),                       # match start (ISO Z);
    #                                                    healthcheck: frozen < this

    Column("outcome", Integer),                        # 1 = p1 won; NULL pending
    Column("model_brier", Float),
    Column("market_brier", Float),
    Column("graded_at", Text),
)


lane_predictions = Table(                              # APPEND-ONLY, all lanes
    "lane_predictions", metadata,
    Column("row_id", Integer, primary_key=True, autoincrement=True),
    Column("lane", Text, nullable=False),              # wnba|weather|ufc|soccer
    Column("mslug", Text, nullable=False, unique=True),  # market-level slug
    Column("title", Text, nullable=False),
    Column("side", Text, nullable=False),              # what P refers to
    Column("model_p", Float),                          # NULL = no model (mirror)
    Column("market_p", Float, nullable=False),
    Column("frozen_at", Text, nullable=False),
    Column("outcome", Integer),                        # 1 side won; NULL pending
    Column("model_brier", Float),
    Column("market_brier", Float),
    Column("graded_at", Text),
)


weather_watch = Table(                                 # near-resolution lag log
    "weather_watch", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("mslug", Text, nullable=False, unique=True),
    Column("city", Text, nullable=False),
    Column("state", Text, nullable=False),             # DEAD | PROVEN
    Column("obs_max", Float, nullable=False),          # station max at detection
    Column("boundary", Float, nullable=False),
    Column("market_p_detect", Float, nullable=False),  # price when fact locked
    Column("detected_at", Text, nullable=False),
    Column("priced_at", Text),                         # when market caught up
    Column("lag_s", Integer),                          # the entire edge, measured
)


trigger_events = Table(                                # APPEND-ONLY snapshot log
    "trigger_events", metadata,                        # one row per poll per locked
    Column("id", Integer, primary_key=True, autoincrement=True),  # bucket; timeline
    Column("mslug", Text, nullable=False),             # is the ordered sequence
    Column("city", Text, nullable=False),
    Column("icao", Text, nullable=False),              # settlement station
    Column("side", Text, nullable=False),              # the bucket label
    Column("state", Text, nullable=False),             # PROVEN | DEAD
    Column("kind", Text, nullable=False),              # LOCK | SNAPSHOT | CONCEDE
    Column("boundary", Float, nullable=False),
    Column("unit", Text, nullable=False),              # celsius | fahrenheit
    Column("obs_max", Float, nullable=False),          # station max at snapshot
    Column("fair", Float, nullable=False),             # 1.0 PROVEN, 0.0 DEAD
    Column("market_p", Float, nullable=False),         # yes-price at snapshot
    Column("best_bid", Float),                         # certain side's token
    Column("best_ask", Float),
    Column("mispricing_cents", Float),                 # (fair - best_ask)*100
    Column("edge_dollars", Float),                     # fillable $ better than fair
    Column("depth_json", Text),                        # walked book at snapshot
    Column("snapshot_at", Text, nullable=False),       # UTC ISO
)


trigger_grades = Table(                                # APPEND-ONLY, one per bucket
    "trigger_grades", metadata,                        # the verdict on each lock:
    Column("id", Integer, primary_key=True, autoincrement=True),  # did it settle
    Column("mslug", Text, nullable=False, unique=True),  # the way we locked it?
    Column("city", Text),
    Column("side", Text),
    Column("state", Text),                             # PROVEN | DEAD, as locked
    Column("boundary", Float),
    Column("unit", Text),
    Column("locked_obs_max", Float),                   # station max at lock
    Column("locked_at", Text),                         # first LOCK snapshot_at
    Column("outcome", Integer),                        # resolved_outcome: 1 | 0
    Column("lock_correct", Integer, nullable=False),   # 1 if lock matched result
    Column("graded_at", Text, nullable=False),         # UTC ISO
)


trigger_reconciliations = Table(                       # APPEND-ONLY, one per event
    "trigger_reconciliations", metadata,               # our peak METAR read vs the
    Column("id", Integer, primary_key=True, autoincrement=True),  # bucket that
    Column("event_slug", Text, nullable=False, unique=True),  # actually settled —
    Column("city", Text),                              # the empirical margin.
    Column("icao", Text),
    Column("unit", Text),                              # celsius | fahrenheit
    Column("our_obs_max", Float),                      # peak we read at lock
    Column("won_lo", Float),                           # settled bucket bounds =
    Column("won_hi", Float),                           # Wunderground published high
    Column("delta_deg", Float),                        # obs_max - won_hi (+ = high)
    Column("reconciled_at", Text, nullable=False),     # UTC ISO
)


station_health = Table(                                 # APPEND-ONLY, one row per
    "station_health", metadata,                        # station per probe run —
    Column("id", Integer, primary_key=True, autoincrement=True),  # tracks whether
    Column("run_at", Text, nullable=False),            # our settlement instrument
    Column("icao", Text, nullable=False),              # (US ASOS/METAR) is degrading
    Column("city", Text),                              # under NOAA/NWS budget cuts:
    Column("is_us", Integer),                          # outages, missing obs, dropped
    Column("n_obs_6h", Integer),                       # 6hr-max groups, stale reads.
    Column("staleness_min", Float),                    # mins since latest ob (None=none)
    Column("has_6hr_max", Integer),                    # 1sTTT group seen (US signal)
    Column("ok", Integer),                             # fetch returned usable data
    Column("checked_at", Text, nullable=False),
)


depth_reality = Table(                                  # APPEND-ONLY, one per lock
    "depth_reality", metadata,                         # — did the below-fair depth
    Column("id", Integer, primary_key=True, autoincrement=True),  # we SAW at lock
    Column("mslug", Text, nullable=False, unique=True),  # actually TRADE before it
    Column("city", Text),                              # conceded, or vanish unfilled
    Column("event_slug", Text),                        # (spoof-and-withdraw)?
    Column("state", Text),                             # PROVEN | DEAD
    Column("displayed_shares", Float),                 # below-fair asks at lock
    Column("traded_shares", Float),                    # below-fair BUYs that printed
    Column("phantom_ratio", Float),                    # 1 - traded/displayed (LOWER
    Column("lock_at", Text),                           #  bound on phantom-ness)
    Column("concede_at", Text),
    Column("computed_at", Text, nullable=False),
)


tennis_depth = Table(                                   # APPEND-ONLY snapshots —
    "tennis_depth", metadata,                          # is the +EV tennis edge
    Column("id", Integer, primary_key=True, autoincrement=True),  # actually
    Column("slug", Text, nullable=False),              # FILLABLE? (the question
    Column("title", Text),                             # that killed weather)
    Column("edge_player", Text),                       # the model-edge side we'd buy
    Column("price_paid", Float),                       # best ask on that side
    Column("model_fair", Float),                       # model prob of that side
    Column("edge_cents", Float),                       # (fair - price) * 100
    Column("ev_edge_dollars", Float),                  # sum (fair-ask)*size below fair
    Column("fillable_notional", Float),                # $ deployable at +EV prices
    Column("n_levels", Integer),
    Column("best_ask", Float),
    Column("captured_at", Text, nullable=False),
)


def get_engine():
    return create_engine(DB_URL)


def init_db():
    engine = get_engine()
    metadata.create_all(engine)
    _migrate(engine)
    return engine


def _migrate(engine):
    """create_all never alters existing tables — add late columns by hand."""
    from sqlalchemy import inspect, text
    existing = {c["name"] for c in inspect(engine).get_columns("crypto_signals")}
    additions = [("trade_side", "TEXT"), ("trade_price", "REAL"),
                 ("trade_pnl", "REAL")]
    missing = [(n, t) for n, t in additions if n not in existing]
    if missing:
        with engine.begin() as conn:
            for name, ddl in missing:
                conn.execute(text(
                    f"ALTER TABLE crypto_signals ADD COLUMN {name} {ddl}"))

    tp_cols = {c["name"] for c in inspect(engine).get_columns("tennis_predictions")}
    with engine.begin() as conn:
        if "event_start" not in tp_cols:
            conn.execute(text(
                "ALTER TABLE tennis_predictions ADD COLUMN event_start TEXT"))
        if "market_p2" not in tp_cols:
            conn.execute(text(
                "ALTER TABLE tennis_predictions ADD COLUMN market_p2 REAL"))
