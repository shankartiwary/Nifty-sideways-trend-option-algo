from datetime import datetime
import math
import time
from typing import Optional, Tuple
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
        self.underlying = ""
        self.expiry = ""


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
        """
        Fetches the last traded price of the underlying future.
        In dry run mode, it simulates the price.
        For live mode, this needs to be implemented using the broker's API.
        """
        if self.dry_run or self.sc is None:
            base = 25700.0
            t = time.time()
            return base + 40.0 * math.sin(t / 25.0) + 10.0 * math.sin(t / 5.0)
        # TODO: Implement the logic to fetch the live future LTP using the Angel One API
        # Example:
        # return self.sc.ltpData("NFO", "NIFTY24OCTFUT", "2024-10-24")['ltp']
        raise NotImplementedError("Implement fut_ltp() with Angel getQuote/ltpData")

    def best_credit_call_spread(self, shortK, longK, lots) -> Tuple[Optional[float], dict]:
        short_sym = self.option_symbol(self.underlying, self.expiry, shortK, "CE")
        long_sym = self.option_symbol(self.underlying, self.expiry, longK, "CE")
        short_ltp = self._get_option_ltp(short_sym)
        long_ltp = self._get_option_ltp(long_sym)
        if short_ltp is None or long_ltp is None: return (None, {})
        credit = max(0.05, short_ltp - long_ltp)
        return credit, {"short": short_ltp, "long": long_ltp}

    def best_credit_put_spread(self, shortK, longK, lots) -> Tuple[Optional[float], dict]:
        short_sym = self.option_symbol(self.underlying, self.expiry, shortK, "PE")
        long_sym = self.option_symbol(self.underlying, self.expiry, longK, "PE")
        short_ltp = self._get_option_ltp(short_sym)
        long_ltp = self._get_option_ltp(long_sym)
        if short_ltp is None or long_ltp is None: return (None, {})
        credit = max(0.05, short_ltp - long_ltp)
        return credit, {"short": short_ltp, "long": long_ltp}

    def _get_option_ltp(self, symbol):
        """
        Fetches the last traded price of a given option symbol.
        In dry run mode, it simulates the price.
        For live mode, this needs to be implemented using the broker's API.
        """
        if self.dry_run:
            # crude synthetic LTP for demo
            digits = "".join([ch for ch in symbol if ch.isdigit()])
            strike = int(digits[-5:]) if len(digits) >= 5 else 25000
            S = self.fut_ltp()
            m = abs(S - strike)
            return float(max(2.0, 45.0 - 0.08 * m))
        # TODO: Implement the logic to fetch the live option LTP using the Angel One API
        # Example:
        # return self.sc.ltpData("NFO", symbol, "2024-10-24")['ltp']
        raise NotImplementedError("Implement option LTP with Angel getQuote/ltpData")

    def sell_call_spread(self, shortK, longK, lots) -> Tuple[str, str]:
        """
        Places a sell call spread order.
        In dry run mode, it simulates the order placement.
        For live mode, this needs to be implemented using the broker's API.
        """
        short_sym = self.option_symbol(self.underlying, self.expiry, shortK, "CE")
        long_sym = self.option_symbol(self.underlying, self.expiry, longK, "CE")
        if self.dry_run:
            oid_s = f"SIM-S-C-{shortK}-{int(time.time())}"
            oid_l = f"SIM-B-C-{longK}-{int(time.time())}"
            print(f"[DRY] SELL CALL SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return oid_s, oid_l
        # TODO: Implement the logic to place a live sell call spread order using the Angel One API
        raise NotImplementedError("Implement placeOrder for call spread")

    def sell_put_spread(self, shortK, longK, lots) -> Tuple[str, str]:
        """
        Places a sell put spread order.
        In dry run mode, it simulates the order placement.
        For live mode, this needs to be implemented using the broker's API.
        """
        short_sym = self.option_symbol(self.underlying, self.expiry, shortK, "PE")
        long_sym = self.option_symbol(self.underlying, self.expiry, longK, "PE")
        if self.dry_run:
            oid_s = f"SIM-S-P-{shortK}-{int(time.time())}"
            oid_l = f"SIM-B-P-{longK}-{int(time.time())}"
            print(f"[DRY] SELL PUT SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return oid_s, oid_l
        # TODO: Implement the logic to place a live sell put spread order using the Angel One API
        raise NotImplementedError("Implement placeOrder for put spread")

    def close_spread(self, oid_short: str, oid_long: str):
        """
        Closes a spread given the order IDs of the short and long legs.
        In dry run mode, it simulates the closing of the spread.
        For live mode, this needs to be implemented using the broker's API.
        """
        if self.dry_run:
            print(f"[DRY] CLOSE SPREAD short={oid_short} long={oid_long}")
            return
        # TODO: Implement the logic to close a live spread using the Angel One API
        raise NotImplementedError("Implement closing spread via reverse orders")