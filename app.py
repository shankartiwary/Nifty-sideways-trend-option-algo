import streamlit as st
from strategy.deployable_algo import TradingBot, WaveCfg, SurvivorCfg
import queue
import logging
import sys

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
        client_code = st.text_input("Client Code", "YOUR_CLIENT_CODE")
        password = st.text_input("Password", "YOUR_PASSWORD", type="password")
        totp_secret = st.text_input("TOTP Secret", "YOUR_TOTP_SECRET")

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
col1, col2 = st.columns(2)

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


# --- Display Bot Status and Logs ---
status_placeholder = st.empty()
log_area = st.empty()

log_messages = []
while not st.session_state.log_queue.empty():
    log_messages.insert(0, st.session_state.log_queue.get())

if st.session_state.bot and st.session_state.bot._is_running:
    status_placeholder.success("Bot is RUNNING.")
else:
    status_placeholder.warning("Bot is STOPPED.")

import time

log_area.text_area("Live Logs", "\n".join(log_messages), height=400)

time.sleep(2)
st.rerun()
