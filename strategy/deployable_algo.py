# strategy/deployable_algo.py
# strategy/deployable_algo.py
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from typing import Dict
import threading
import pytz


from brokers.angelone import AngelBroker


# --- Configuration Section ---
# These will be passed in from the Streamlit app
# DRY_RUN = True
# API_KEY = "YOUR_API_KEY"
# ... etc.


def round_to_nearest(x, step): return int(round(float(x) / step) * step)


class OrderManager:
    def __init__(self, broker: AngelBroker, logger, trade_history):
        self.broker = broker
        self.logger = logger
        self._spreads: Dict[str, Dict] = {}
        self.trade_history = trade_history

    def sell_call_spread(self, shortK, longK, lots):
        self.logger.info(f"OM: Selling CALL spread {shortK}/{longK}")
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        short_sym = f"{self.broker.underlying}{self.broker.expiry}{shortK}CE"
        long_sym = f"{self.broker.underlying}{self.broker.expiry}{longK}CE"

        oid_s, msg_s, oid_l, msg_l = self.broker.sell_call_spread(shortK, longK, lots)

        # Record short leg
        status_s = f"Success ({oid_s})" if oid_s else f"Failed ({msg_s})"
        self.trade_history.append({"timestamp": now, "symbol": short_sym, "type": "SELL", "qty": lots, "status": status_s})

        # Record long leg
        status_l = f"Success ({oid_l})" if oid_l else f"Failed ({msg_l})"
        self.trade_history.append({"timestamp": now, "symbol": long_sym, "type": "BUY", "qty": lots, "status": status_l})

        if oid_s:
             self._spreads[oid_s] = {"type": "CALL", "shortK": shortK, "longK": longK, "qty": lots, "peer": oid_l}
        return oid_s, oid_l

    def sell_put_spread(self, shortK, longK, lots):
        self.logger.info(f"OM: Selling PUT spread {shortK}/{longK}")
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        short_sym = f"{self.broker.underlying}{self.broker.expiry}{shortK}PE"
        long_sym = f"{self.broker.underlying}{self.broker.expiry}{longK}PE"

        oid_s, msg_s, oid_l, msg_l = self.broker.sell_put_spread(shortK, longK, lots)

        # Record short leg
        status_s = f"Success ({oid_s})" if oid_s else f"Failed ({msg_s})"
        self.trade_history.append({"timestamp": now, "symbol": short_sym, "type": "SELL", "qty": lots, "status": status_s})

        # Record long leg
        status_l = f"Success ({oid_l})" if oid_l else f"Failed ({msg_l})"
        self.trade_history.append({"timestamp": now, "symbol": long_sym, "type": "BUY", "qty": lots, "status": status_l})

        if oid_s:
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


class TradingBot:
    def __init__(self, config, logger, status_queue=None):
        self.config = config
        self.logger = logger
        self.status_queue = status_queue
        self._is_running = False
        self._thread = None
        self.broker = None
        self.strategy = None
        self.trade_history = []

    @property
    def is_connected(self) -> bool:
        return self.broker is not None and self.broker.is_connected()

    def get_funds(self):
        if self.broker:
            return self.broker.get_funds()
        return None

    def fire_test_order(self):
        if not self.is_connected:
            self.logger.error("Cannot fire test order: Not connected to broker.")
            return

        self.logger.info("Firing a test order...")
        try:
            # Create a dummy far-OTM call spread to test
            current_price = self.broker.fut_ltp()
            if not current_price:
                self.logger.error("Could not fetch current price to fire a test order.")
                return

            strike = round_to_nearest(current_price + 500, 50) # Far OTM
            om = OrderManager(self.broker, self.logger, self.trade_history)
            om.sell_call_spread(strike, strike + 100, 1)
            self.logger.info("Test order sequence complete.")

        except Exception as e:
            self.logger.error(f"An exception occurred while firing test order: {e}")

    def _run_loop(self):
        try:
            self.broker = AngelBroker(
                api_key=self.config['API_KEY'],
                client_code=self.config['CLIENT_CODE'],
                password=self.config['PASSWORD'],
                totp_secret=self.config['TOTP_SECRET'],
                dry_run=self.config['DRY_RUN'],
                logger=self.logger
            )
            self.broker.login()
            if self.broker.is_connected() and self.status_queue:
                self.status_queue.put("CONNECTED")

            self.broker.underlying = self.config['UNDERLYING']
            self.broker.expiry = self.config['EXPIRY']

            om = OrderManager(self.broker, self.logger, self.trade_history)
            surv_cfg = SurvivorCfg(**self.config['SURVIVOR_CFG'])
            self.strategy = SurvivorStrategy(self.broker, om, surv_cfg, self.logger)


            self.logger.info(f"Starting loop. DRY_RUN = {self.config['DRY_RUN']}")

            while self._is_running:
                ist = pytz.timezone('Asia/Kolkata')
                now = datetime.now(ist)

                is_weekday = now.weekday() < 5 # Monday is 0 and Sunday is 6
                is_trading_hours = dt_time(9, 15) <= now.time() <= dt_time(15, 30)

                if is_weekday and is_trading_hours:
                    price = self.broker.fut_ltp()
                    self.strategy.on_ticks_update({"fut_ltp": price})

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
