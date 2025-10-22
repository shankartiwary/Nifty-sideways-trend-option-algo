from datetime import datetime
import math
import time
from typing import Optional, Tuple, Dict
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
        """Downloads the full list of instruments from a static URL and creates a symbol-to-instrument map."""
        try:
            instrument_url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
            import requests
            response = requests.get(instrument_url)
            if response.status_code == 200:
                instrument_list = response.json()
                for instrument in instrument_list:
                    if 'symbol' in instrument:
                        self.instrument_map[instrument['symbol']] = instrument
                self.logger.info(f"Successfully downloaded and mapped {len(self.instrument_map)} instruments.")
            else:
                self.logger.error(f"Failed to download instrument list. Status code: {response.status_code}")
        except Exception as e:
            self.logger.error(f"Error downloading instrument list: {e}")

    def get_instrument_details(self, symbol: str) -> Optional[dict]:
        return self.instrument_map.get(symbol)

    def get_token(self, symbol: str) -> Optional[str]:
        instrument = self.get_instrument_details(symbol)
        return instrument.get('token') if instrument else None

    def is_connected(self) -> bool:
        return self.session and 'feedToken' in self.session

    def get_funds(self) -> Optional[Dict[str, float]]:
        """Fetches available and used margin from the broker."""
        if self.dry_run:
            # Return dummy data for dry run
            return {'available': 50000.0, 'used': 10000.0}

        if not self.is_connected():
            return None

        try:
            rms_data = self.sc.rmsLimit()
            if rms_data and rms_data.get('status') and rms_data.get('data'):
                # Extracting relevant margin details. Adjust keys if necessary based on API response.
                available_margin = float(rms_data['data'].get('availablecash', 0))
                used_margin = float(rms_data['data'].get('marginused', 0))
                return {'available': available_margin, 'used': used_margin}
            else:
                self.logger.error(f"Failed to fetch RMS data: {rms_data.get('message')}")
                return None
        except Exception as e:
            self.logger.error(f"Exception while fetching funds: {e}")
            return None

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

    def _place_order(self, symbol: str, token: str, tx_type: str, qty: int) -> Tuple[Optional[str], str]:
        """
        Places an order and returns the order ID and a status message.
        Returns (order_id, "Success") or (None, "Rejection Reason").
        """
        try:
            params = {
                "variety": "NORMAL", "tradingsymbol": symbol, "symboltoken": token,
                "transactiontype": tx_type, "exchange": "NFO", "ordertype": "MARKET",
                "producttype": "CARRYFORWARD", "duration": "DAY", "quantity": str(qty)
            }
            response = self.sc.placeOrder(params)

            if response is None:
                error_message = "API returned no response"
                self.logger.error(f"Failed to place {tx_type} order for {symbol}. Reason: {error_message}")
                return None, error_message

            if response.get('status') and response.get('data', {}).get('orderid'):
                order_id = response['data']['orderid']
                self.logger.info(f"Placed {tx_type} order for {symbol}: {order_id}")
                return order_id, "Success"
            else:
                error_message = response.get('message', 'Unknown error from API')
                self.logger.error(f"Failed to place {tx_type} order for {symbol}. Reason: {error_message}")
                return None, error_message

        except Exception as e:
            error_message = str(e)
            self.logger.error(f"Failed to place {tx_type} order for {symbol}. Exception: {error_message}")
            return None, error_message

    def sell_call_spread(self, shortK: int, longK: int, lots: int) -> Tuple[Optional[str], str, Optional[str], str]:
        short_sym = f"{self.underlying}{self.expiry}{shortK}CE"
        long_sym = f"{self.underlying}{self.expiry}{longK}CE"

        if self.dry_run:
            self.logger.info(f"[DRY] SELL CALL SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return f"SIM-S-C-{shortK}", "Dry Run", f"SIM-B-C-{longK}", "Dry Run"

        short_instrument = self.get_instrument_details(short_sym)
        long_instrument = self.get_instrument_details(long_sym)

        if not all([short_instrument, long_instrument]):
            msg = f"Could not find instrument details for call spread: {short_sym}, {long_sym}"
            self.logger.error(msg)
            return None, msg, None, msg

        lot_size = int(short_instrument.get('lotsize', 25)) # Default to 25 if not found
        qty = lots * lot_size

        short_token = short_instrument['token']
        long_token = long_instrument['token']

        oid_s, msg_s = self._place_order(short_sym, short_token, "SELL", qty)
        oid_l, msg_l = self._place_order(long_sym, long_token, "BUY", qty)
        return oid_s, msg_s, oid_l, msg_l

    def sell_put_spread(self, shortK: int, longK: int, lots: int) -> Tuple[Optional[str], str, Optional[str], str]:
        short_sym = f"{self.underlying}{self.expiry}{shortK}PE"
        long_sym = f"{self.underlying}{self.expiry}{longK}PE"

        if self.dry_run:
            self.logger.info(f"[DRY] SELL PUT SPR {short_sym} / BUY {long_sym}, lots={lots}")
            return f"SIM-S-P-{shortK}", "Dry Run", f"SIM-B-P-{longK}", "Dry Run"

        short_instrument = self.get_instrument_details(short_sym)
        long_instrument = self.get_instrument_details(long_sym)

        if not all([short_instrument, long_instrument]):
            msg = f"Could not find instrument details for put spread: {short_sym}, {long_sym}"
            self.logger.error(msg)
            return None, msg, None, msg

        lot_size = int(short_instrument.get('lotsize', 25)) # Default to 25
        qty = lots * lot_size

        short_token = short_instrument['token']
        long_token = long_instrument['token']

        oid_s, msg_s = self._place_order(short_sym, short_token, "SELL", qty)
        oid_l, msg_l = self._place_order(long_sym, long_token, "BUY", qty)
        return oid_s, msg_s, oid_l, msg_l

    def close_spread(self, oid_short: str, oid_long: str):
        if self.dry_run:
            self.logger.info(f"[DRY] CLOSE SPREAD short={oid_short} long={oid_long}")
            return

        for oid in [oid_short, oid_long]:
            order_details = self._get_order_details(oid)
            if not order_details:
                self.logger.error(f"Could not retrieve details for order {oid}. Cannot close position.")
                continue

            symbol = order_details['tradingsymbol']
            token = self.get_token(symbol)
            qty = int(order_details['quantity'])
            tx_type = order_details['transactiontype']

            # Reverse the transaction
            reverse_tx_type = "BUY" if tx_type == "SELL" else "SELL"

            self._place_order(symbol, token, reverse_tx_type, qty)

    def _get_order_details(self, order_id: str) -> Optional[dict]:
        """Fetches details of a specific order from the order book."""
        try:
            order_book = self.sc.orderBook()
            if order_book and order_book.get('status') and order_book.get('data'):
                for order in order_book['data']:
                    if order.get('orderid') == order_id:
                        return order
            self.logger.warning(f"Order ID {order_id} not found in the order book.")
            return None
        except Exception as e:
            self.logger.error(f"Exception while fetching order book: {e}")
            return None
