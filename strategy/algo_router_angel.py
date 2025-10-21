# algo_router_angel.py
# Wave Extractor + Survivor + Router, executable via Angel One SmartAPI
# ---------------------------------------------------------------
# USE AT YOUR OWN RISK. Paper mode ON by default (DRY_RUN=True).
# Fill your Angel credentials below before live trading.

import time, math, traceback
from dataclasses import dataclass
from collections import deque
from datetime import datetime
from typing import Optional, Tuple, Dict

import numpy as np

# ------------- USER CONFIG -------------------------------------------------

DRY_RUN = True  # <<< set to False only after paper-testing thoroughly

API_KEY      = "YOUR_API_KEY"
CLIENT_CODE  = "YOUR_CLIENT_CODE"
PASSWORD     = "YOUR_PASSWORD"
TOTP_SECRET  = "YOUR_TOTP_SECRET"  # base32 for OTP

UNDERLYING   = "NIFTY"       # Index name
LOT_SIZE     = 50            # NIFTY lot size (confirm with your broker)
EXCHANGE     = "NFO"

# Choose the EXACT expiry code you want to trade (weekly/fortnightly)
# Examples: "24OCT24" (DDMMMYY) for weekly, or "28NOV24" etc.
# You can rotate this fortnightly per your rules.
EXPIRY       = "24OCT24"

# Polling / bars
LTP_POLL_SEC = 1.0
BAR_SECONDS  = 60

# ------------- ANGEL ONE (SmartAPI) BROKER ADAPTER -------------------------

try:
    from SmartApi import SmartConnect
    import pyotp
except Exception:
    SmartConnect = None
    pyotp = None

class AngelBroker:
    def __init__(self, api_key, client_code, password, totp_secret, dry_run=True):
        self.api_key = api_key
        self.client_code = client_code
        self.password = password
        self.totp_secret = totp_secret
        self.sc = None
        self.dry_run = dry_run
        self.session = {}

    def login(self):
        if self.dry_run or SmartConnect is None:
            print("[BROKER] DRY_RUN or SmartConnect not installed. Skipping Angel login.")
            return
        self.sc = SmartConnect(api_key=self.api_key)
        otp = pyotp.TOTP(self.totp_secret).now()
        data = self.sc.generateSession(self.client_code, self.password, otp)
        if "data" not in data:
            raise RuntimeError(f"Angel login failed: {data}")
        self.session = data["data"]
        print("[BROKER] Logged in to Angel One.")

    def now_hhmm(self) -> str:
        return datetime.now().strftime("%H:%M")

    @staticmethod
    def round_to_strike(x, step=50):
        return int(round(float(x)/step)*step)

    def option_symbol(self, symbol: str, expiry_code: str, strike: int, opttype: str):
        return f"{symbol}{expiry_code}{strike}{opttype}"

    def fut_ltp(self) -> float:
        if self.dry_run or self.sc is None:
            base = 25700.0
            t = time.time()
            return base + 40.0*math.sin(t/25.0) + 10.0*math.sin(t/5.0)
        raise NotImplementedError("Implement fut_ltp() with Angel getQuote/ltpData")

    def best_credit_call_spread(self, shortK, longK, lots) -> Tuple[Optional[float], dict]:
        short_sym = self.option_symbol(UNDERLYING, EXPIRY, shortK, "CE")
        long_sym  = self.option_symbol(UNDERLYING, EXPIRY, longK, "CE")
        short_ltp = self._ltp_option(short_sym)
        long_ltp  = self._ltp_option(long_sym)
        if short_ltp is None or long_ltp is None: return (None, {})
        credit = max(0.05, short_ltp - long_ltp)
        return credit, {"short": short_ltp, "long": long_ltp}

    def best_credit_put_spread(self, shortK, longK, lots) -> Tuple[Optional[float], dict]:
        short_sym = self.option_symbol(UNDERLYING, EXPIRY, shortK, "PE")
        long_sym  = self.option_symbol(UNDERLYING, EXPIRY, longK, "PE")
        short_ltp = self._ltp_option(short_sym)
        long_ltp  = self._ltp_option(long_sym)
        if short_ltp is None or long_ltp is None: return (None, {})
        credit = max(0.05, short_ltp - long_ltp)
        return credit, {"short": short_ltp, "long": long_ltp}

    def _ltp_option(self, tradingsymbol: str) -> Optional[float]:
        if self.dry_run:
            # crude synthetic LTP for demo
            digits = "".join([ch for ch in tradingsymbol if ch.isdigit()])
            strike = int(digits[-5:]) if len(digits)>=5 else 25000
            S = self.fut_ltp()
            m = abs(S - strike)
            return float(max(2.0, 45.0 - 0.08*m))
        raise NotImplementedError("Implement option LTP with Angel getQuote/ltpData")

    def sell_call_spread(self, shortK, longK, lots) -> Tuple[str, str]:
        short_sym = self.option_symbol(UNDERLYING, EXPIRY, shortK, "CE")
        long_sym  = self.option_symbol(UNDERLYING, EXPIRY, longK, "CE")
        if self.dry_run:
            oid_s = f"SIM-S-C-{shortK}-{int(time.time())}"
            oid_l = f"SIM-B-C-{longK}-{int(time.time())}"
            print(f"[DRY] SELL CALL SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return oid_s, oid_l
        raise NotImplementedError("Implement placeOrder for call spread")

    def sell_put_spread(self, shortK, longK, lots) -> Tuple[str, str]:
        short_sym = self.option_symbol(UNDERLYING, EXPIRY, shortK, "PE")
        long_sym  = self.option_symbol(UNDERLYING, EXPIRY, longK, "PE")
        if self.dry_run:
            oid_s = f"SIM-S-P-{shortK}-{int(time.time())}"
            oid_l = f"SIM-B-P-{longK}-{int(time.time())}"
            print(f"[DRY] SELL PUT SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return oid_s, oid_l
        raise NotImplementedError("Implement placeOrder for put spread")

    def close_spread(self, oid_short: str, oid_long: str):
        if self.dry_run:
            print(f"[DRY] CLOSE SPREAD short={oid_short} long={oid_long}")
            return
        raise NotImplementedError("Implement closing spread via reverse orders")


class OrderManager:
    def __init__(self, broker: AngelBroker):
        self.broker = broker
        self._spreads: Dict[str, Dict] = {}

    def sell_call_spread(self, shortK, longK, lots):
        oid_s, oid_l = self.broker.sell_call_spread(shortK, longK, lots)
        self._spreads[oid_s] = {"type":"CALL", "shortK":shortK, "longK":longK, "qty":lots, "peer":oid_l}
        return oid_s, oid_l

    def sell_put_spread(self, shortK, longK, lots):
        oid_s, oid_l = self.broker.sell_put_spread(shortK, longK, lots)
        self._spreads[oid_s] = {"type":"PUT", "shortK":shortK, "longK":longK, "qty":lots, "peer":oid_l}
        return oid_s, oid_l

    def close_spread(self, oid_short, oid_long):
        self.broker.close_spread(oid_short, oid_long)
        self._spreads.pop(oid_short, None)

# ---------------- WAVE EXTRACTOR ----------------

def round_to_nearest(x, step): return int(round(float(x)/step)*step)

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
    side: str       # "UP" or "DOWN"
    shortK: int
    longK: int
    qty: int
    credit: float
    ref_level: float
    oid_short: str
    oid_long: str

class WaveExtractor:
    def __init__(self, broker: AngelBroker, om: OrderManager, cfg: WaveCfg):
        self.broker, self.om, self.cfg = broker, om, cfg
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

    def stop_new_entries(self): self.enabled = False
    def resume_new_entries(self): self.enabled = True

    def on_tick(self, _tick=None):
        if self.anchor is None: self.start()
        S = self.broker.fut_ltp()
        hhmm = self.broker.now_hhmm()
        if hhmm >= self.cfg.flatten_time:
            self._flatten_all(); return
        if hhmm >= self.cfg.daily_trade_cutoff:
            self.enabled = False

        if self.enabled:
            self._try_up(S); self._try_down(S)
        self._try_reversal(S)

    def _try_up(self, S):
        if S >= self.pending_up and self.cons_up < self.cfg.max_consecutive_adds:
            shortK = round_to_nearest(S, self.cfg.strike_step)
            longK = shortK + self.cfg.spread_wing
            credit, _ = self.broker.best_credit_call_spread(shortK, longK, self.cfg.lot_qty)
            if credit and credit >= self.cfg.min_credit:
                oid_s, oid_l = self.om.sell_call_spread(shortK, longK, self.cfg.lot_qty)
                self.stack_up.append(WaveFill("UP", shortK, longK, self.cfg.lot_qty, credit, self.pending_up, oid_s, oid_l))
                self.cons_up += 1
                if self.cons_up < self.cfg.max_consecutive_adds:
                    self.pending_up += self.cfg.step_pts
                self.pending_down = max(self.pending_down, self.pending_up - self.cfg.step_pts)
            else:
                self.pending_up += self.cfg.step_pts

    def _try_down(self, S):
        if S <= self.pending_down and self.cons_down < self.cfg.max_consecutive_adds:
            shortK = round_to_nearest(S, self.cfg.strike_step)
            longK = shortK - self.cfg.spread_wing
            credit, _ = self.broker.best_credit_put_spread(shortK, longK, self.cfg.lot_qty)
            if credit and credit >= self.cfg.min_credit:
                oid_s, oid_l = self.om.sell_put_spread(shortK, longK, self.cfg.lot_qty)
                self.stack_down.append(WaveFill("DOWN", shortK, longK, self.cfg.lot_qty, credit, self.pending_down, oid_s, oid_l))
                self.cons_down += 1
                if self.cons_down < self.cfg.max_consecutive_adds:
                    self.pending_down -= self.cfg.step_pts
                self.pending_up = min(self.pending_up, self.pending_down + self.cfg.step_pts)
            else:
                self.pending_down -= self.cfg.step_pts

    def _try_reversal(self, S):
        if self.stack_up and S <= (self.stack_up[-1].ref_level - self.cfg.step_pts):
            f = self.stack_up.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
            self.cons_up = max(0, self.cons_up - 1)
            self.pending_up = round_to_nearest(S, self.cfg.step_pts) + self.cfg.step_pts

        if self.stack_down and S >= (self.stack_down[-1].ref_level + self.cfg.step_pts):
            f = self.stack_down.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
            self.cons_down = max(0, self.cons_down - 1)
            self.pending_down = round_to_nearest(S, self.cfg.step_pts) - self.cfg.step_pts

    def _flatten_all(self):
        while self.stack_up:
            f = self.stack_up.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
        while self.stack_down:
            f = self.stack_down.pop()
            self.om.close_spread(f.oid_short, f.oid_long)
        self.cons_up = self.cons_down = 0

# ---------------- SURVIVOR (simplified) --------------

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
    def __init__(self, broker: AngelBroker, om: OrderManager, cfg: SurvivorCfg):
        self.broker, self.om, self.cfg = broker, om, cfg
        S = self.broker.fut_ltp()
        self.pe_anchor = S
        self.ce_anchor = S
        self.high_since_pe = S
        self.low_since_ce  = S

    def on_ticks_update(self, _tick=None):
        S = self.broker.fut_ltp()
        hhmm = self.broker.now_hhmm()
        if hhmm >= self.cfg.flatten_time: return

        # UP move -> sell PUT spread
        if S >= self.pe_anchor + self.cfg.pe_gap:
            gaps = int((S - self.pe_anchor) // self.cfg.pe_gap)
            for _ in range(gaps):
                shortK = round_to_nearest(S - self.cfg.symbol_gap, self.cfg.strike_step)
                longK  = shortK - self.cfg.spread_wing
                credit, _ = self.broker.best_credit_put_spread(shortK, longK, self.cfg.lot_qty)
                if credit and credit >= self.cfg.min_credit:
                    self.om.sell_put_spread(shortK, longK, self.cfg.lot_qty)
            self.pe_anchor += gaps * self.cfg.pe_gap
            self.high_since_pe = max(self.high_since_pe, S)

        # DOWN move -> sell CALL spread
        if S <= self.ce_anchor - self.cfg.ce_gap:
            gaps = int((self.ce_anchor - S) // self.cfg.ce_gap)
            for _ in range(gaps):
                shortK = round_to_nearest(S + self.cfg.symbol_gap, self.cfg.strike_step)
                longK  = shortK + self.cfg.spread_wing
                credit, _ = self.broker.best_credit_call_spread(shortK, longK, self.cfg.lot_qty)
                if credit and credit >= self.cfg.min_credit:
                    self.om.sell_call_spread(shortK, longK, self.cfg.lot_qty)
            self.ce_anchor -= gaps * self.cfg.ce_gap
            self.low_since_ce = min(self.low_since_ce, S)

        # Favorable retrace resets
        if S <= (self.high_since_pe - self.cfg.reset_gap):
            self.pe_anchor = S
            self.high_since_pe = S
        if S >= (self.low_since_ce + self.cfg.reset_gap):
            self.ce_anchor = S
            self.low_since_ce = S

# ---------------- ROUTER & BARS ----------------------

class TrendDetector:
    def __init__(self, mom_bp=0.006, lookback_bars=15, range_mult=1.4):
        self.mom_bp = mom_bp
        self.look = lookback_bars
        self.range_mult = range_mult
        self.closes = deque(maxlen=240)
        self.ranges = deque(maxlen=60)

    def on_bar(self, o,h,l,c):
        self.closes.append(c)
        self.ranges.append(h-l)
        trend_mom = False
        if len(self.closes) >= self.look:
            mom = (self.closes[-1]-self.closes[-self.look]) / self.closes[-self.look]
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
    active: str = "WAVE"

    def on_tick(self, tick=None):
        if self.active == "WAVE":
            self.wave.on_tick(tick)
        else:
            self.survivor.on_ticks_update(tick)

    def on_bar(self, o,h,l,c):
        regime = self.detector.on_bar(o,h,l,c)
        if regime == "TREND" and self.active != "SURVIVOR":
            print("[ROUTER] → SURVIVOR (TREND)")
            self.wave.stop_new_entries()
            self.active = "SURVIVOR"
        elif regime == "CHOP" and self.active != "WAVE":
            print("[ROUTER] → WAVE (CHOP)")
            self.wave.resume_new_entries()
            self.active = "WAVE"

class BarBuilder:
    def __init__(self, seconds=60):
        self.seconds = seconds
        self.reset()

    def reset(self):
        self.o=self.h=self.l=self.c=None
        self.ts = None

    def add(self, price: float):
        now = datetime.now()
        if self.ts is None:
            self.ts = now.replace(second=0, microsecond=0)
            self.o=self.h=self.l=self.c=price
            return None
        if (now - self.ts).total_seconds() >= self.seconds:
            bar=(self.o,self.h,self.l,self.c)
            self.reset()
            self.ts = now.replace(second=0, microsecond=0)
            self.o=self.h=self.l=self.c=price
            return bar
        self.c = price
        self.h = price if self.h is None else max(self.h, price)
        self.l = price if self.l is None else min(self.l, price)
        return None

# ---------------- MAIN LOOP --------------------------

def main():
    broker = AngelBroker(API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET, dry_run=DRY_RUN)
    broker.login()

    om  = OrderManager(broker)
    wave_cfg = WaveCfg(step_pts=50, spread_wing=100, min_credit=8.0, max_consecutive_adds=2, lot_qty=1)
    surv_cfg = SurvivorCfg(pe_gap=100, ce_gap=100, symbol_gap=200, min_credit=5.0, lot_qty=1)

    wave = WaveExtractor(broker, om, wave_cfg)
    survivor = SurvivorStrategy(broker, om, surv_cfg)
    detector = TrendDetector(mom_bp=0.006, lookback_bars=15, range_mult=1.4)
    router = Router(wave=wave, survivor=survivor, detector=detector, active="WAVE")

    bar = BarBuilder(seconds=BAR_SECONDS)

    print("[INFO] Starting loop. DRY_RUN =", DRY_RUN)
    wave.start()

    try:
        while True:
            price = broker.fut_ltp()
            router.on_tick({"fut_ltp":price})
            b = bar.add(price)
            if b is not None:
                o,h,l,c = b
                router.on_bar(o,h,l,c)
            time.sleep(LTP_POLL_SEC)
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    except Exception as e:
        print("[ERR] ", e)
        traceback.print_exc()

if __name__ == "__main__":
    main()
