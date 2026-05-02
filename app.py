import streamlit as st
import pandas as pd
import numpy as np

# -----------------------------
# Dummy Data Setup
# -----------------------------
def load_dummy_prices():
    """Generate dummy electricity prices for testing"""
    timestamps = pd.date_range(start=pd.Timestamp.now().floor('D'), periods=96, freq='30T', tz='UTC')
    return pd.DataFrame({
        "timestamp_utc": timestamps,
        "timestamp_local": timestamps.tz_convert("Europe/London"),
        "price_p_per_kwh": np.random.rand(96) * 50  # random prices
    })

# -----------------------------
# Session State Defaults
# -----------------------------
if "df_prices" not in st.session_state:
    st.session_state.df_prices = load_dummy_prices()

if "region_letter" not in st.session_state:
    st.session_state.region_letter = "A"

if "tariff_code_base" not in st.session_state:
    st.session_state.tariff_code_base = "E-1R"

if "product_code" not in st.session_state:
    st.session_state.product_code = "DUMMY-24-10-01"

if "tariff_code" not in st.session_state:
    st.session_state.tariff_code = "DUMMY-TARIFF"

# -----------------------------
# Sidebar / Controls
# -----------------------------
st.sidebar.header("Settings")
region = st.sidebar.selectbox("Select region", ["A", "B", "C"])
if region != st.session_state.region_letter:
    st.session_state.region_letter = region
    st.session_state.df_prices = load_dummy_prices()  # reload dummy data

st.sidebar.write(f"Product code: {st.session_state.product_code}")
st.sidebar.write(f"Tariff code: {st.session_state.tariff_code}")

# -----------------------------
# Main Dashboard
# -----------------------------
st.title("Smart Energy Helper - Dummy Version")

st.subheader("Electricity Prices (Next 24h)")

st.dataframe(st.session_state.df_prices)

st.line_chart(
    st.session_state.df_prices.set_index("timestamp_local")["price_p_per_kwh"]
)

# -----------------------------
# Dummy AI Panel
# -----------------------------
st.subheader("AI Panel (Dummy)")

dummy_ai_response = "This is a placeholder for AI responses. The real AI panel is disabled."
st.text_area("AI Response", dummy_ai_response, height=100)

st.info("⚠️ Octopus API and OpenAI AI calls are disabled in this dummy version. "
        "Prices are randomly generated for demonstration only.")