import datetime
import json
import re
import time

import extra_streamlit_components as stx
import gspread
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

# ==============================================================================
# STREAMLIT PAGE CONFIGURATION
# ==============================================================================
st.set_page_config(
    page_title="Quantix | SMC Terminal",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# INSTITUTIONAL DARK UI CSS
st.markdown(
    """
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header[data-testid="stHeader"] {background: transparent;}
        .stDeployButton {display: none;}

        .block-container {
            padding-top: 1.5rem !important;
        }

        [data-testid="collapsedControl"] {
            display: flex !important;
            background-color: #1f6feb !important;
            color: white !important;
            border-radius: 50% !important;
            padding: 5px !important;
        }

        .neon-title {
            background: -webkit-linear-gradient(45deg, #1f6feb, #b359f8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-size: 3.8rem;
            font-weight: 900;
            line-height: 1.1;
            margin-bottom: 10px;
        }

        /* Dark Institutional Metric Cards */
        div[data-testid="stMetric"] {
            border: 1px solid #21262d;
            background-color: #12161f;
            padding: 18px;
            border-radius: 10px;
            box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
            text-align: center;
        }
        div[data-testid="stMetric"] label {
            font-size: 1.0rem !important;
            font-weight: 600 !important;
            color: #8b949e !important;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            color: #e6edf3 !important;
            font-size: 2.1rem !important;
            font-weight: 800 !important;
        }

        /* Form Customization */
        div[data-testid="stForm"] {
            border: 1px solid #1f6feb;
            border-radius: 14px;
            padding: 25px;
            box-shadow: 0 8px 24px rgba(31, 111, 235, 0.12);
            background-color: #12161f;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# ==============================================================================
# 1. GOOGLE SHEETS CONNECTION, LOGGING & DATA INGESTION
# ==============================================================================
SHEET_ID = "1QGQ-sQwA1hdQAKKd9KxzVQMqV95l7zs5JXlPKY1hyGU"


def _get_gspread_sheet():
    creds_json = None
    try:
        if "GCP_SERVICE_ACCOUNT_JSON" in st.secrets:
            creds_json = st.secrets["GCP_SERVICE_ACCOUNT_JSON"]
    except Exception:
        creds_json = None

    if creds_json:
        creds = json.loads(creds_json) if isinstance(creds_json, str) else dict(creds_json)
        gc = gspread.service_account_from_dict(creds)
    else:
        gc = gspread.service_account(filename="service_account.json")

    return gc.open_by_key(SHEET_ID)


def log_to_sheet(tab_name, row_values):
    try:
        sh = _get_gspread_sheet()
        sh.worksheet(tab_name).append_row(row_values, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        print(f"Sheet log error ({tab_name}): {e}")
        return False


@st.cache_data(ttl=300)
def fetch_enterprise_data():
    r_cols = [
        "Ticker",
        "Asset_Class",
        "Live_Price",
        "Trend",
        "Nearest_Demand_OB",
        "Demand_Dist_%",
        "Nearest_Supply_OB",
        "Supply_Dist_%",
    ]
    a_cols = [
        "Ticker",
        "Asset_Class",
        "Direction",
        "Live_Price",
        "Entry",
        "Stop_Loss",
        "Target",
        "R:R",
        "OB_Date",
    ]

    try:
        sh = _get_gspread_sheet()
        r_df = pd.DataFrame(sh.worksheet("Radar").get_all_records())
        a_df = pd.DataFrame(sh.worksheet("Alerts").get_all_records())

        if r_df.empty or "Trend" not in r_df.columns:
            r_df = pd.DataFrame(columns=r_cols)
        else:
            for col in ["Demand_Dist_%", "Supply_Dist_%"]:
                if col in r_df.columns:
                    r_df[col] = pd.to_numeric(r_df[col], errors="coerce")
            if "Asset_Class" in r_df.columns and "Ticker" in r_df.columns:
                r_df = r_df.sort_values(by=["Asset_Class", "Ticker"], ascending=[True, True])

        if a_df.empty or "Direction" not in a_df.columns:
            a_df = pd.DataFrame(columns=a_cols)
        else:
            if "R:R" in a_df.columns:
                a_df["R:R"] = pd.to_numeric(a_df["R:R"], errors="coerce")
            if "Asset_Class" in a_df.columns and "Ticker" in a_df.columns:
                a_df = a_df.sort_values(by=["Asset_Class", "Ticker"], ascending=[True, True])

        fetched_at = datetime.datetime.now().strftime("%d %b %Y, %I:%M %p IST")
        return r_df, a_df, fetched_at

    except Exception as e:
        print(f"Data load error: {e}")
        return pd.DataFrame(columns=r_cols), pd.DataFrame(columns=a_cols), "Sync unavailable"


@st.cache_data(ttl=300)
def fetch_nifty_context():
    try:
        hist = yf.Ticker("^NSEI").history(period="5d")
        if len(hist) >= 2:
            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2])
            chg_pct = ((last_close - prev_close) / prev_close) * 100
            return last_close, chg_pct
    except Exception:
        pass
    return None, None


# ==============================================================================
# 2. INITIALIZE SESSION STATE & COOKIES
# ==============================================================================
cookie_manager = stx.CookieManager(key="quantix_cookies")

if "is_registered" not in st.session_state:
    st.session_state.is_registered = False
if "logged_out" not in st.session_state:
    st.session_state.logged_out = False
if "pending_cookie" not in st.session_state:
    st.session_state.pending_cookie = False

if st.session_state.logged_out:
    if cookie_manager.get(cookie="quantix_auth") is not None:
        cookie_manager.delete("quantix_auth")
elif st.session_state.pending_cookie:
    cookie_manager.set(
        "quantix_auth",
        "valid",
        expires_at=datetime.datetime.now() + datetime.timedelta(days=30),
    )
    st.session_state.pending_cookie = False
elif cookie_manager.get(cookie="quantix_auth") == "valid":
    st.session_state.is_registered = True


# ==============================================================================
# 3. ONBOARDING & PUBLIC BETA REGISTRATION PAGE
# ==============================================================================
def show_registration_page():
    try:
        st.image("quantix_logo.png", width=300)
    except Exception:
        pass

    st.markdown("<br>", unsafe_allow_html=True)

    c1, spacer, c2 = st.columns([1.2, 0.1, 1])

    with c1:
        st.markdown("<div class='neon-title'>Quantix Terminal.</div>", unsafe_allow_html=True)
        st.markdown(
            "<p style='font-size: 1.3rem; margin-bottom: 30px; color: #8b949e;'>Institutional Smart Money Concepts mapping across 2,600+ equities and global commodities. <b>Stop guessing. Start executing.</b></p>",
            unsafe_allow_html=True,
        )

        st.success("⚡ **Fully Automated Daily Scans:** The quantitative engine processes the entire NSE universe EOD.")
        st.info("🎯 **Actionable Strike Zones:** Algorithmic liquidity sweep detection delivers only high-probability setups.")
        st.warning("📡 **3-Stage State Funnel:** Track Master Radar, Forming setups (≤ 1.5% from OB), and confirmed Actionable Strikes.")

        st.markdown("---")
        st.markdown("### ⚡ Open Public Beta")
        st.markdown("Enter your name and email for instant access to the live terminal.")

    with c2:
        with st.form("registration_form", clear_on_submit=False):
            st.markdown("<h3 style='margin-bottom: 5px; color: #1f6feb;'>⚡ Public Beta Access</h3>", unsafe_allow_html=True)
            st.markdown("<p style='margin-bottom: 20px; font-size: 0.95rem; color: #8b949e;'>Enter your name and email to unlock the live terminal.</p>", unsafe_allow_html=True)

            name = st.text_input("Full Name", placeholder="Full Name", autocomplete="off")
            email = st.text_input("Email Address", placeholder="name@company.com", autocomplete="off")

            st.markdown("<br>", unsafe_allow_html=True)
            submit = st.form_submit_button("Unlock Live Terminal ⚡", use_container_width=True, type="primary")

            if submit:
                if name.strip() and email.strip():
                    if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email.strip()):
                        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        log_to_sheet("Users", [ts, name.strip(), email.strip().lower()])
                        st.session_state.logged_out = False
                        st.session_state.pending_cookie = True
                        st.session_state.is_registered = True
                        st.success("Access granted. Initializing Quantix Terminal...")
                        time.sleep(1)
                        st.session_state.is_registered = True
                        st.rerun()
                    else:
                        st.error("❌ Please enter a valid email address.")
                else:
                    st.error("Please provide both your name and email.")


# ==============================================================================
# 4. MAIN TERMINAL & NAVIGATION PANEL
# ==============================================================================
def show_main_terminal():
    try:
        st.sidebar.image("quantix_logo.png", use_container_width=True)
    except Exception:
        st.sidebar.markdown("<h2 style='color: #1f6feb;'>⚡ Quantix Terminal</h2>", unsafe_allow_html=True)

    st.sidebar.caption("SMC Engine v1.2 | Beta Build")
    st.sidebar.divider()

    nav_choice = st.sidebar.radio("Navigation Menu", ["Live Market Dashboard", "📖 How to Trade Setups"], index=0)

    if st.sidebar.button("🔄 Force Data Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.sidebar.divider()
    st.sidebar.markdown("### 🐛 Beta Feedback")

    with st.sidebar.form("feedback_form", clear_on_submit=True):
        fb_email = st.text_input("Your Email (optional)", placeholder="name@company.com", autocomplete="off")
        fb_text = st.text_area("Found a bug or have an idea?", placeholder="Type your feedback here...")
        fb_submit = st.form_submit_button("Submit Feedback 🚀", use_container_width=True)

        if fb_submit:
            if fb_text.strip():
                ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ok = log_to_sheet("Feedback", [ts, fb_email.strip().lower() or "Anonymous", fb_text.strip()])
                if ok:
                    st.success("Thank you! Feedback logged.")
                else:
                    st.error("Failed to log feedback. Try again later.")
            else:
                st.warning("Please type your feedback before submitting.")

    st.sidebar.divider()
    st.sidebar.caption(
        "⚠️ **Disclaimer:** Quantix is a quantitative research tool for educational purposes. We are not SEBI-registered financial advisors. Trading equities and commodities carries significant risk. Do not execute trades blindly."
    )

    st.sidebar.divider()
    if st.sidebar.button("🚪 Logout / Clear Session", use_container_width=True):
       st.session_state.logged_out = True
       st.session_state.pending_cookie = False
       st.session_state.is_registered = False
       st.rerun()

    if nav_choice == "📖 How to Trade Setups":
        st.title("📖 Standard Operating Procedure")
        st.markdown(
            """
            ### Institutional SMC Execution Guide

            **1. Check Stage 3: Actionable Strikes**
            - Assets listed here have triggered a structural reversal within a confirmed institutional block.
            - Do not execute blindly. This is a watchlist of high-probability zones.

            **2. Track Stage 2: Forming Setups**
            - Monitor assets trading within **1.5%** of their nearest unmitigated Supply or Demand Order Block.
            - Verify the liquidity sweep and Fair Value Gap (FVG) on your broker chart before placing an order.

            **3. Risk Management Engine**
            - Never risk more than 1% of your total equity on a single strike.
            """
        )
        return

    if nav_choice == "Live Market Dashboard":
        st.markdown("<h1 style='text-align: center; color: #b359f8;'>⚡ Quantix Quantitative Terminal</h1>", unsafe_allow_html=True)
        st.markdown("<p style='text-align: center; font-size: 1.15rem; color: #8b949e;'>Institutional Smart Money Concepts (SMC) State-Machine Engine</p>", unsafe_allow_html=True)

        if "first_visit" not in st.session_state:
            st.session_state.first_visit = True

        if st.session_state.first_visit:
            with st.expander("👋 Welcome to Quantix Beta! Read this before trading:", expanded=True):
                st.markdown(
                    """
                    **Here is how to navigate the terminal:**
                    - ⬅️ **The Sidebar:** Use the left panel to switch between the *Live Dashboard* and the *How to Trade* guide.
                    - 🔥 **3-Stage State Funnel:** Move from **Master Radar** → **Forming (≤ 1.5% from OB)** → **Actionable Strikes**.
                    - ⚙️ **Force Refresh:** EOD Data updates at 7:00 PM IST. Click **Force Data Refresh** in the sidebar to pull the latest cloud sheet.
                    """
                )
                if st.button("Got it! Let's hunt some setups.", type="primary"):
                    st.session_state.first_visit = False
                    st.rerun()

        radar_df, alerts_df, last_sync = fetch_enterprise_data()

        st.markdown(
            f"<p style='text-align: center; font-size: 0.95rem; color: #2ea043; font-weight: bold;'>🟢 Cloud Feed Synced: {last_sync} (EOD Pipeline)</p>",
            unsafe_allow_html=True,
        )

        # --- MACRO MARKET CONTEXT STRIP ---
        nifty_val, nifty_chg = fetch_nifty_context()
        bull_cnt = len(radar_df[radar_df["Trend"].astype(str).str.contains("BULLISH|LONG", na=False)]) if not radar_df.empty else 0
        bear_cnt = len(radar_df[radar_df["Trend"].astype(str).str.contains("BEARISH|SHORT", na=False)]) if not radar_df.empty else 0

        if not radar_df.empty:
            d_dist = pd.to_numeric(radar_df["Demand_Dist_%"], errors="coerce").abs()
            s_dist = pd.to_numeric(radar_df["Supply_Dist_%"], errors="coerce").abs()
            forming_df = radar_df[(d_dist <= 1.5) | (s_dist <= 1.5)].copy()
        else:
            forming_df = pd.DataFrame(columns=radar_df.columns)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            "🇮🇳 NIFTY 50",
            f"{nifty_val:,.2f}" if nifty_val else "N/A",
            f"{nifty_chg:+.2f}%" if nifty_chg is not None else "Offline",
            delta_color="normal" if nifty_chg is not None else "off",
        )
        m2.metric("📈 Market Breadth", f"{bull_cnt} / {bear_cnt}", "Bullish vs Bearish", delta_color="off")
        m3.metric("🔥 Stage 2: Forming", len(forming_df), "≤ 1.5% from OB", delta_color="off")
        m4.metric("📡 Stage 1: Scanned", len(radar_df), "Active Universe", delta_color="off")

        st.markdown("---")

        # --- STRIKE HUD ---
        hud_total = len(alerts_df) if not alerts_df.empty else 0
        hud_longs = len(alerts_df[alerts_df["Direction"].astype(str).str.contains("LONG", na=False)]) if hud_total > 0 else 0
        hud_shorts = len(alerts_df[alerts_df["Direction"].astype(str).str.contains("SHORT", na=False)]) if hud_total > 0 else 0

        if hud_total > 0 and "R:R" in alerts_df.columns and not alerts_df["R:R"].dropna().empty:
            hud_rr = round(float(alerts_df["R:R"].max()), 2)
        else:
            hud_rr = 0.0

        c1, c2, c3 = st.columns([1, 2, 1])

        with c1:
            st.metric(
                "🎯 Active Strikes",
                hud_total,
                delta="Awaiting Execution" if hud_total > 0 else "Scanning...",
                delta_color="off",
            )
            st.metric(
                "💎 Peak R:R",
                f"1 : {hud_rr}",
                delta="Optimized" if hud_rr >= 2.0 else "Standby",
                delta_color="normal" if hud_rr >= 2.0 else "off",
            )

        with c2:
            bias_score = (hud_longs / hud_total * 100) if hud_total > 0 else 50
            fig_gauge = go.Figure(
                go.Indicator(
                    mode="gauge+number",
                    value=bias_score,
                    domain={"x": [0, 1], "y": [0, 1]},
                    title={"text": "Market Liquidity Bias", "font": {"size": 18, "color": "#8b949e"}},
                    number={"suffix": "% Long", "font": {"color": "#1f6feb"}},
                    gauge={
                        "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#21262d"},
                        "bar": {"color": "rgba(31, 111, 235, 0.9)", "thickness": 0.3},
                        "bgcolor": "rgba(0,0,0,0)",
                        "borderwidth": 2,
                        "bordercolor": "#21262d",
                        "steps": [
                            {"range": [0, 45], "color": "rgba(242, 54, 69, 0.75)"},
                            {"range": [45, 55], "color": "rgba(139, 148, 158, 0.3)"},
                            {"range": [55, 100], "color": "rgba(46, 160, 67, 0.75)"},
                        ],
                    },
                )
            )
            fig_gauge.update_layout(height=250, margin=dict(l=20, r=20, t=40, b=20), paper_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_gauge, use_container_width=True)

        with c3:
            st.metric("🟢 Long Bias", hud_longs)
            st.metric("🔴 Short Bias", hud_shorts)

        st.markdown("---")

        COMMODITY_MAP = {
            "GC=F": "Gold (USD)",
            "SI=F": "Silver (USD)",
            "CL=F": "Crude Oil (USD)",
            "BZ=F": "Brent Crude (USD)",
            "HG=F": "Copper (USD)",
            "NG=F": "Natural Gas (USD)",
            "PA=F": "Palladium (USD)",
            "PL=F": "Platinum (USD)",
        }

        def format_price(row, col_name):
            val = row.get(col_name, "None")
            if pd.isna(val) or val == "None" or str(val).strip() == "":
                return "None"
            try:
                val_float = float(val)
                if "COMMODITY" in str(row.get("Asset_Class", "")):
                    return f"$ {val_float:,.2f}"
                return f"₹ {val_float:,.2f}"
            except Exception:
                return val

        def heatmap_distance(val):
            if pd.isna(val) or val == "None" or str(val).strip() == "":
                return ""
            try:
                v = float(val)
                if abs(v) <= 1.5:
                    return "background-color: rgba(210, 153, 34, 0.35); color: #f0b90b; font-weight: bold;"
                if 1.5 < v <= 5.0:
                    return "color: #8b949e;"
                return "color: #6e7681;"
            except Exception:
                return ""

        def highlight_trend(val):
            s = str(val)
            if "BULLISH" in s or "LONG" in s:
                return "color: #2ea043; font-weight: bold;"
            if "BEARISH" in s or "SHORT" in s:
                return "color: #f23645; font-weight: bold;"
            if "IN_TRADE" in s:
                return "color: #1f6feb; font-weight: bold;"
            return "color: #8b949e;"

        # --- 3-STAGE STATE FUNNEL TABS ---
        tabs = st.tabs(
            [
                f"🎯 Stage 3: Actionable Strikes ({hud_total})",
                f"🔥 Stage 2: Forming ({len(forming_df)})",
                f"📡 Stage 1: Master Radar ({len(radar_df)})",
            ]
        )

        with tabs[0]:
            st.subheader("Actionable Strike Setups")
            st.caption("Confirmed entries meeting structural reversal rules within the immediate institutional strike zone.")

            if hud_total > 0:
                alerts_display = alerts_df.copy()
                alerts_display["Asset"] = alerts_display["Ticker"].map(
                    lambda x: COMMODITY_MAP.get(x, str(x).replace(".NS", ""))
                )

                for col in ["Live_Price", "Entry", "Stop_Loss", "Target"]:
                    if col in alerts_display.columns:
                        alerts_display[col] = alerts_display.apply(lambda row: format_price(row, col), axis=1)

                def highlight_direction(val):
                    if "LONG" in str(val):
                        return "background-color: rgba(46, 160, 67, 0.2); color: #2ea043; font-weight: bold; border-left: 4px solid #2ea043;"
                    if "SHORT" in str(val):
                        return "background-color: rgba(242, 54, 69, 0.2); color: #f23645; font-weight: bold; border-left: 4px solid #f23645;"
                    return ""

                styled_df = alerts_display.style.map(highlight_direction, subset=["Direction"])

                st.dataframe(
                    styled_df,
                    use_container_width=True,
                    height=400,
                    hide_index=True,
                    column_order=["Asset", "Asset_Class", "Direction", "Live_Price", "Entry", "Stop_Loss", "Target", "R:R", "OB_Date"],
                    column_config={
                        "Asset": st.column_config.TextColumn("Asset", width="medium"),
                        "Live_Price": st.column_config.TextColumn("Live Price"),
                        "Entry": st.column_config.TextColumn("Entry"),
                        "Stop_Loss": st.column_config.TextColumn("Stop Loss"),
                        "Target": st.column_config.TextColumn("Target"),
                        "R:R": st.column_config.ProgressColumn("Risk/Reward Profile", format="1:%.2f", min_value=0, max_value=5),
                    },
                )
            else:
                st.info("No assets currently in confirmed strike zone. Check Stage 2: Forming for approaching setups.")

        with tabs[1]:
            st.subheader("Forming Setups (≤ 1.5% from Institutional Zone)")
            st.caption("Assets actively approaching an unmitigated Supply or Demand Order Block—high-priority watchlist for upcoming triggers.")

            if not forming_df.empty:
                f_display = forming_df.copy()
                f_display["Asset"] = f_display["Ticker"].map(
                    lambda x: COMMODITY_MAP.get(x, str(x).replace(".NS", ""))
                )
                for col in ["Live_Price", "Nearest_Demand_OB", "Nearest_Supply_OB"]:
                    if col in f_display.columns:
                        f_display[col] = f_display.apply(lambda row: format_price(row, col), axis=1)

                styled_forming = f_display.style.map(highlight_trend, subset=["Trend"]).map(
                    heatmap_distance, subset=["Demand_Dist_%", "Supply_Dist_%"]
                )

                st.dataframe(
                    styled_forming,
                    use_container_width=True,
                    height=450,
                    hide_index=True,
                    column_order=[
                        "Asset",
                        "Asset_Class",
                        "Live_Price",
                        "Trend",
                        "Nearest_Demand_OB",
                        "Demand_Dist_%",
                        "Nearest_Supply_OB",
                        "Supply_Dist_%",
                    ],
                    column_config={
                        "Asset": st.column_config.TextColumn("Asset", width="medium"),
                        "Live_Price": st.column_config.TextColumn("Price"),
                        "Nearest_Demand_OB": st.column_config.TextColumn("Nearest Demand"),
                        "Nearest_Supply_OB": st.column_config.TextColumn("Nearest Supply"),
                        "Demand_Dist_%": st.column_config.NumberColumn("Demand Dist.", format="%.2f %%"),
                        "Supply_Dist_%": st.column_config.NumberColumn("Supply Dist.", format="%.2f %%"),
                    },
                )
            else:
                st.info("No assets currently within 1.5% of an active Order Block.")

        with tabs[2]:
            st.subheader("Global Asset Structure Radar")
            st.caption("Complete structural mapping of every scanned asset relative to nearest Supply and Demand blocks.")

            if not radar_df.empty:
                search_query = st.text_input("Filter Asset / Class:", placeholder="e.g. RELIANCE, Gold, COMMODITY...")

                radar_display = radar_df.copy()
                radar_display["Asset"] = radar_display["Ticker"].map(
                    lambda x: COMMODITY_MAP.get(x, str(x).replace(".NS", ""))
                )

                filtered_radar = (
                    radar_display
                    if not search_query
                    else radar_display[
                        radar_display["Asset"].str.contains(search_query, case=False, na=False)
                        | radar_display["Asset_Class"].astype(str).str.contains(search_query, case=False, na=False)
                    ]
                )

                for col in ["Live_Price", "Nearest_Demand_OB", "Nearest_Supply_OB"]:
                    if col in filtered_radar.columns:
                        filtered_radar[col] = filtered_radar.apply(lambda row: format_price(row, col), axis=1)

                styled_radar = filtered_radar.style.map(highlight_trend, subset=["Trend"]).map(
                    heatmap_distance, subset=["Demand_Dist_%", "Supply_Dist_%"]
                )

                st.dataframe(
                    styled_radar,
                    use_container_width=True,
                    height=500,
                    hide_index=True,
                    column_order=[
                        "Asset",
                        "Asset_Class",
                        "Live_Price",
                        "Trend",
                        "Nearest_Demand_OB",
                        "Demand_Dist_%",
                        "Nearest_Supply_OB",
                        "Supply_Dist_%",
                    ],
                    column_config={
                        "Asset": st.column_config.TextColumn("Asset", width="medium"),
                        "Live_Price": st.column_config.TextColumn("Price"),
                        "Nearest_Demand_OB": st.column_config.TextColumn("Nearest Demand"),
                        "Nearest_Supply_OB": st.column_config.TextColumn("Nearest Supply"),
                        "Demand_Dist_%": st.column_config.NumberColumn("Demand Dist. (Gold = Hot)", format="%.2f %%"),
                        "Supply_Dist_%": st.column_config.NumberColumn("Supply Dist. (Gold = Hot)", format="%.2f %%"),
                    },
                )
            else:
                st.info("Radar feed not found in cloud cache. Wait for next automated backend scan.")


# ==============================================================================
# 5. APP ROUTER
# ==============================================================================
if not st.session_state.is_registered:
    show_registration_page()
else:
    show_main_terminal()