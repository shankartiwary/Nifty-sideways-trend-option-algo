import streamlit as st
from strategy.deployable_algo import TradingBot, WaveCfg, SurvivorCfg
import queue
import logging
import sys
import pandas as pd

# --- Helper for logging ---
class QueueLogHandler(logging.Handler):
    def __init__(self, queue):
        super().__init__()
        self.queue = queue

    def emit(self, record):
        self.queue.put(self.format(record))

class StreamlitLogger:
    def __init__(self, queue):
        self.log_queue = queue
        self.logger = logging.getLogger('TradingBotLogger')
        if not self.logger.handlers:
            self.logger.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

            # Add queue handler to send logs to Streamlit UI
            queue_handler = QueueLogHandler(self.log_queue)
            queue_handler.setFormatter(formatter)
            self.logger.addHandler(queue_handler)

            # Add console handler to also print logs to console
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)

    def info(self, msg):
        self.logger.info(msg)

    def warning(self, msg):
        self.logger.warning(msg)

    def error(self, msg):
        self.logger.error(msg)


# --- Streamlit App Layout ---
st.set_page_config(layout="wide")
st.title("Trading Bot Controller")

# --- Connection Status Indicator ---
status_indicator = st.empty()

# --- Margin Ticker ---
margin_col1, margin_col2 = st.columns(2)
available_margin_ph = margin_col1.empty()
used_margin_ph = margin_col2.empty()

# --- Session State Initialization ---
if 'bot' not in st.session_state:
    st.session_state.bot = None
if 'log_queue' not in st.session_state:
    st.session_state.log_queue = queue.Queue()
if 'logger' not in st.session_state:
    st.session_state.logger = StreamlitLogger(st.session_state.log_queue)


# --- Sidebar for Configuration ---
with st.sidebar:
    st.header("Configuration")

    with st.expander("API Credentials", expanded=True):
        api_key = st.text_input("API Key", "YOUR_API_KEY")
        st.markdown('<a href="https://smartapi.angelbroking.com/my-apps" target="_blank" style="font-size: 0.8em;">How to get API Key?</a>', unsafe_allow_html=True)
        client_code = st.text_input("Client Code", "YOUR_CLIENT_CODE")
        password = st.text_input("Password", "YOUR_PASSWORD", type="password")
        totp_secret = st.text_input(
            "TOTP Secret",
            "YOUR_TOTP_SECRET",
            help="This is the Base32 secret key provided by Angel One when you set up 2FA with an authenticator app (like Google Authenticator)."
        )
        st.markdown('<a href="https://smartapi.angelbroking.com/enable-totp" target="_blank" style="font-size: 0.8em;">How to get TOTP Secret?</a>', unsafe_allow_html=True)

    with st.expander("Trading Parameters", expanded=True):
        underlying = st.text_input("Underlying", "NIFTY")
        expiry = st.text_input("Expiry (e.g., 24OCT24)", "24OCT24")
        dry_run = st.checkbox("Dry Run (Paper Trading)", True)

    with st.expander("Wave Extractor Config"):
        wave_cfg_params = {
            'step_pts': st.number_input("Wave: Step Pts", value=50),
            'spread_wing': st.number_input("Wave: Spread Wing", value=100),
            'min_credit': st.number_input("Wave: Min Credit", value=8.0),
            'lot_qty': st.number_input("Wave: Lot Qty", value=1)
        }

    with st.expander("Survivor Config"):
        survivor_cfg_params = {
            'pe_gap': st.number_input("Survivor: PE Gap", value=100),
            'ce_gap': st.number_input("Survivor: CE Gap", value=100),
            'symbol_gap': st.number_input("Survivor: Symbol Gap", value=200),
            'min_credit': st.number_input("Survivor: Min Credit", value=5.0),
            'lot_qty': st.number_input("Survivor: Lot Qty", value=1)
        }

# --- Main App Area ---
col1, col2, col3 = st.columns(3)

if col1.button("Start Bot"):
    if st.session_state.bot is None:
        bot_config = {
            'API_KEY': api_key,
            'CLIENT_CODE': client_code,
            'PASSWORD': password,
            'TOTP_SECRET': totp_secret,
            'DRY_RUN': dry_run,
            'UNDERLYING': underlying,
            'EXPIRY': expiry,
            'WAVE_CFG': wave_cfg_params,
            'SURVIVOR_CFG': survivor_cfg_params,
            'LTP_POLL_SEC': 1.0,
            'BAR_SECONDS': 60
        }
        st.session_state.bot = TradingBot(bot_config, st.session_state.logger)
        st.session_state.bot.start()
        st.success("Bot started successfully!")
    else:
        st.warning("Bot is already running.")

if col2.button("Stop Bot"):
    if st.session_state.bot:
        st.session_state.bot.stop()
        st.session_state.bot = None
        st.info("Bot stopped.")
    else:
        st.warning("Bot is not running.")

if col3.button("Fire Test Order"):
    if st.session_state.bot and st.session_state.bot.is_connected:
        st.session_state.bot.fire_test_order()
        st.success("Test order fired. Check the trade history below for the result.")
    else:
        st.warning("Bot must be running and connected to fire a test order.")


# --- Display Bot Status and Logs ---
status_placeholder = st.empty()

# --- Trade History ---
st.subheader("Trade History")
trade_history_placeholder = st.empty()

log_area = st.empty()

log_messages = []
while not st.session_state.log_queue.empty():
    log_messages.insert(0, st.session_state.log_queue.get())

if st.session_state.bot and st.session_state.bot._is_running:
    status_placeholder.success("Bot is RUNNING.")
    if st.session_state.bot.is_connected:
        status_indicator.markdown('<span style="color:green">●</span> Connected to Broker', unsafe_allow_html=True)
        funds = st.session_state.bot.get_funds()
        if funds:
            available_margin_ph.metric("Available Margin", f"₹ {funds['available']:,.2f}")
            used_margin_ph.metric("Used Margin", f"₹ {funds['used']:,.2f}")

        if st.session_state.bot.trade_history:
            df = pd.DataFrame(st.session_state.bot.trade_history)
            trade_history_placeholder.dataframe(df)
        else:
            trade_history_placeholder.info("No trades have been attempted yet.")

    else:
        status_indicator.markdown('<span style="color:red">●</span> Disconnected from Broker', unsafe_allow_html=True)
        available_margin_ph.metric("Available Margin", "₹ 0.00")
        used_margin_ph.metric("Used Margin", "₹ 0.00")
else:
    status_placeholder.warning("Bot is STOPPED.")
    status_indicator.markdown('<span style="color:red">●</span> Disconnected from Broker', unsafe_allow_html=True)
    available_margin_ph.metric("Available Margin", "₹ 0.00")
    used_margin_ph.metric("Used Margin", "₹ 0.00")


import time

log_area.text_area("Live Logs", "\n".join(log_messages), height=400)

time.sleep(2)
st.rerun()
