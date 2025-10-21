# strategy/deployable_algo.py
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Dict
import threading
import pytz

import numpy as np

from brokers.angelone import AngelBroker


# --- Configuration Section ---
# These will be passed in from the Streamlit app
# DRY_RUN = True
# API_KEY = "YOUR_API_KEY"
# ... etc.


def round_to_nearest(x, step): return int(round(float(x) / step) * step)


@dataclass
class WaveCfg:
    step_pts: int = 50
    spread_wing: int = 100
    min_credit: float = 8.0
    max_consecutive_adds: int = 2
    lot_qty: int = 1
    strike_step: int = 50
    daily_trade_cutoff: str = "15:00"
    flatten_time: str = "15:25"


@dataclass
class WaveFill:
    side: str
    shortK: int
    longK: int
    qty: int
    credit: float
    ref_level: float
    oid_short: str
    oid_long: str


class WaveExtractor:
    def __init__(self, broker: AngelBroker, om, cfg: WaveCfg, logger):
        self.broker, self.om, self.cfg, self.logger = broker, om, cfg, logger
        self.anchor = None
        self.pending_up = None
        self.pending_down = None
        self.stack_up: deque[WaveFill] = deque()
        self.stack_down: deque[WaveFill] = deque()
        self.cons_up = 0
        self.cons_down = 0
        self.enabled = True

    def start(self):
        S = self.broker.fut_ltp()
        s = self.cfg.step_pts
        self.anchor = round_to_nearest(S, s)
        self.pending_up = self.anchor + s
        self.pending_down = self.anchor - s
        self.logger.info(f"Wave Extractor started. Anchor: {self.anchor}")

    def stop_new_entries(self):
        self.enabled = False

    def resume_new_entries(self):
        self.enabled = True

    def on_tick(self, _tick=None):
        if self.anchor is None: self.start()
        S = self.broker.fut_ltp()
        hhmm = self.broker.now_hhmm()
        if hhmm >= self.cfg.flatten_time:
            self._flatten_all()
            return
        if hhmm >= self.cfg.daily_trade_cutoff:
            self.enabled = False

        if self.enabled:
            self._try_up(S)
            self._try_down(S)
        self._try_reversal(S)

    def _try_up(self, S):
        if S >= self.pending_up and self.cons_up < self.cfg.max_consecutive_adds:
            self.logger.info("Wave trying UP trade...")
            shortK = round_to_nearest(S, self.cfg.strike_step)
            longK = shortK + self.cfg.spread_wing
            credit, _ = self.broker.best_credit_call_spread(shortK, longK, self.cfg.lot_qty)
            if credit and credit >= self.cfg.min_credit:
                oid_s, oid_l = self.om.sell_call_spread(shortK, longK, self.cfg.lot_qty)
                self.stack_up.append(
                    WaveFill("UP", shortK, longK, self.cfg.lot_qty, credit, self.pending_up, oid_s, oid_l))
                self.cons_up += 1
                if self.cons_up < self.cfg.max_consecutive_adds:
                    self.pending_up += self.cfg.step_pts
                self.pending_down = max(self.pending_down, self.pending_up - self.cfg.step_pts)
            else:
                self.pending_up += self.cfg.step_pts

    def _try_down(self, S):
        if S <= self.pending_down and self.cons_down < self.cfg.max_consecutive_adds:
            self.logger.info("Wave trying DOWN trade...")
            shortK = round_to_nearest(S, self.cfg.strike_step)
            longK = shortK - self.cfg.spread_wing
            credit, _ = self.broker.best_credit_put_spread(shortK, longK, self.cfg.lot_qty)
            if credit and credit >= self.cfg.min_credit:
                oid_s, oid_l = self.om.sell_put_spread(shortK, longK, self.cfg.lot_qty)
                self.stack_down.append(
                    WaveFill("DOWN", shortK, longK, self.cfg.lot_qty, credit, self.pending_down, oid_s, oid_l))
                self.cons_down += 1
                if self.cons_down < self.cfg.max_consecutive_adds:
                    self.pending_down -= self.cfg.step_pts
                self.pending_up = min(self.pending_up, self.pending_down + self.cfg.step_pts)
            else:
                self.pending_down -= self.cfg.step_pts

    def _try_reversal(self, S):
        if self.stack_up and S <= (self.stack_up[-1].ref_level - self.cfg.step_pts):
            self.logger.info("Wave trying UP reversal...")
            f = self.stack_up.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
            self.cons_up = max(0, self.cons_up - 1)
            self.pending_up = round_to_nearest(S, self.cfg.step_pts) + self.cfg.step_pts
        if self.stack_down and S >= (self.stack_down[-1].ref_level + self.cfg.step_pts):
            self.logger.info("Wave trying DOWN reversal...")
            f = self.stack_down.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
            self.cons_down = max(0, self.cons_down - 1)
            self.pending_down = round_to_nearest(S, self.cfg.step_pts) - self.cfg.step_pts

    def _flatten_all(self):
        self.logger.info("Wave flattening all positions.")
        while self.stack_up:
            f = self.stack_up.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
        while self.stack_down:
            f = self.stack_down.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
        self.cons_up = self.cons_down = 0


class OrderManager:
    def __init__(self, broker: AngelBroker, logger):
        self.broker = broker
        self.logger = logger
        self._spreads: Dict[str, Dict] = {}

    def sell_call_spread(self, shortK, longK, lots):
        self.logger.info(f"OM: Selling CALL spread {shortK}/{longK}")
        oid_s, oid_l = self.broker.sell_call_spread(shortK, longK, lots)
        self._spreads[oid_s] = {"type": "CALL", "shortK": shortK, "longK": longK, "qty": lots, "peer": oid_l}
        return oid_s, oid_l

    def sell_put_spread(self, shortK, longK, lots):
        self.logger.info(f"OM: Selling PUT spread {shortK}/{longK}")
        oid_s, oid_l = self.broker.sell_put_spread(shortK, longK, lots)
        self._spreads[oid_s] = {"type": "PUT", "shortK": shortK, "longK": longK, "qty": lots, "peer": oid_l}
        return oid_s, oid_l

    def close_spread(self, oid_short, oid_long):
        self.logger.info(f"OM: Closing spread {oid_short}/{oid_long}")
        self.broker.close_spread(oid_short, oid_long)
        self._spreads.pop(oid_short, None)


@dataclass
class SurvivorCfg:
    pe_gap: int = 100
    ce_gap: int = 100
    symbol_gap: int = 200
    strike_step: int = 50
    min_credit: float = 5.0
    lot_qty: int = 1
    spread_wing: int = 100
    reset_gap: int = 60
    flatten_time: str = "15:25"


class SurvivorStrategy:
    def __init__(self, broker: AngelBroker, om: OrderManager, cfg: SurvivorCfg, logger):
        self.broker, self.om, self.cfg, self.logger = broker, om, cfg, logger
        S = self.broker.fut_ltp()
        self.pe_anchor = S
        self.ce_anchor = S
        self.high_since_pe = S
        self.low_since_ce = S
        self.logger.info(f"Survivor started. Anchors: PE={S}, CE={S}")

    def on_ticks_update(self, _tick=None):
        S = self.broker.fut_ltp()
        hhmm = self.broker.now_hhmm()
        if hhmm >= self.cfg.flatten_time: return

        if S >= self.pe_anchor + self.cfg.pe_gap:
            self.logger.info("Survivor trying PE trade...")
            gaps = int((S - self.pe_anchor) // self.cfg.pe_gap)
            for _ in range(gaps):
                shortK = round_to_nearest(S - self.cfg.symbol_gap, self.cfg.strike_step)
                longK = shortK - self.cfg.spread_wing
                credit, _ = self.broker.best_credit_put_spread(shortK, longK, self.cfg.lot_qty)
                if credit and credit >= self.cfg.min_credit:
                    self.om.sell_put_spread(shortK, longK, self.cfg.lot_qty)
            self.pe_anchor += gaps * self.cfg.pe_gap
            self.high_since_pe = max(self.high_since_pe, S)

        if S <= self.ce_anchor - self.cfg.ce_gap:
            self.logger.info("Survivor trying CE trade...")
            gaps = int((self.ce_anchor - S) // self.cfg.ce_gap)
            for _ in range(gaps):
                shortK = round_to_nearest(S + self.cfg.symbol_gap, self.cfg.strike_step)
                longK = shortK + self.cfg.spread_wing
                credit, _ = self.broker.best_credit_call_spread(shortK, longK, self.cfg.lot_qty)
                if credit and credit >= self.cfg.min_credit:
                    self.om.sell_call_spread(shortK, longK, self.cfg.lot_qty)
            self.ce_anchor -= gaps * self.cfg.ce_gap
            self.low_since_ce = min(self.low_since_ce, S)

        if S <= (self.high_since_pe - self.cfg.reset_gap):
            self.logger.info(f"Survivor resetting PE anchor from {self.pe_anchor} to {S}")
            self.pe_anchor = S
            self.high_since_pe = S
        if S >= (self.low_since_ce + self.cfg.reset_gap):
            self.logger.info(f"Survivor resetting CE anchor from {self.ce_anchor} to {S}")
            self.ce_anchor = S
            self.low_since_ce = S


class TrendDetector:
    def __init__(self, mom_bp=0.006, lookback_bars=15, range_mult=1.4):
        self.mom_bp = mom_bp
        self.look = lookback_bars
        self.range_mult = range_mult
        self.closes = deque(maxlen=240)
        self.ranges = deque(maxlen=60)

    def on_bar(self, o, h, l, c):
        self.closes.append(c)
        self.ranges.append(h - l)
        trend_mom = False
        if len(self.closes) >= self.look:
            mom = (self.closes[-1] - self.closes[-self.look]) / self.closes[-self.look]
            trend_mom = abs(mom) >= self.mom_bp
        range_exp = False
        if len(self.ranges) >= 20:
            rng = np.mean(list(self.ranges)[-10:])
            base = np.mean(list(self.ranges)[:10])
            range_exp = rng > self.range_mult * max(1.0, base)
        return "TREND" if (trend_mom or range_exp) else "CHOP"


@dataclass
class Router:
    wave: WaveExtractor
    survivor: SurvivorStrategy
    detector: TrendDetector
    logger: any
    active: str = "WAVE"

    def on_tick(self, tick=None):
        if self.active == "WAVE":
            self.wave.on_tick(tick)
        else:
            self.survivor.on_ticks_update(tick)

    def on_bar(self, o, h, l, c):
        regime = self.detector.on_bar(o, h, l, c)
        if regime == "TREND" and self.active != "SURVIVOR":
            self.logger.info("[ROUTER] → SURVIVOR (TREND)")
            self.wave.stop_new_entries()
            self.active = "SURVIVOR"
        elif regime == "CHOP" and self.active != "WAVE":
            self.logger.info("[ROUTER] → WAVE (CHOP)")
            self.wave.resume_new_entries()
            self.active = "WAVE"


class BarBuilder:
    def __init__(self, seconds=60):
        self.seconds = seconds
        self.reset()

    def reset(self):
        self.o = self.h = self.l = self.c = None
        self.ts = None

    def add(self, price: float):
        now = datetime.now()
        if self.ts is None:
            self.ts = now.replace(second=0, microsecond=0)
            self.o = self.h = self.l = self.c = price
            return None
        if (now - self.ts).total_seconds() >= self.seconds:
            bar = (self.o, self.h, self.l, self.c)
            self.reset()
            self.ts = now.replace(second=0, microsecond=0)
            self.o = self.h = self.l = self.c = price
            return bar
        self.c = price
        self.h = price if self.h is None else max(self.h, price)
        self.l = price if self.l is None else min(self.l, price)
        return None


class TradingBot:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self._is_running = False
        self._thread = None
        self.broker = None
        self.router = None

    def _run_loop(self):
        try:
            self.broker = AngelBroker(
                api_key=self.config['API_KEY'],
                client_code=self.config['CLIENT_CODE'],
                password=self.config['PASSWORD'],
                totp_secret=self.config['TOTP_SECRET'],
                dry_run=self.config['DRY_RUN']
            )
            self.broker.login()
            self.broker.underlying = self.config['UNDERLYING']
            self.broker.expiry = self.config['EXPIRY']

            om = OrderManager(self.broker, self.logger)
            wave_cfg = WaveCfg(**self.config['WAVE_CFG'])
            surv_cfg = SurvivorCfg(**self.config['SURVIVOR_CFG'])

            wave = WaveExtractor(self.broker, om, wave_cfg, self.logger)
            survivor = SurvivorStrategy(self.broker, om, surv_cfg, self.logger)
            detector = TrendDetector()
            self.router = Router(wave=wave, survivor=survivor, detector=detector, logger=self.logger, active="WAVE")

            bar = BarBuilder(seconds=self.config['BAR_SECONDS'])

            self.logger.info(f"Starting loop. DRY_RUN = {self.config['DRY_RUN']}")
            wave.start()

            while self._is_running:
                ist = pytz.timezone('Asia/Kolkata')
                now = datetime.now(ist)

                is_weekday = now.weekday() < 5 # Monday is 0 and Sunday is 6
                is_trading_hours = dt_time(9, 15) <= now.time() <= dt_time(15, 30)

                if is_weekday and is_trading_hours:
                    price = self.broker.fut_ltp()
                    self.router.on_tick({"fut_ltp": price})
                    b = bar.add(price)
                    if b is not None:
                        o, h, l, c = b
                        self.router.on_bar(o, h, l, c)
                else:
                    self.logger.info("Outside trading hours. Sleeping for 1 minute...")
                    time.sleep(60) # Sleep for a minute if outside trading hours
                    continue

                time.sleep(self.config['LTP_POLL_SEC'])
        except Exception as e:
            self.logger.error(f"[ERR] {e}")
            self.logger.error(traceback.format_exc())
        finally:
            self.logger.info("Trading loop stopped.")
            self._is_running = False

    def start(self):
        if not self._is_running:
            self._is_running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self.logger.info("Trading bot started.")

    def stop(self):
        if self._is_running:
            self.logger.info("Stopping trading bot...")
            self._is_running = False
            # self._thread.join() # Removing this blocking call
            self.logger.info("Trading bot stop signal sent.")
