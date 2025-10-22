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
    def __init__(self, api_key, client_code, password, totp_secret, dry_run=True, logger=None):
        self.api_key = api_key
        self.client_code = client_code
        self.password = password
        self.totp_secret = totp_secret
        self.sc = None
        self.dry_run = dry_run
        self.session = {}
        self.underlying = ""
        self.expiry = ""
        self.logger = logger or logging.getLogger(__name__)
        self.instrument_map = {}

    def login(self):
        """
        Logs into the broker.
        """
        if self.dry_run or SmartConnect is None:
            self.logger.info("[BROKER] DRY_RUN or SmartConnect not installed. Skipping Angel login.")
            return
        self.sc = SmartConnect(api_key=self.api_key)
        try:
            otp = pyotp.TOTP(self.totp_secret).now()
        except Exception:
            raise ValueError("Invalid TOTP Secret. Please provide a valid Base32 key.")

        data = self.sc.generateSession(self.client_code, self.password, otp)
        if "data" not in data or data["data"] is None:
            raise RuntimeError(f"Angel login failed: {data.get('message', 'Unknown error')}")

        self.session = data["data"]
        self.logger.info("[BROKER] Logged in to Angel One.")
        self._fetch_instrument_list()

    def _fetch_instrument_list(self):
        """Downloads the full list of instruments and creates a symbol-to-token map."""
        try:
            # The modern method is to get a URL and download the instrument list as a JSON file.
            instrument_url = self.sc.get_instrument_list()
            if instrument_url:
                import requests
                response = requests.get(instrument_url)
                if response.status_code == 200:
                    instrument_list = response.json()
                    for instrument in instrument_list:
                        self.instrument_map[instrument['symbol']] = instrument['token']
                    self.logger.info(f"Successfully downloaded and mapped {len(self.instrument_map)} instruments.")
                else:
                    self.logger.error(f"Failed to download instrument list. Status code: {response.status_code}")
            else:
                self.logger.error("Failed to get instrument list URL.")
        except Exception as e:
            self.logger.error(f"Error downloading instrument list: {e}")

    def get_token(self, symbol: str) -> Optional[str]:
        return self.instrument_map.get(symbol)

    def is_connected(self) -> bool:
        return self.session and 'feedtoken' in self.session

    def now_hhmm(self) -> str:
        return datetime.now().strftime("%H:%M")

    def fut_ltp(self) -> float:
        if self.dry_run:
            # Simulation logic
            return 25700.0 + 40.0 * math.sin(time.time() / 25.0) + 10.0 * math.sin(time.time() / 5.0)

        # --- IMPORTANT: TODO ---
        # You must find the correct symbol for the NIFTY future you want to trade.
        # It will be something like 'NIFTY24OCTFUT'.
        future_symbol = f"NIFTY{self.expiry}FUT" # Adjust format if needed
        future_token = self.get_token(future_symbol)

        if not future_token:
            self.logger.error(f"Could not find token for future symbol: {future_symbol}")
            return 0.0

        try:
            quote = self.sc.ltpData("NFO", future_symbol, future_token)
            if quote.get('data') and 'ltp' in quote['data']:
                return quote['data']['ltp']
            else:
                self.logger.error(f"Could not fetch LTP for future: {quote}")
                return 0.0
        except Exception as e:
            self.logger.error(f"Exception while fetching future LTP: {e}")
            return 0.0

    def _get_option_ltp(self, symbol: str) -> Optional[float]:
        if self.dry_run:
            S = self.fut_ltp()
            strike = int("".join([ch for ch in symbol if ch.isdigit()][-5:]))
            return float(max(2.0, 45.0 - 0.08 * abs(S - strike)))

        token = self.get_token(symbol)
        if not token:
            self.logger.error(f"Could not find token for option symbol: {symbol}")
            return None

        try:
            quote = self.sc.ltpData("NFO", symbol, token)
            if quote.get('data') and 'ltp' in quote['data']:
                return quote['data']['ltp']
            else:
                self.logger.error(f"Could not fetch LTP for {symbol}: {quote}")
                return None
        except Exception as e:
            self.logger.error(f"Exception while fetching option LTP for {symbol}: {e}")
            return None

    def _place_order(self, symbol: str, token: str, tx_type: str, qty: int) -> Optional[str]:
        try:
            params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": tx_type, "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "CARRYFORWARD", "duration": "DAY", "quantity": str(qty)
            }
            order_id = self.sc.placeOrder(params)
            self.logger.info(f"Placed {tx_type} order for {symbol}: {order_id}")
            return order_id
        except Exception as e:
            self.logger.error(f"Failed to place {tx_type} order for {symbol}: {e}")
            return None

    def sell_call_spread(self, shortK: int, longK: int, lots: int) -> Tuple[Optional[str], Optional[str]]:
        short_sym = f"{self.underlying}{self.expiry}{shortK}CE"
        long_sym = f"{self.underlying}{self.expiry}{longK}CE"

        if self.dry_run:
            self.logger.info(f"[DRY] SELL CALL SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return f"SIM-S-C-{shortK}", f"SIM-B-C-{longK}"

        short_token, long_token = self.get_token(short_sym), self.get_token(long_sym)
        if not all([short_token, long_token]):
            self.logger.error(f"Could not find tokens for call spread: {short_sym}, {long_sym}")
            return None, None

        qty = lots * 50 # Assuming NIFTY lot size
        oid_s = self._place_order(short_sym, short_token, "SELL", qty)
        oid_l = self._place_order(long_sym, long_token, "BUY", qty)
        return oid_s, oid_l

    def sell_put_spread(self, shortK: int, longK: int, lots: int) -> Tuple[Optional[str], Optional[str]]:
        short_sym = f"{self.underlying}{self.expiry}{shortK}PE"
        long_sym = f"{self.underlying}{self.expiry}{longK}PE"

        if self.dry_run:
            self.logger.info(f"[DRY] SELL PUT SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return f"SIM-S-P-{shortK}", f"SIM-B-P-{longK}"

        short_token, long_token = self.get_token(short_sym), self.get_token(long_sym)
        if not all([short_token, long_token]):
            self.logger.error(f"Could not find tokens for put spread: {short_sym}, {long_sym}")
            return None, None

        qty = lots * 50
        oid_s = self._place_order(short_sym, short_token, "SELL", qty)
        oid_l = self._place_order(long_sym, long_token, "BUY", qty)
        return oid_s, oid_l

    def close_spread(self, oid_short: str, oid_long: str):
        if self.dry_run:
            self.logger.info(f"[DRY] CLOSE SPREAD short={oid_short} long={oid_long}")
            return
        # TODO: Implement order cancellation or reverse trades
        self.logger.warning("Live spread closing is not fully implemented.")
