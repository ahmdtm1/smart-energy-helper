from __future__ import annotations

import html as _html
import json
import os
import re
import smtplib
import socket
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

OpenAI = None
try:
    from openai import OpenAI as _OpenAI

    OpenAI = _OpenAI
except Exception:
    OpenAI = None

try:
    import plotly.graph_objects as go
except Exception:
    go = None

try:
    import mailtrap as mt
except Exception:
    mt = None

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors as rl_colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    HAS_REPORTLAB = True
except Exception:
    HAS_REPORTLAB = False


st.set_page_config(
    page_title="Smart Energy Helper",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

UK_TZ = ZoneInfo("Europe/London")

REGIONS = [
    ("A", "East England"),
    ("B", "East Midlands"),
    ("C", "London"),
    ("D", "Merseyside & Northern Wales"),
    ("E", "West Midlands"),
    ("F", "North Eastern England"),
    ("G", "North Western England"),
    ("H", "Southern England"),
    ("J", "South Eastern England"),
    ("K", "South Wales"),
    ("L", "South Western England"),
    ("M", "Yorkshire"),
    ("N", "Southern Scotland"),
    ("P", "Northern Scotland"),
]
REGION_DICT = dict(REGIONS)

# Carbon intensity by region (gCO2/kWh approximate averages)
CARBON_INTENSITY_BY_REGION = {
    "A": 210, "B": 225, "C": 180, "D": 195, "E": 230,
    "F": 200, "G": 190, "H": 205, "J": 185, "K": 215,
    "L": 200, "M": 220, "N": 170, "P": 145,
}

# Appliance library with power (kW), typical run hours, flexibility
APPLIANCE_LIBRARY = {
    "Washing Machine": {"power_kw": 1.5, "run_hours": 1.5, "flexible": True, "icon": "🫧"},
    "Tumble Dryer": {"power_kw": 3.0, "run_hours": 1.0, "flexible": True, "icon": "🌀"},
    "Dishwasher": {"power_kw": 1.2, "run_hours": 1.3, "flexible": True, "icon": "🍽️"},
    "EV Charger (7kW)": {"power_kw": 7.0, "run_hours": 4.0, "flexible": True, "icon": "🔌"},
    "EV Charger (22kW)": {"power_kw": 22.0, "run_hours": 1.5, "flexible": True, "icon": "⚡"},
    "Oven": {"power_kw": 2.2, "run_hours": 1.0, "flexible": False, "icon": "🔥"},
    "Kettle": {"power_kw": 3.0, "run_hours": 0.1, "flexible": False, "icon": "☕"},
    "Immersion Heater": {"power_kw": 3.0, "run_hours": 2.0, "flexible": True, "icon": "🚿"},
    "Heat Pump": {"power_kw": 1.5, "run_hours": 8.0, "flexible": True, "icon": "🌡️"},
    "Electric Shower": {"power_kw": 9.0, "run_hours": 0.17, "flexible": False, "icon": "🚿"},
    "Gaming PC": {"power_kw": 0.4, "run_hours": 3.0, "flexible": True, "icon": "🎮"},
    "Laptop": {"power_kw": 0.065, "run_hours": 8.0, "flexible": True, "icon": "💻"},
}

# Smart grid events (simulated demand response signals)
DEMAND_RESPONSE_EVENTS = [
    {"start_hour": 17, "end_hour": 19, "label": "Peak demand period", "action": "Avoid usage"},
    {"start_hour": 7, "end_hour": 9, "label": "Morning peak", "action": "Delay if possible"},
    {"start_hour": 0, "end_hour": 6, "label": "Off-peak overnight", "action": "Ideal for EV/storage"},
]

# ─── TARIFF COMPARISON DATA ──────────────────────────────────────────────────
COMPARISON_TARIFFS = {
    "Standard Variable (Ofgem Cap)": {"rate_p_per_kwh": 24.50, "standing_p_per_day": 61.64, "description": "Ofgem price cap rate — the default if you haven't switched."},
    "Octopus Go": {"rate_p_per_kwh": 15.50, "off_peak_rate": 7.50, "off_peak_hours": (0, 5), "standing_p_per_day": 46.36, "description": "Cheap overnight rate (00:30-05:30) for EV owners."},
    "Octopus Cosy": {"rate_p_per_kwh": 18.00, "off_peak_rate": 10.00, "off_peak_hours": (1, 7), "standing_p_per_day": 46.36, "description": "Heat pump friendly with longer cheap windows."},
    "Economy 7": {"rate_p_per_kwh": 22.00, "off_peak_rate": 9.50, "off_peak_hours": (0, 7), "standing_p_per_day": 53.00, "description": "Legacy two-rate tariff with 7h off-peak overnight."},
    "Fixed Rate (Typical)": {"rate_p_per_kwh": 22.36, "standing_p_per_day": 53.56, "description": "Typical 12-month fixed rate — price certainty, no flexibility reward."},
}


def _rerun() -> None:
    fn = getattr(st, "rerun", None)
    if callable(fn):
        fn()
    else:
        st.experimental_rerun()


def normalize_tariff_code(base_tariff_code: str, region_letter: str) -> str:
    tariff = (base_tariff_code or "").strip()
    if not tariff:
        return tariff
    if re.search(r"-[A-Z]$", tariff):
        return re.sub(r"-[A-Z]$", f"-{region_letter}", tariff)
    return f"{tariff}-{region_letter}"


def apply_theme() -> None:
    st.markdown(
        r"""
<style>
header, footer, #MainMenu { visibility: hidden; }
div[data-testid="stSidebarCollapseButton"] { display: none !important; }
div[data-testid="stSidebarCollapsedControl"] { display:none !important; }
div[data-testid="collapsedControl"] { display:none !important; }
button[kind="header"] { display:none !important; }
button[title*="sidebar" i] { display:none !important; }
button[aria-label*="sidebar" i] { display:none !important; }

html, body, [class*="css"] {
  font-family: "Avenir Next", "SF Pro Display", "Segoe UI", sans-serif;
}

.stApp{
  background:
    radial-gradient(1100px 640px at 14% 0%, rgba(168,85,247,0.24), transparent 55%),
    radial-gradient(920px 560px at 82% 0%, rgba(59,130,246,0.12), transparent 58%),
    radial-gradient(760px 520px at 55% 44%, rgba(34,197,94,0.08), transparent 68%),
    linear-gradient(180deg, #120d2f 0%, #0b0a24 48%, #060815 100%);
  color: rgba(255,255,255,0.94);
}

.block-container{
  max-width: 1400px;
  padding-top: 0.7rem;
  padding-bottom: 1.25rem;
  padding-left: 1.7rem;
  padding-right: 1.7rem;
}

/* ── SIDEBAR: narrowed to 210px ── */
section[data-testid="stSidebar"]{
  width: 210px !important;
  min-width: 210px !important;
  background: linear-gradient(180deg, rgba(255,255,255,0.065), rgba(255,255,255,0.02));
  border-right: 1px solid rgba(255,255,255,0.10);
  box-shadow: inset -1px 0 0 rgba(255,255,255,0.04);
}
section[data-testid="stSidebar"] > div{
  width: 210px !important;
  min-width: 210px !important;
}
section[data-testid="stSidebar"] .block-container{
  padding-top: 0.12rem !important;
  padding-bottom: 0.28rem !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
  width: 210px !important;
}

.sidebrand{
  display:flex;
  align-items:center;
  gap:7px;
  padding: 12px 10px 8px 10px;
  font-weight:900;
  font-size:16px;
  letter-spacing: 0.2px;
}
.sidebrand .bolt{
  font-size:16px;
  filter: drop-shadow(0 0 14px rgba(251,191,36,0.45));
}

.navbtn{ width:100% !important; }
.navbtn > button{
  position: relative !important;
  width: calc(100% - 16px) !important;
  box-sizing: border-box !important;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-start !important;
  gap: 7px !important;
  border-radius: 12px !important;
  border: 1px solid rgba(255,255,255,0.12) !important;
  background: linear-gradient(180deg, rgba(83,54,130,0.54), rgba(61,40,101,0.48)) !important;
  text-align: left !important;
  padding: 8px 10px 8px 28px !important;
  margin: 4px 8px !important;
  min-height: 40px !important;
  font-weight: 700 !important;
  font-size: 0.82rem !important;
  letter-spacing: 0.05px;
  box-shadow: 0 4px 10px rgba(0,0,0,0.14);
}
section[data-testid="stSidebar"] .stButton,
section[data-testid="stSidebar"] .stButton > button{
  width:100% !important;
  box-sizing:border-box !important;
}
.navbtn > button::after{
  content: "";
  position: absolute;
  left: 11px;
  top: 50%;
  transform: translateY(-50%);
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgba(192,132,252,0.55);
  box-shadow: 0 0 8px rgba(192,132,252,0.42);
}
.navbtn.active > button{
  background: linear-gradient(180deg, rgba(116,74,190,0.58), rgba(86,54,146,0.52)) !important;
  border-color: rgba(192,132,252,0.55) !important;
  box-shadow:
    inset 2px 0 0 0 rgba(216,180,254,0.95),
    0 6px 14px rgba(56,30,94,0.18) !important;
}
.navbtn.active > button::after{
  background: rgba(216,180,254,0.99);
  box-shadow: 0 0 8px rgba(216,180,254,0.65), 0 0 14px rgba(168,85,247,0.32);
}

section[data-testid="stSidebar"] .panel{
  margin: 6px 8px 0 8px !important;
  padding: 10px !important;
  border-radius: 14px !important;
}

section[data-testid="stSidebar"] .pill{
  font-size: 0.76rem !important;
  padding: 5px 8px !important;
}

/* sidebar select/radio smaller */
section[data-testid="stSidebar"] label {
  font-size: 0.78rem !important;
}
section[data-testid="stSidebar"] .stSelectbox [data-baseweb="select"] > div {
  font-size: 0.78rem !important;
  min-height: 34px !important;
}

.card{
  background: linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.024));
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 24px;
  padding: 22px;
  box-shadow: 0 18px 42px rgba(0,0,0,0.25);
}
.panel{
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 20px;
  padding: 16px;
}
.pill{
  display:inline-block;
  padding: 10px 14px;
  border-radius: 999px;
  border: 1px solid rgba(255,255,255,0.14);
  background: rgba(8,10,24,0.56);
  font-weight: 800;
  margin-right: 10px;
  margin-bottom: 10px;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
}
.mini-metric{
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 18px;
  padding: 14px;
  min-height: 88px;
}
.mini-metric .label{
  opacity: 0.72;
  font-size: 0.85rem;
  margin-bottom: 8px;
}
.mini-metric .value{
  font-size: 1.2rem;
  font-weight: 900;
}

.rec{
  border-radius: 18px;
  padding: 15px 16px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  margin-bottom: 10px;
}
.rec.green{
  background: linear-gradient(180deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05));
  border-color: rgba(34,197,94,0.24);
}
.rec.yellow{
  background: linear-gradient(180deg, rgba(245,158,11,0.15), rgba(245,158,11,0.05));
  border-color: rgba(245,158,11,0.22);
}
.rec.red{
  background: linear-gradient(180deg, rgba(251,113,133,0.15), rgba(251,113,133,0.05));
  border-color: rgba(251,113,133,0.22);
}
.rec.blue{
  background: linear-gradient(180deg, rgba(59,130,246,0.15), rgba(59,130,246,0.05));
  border-color: rgba(59,130,246,0.24);
}

/* Carbon badge */
.carbon-badge{
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 0.82rem;
  font-weight: 800;
}
.carbon-low{ background: rgba(34,197,94,0.18); border: 1px solid rgba(34,197,94,0.35); color: #4ade80; }
.carbon-mid{ background: rgba(245,158,11,0.18); border: 1px solid rgba(245,158,11,0.35); color: #fbbf24; }
.carbon-high{ background: rgba(251,113,133,0.18); border: 1px solid rgba(251,113,133,0.35); color: #fb7185; }

/* Smart schedule table */
.sched-row{
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding: 10px 14px;
  border-radius: 12px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.03);
  margin-bottom: 8px;
  font-size: 0.88rem;
}
.sched-row .badge-ok{ color:#4ade80; font-weight:800; }
.sched-row .badge-warn{ color:#fbbf24; font-weight:800; }
.sched-row .badge-bad{ color:#fb7185; font-weight:800; }

/* Compare page winner row */
.compare-row{
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding: 12px 16px;
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.03);
  margin-bottom: 8px;
  font-size: 0.9rem;
}
.compare-row.winner{
  background: linear-gradient(180deg, rgba(34,197,94,0.15), rgba(34,197,94,0.05));
  border-color: rgba(34,197,94,0.30);
}

.stButton button, .stDownloadButton button, .stFormSubmitButton button{
  border-radius: 18px !important;
  border: 1px solid rgba(168,85,247,0.34) !important;
  background: linear-gradient(180deg, rgba(97,61,156,0.96), rgba(73,44,123,0.97)) !important;
  color: rgba(255,255,255,0.96) !important;
  padding: 12px 16px !important;
  min-height: 52px !important;
  font-weight: 800 !important;
  letter-spacing: 0.15px;
  box-shadow: 0 12px 24px rgba(29,16,59,0.24);
}
.stButton button:hover, .stDownloadButton button:hover, .stFormSubmitButton button:hover{
  background: linear-gradient(180deg, rgba(112,73,180,0.98), rgba(82,49,138,1)) !important;
}

div[data-testid="stTextInput"] [data-baseweb="base-input"]{
  background: rgba(0,0,0,0.30) !important;
  color: rgba(255,255,255,0.94) !important;
  border: 1px solid rgba(255,255,255,0.14) !important;
  border-radius: 16px !important;
}
div[data-testid="stNumberInput"] [data-baseweb="base-input"]{
  background: rgba(0,0,0,0.30) !important;
  color: rgba(255,255,255,0.94) !important;
  border: 1px solid rgba(255,255,255,0.14) !important;
  border-radius: 16px !important;
}
div[data-testid="stSelectbox"] [data-baseweb="select"] > div{
  background: rgba(0,0,0,0.30) !important;
  border: 1px solid rgba(255,255,255,0.14) !important;
  border-radius: 16px !important;
}
div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input{
  color: rgba(255,255,255,0.94) !important;
}

.ai-glass{
  background: linear-gradient(160deg, rgba(83,54,140,0.18) 0%, rgba(15,12,40,0.55) 100%);
  border: 1px solid rgba(168,85,247,0.22);
  border-radius: 24px;
  padding: 18px 16px 14px 16px;
  box-shadow: 0 20px 50px rgba(0,0,0,0.38), inset 0 1px 0 rgba(255,255,255,0.06);
}
.ai-head{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:10px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid rgba(255,255,255,0.07);
}
.ai-title{
  display:flex;
  align-items:center;
  gap:10px;
  font-weight:900;
  font-size:18px;
  letter-spacing: -0.2px;
}
.ai-title-icon{
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: linear-gradient(135deg, #ffbf47 0%, #ff922b 100%);
  display: grid;
  place-items: center;
  box-shadow: 0 4px 12px rgba(255,146,43,0.30);
  font-size: 16px;
}
.ai-status-badge{
  display:inline-flex;
  align-items:center;
  gap:6px;
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.4px;
  text-transform: uppercase;
  padding: 4px 10px;
  border-radius: 999px;
}
.ai-status-badge.on{
  background: rgba(34,197,94,0.12);
  border: 1px solid rgba(34,197,94,0.28);
  color: rgba(74,222,128,0.95);
}
.ai-status-badge.off{
  background: rgba(148,163,184,0.08);
  border: 1px solid rgba(148,163,184,0.18);
  color: rgba(148,163,184,0.75);
}
.ai-dot{
  width:7px;
  height:7px;
  border-radius:999px;
}
.ai-dot.live{
  background: rgba(34,197,94,0.95);
  animation: pulse-dot 1.8s infinite ease-in-out;
}
.ai-dot.off{
  background: rgba(148,163,184,0.8);
}
@keyframes pulse-dot{
  0%,100%{box-shadow:0 0 0 0 rgba(34,197,94,0.55);}
  50%{box-shadow:0 0 0 6px rgba(34,197,94,0);}
}

.msg-row{
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 8px 0;
}
.msg-row .badge{
  width: 34px;
  height: 34px;
  min-width: 34px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  box-shadow: 0 4px 10px rgba(0,0,0,0.22);
}
.msg-row.assistant .badge{
  background: linear-gradient(135deg, #ffbf47 0%, #ff922b 100%);
}
.msg-row.user .badge{
  background: linear-gradient(135deg, #a78bfa 0%, #7c3aed 100%);
}
.msg-row.assistant .bubble{
  background: rgba(255,255,255,0.05);
  border: 1px solid rgba(255,255,255,0.09);
  border-radius: 4px 16px 16px 16px;
  padding: 10px 13px;
  line-height: 1.55;
  font-size: 0.9rem;
  color: rgba(255,255,255,0.92);
  word-break: break-word;
  overflow-wrap: anywhere;
  flex: 1;
}
.msg-row.user .bubble{
  background: rgba(124,58,237,0.18);
  border: 1px solid rgba(124,58,237,0.28);
  border-radius: 16px 4px 16px 16px;
  padding: 10px 13px;
  line-height: 1.55;
  font-size: 0.9rem;
  color: rgba(255,255,255,0.92);
  word-break: break-word;
  overflow-wrap: anywhere;
  flex: 1;
}

.ai-chatbox{
  margin-top: 0;
  height: 280px;
  overflow-y: auto;
  padding: 4px 2px;
  scroll-behavior: smooth;
}

.typing{ display:inline-flex; align-items:center; gap:5px; }
.dot{
  width:5px;
  height:5px;
  border-radius:50%;
  background: rgba(255,255,255,0.75);
  animation: blink 1.2s infinite ease-in-out;
  opacity: .3;
}
.dot.d2{ animation-delay: .2s; }
.dot.d3{ animation-delay: .4s; }
@keyframes blink{
  0%,80%,100%{opacity:.25;transform:translateY(0)}
  40%{opacity:1;transform:translateY(-3px)}
}

.ai-divider{
  height: 1px;
  background: rgba(255,255,255,0.07);
  margin: 12px 0;
}

.ai-starters-label{
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.5px;
  text-transform: uppercase;
  color: rgba(168,85,247,0.85);
  margin-bottom: 8px;
}

.ai-input-row {
  margin-top: 10px;
}
.ai-compose-shell{
  background: rgba(10,8,28,0.70);
  border: 1px solid rgba(168,85,247,0.40);
  border-radius: 999px;
  padding: 4px 4px 4px 14px;
}
.ai-compose-shell form{
  margin: 0 !important;
}
.ai-compose-shell [data-testid="stHorizontalBlock"]{
  gap: 0.2rem !important;
  align-items: center !important;
}
.ai-input .stTextInput{
  margin-bottom: 0 !important;
}
.ai-input .stTextInput input{
  border-radius: 999px !important;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  color: rgba(255,255,255,0.94) !important;
  height: 40px;
  font-size: 0.88rem !important;
  padding: 8px 8px !important;
}
.ai-input .stTextInput input::placeholder{
  color: rgba(255,255,255,0.35) !important;
}
.ai-connected-send{
  display: flex;
  align-items: center;
}
.ai-connected-send .stFormSubmitButton,
.ai-connected-send .stFormSubmitButton > button{
  width: 100% !important;
}
.ai-connected-send .stFormSubmitButton > button{
  border-radius: 999px !important;
  background: linear-gradient(135deg, rgba(168,85,247,0.95), rgba(109,40,217,0.98)) !important;
  border: 1px solid rgba(168,85,247,0.6) !important;
  width: 40px !important;
  min-width: 40px !important;
  height: 40px !important;
  min-height: 40px !important;
  padding: 0 !important;
  box-shadow: 0 4px 14px rgba(109,40,217,0.35) !important;
  font-size: 1rem !important;
}

.ai-chip-grid{
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px;
  margin-bottom: 4px;
}
.ai-chip{
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 10px 12px;
  border-radius: 14px;
  border: 1px solid rgba(255,255,255,0.10);
  background: rgba(255,255,255,0.04);
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.ai-chip:hover{
  background: rgba(168,85,247,0.12);
  border-color: rgba(168,85,247,0.35);
}
.ai-chip .chip-icon{
  font-size: 15px;
  line-height: 1;
}
.ai-chip .chip-title{
  font-size: 0.82rem;
  font-weight: 800;
  color: rgba(255,255,255,0.94);
  white-space: nowrap;
}
.ai-chip .chip-sub{
  font-size: 0.72rem;
  color: rgba(255,255,255,0.48);
  line-height: 1.2;
}
.ai-chip-row .stButton > button{
  min-height: 0 !important;
  height: auto !important;
  border-radius: 14px !important;
  font-size: 0.82rem !important;
  font-weight: 700 !important;
  line-height: 1.2 !important;
  padding: 10px 12px !important;
  text-align: left !important;
  white-space: normal !important;
  background: rgba(255,255,255,0.04) !important;
  border: 1px solid rgba(255,255,255,0.10) !important;
  box-shadow: none !important;
}
.ai-chip-row .stButton > button:hover{
  background: rgba(168,85,247,0.12) !important;
  border-color: rgba(168,85,247,0.35) !important;
}

label[data-testid="stWidgetLabel"]:empty { display:none !important; }
div[data-testid="stMarkdownContainer"]:has(> :empty) { display:none !important; }
div.element-container:has(> :empty) { display:none !important; }
div[data-testid="stVerticalBlock"] > div:empty { display:none !important; height:0 !important; margin:0 !important; padding:0 !important; }
div[data-testid="stHorizontalBlock"] > div:empty { display:none !important; height:0 !important; margin:0 !important; padding:0 !important; }
hr {
  border: none !important;
  height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
}

:focus-visible{
  outline: 2px solid rgba(168,85,247,0.92) !important;
  outline-offset: 2px !important;
  border-radius: 8px !important;
}
</style>
""",
        unsafe_allow_html=True,
    )


apply_theme()


def _clean_key(key: str) -> str:
    value = (key or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def _get_secret(path: str, default: Any = None) -> Any:
    try:
        node = st.secrets
        for part in path.split("."):
            node = node[part]
        return node
    except Exception:
        return default


def get_openai_key() -> Optional[str]:
    key = _get_secret("openai.api_key")
    if key:
        return _clean_key(str(key))
    env_key = _clean_key(os.getenv("OPENAI_API_KEY", ""))
    return env_key or None


@st.cache_resource(show_spinner=False)
def get_ai_client(api_key: str):
    if OpenAI is None:
        return None
    return OpenAI(api_key=api_key)


def ai_call_text(prompt: str) -> Tuple[bool, str]:
    try:
        api_key = get_openai_key()
        if not api_key:
            return False, "Assistant is off. Add OPENAI_API_KEY or [openai].api_key in .streamlit/secrets.toml."
        if OpenAI is None:
            return False, "OpenAI package not installed. Run: pip install openai"

        client = get_ai_client(api_key)
        if client is None:
            return False, "Assistant client unavailable."

        try:
            model_name = _get_secret("openai.model", "gpt-4.1-mini")
            response = client.responses.create(model=model_name, input=prompt)
            text = (getattr(response, "output_text", "") or "").strip()
            if text:
                return True, text
            try:
                text = response.output[0].content[0].text.value.strip()
                if text:
                    return True, text
            except Exception:
                pass
            return False, "No response."
        except AttributeError:
            pass

        try:
            import openai as openai_v0

            openai_v0.api_key = api_key
            chat = openai_v0.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = (chat.choices[0].message.get("content") or "").strip()
            if text:
                return True, text
            return False, "No response."
        except Exception as exc:
            return False, f"Legacy failed: {str(exc)[:180]}"

    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
            return False, "Invalid API key. Re-check secrets and restart."
        return False, f"Error: {msg[:180]}"


def _get_mailtrap_smtp_settings() -> Tuple[bool, Dict[str, Any], str]:
    try:
        cfg = {
            "host": str(_get_secret("MAILTRAP_HOST")).strip(),
            "port": int(_get_secret("MAILTRAP_PORT", 2525)),
            "username": str(_get_secret("MAILTRAP_USERNAME")).strip(),
            "password": str(_get_secret("MAILTRAP_PASSWORD")).strip(),
            "from_name": str(_get_secret("MAIL_FROM_NAME", "Smart Energy")).strip(),
            "from_email": str(_get_secret("MAIL_FROM_EMAIL", "hello@example.com")).strip(),
        }
    except Exception as exc:
        return False, {}, f"Mailtrap SMTP not configured: {str(exc)[:120]}"
    if not cfg["username"] or not cfg["password"]:
        return False, {}, "Mailtrap SMTP username/password missing in secrets."
    return True, cfg, "ok"


def _get_mailtrap_sdk_settings() -> Tuple[bool, Dict[str, Any], str]:
    try:
        cfg = {
            "token": str(_get_secret("MAILTRAP_API_TOKEN")).strip(),
            "inbox_id": int(_get_secret("MAILTRAP_INBOX_ID")),
            "from_name": str(_get_secret("MAIL_FROM_NAME", "Smart Energy")).strip(),
            "from_email": str(_get_secret("MAIL_FROM_EMAIL", "hello@example.com")).strip(),
        }
    except Exception as exc:
        return False, {}, f"Mailtrap API not configured: {str(exc)[:120]}"
    if not cfg["token"]:
        return False, {}, "MAILTRAP_API_TOKEN missing in secrets."
    return True, cfg, "ok"


def send_email_mailtrap_smtp(to_email: str, subject: str, body_text: str) -> Tuple[bool, str]:
    ok, cfg, msg = _get_mailtrap_smtp_settings()
    if not ok:
        return False, msg

    email_message = EmailMessage()
    email_message["From"] = f"{cfg['from_name']} <{cfg['from_email']}>"
    email_message["To"] = to_email.strip()
    email_message["Subject"] = subject.strip()
    email_message.set_content(body_text or "")

    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except Exception:
                pass
            server.login(cfg["username"], cfg["password"])
            server.send_message(email_message)
        return True, "Sent"
    except smtplib.SMTPAuthenticationError:
        return False, "SMTP auth failed."
    except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, socket.timeout, ConnectionResetError):
        return False, "SMTP blocked or closed by network."
    except Exception as exc:
        return False, f"SMTP failed: {str(exc)[:160]}"


def send_email_mailtrap_sdk(to_email: str, subject: str, body_text: str) -> Tuple[bool, str]:
    if mt is None:
        return False, "Mailtrap package missing. Run: pip install mailtrap"

    ok, cfg, msg = _get_mailtrap_sdk_settings()
    if not ok:
        return False, msg

    try:
        mail = mt.Mail(
            sender=mt.Address(email=cfg["from_email"], name=cfg["from_name"]),
            to=[mt.Address(email=to_email.strip())],
            subject=subject.strip(),
            text=(body_text or ""),
            category="Smart Energy",
        )
        client = mt.MailtrapClient(token=cfg["token"], sandbox=True, inbox_id=cfg["inbox_id"])
        client.send(mail)
        return True, "Sent"
    except Exception as exc:
        return False, f"Mailtrap API failed: {str(exc)[:180]}"


def send_email_mailtrap(to_email: str, subject: str, body_text: str) -> Tuple[bool, str]:
    ok, smtp_msg = send_email_mailtrap_smtp(to_email, subject, body_text)
    if ok:
        return True, smtp_msg
    ok2, api_msg = send_email_mailtrap_sdk(to_email, subject, body_text)
    if ok2:
        return True, api_msg
    return False, f"Email failed. SMTP: {smtp_msg} | API: {api_msg}"


@st.cache_data(show_spinner=False)
def fetch_octopus_unit_rates(
    product_code: str,
    tariff_code: str,
    period_from: str,
    period_to: str,
) -> pd.DataFrame:
    base_url = f"https://api.octopus.energy/v1/products/{product_code}/electricity-tariffs/{tariff_code}/standard-unit-rates/"
    params = {"page_size": 1500, "period_from": period_from, "period_to": period_to}

    rows_all = []
    next_url = base_url
    first_page = True

    while next_url:
        if first_page:
            response = requests.get(next_url, params=params, timeout=25)
            first_page = False
        else:
            response = requests.get(next_url, timeout=25)

        response.raise_for_status()
        payload = response.json()
        rows_all.extend(payload.get("results", []))
        next_url = payload.get("next")

        if len(rows_all) > 20000:
            break

    if not rows_all:
        return pd.DataFrame(columns=["timestamp_utc", "timestamp_local", "price_p_per_kwh"])

    df = pd.DataFrame(rows_all)
    df["timestamp_utc"] = pd.to_datetime(df["valid_from"], utc=True, errors="coerce")
    df["price_p_per_kwh"] = pd.to_numeric(df["value_inc_vat"], errors="coerce")
    df = df.dropna(subset=["timestamp_utc", "price_p_per_kwh"]).copy()
    df = df.sort_values("timestamp_utc")
    df = df.drop_duplicates(subset=["timestamp_utc"], keep="last").reset_index(drop=True)
    df["timestamp_local"] = df["timestamp_utc"].dt.tz_convert(UK_TZ)
    return df[["timestamp_utc", "timestamp_local", "price_p_per_kwh"]]


def load_prices(period_from_utc: datetime, period_to_utc: datetime) -> pd.DataFrame:
    df = fetch_octopus_unit_rates(
        st.session_state.product_code,
        st.session_state.tariff_code,
        period_from_utc.isoformat(),
        period_to_utc.isoformat(),
    )
    st.session_state.df_prices = df
    return df


def load_last_48h_next_24h() -> pd.DataFrame:
    now_utc = datetime.now(timezone.utc)
    return load_prices(now_utc - timedelta(hours=48), now_utc + timedelta(hours=24))


def _features_from_time(ts_local: pd.Series) -> pd.DataFrame:
    dt = ts_local.dt
    return pd.DataFrame(
        {
            "hour": dt.hour.astype(int),
            "minute": dt.minute.astype(int),
            "dow": dt.dayofweek.astype(int),
            "month": dt.month.astype(int),
            "sin_h": np.sin(2 * np.pi * (dt.hour + dt.minute / 60.0) / 24.0),
            "cos_h": np.cos(2 * np.pi * (dt.hour + dt.minute / 60.0) / 24.0),
        }
    )


def make_forecast(df: pd.DataFrame, method: str = "Persistence") -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    ordered = df.sort_values("timestamp_local").copy()
    now = datetime.now(tz=UK_TZ)
    hist = ordered[ordered["timestamp_local"] < now].copy()
    fut = ordered[ordered["timestamp_local"] >= now].copy()
    if hist.empty or fut.empty:
        return pd.DataFrame()

    if method == "Persistence":
        last = float(hist["price_p_per_kwh"].iloc[-1])
        fut["forecast"] = last
        fut["actual"] = fut["price_p_per_kwh"].astype(float)
        return fut[["timestamp_local", "actual", "forecast"]].copy()

    try:
        from sklearn.ensemble import RandomForestRegressor
    except Exception:
        last = float(hist["price_p_per_kwh"].iloc[-1])
        fut["forecast"] = last
        fut["actual"] = fut["price_p_per_kwh"].astype(float)
        out = fut[["timestamp_local", "actual", "forecast"]].copy()
        out.attrs["note"] = "scikit-learn not installed; used persistence."
        return out

    x_train = _features_from_time(hist["timestamp_local"])
    y_train = hist["price_p_per_kwh"].astype(float).to_numpy()
    x_test = _features_from_time(fut["timestamp_local"])
    y_test = fut["price_p_per_kwh"].astype(float).to_numpy()

    try:
        model = RandomForestRegressor(
            n_estimators=250,
            random_state=42,
            min_samples_leaf=2,
            n_jobs=-1,
        )
        model.fit(x_train, y_train)
        pred = model.predict(x_test)
    except Exception:
        pred = np.full_like(y_test, y_train[-1])

    out = fut[["timestamp_local"]].copy()
    out["actual"] = y_test
    out["forecast"] = pred
    return out


def find_cheapest_window_from_series(times_local: pd.Series, prices: np.ndarray, window_hours: float) -> Dict[str, Any]:
    if len(prices) == 0:
        return {}
    slots = max(int((window_hours * 60) / 30), 1)
    if len(prices) < slots:
        return {}
    window_sums = np.convolve(prices, np.ones(slots), mode="valid")
    min_idx = int(np.argmin(window_sums))
    start = times_local.iloc[min_idx]
    end = times_local.iloc[min_idx + slots - 1] + timedelta(minutes=30)
    return {
        "start_local": start,
        "end_local": end,
        "avg_price_p_per_kwh": float(window_sums[min_idx] / slots),
    }


def find_cheapest_window(df: pd.DataFrame, window_hours: float) -> Dict[str, Any]:
    if df is None or df.empty:
        return {}
    ordered = df.sort_values("timestamp_local")
    return find_cheapest_window_from_series(
        ordered["timestamp_local"],
        ordered["price_p_per_kwh"].to_numpy(dtype=float),
        window_hours,
    )


def current_slot_price(df: pd.DataFrame) -> Optional[float]:
    if df is None or df.empty:
        return None
    now = datetime.now(tz=UK_TZ)
    ordered = df.sort_values("timestamp_local")
    idx = ordered["timestamp_local"].searchsorted(now, side="right") - 1
    if idx < 0 or idx >= len(ordered):
        return None
    return float(ordered.iloc[idx]["price_p_per_kwh"])


def estimate_cost_gbp(power_kw: float, hours: float, price_p_per_kwh: float) -> float:
    kwh = max(power_kw, 0) * max(hours, 0)
    return (kwh * price_p_per_kwh) / 100.0


def get_next_cheapest_slots(df: pd.DataFrame, n: int = 8) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    now = datetime.now(tz=UK_TZ)
    future = df[df["timestamp_local"] >= now].copy()
    source = future if not future.empty else df.copy()
    return source.sort_values("price_p_per_kwh").head(n)


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    out = df.copy()
    out["time_uk"] = out["timestamp_local"].dt.strftime("%Y-%m-%d %H:%M")
    out = out[["time_uk", "price_p_per_kwh"]]
    return out.to_csv(index=False).encode("utf-8")


def build_context(df: pd.DataFrame, best: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    region_letter = st.session_state.region_letter
    region_name = REGION_DICT.get(region_letter, region_letter)
    ctx: Dict[str, Any] = {
        "region": f"{region_letter} - {region_name}",
        "tariff_code": st.session_state.tariff_code,
        "run_hours": float(st.session_state.run_hours),
        "forecast_method": st.session_state.forecast_method,
    }
    if df is not None and not df.empty:
        ctx.update(
            {
                "mean_p_per_kwh": float(df["price_p_per_kwh"].mean()),
                "min_p_per_kwh": float(df["price_p_per_kwh"].min()),
                "max_p_per_kwh": float(df["price_p_per_kwh"].max()),
                "current_p_per_kwh": current_slot_price(df),
            }
        )
        # Add tariff comparison data for AI context
        comparison = compute_tariff_comparison(df, float(st.session_state.daily_kwh))
        if comparison:
            ctx["tariff_comparison"] = comparison
    if best:
        ctx["best_window"] = {
            "start_uk": best["start_local"].strftime("%a %d %b %H:%M"),
            "end_uk": best["end_local"].strftime("%a %d %b %H:%M"),
            "avg_p_per_kwh": float(best["avg_price_p_per_kwh"]),
        }
    return ctx


def build_price_snapshot(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {}

    now = datetime.now(tz=UK_TZ)
    future = df[df["timestamp_local"] >= now].copy()
    source = future if not future.empty else df.copy()
    if source.empty:
        return {}

    current = current_slot_price(df)
    best = find_cheapest_window(source, float(st.session_state.run_hours))
    lowest = float(source["price_p_per_kwh"].min())
    highest = float(source["price_p_per_kwh"].max())
    spread = highest - lowest
    q25 = float(source["price_p_per_kwh"].quantile(0.25))
    q75 = float(source["price_p_per_kwh"].quantile(0.75))
    negative_slots = int((source["price_p_per_kwh"] < 0).sum())
    peak_slots = source.sort_values("price_p_per_kwh", ascending=False).head(3)
    cheap_slots = source[source["price_p_per_kwh"] <= q25].sort_values("timestamp_local").head(1)

    peaks = [
        f"{row['timestamp_local'].strftime('%a %H:%M')} ({float(row['price_p_per_kwh']):.2f} p/kWh)"
        for _, row in peak_slots.iterrows()
    ]

    next_cheap_label = "No low slot found"
    if not cheap_slots.empty:
        row = cheap_slots.iloc[0]
        next_cheap_label = f"{row['timestamp_local'].strftime('%a %H:%M')} at {float(row['price_p_per_kwh']):.2f} p/kWh"

    if current is None:
        signal = "Live signal unavailable"
    elif current <= q25:
        signal = "Cheap now"
    elif current >= q75:
        signal = "Expensive now"
    else:
        signal = "Moderate now"

    return {
        "current": current,
        "best": best,
        "lowest": lowest,
        "highest": highest,
        "spread": spread,
        "signal": signal,
        "next_cheap_label": next_cheap_label,
        "negative_slots": negative_slots,
        "peaks": peaks,
    }


def build_auto_alert_plan(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {
            "title": "Smart price alerts",
            "threshold": 15.0,
            "mode": "Balanced",
            "message": "Load prices to generate an automatic alert plan.",
            "best": {},
            "negative_slots": 0,
        }

    snapshot = build_price_snapshot(df)
    now = datetime.now(tz=UK_TZ)
    future = df[df["timestamp_local"] >= now].copy()
    source = future if not future.empty else df.copy()
    threshold = float(source["price_p_per_kwh"].quantile(0.25))
    threshold = round(threshold, 2)

    spread = snapshot["spread"]
    negative_slots = snapshot["negative_slots"]
    if negative_slots > 0:
        mode = "Opportunity"
        message = f"{negative_slots} negative-price slot(s) detected in the loaded data."
    elif spread >= 18:
        mode = "High volatility"
        message = "Prices are moving sharply, so automatic alerts can capture stronger savings."
    elif spread >= 10:
        mode = "Balanced"
        message = "Good day for standard smart alerts with clear cheap windows."
    else:
        mode = "Low volatility"
        message = "Prices look fairly stable, so alerts will focus on the lowest window only."

    return {
        "title": "Smart price alerts",
        "threshold": threshold,
        "mode": mode,
        "message": message,
        "best": snapshot["best"],
        "negative_slots": negative_slots,
        "current": snapshot["current"],
        "spread": spread,
    }


def build_ics(start: datetime, end: datetime, title: str, description: str = "", location: str = "Home") -> bytes:
    def _fmt(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    uid = f"{int(datetime.now().timestamp())}@smart-energy"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Smart Energy//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_fmt(datetime.now(timezone.utc))}",
        f"DTSTART:{_fmt(start)}",
        f"DTEND:{_fmt(end)}",
        f"SUMMARY:{title}",
        f"DESCRIPTION:{description}",
        f"LOCATION:{location}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def budget_guardrail_message(now_cost: float, best_cost: float) -> str:
    if best_cost <= 0:
        return "Not enough data to compare cost impact."
    if now_cost > best_cost * 1.4:
        return "Running now is much costlier than the best window."
    if now_cost <= best_cost * 1.05:
        return "Running now is almost as good as the best window."
    return "There is some value in waiting for the lower-price window."


def mae(actual: np.ndarray, pred: np.ndarray) -> float:
    if len(actual) == 0:
        return float("nan")
    return float(np.mean(np.abs(actual - pred)))


def cheapest_window_regret_pence(times_local: pd.Series, actual: np.ndarray, pred: np.ndarray, window_h: float) -> float:
    if len(actual) == 0 or len(pred) == 0:
        return float("nan")

    true_best = find_cheapest_window_from_series(times_local, actual, window_h)
    pred_best = find_cheapest_window_from_series(times_local, pred, window_h)
    if not true_best or not pred_best:
        return float("nan")

    mask_pred = (times_local >= pred_best["start_local"]) & (times_local < pred_best["end_local"])
    mask_true = (times_local >= true_best["start_local"]) & (times_local < true_best["end_local"])
    if mask_pred.sum() == 0 or mask_true.sum() == 0:
        return float("nan")

    actual_avg_pred = float(np.mean(actual[mask_pred.to_numpy()]))
    actual_avg_true = float(np.mean(actual[mask_true.to_numpy()]))
    return max(actual_avg_pred - actual_avg_true, 0.0)


# ─── NEW / ENHANCED FEATURE HELPERS ──────────────────────────────────────────

def get_carbon_intensity(region_letter: str) -> int:
    return CARBON_INTENSITY_BY_REGION.get(region_letter, 200)


def estimate_carbon_kg(power_kw: float, hours: float, region_letter: str) -> float:
    kwh = power_kw * hours
    intensity = get_carbon_intensity(region_letter)
    return (kwh * intensity) / 1000.0


def carbon_badge_html(gco2_per_kwh: int) -> str:
    if gco2_per_kwh < 180:
        cls = "carbon-low"
        label = f"🌱 Low carbon · {gco2_per_kwh} gCO₂/kWh"
    elif gco2_per_kwh < 220:
        cls = "carbon-mid"
        label = f"⚡ Medium carbon · {gco2_per_kwh} gCO₂/kWh"
    else:
        cls = "carbon-high"
        label = f"🔥 High carbon · {gco2_per_kwh} gCO₂/kWh"
    return f"<span class='carbon-badge {cls}'>{label}</span>"


def smart_schedule_appliances(df: pd.DataFrame, appliances: list) -> list:
    if df is None or df.empty:
        return []
    now = datetime.now(tz=UK_TZ)
    future = df[df["timestamp_local"] >= now].copy()
    source = future if not future.empty else df.copy()
    region = st.session_state.region_letter

    results = []
    for name in appliances:
        info = APPLIANCE_LIBRARY.get(name, {})
        if not info:
            continue
        power = info["power_kw"]
        hours = info["run_hours"]
        best = find_cheapest_window_from_series(
            source["timestamp_local"],
            source["price_p_per_kwh"].to_numpy(dtype=float),
            hours,
        )
        if not best:
            continue
        cost = estimate_cost_gbp(power, hours, best["avg_price_p_per_kwh"])
        carbon = estimate_carbon_kg(power, hours, region)
        kwh = power * hours
        results.append({
            "name": name,
            "icon": info["icon"],
            "power_kw": power,
            "run_hours": hours,
            "flexible": info["flexible"],
            "best_start": best["start_local"],
            "best_end": best["end_local"],
            "avg_price": best["avg_price_p_per_kwh"],
            "cost_gbp": cost,
            "carbon_kg": carbon,
            "kwh": kwh,
        })
    return results


def price_volatility_score(df: pd.DataFrame) -> Dict[str, Any]:
    if df is None or df.empty:
        return {"score": 0, "band": "Unknown", "color": "yellow"}
    prices = df["price_p_per_kwh"].astype(float)
    spread = float(prices.max() - prices.min())
    std = float(prices.std())
    score = min(100, int((spread / 40.0) * 60 + (std / 10.0) * 40))
    if score < 30:
        band, color = "Stable", "green"
    elif score < 60:
        band, color = "Moderate", "yellow"
    else:
        band, color = "Volatile", "red"
    return {"score": score, "band": band, "color": color, "spread": spread, "std": std}


def monthly_bill_projection(df: pd.DataFrame, daily_kwh: float) -> Dict[str, float]:
    if df is None or df.empty:
        return {}
    avg_p = float(df["price_p_per_kwh"].mean())
    daily_cost = (daily_kwh * avg_p) / 100.0
    monthly = daily_cost * 30
    annual = daily_cost * 365
    return {"avg_p_per_kwh": avg_p, "daily_cost": daily_cost, "monthly": monthly, "annual": annual}


def demand_response_status(now: datetime) -> Dict[str, Any]:
    hour = now.hour
    for event in DEMAND_RESPONSE_EVENTS:
        if event["start_hour"] <= hour < event["end_hour"]:
            return {"active": True, "event": event}
    return {"active": False, "event": None}


def price_percentile_rank(df: pd.DataFrame, price: float) -> float:
    if df is None or df.empty:
        return 50.0
    prices = df["price_p_per_kwh"].astype(float).to_numpy()
    rank = float(np.mean(prices <= price) * 100)
    return rank


def compute_savings_vs_peak(df: pd.DataFrame, power_kw: float, run_hours: float) -> Dict[str, float]:
    if df is None or df.empty:
        return {}
    prices = df["price_p_per_kwh"].astype(float)
    cheapest_avg = float(prices.nsmallest(int(run_hours * 2)).mean())
    priciest_avg = float(prices.nlargest(int(run_hours * 2)).mean())
    kwh = power_kw * run_hours
    cost_cheap = (kwh * cheapest_avg) / 100.0
    cost_peak = (kwh * priciest_avg) / 100.0
    return {
        "cost_cheap": cost_cheap,
        "cost_peak": cost_peak,
        "saving": cost_peak - cost_cheap,
        "cheapest_avg": cheapest_avg,
        "priciest_avg": priciest_avg,
    }


# ─── TARIFF COMPARISON HELPER ────────────────────────────────────────────────

def compute_tariff_comparison(df: pd.DataFrame, daily_kwh: float) -> Dict[str, Any]:
    """Compare Agile actual cost vs other UK tariffs for the loaded period."""
    if df is None or df.empty:
        return {}

    agile_avg = float(df["price_p_per_kwh"].mean())
    agile_daily = (daily_kwh * agile_avg) / 100.0
    agile_monthly = agile_daily * 30

    results = {
        "Octopus Agile (yours)": {
            "avg_rate": agile_avg,
            "monthly_cost": agile_monthly,
            "description": "Variable half-hourly pricing — rewards smart shifting.",
        }
    }

    for name, tariff in COMPARISON_TARIFFS.items():
        flat_rate = tariff["rate_p_per_kwh"]
        standing = tariff.get("standing_p_per_day", 0)
        off_peak_rate = tariff.get("off_peak_rate")
        off_peak_hours = tariff.get("off_peak_hours")

        if off_peak_rate is not None and off_peak_hours is not None:
            # Assume 30% of usage happens in off-peak hours for dual-rate tariffs
            off_peak_frac = 0.30
            blended = off_peak_rate * off_peak_frac + flat_rate * (1 - off_peak_frac)
            daily_cost = (daily_kwh * blended) / 100.0 + standing / 100.0
        else:
            blended = flat_rate
            daily_cost = (daily_kwh * flat_rate) / 100.0 + standing / 100.0

        monthly = daily_cost * 30

        results[name] = {
            "avg_rate": blended,
            "monthly_cost": monthly,
            "description": tariff["description"],
        }

    # Find winner
    winner = min(results, key=lambda k: results[k]["monthly_cost"])
    for k in results:
        results[k]["is_winner"] = (k == winner)
        results[k]["vs_agile"] = results[k]["monthly_cost"] - agile_monthly

    return results


def compute_smart_shift_value(df: pd.DataFrame, daily_kwh: float) -> Dict[str, float]:
    """Compute the £/month value of smart shifting on Agile vs doing nothing."""
    if df is None or df.empty:
        return {}
    prices = df["price_p_per_kwh"].astype(float)
    avg_price = float(prices.mean())
    cheap_avg = float(prices.nsmallest(max(int(len(prices) * 0.25), 1)).mean())

    # If user shifts 40% of usage to cheapest 25% slots
    shift_frac = 0.40
    shifted_kwh = daily_kwh * shift_frac
    unshifted_kwh = daily_kwh - shifted_kwh
    daily_naive = (daily_kwh * avg_price) / 100.0
    daily_smart = (unshifted_kwh * avg_price + shifted_kwh * cheap_avg) / 100.0
    monthly_saving = (daily_naive - daily_smart) * 30
    return {
        "monthly_saving": monthly_saving,
        "daily_naive": daily_naive,
        "daily_smart": daily_smart,
        "shift_pct": shift_frac * 100,
    }


# ─── SESSION AT A GLANCE ─────────────────────────────────────────────────────

def update_session_stats(df: pd.DataFrame) -> None:
    """Track session-level statistics for 'at a glance' card."""
    if df is None or df.empty:
        return
    prices = df["price_p_per_kwh"].astype(float)
    stats = st.session_state.get("session_stats", {})
    stats["slots_loaded"] = len(df)
    stats["cheapest_seen"] = float(prices.min())
    stats["priciest_seen"] = float(prices.max())
    stats["last_load_time"] = datetime.now(tz=UK_TZ).strftime("%H:%M:%S")
    st.session_state.session_stats = stats


# ─── PDF REPORT BUILDER ──────────────────────────────────────────────────────

def build_pdf_report(df: pd.DataFrame, daily_kwh: float) -> Optional[bytes]:
    """Generate an A4 PDF report with price summary, bill projection, carbon, and tariff comparison."""
    if not HAS_REPORTLAB:
        return None

    import io
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20*mm, bottomMargin=15*mm, leftMargin=15*mm, rightMargin=15*mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Heading1"], fontSize=20, spaceAfter=10, textColor=rl_colors.HexColor("#5B21B6"))
    heading_style = ParagraphStyle("ReportH2", parent=styles["Heading2"], fontSize=14, spaceBefore=14, spaceAfter=6, textColor=rl_colors.HexColor("#7C3AED"))
    body_style = styles["BodyText"]

    elements = []
    now_str = datetime.now(tz=UK_TZ).strftime("%A %d %B %Y %H:%M")
    region_letter = st.session_state.region_letter
    region_name = REGION_DICT.get(region_letter, region_letter)

    elements.append(Paragraph("Smart Energy Report", title_style))
    elements.append(Paragraph(f"Generated: {now_str}", body_style))
    elements.append(Paragraph(f"Region: {region_letter} — {region_name} | Tariff: {st.session_state.tariff_code}", body_style))
    elements.append(Spacer(1, 8*mm))

    # Price summary table
    elements.append(Paragraph("Price Summary", heading_style))
    if df is not None and not df.empty:
        prices = df["price_p_per_kwh"].astype(float)
        price_data = [
            ["Metric", "Value"],
            ["Slots loaded", str(len(df))],
            ["Mean price", f"{prices.mean():.2f} p/kWh"],
            ["Min price", f"{prices.min():.2f} p/kWh"],
            ["Max price", f"{prices.max():.2f} p/kWh"],
            ["Spread", f"{(prices.max() - prices.min()):.2f} p"],
            ["Std deviation", f"{prices.std():.2f} p"],
            ["Negative slots", str(int((prices < 0).sum()))],
        ]
        t = Table(price_data, colWidths=[120, 180])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#7C3AED")),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#D4D4D8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.HexColor("#F5F3FF"), rl_colors.white]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t)
    else:
        elements.append(Paragraph("No price data loaded.", body_style))

    elements.append(Spacer(1, 6*mm))

    # Bill projection
    elements.append(Paragraph("Bill Projection", heading_style))
    proj = monthly_bill_projection(df, daily_kwh)
    if proj:
        bill_data = [
            ["Metric", "Value"],
            ["Daily usage", f"{daily_kwh:.1f} kWh"],
            ["Average price", f"{proj['avg_p_per_kwh']:.2f} p/kWh"],
            ["Daily cost", f"£{proj['daily_cost']:.2f}"],
            ["Monthly estimate", f"£{proj['monthly']:.2f}"],
            ["Annual estimate", f"£{proj['annual']:.0f}"],
        ]
        t2 = Table(bill_data, colWidths=[120, 180])
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#059669")),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#D4D4D8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.HexColor("#ECFDF5"), rl_colors.white]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t2)

    elements.append(Spacer(1, 6*mm))

    # Carbon footprint
    elements.append(Paragraph("Carbon Footprint", heading_style))
    carbon_g = get_carbon_intensity(region_letter)
    elements.append(Paragraph(f"Regional carbon intensity: {carbon_g} gCO₂/kWh", body_style))
    daily_carbon_kg = estimate_carbon_kg(1.0, daily_kwh, region_letter)
    elements.append(Paragraph(f"Daily CO₂ for {daily_kwh:.1f} kWh: {daily_carbon_kg*1000:.0f}g ({daily_carbon_kg:.3f} kg)", body_style))
    elements.append(Paragraph(f"Monthly CO₂ estimate: {daily_carbon_kg*30:.2f} kg", body_style))

    elements.append(Spacer(1, 6*mm))

    # Tariff comparison
    elements.append(Paragraph("Tariff Comparison", heading_style))
    comparison = compute_tariff_comparison(df, daily_kwh)
    if comparison:
        comp_data = [["Tariff", "Avg rate (p/kWh)", "Monthly cost", "vs Agile"]]
        for tname, tdata in comparison.items():
            vs_str = f"£{tdata['vs_agile']:+.2f}" if tdata.get("vs_agile", 0) != 0 else "—"
            winner_mark = " ⭐" if tdata.get("is_winner") else ""
            comp_data.append([
                f"{tname}{winner_mark}",
                f"{tdata['avg_rate']:.2f}",
                f"£{tdata['monthly_cost']:.2f}",
                vs_str,
            ])
        t3 = Table(comp_data, colWidths=[140, 90, 80, 70])
        t3.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), rl_colors.HexColor("#1E40AF")),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl_colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, rl_colors.HexColor("#D4D4D8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [rl_colors.HexColor("#EFF6FF"), rl_colors.white]),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        elements.append(t3)

    elements.append(Spacer(1, 8*mm))
    elements.append(Paragraph("Report generated by Smart Energy Helper — smartenergy.app", ParagraphStyle("Footer", parent=body_style, fontSize=8, textColor=rl_colors.grey)))

    doc.build(elements)
    return buffer.getvalue()


def build_txt_report(df: pd.DataFrame, daily_kwh: float) -> str:
    """Fallback text report if reportlab is not installed."""
    now_str = datetime.now(tz=UK_TZ).strftime("%A %d %B %Y %H:%M")
    region_letter = st.session_state.region_letter
    region_name = REGION_DICT.get(region_letter, region_letter)
    lines = [
        "SMART ENERGY REPORT",
        f"Generated: {now_str}",
        f"Region: {region_letter} - {region_name}",
        f"Tariff: {st.session_state.tariff_code}",
        "",
        "── PRICE SUMMARY ──",
    ]
    if df is not None and not df.empty:
        prices = df["price_p_per_kwh"].astype(float)
        lines += [
            f"Slots loaded: {len(df)}",
            f"Mean: {prices.mean():.2f} p/kWh",
            f"Min: {prices.min():.2f} p/kWh",
            f"Max: {prices.max():.2f} p/kWh",
            f"Spread: {(prices.max() - prices.min()):.2f} p",
            f"Negative slots: {int((prices < 0).sum())}",
        ]
    else:
        lines.append("No price data loaded.")

    lines += ["", "── BILL PROJECTION ──"]
    proj = monthly_bill_projection(df, daily_kwh)
    if proj:
        lines += [
            f"Daily usage: {daily_kwh} kWh",
            f"Avg price: {proj['avg_p_per_kwh']:.2f} p/kWh",
            f"Daily cost: £{proj['daily_cost']:.2f}",
            f"Monthly: £{proj['monthly']:.2f}",
            f"Annual: £{proj['annual']:.0f}",
        ]

    lines += ["", "── CARBON FOOTPRINT ──"]
    carbon_g = get_carbon_intensity(region_letter)
    daily_carbon = estimate_carbon_kg(1.0, daily_kwh, region_letter)
    lines += [
        f"Carbon intensity: {carbon_g} gCO2/kWh",
        f"Daily CO2: {daily_carbon*1000:.0f}g",
        f"Monthly CO2: {daily_carbon*30:.2f} kg",
    ]

    lines += ["", "── TARIFF COMPARISON ──"]
    comparison = compute_tariff_comparison(df, daily_kwh)
    if comparison:
        for tname, tdata in comparison.items():
            winner = " ⭐ WINNER" if tdata.get("is_winner") else ""
            lines.append(f"  {tname}: £{tdata['monthly_cost']:.2f}/month ({tdata['avg_rate']:.2f} p/kWh){winner}")

    lines += ["", "Report by Smart Energy Helper"]
    return "\n".join(lines)


# ─── END NEW FEATURE HELPERS ─────────────────────────────────────────────────


def set_defaults() -> None:
    defaults = {
        "page": "Dashboard",
        "region_letter": "C",
        "product_code": "AGILE-24-10-01",
        "tariff_code_base": "E-1R-AGILE-24-10-01",
        "df_prices": pd.DataFrame(columns=["timestamp_utc", "timestamp_local", "price_p_per_kwh"]),
        "run_hours": 2.0,
        "device_power_kw": 7.0,
        "forecast_method": "Persistence",
        "ai_chat": [],
        "ai_prefill": "",
        "ai_show_starters": True,
        "ai_welcomed": False,
        "ai_inflight": False,
        "ai_last_q": "",
        "pending_user_text": "",
        "pending_prompt": "",
        "sub_email": "",
        "sub_last_hash": "",
        "selected_appliances": ["Washing Machine", "EV Charger (7kW)", "Dishwasher"],
        "daily_kwh": 10.0,
        "session_stats": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    st.session_state.tariff_code = normalize_tariff_code(
        st.session_state.tariff_code_base,
        st.session_state.region_letter,
    )


set_defaults()


def nav_button(label: str, page_key: str, icon: str) -> None:
    active = st.session_state.page == page_key
    cls = "navbtn active" if active else "navbtn"
    st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
    if st.button(f"{icon}  {label}", key=f"nav_{page_key}", width="stretch"):
        st.session_state.page = page_key
        _rerun()
    st.markdown("</div>", unsafe_allow_html=True)


with st.sidebar:
    st.markdown(
        """
        <div class="sidebrand">
          <span class="bolt">⚡</span>
          <span>SmartEnergy</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    nav_button("Dashboard", "Dashboard", "🏠")
    nav_button("Prices", "Prices", "📈")
    nav_button("Scheduler", "Scheduler", "🗓️")
    nav_button("Carbon", "Carbon", "🌱")
    nav_button("Bill", "Bill", "💷")
    nav_button("Compare", "Compare", "⚖️")
    nav_button("Heatmap", "Heatmap", "🟥")
    nav_button("Evaluation", "Evaluation", "📊")
    nav_button("Subscribe", "Subscribe", "🔔")
    nav_button("Settings", "Settings", "⚙️")

    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='panel'>", unsafe_allow_html=True)

    st.markdown("**Region**")
    region_choice = st.selectbox(
        "Region",
        options=[f"{key} - {name}" for key, name in REGIONS],
        index=[key for key, _ in REGIONS].index(st.session_state.region_letter),
        key="region_select",
        label_visibility="collapsed",
    )
    new_region_letter = region_choice.split("-")[0].strip()

    st.markdown("**Forecast**")
    forecast_choice = st.radio(
        "Forecast method",
        options=["RF", "Pers"],
        index=0 if st.session_state.forecast_method == "RF" else 1,
        horizontal=True,
        key="fc_method_radio",
        label_visibility="collapsed",
    )
    st.session_state.forecast_method = "RF" if forecast_choice == "RF" else "Persistence"

    if new_region_letter != st.session_state.region_letter:
        st.session_state.region_letter = new_region_letter
        st.session_state.tariff_code = normalize_tariff_code(
            st.session_state.tariff_code_base,
            st.session_state.region_letter,
        )
        st.session_state.df_prices = pd.DataFrame(columns=["timestamp_utc", "timestamp_local", "price_p_per_kwh"])
        st.cache_data.clear()
        st.info("Region updated.")

    sidebar_df = st.session_state.df_prices
    mean_val = "-"
    if isinstance(sidebar_df, pd.DataFrame) and not sidebar_df.empty:
        mean_val = f"{sidebar_df['price_p_per_kwh'].mean():.1f}p"
    st.markdown(f"<div class='pill'>Avg: {mean_val}</div>", unsafe_allow_html=True)

    carbon_g = get_carbon_intensity(st.session_state.region_letter)
    st.markdown(carbon_badge_html(carbon_g), unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def _svg_robot_24() -> str:
    return (
        "<svg width='18' height='18' viewBox='0 0 24 24' fill='none' "
        "xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>"
        "<path d='M12 3V5' stroke='currentColor' stroke-width='1.8' stroke-linecap='round'/>"
        "<rect x='5.25' y='7' width='13.5' height='10.5' rx='4' stroke='currentColor' stroke-width='1.8' fill='currentColor' fill-opacity='0.12'/>"
        "<circle cx='9.3' cy='12.1' r='1.15' fill='currentColor'/>"
        "<circle cx='14.7' cy='12.1' r='1.15' fill='currentColor'/>"
        "<path d='M9.2 15.1C10.05 15.8 10.97 16.15 12 16.15C13.03 16.15 13.95 15.8 14.8 15.1' stroke='currentColor' stroke-width='1.7' stroke-linecap='round'/>"
        "<path d='M7.1 7V6.2C7.1 4.99 8.09 4 9.3 4H14.7C15.91 4 16.9 4.99 16.9 6.2V7' stroke='currentColor' stroke-width='1.5' stroke-linecap='round'/>"
        "<path d='M5.25 10H4M20 10H18.75M8 17.7L6.8 19.4M16 17.7L17.2 19.4' stroke='currentColor' stroke-width='1.5' stroke-linecap='round'/>"
        "</svg>"
    )


def _svg_user_24() -> str:
    return (
        "<svg width='16' height='16' viewBox='0 0 24 24' fill='none' "
        "xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>"
        "<path d='M12 2a6 6 0 1 1 0 12A6 6 0 0 1 12 2Zm0 14c4.418 0 8 2.015 8 4.5V22H4v-1.5C4 18.015 7.582 16 12 16Z' fill='currentColor'/>"
        "</svg>"
    )


def render_chat_viewport(inflight: bool = False) -> None:
    rows = []
    for role, msg in st.session_state.ai_chat[-120:]:
        side = "assistant" if role == "assistant" else "user"
        content = _html.escape(msg).replace("\n", "<br>")
        icon = _svg_robot_24() if side == "assistant" else _svg_user_24()
        rows.append(
            f"<div class='msg-row {side}'>"
            f"<div class='badge' style='color:#111'>{icon}</div>"
            f"<div class='bubble'>{content}</div>"
            f"</div>"
        )

    if inflight:
        rows.append(
            "<div class='msg-row assistant'>"
            f"<div class='badge' style='color:#111'>{_svg_robot_24()}</div>"
            "<div class='bubble'><span class='typing'>"
            "<span class='dot'></span><span class='dot d2'></span><span class='dot d3'></span>"
            "</span></div>"
            "</div>"
        )

    st.markdown("<div class='ai-chatbox'>" + "".join(rows) + "</div>", unsafe_allow_html=True)
    components.html(
        """
        <script>
          const box = parent.document.querySelector('.ai-chatbox');
          if (box) {
            box.scrollTop = box.scrollHeight;
            setTimeout(() => { box.scrollTop = box.scrollHeight; }, 60);
            setTimeout(() => { box.scrollTop = box.scrollHeight; }, 120);
          }
        </script>
        """,
        height=0,
        width=0,
    )


def _build_ai_prompt(user_text: str) -> str:
    df = st.session_state.df_prices
    best = find_cheapest_window(df, float(st.session_state.run_hours)) if df is not None and not df.empty else None
    ctx = build_context(df, best if best else None)
    carbon_g = get_carbon_intensity(st.session_state.region_letter)
    ctx["carbon_intensity_gco2_per_kwh"] = carbon_g
    return (
        "You are a friendly UK energy helper. Keep answers short and clear.\n"
        "Use the JSON context. If asked about EV cost, assume 7kW unless told otherwise.\n"
        "The context includes tariff_comparison data showing how Agile compares to Standard Variable, "
        "Octopus Go, Cosy, Economy 7, and Fixed Rate tariffs. Use this data when the user asks about "
        "tariff comparisons, whether Agile is worth it, or how their costs compare.\n"
        "If important info is missing, ask one short question.\n"
        f"Context(JSON): {json.dumps(ctx)}\n\n"
        f"User: {user_text}\n"
    )


def set_device_preset(power_kw: float, run_hours: float) -> None:
    st.session_state.device_power_kw = float(power_kw)
    st.session_state.run_hours = float(run_hours)


def render_chat_input() -> None:
    st.markdown("<div class='ai-input-row'>", unsafe_allow_html=True)
    st.markdown("<div class='ai-compose-shell'>", unsafe_allow_html=True)
    with st.form("ai_prompt_form", clear_on_submit=True, border=False, enter_to_submit=True):
        c1, c2 = st.columns([1.0, 0.18], gap="small")
        with c1:
            st.markdown("<div class='ai-input'>", unsafe_allow_html=True)
            user_text = st.text_input(
                "Ask anything...",
                key="ai_form_input",
                label_visibility="collapsed",
                placeholder="Ask anything...",
            )
            st.markdown("</div>", unsafe_allow_html=True)
        with c2:
            st.markdown("<div class='ai-connected-send'>", unsafe_allow_html=True)
            sent = st.form_submit_button("➤", width="stretch")
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    if sent and (user_text or "").strip():
        st.session_state.pending_user_text = user_text.strip()
        _rerun()


def render_askai_panel() -> None:
    if st.session_state.pending_user_text and not st.session_state.ai_inflight:
        question = st.session_state.pending_user_text.strip()
        st.session_state.pending_user_text = ""
        if question and question != st.session_state.ai_last_q:
            st.session_state.ai_last_q = question
            st.session_state.ai_inflight = True
            st.session_state.ai_show_starters = False
            st.session_state.ai_chat.append(("user", question))
            st.session_state.pending_prompt = _build_ai_prompt(question)

    if st.session_state.ai_prefill and not st.session_state.ai_inflight:
        question = st.session_state.ai_prefill.strip()
        st.session_state.ai_prefill = ""
        if question and question != st.session_state.ai_last_q:
            st.session_state.ai_last_q = question
            st.session_state.ai_inflight = True
            st.session_state.ai_show_starters = False
            st.session_state.ai_chat.append(("user", question))
            st.session_state.pending_prompt = _build_ai_prompt(question)

    if not st.session_state.ai_welcomed and not st.session_state.ai_chat:
        st.session_state.ai_chat.append(
            ("assistant", "Hi! How can I help you save energy today? Ask about cheap times, EV charging, tariff comparisons, or when to run appliances.")
        )
        st.session_state.ai_welcomed = True
        st.session_state.ai_show_starters = True

    connected = OpenAI is not None and bool(get_openai_key())
    status_cls = "on" if connected else "off"
    status_txt = "Connected" if connected else "Offline"
    dot_cls = "ai-dot live" if connected else "ai-dot off"

    st.markdown("<div class='ai-glass'>", unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="ai-head">
          <div class="ai-title">
            <div class="ai-title-icon">🤖</div>
            <span>Energy AI</span>
          </div>
          <div class="ai-status-badge {status_cls}">
            <span class="{dot_cls}"></span>
            {status_txt}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_chat_viewport(inflight=st.session_state.ai_inflight)

    if st.session_state.ai_show_starters:
        st.markdown("<div class='ai-divider'></div>", unsafe_allow_html=True)
        st.markdown("<div class='ai-starters-label'>Quick questions</div>", unsafe_allow_html=True)
        st.markdown("<div class='ai-chip-row'>", unsafe_allow_html=True)

        chips = [
            ("chip_tonight",   "Is tonight expensive?",                                              "🌙", "Tonight's price",    "Check evening rates"),
            ("chip_cheapest",  "What's the cheapest 2-hour window today?",                           "⚡", "Cheapest 2h",        "Best run window"),
            ("chip_ev",        "How much will charging my EV for 2 hours cost? Assume 7kW.",         "🔌", "EV charge cost",     "7kW · 2 hours"),
            ("chip_washer",    "When should I run my washing machine to save money?",                "🫧", "Best for washer",    "Flexible appliance"),
            ("chip_spike",     "Are there any price spikes I should avoid today?",                   "📈", "Avoid spikes",       "Today's peaks"),
            ("chip_compare",   "How does Agile compare to a fixed rate tariff?",                     "⚖️", "Tariff comparison",  "Agile vs others"),
        ]

        c1, c2 = st.columns(2, gap="small")
        cols = [c1, c2, c1, c2, c1, c2]
        for (key, prompt, icon, title, sub), col in zip(chips, cols):
            with col:
                if st.button(
                    f"{icon}  {title}\n{sub}",
                    key=key,
                    width="stretch",
                ):
                    st.session_state.ai_prefill = prompt
                    _rerun()

        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("<div class='ai-divider'></div>", unsafe_allow_html=True)

    render_chat_input()

    # Clear chat button
    if st.session_state.ai_chat:
        if st.button("🗑️ Clear chat", key="clear_chat_btn"):
            st.session_state.ai_chat = []
            st.session_state.ai_welcomed = False
            st.session_state.ai_show_starters = True
            st.session_state.ai_last_q = ""
            st.session_state.ai_prefill = ""
            _rerun()

    st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.ai_inflight and st.session_state.pending_prompt:
        ok, answer = ai_call_text(st.session_state.pending_prompt)
        st.session_state.ai_chat.append(("assistant", answer if ok else answer))
        st.session_state.pending_prompt = ""
        st.session_state.ai_inflight = False
        _rerun()


def compose_digest_text(df: pd.DataFrame, run_hours: float) -> str:
    plan = build_auto_alert_plan(df)
    if df is None or df.empty:
        return "No price data loaded."

    best = find_cheapest_window(df, run_hours)
    if not best:
        return "Could not determine the cheapest window from the loaded data."

    start = best["start_local"].strftime("%a %d %b %H:%M")
    end = best["end_local"].strftime("%a %d %b %H:%M")
    current = current_slot_price(df)
    extra = f"\nCurrent p/kWh: {current:.2f}" if current is not None else ""

    return (
        "Smart Energy - Smart Digest\n"
        f"Tariff: {st.session_state.tariff_code} | Region: {st.session_state.region_letter}\n"
        f"Auto mode: {plan['mode']}\n\n"
        f"Cheapest {run_hours:.1f}h window: {start} -> {end}\n"
        f"Average price: {best['avg_price_p_per_kwh']:.2f} p/kWh{extra}\n"
        f"Auto alert threshold: {plan['threshold']:.2f} p/kWh\n"
        f"Plan note: {plan['message']}\n"
    )


def build_subscription_email(email: str, df: pd.DataFrame) -> str:
    plan = build_auto_alert_plan(df)
    best = plan["best"]
    best_line = "Best window will appear after prices are loaded."
    if best:
        best_line = (
            f"Current best window: {best['start_local'].strftime('%a %d %b %H:%M')} -> "
            f"{best['end_local'].strftime('%a %d %b %H:%M')} "
            f"({best['avg_price_p_per_kwh']:.2f} p/kWh)."
        )

    return (
        f"Hi,\n\n"
        f"Smart Energy alerts are now set up for {email}.\n"
        f"We'll automatically track your loaded tariff, region, and price shape.\n"
        f"Alert mode: {plan['mode']}\n"
        f"Auto threshold: {plan['threshold']:.2f} p/kWh\n"
        f"{best_line}\n\n"
        f"We'll highlight cheap windows, unusual spikes, and strong savings opportunities.\n"
        f"You can unsubscribe any time.\n\n"
        f"Smart Energy"
    )


def plot_prices_matplotlib(df: pd.DataFrame, highlight_window: Optional[Dict[str, Any]]) -> None:
    import matplotlib.pyplot as plt

    ordered = df.sort_values("timestamp_local").copy()
    x = ordered["timestamp_local"].dt.tz_localize(None)
    y = ordered["price_p_per_kwh"].astype(float).to_numpy()

    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=160)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    ax.step(x, y, where="post", linewidth=2.5, color="#9b5cff", alpha=0.96)

    if highlight_window:
        start = highlight_window["start_local"].replace(tzinfo=None)
        end = highlight_window["end_local"].replace(tzinfo=None)
        ax.axvspan(start, end, color="#22c55e", alpha=0.15)

    ax.axvline(
        datetime.now(tz=UK_TZ).replace(tzinfo=None),
        linestyle="--",
        linewidth=1,
        color="white",
        alpha=0.35,
    )
    ax.grid(True, alpha=0.12, linestyle="--")
    ax.set_ylabel("p/kWh", color="white", fontsize=11)
    ax.tick_params(colors="white", labelsize=9)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    st.pyplot(fig, width="stretch")


def plot_prices_plotly(df: pd.DataFrame, highlight_window: Optional[Dict[str, Any]]) -> None:
    if go is None:
        st.info("Plotly not installed.")
        return

    ordered = df.sort_values("timestamp_local").copy()
    x = ordered["timestamp_local"].dt.tz_localize(None)
    y = ordered["price_p_per_kwh"].astype(float)

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(x=x, y=y, mode="lines", line=dict(color="#9b5cff", width=3), name="Price")
    )
    if highlight_window:
        start = highlight_window["start_local"].replace(tzinfo=None)
        end = highlight_window["end_local"].replace(tzinfo=None)
        figure.add_vrect(x0=start, x1=end, fillcolor="rgba(34,197,94,0.20)", line_width=0)
    figure.add_vline(
        x=datetime.now(tz=UK_TZ).replace(tzinfo=None),
        line_width=1,
        line_dash="dash",
        line_color="rgba(255,255,255,0.35)",
    )
    figure.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=10, b=10),
        font=dict(color="rgba(255,255,255,0.92)"),
        xaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.10)"),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.10)", title="p/kWh"),
        height=320,
        showlegend=False,
    )
    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})


# ─── SESSION AT A GLANCE CARD (Dashboard) ────────────────────────────────────

def render_session_at_a_glance(df: pd.DataFrame) -> None:
    """Render the 'Session at a glance' card below the price chart on the Dashboard."""
    stats = st.session_state.get("session_stats", {})
    if not stats:
        return

    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("📊 Session at a glance")

    now_price = current_slot_price(df)
    best_window = find_cheapest_window(df, float(st.session_state.run_hours))
    potential_saving = None
    if now_price is not None and best_window:
        power = float(st.session_state.device_power_kw)
        hours = float(st.session_state.run_hours)
        cost_now = estimate_cost_gbp(power, hours, now_price)
        cost_best = estimate_cost_gbp(power, hours, best_window["avg_price_p_per_kwh"])
        potential_saving = max(cost_now - cost_best, 0)

    s1, s2, s3, s4 = st.columns(4)
    with s1:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Slots loaded</div><div class='value'>{stats.get('slots_loaded', 0)}</div></div>",
            unsafe_allow_html=True,
        )
    with s2:
        cheapest = stats.get("cheapest_seen")
        val = f"{cheapest:.2f} p" if cheapest is not None else "-"
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Cheapest seen</div><div class='value' style='color:#4ade80;'>{val}</div></div>",
            unsafe_allow_html=True,
        )
    with s3:
        priciest = stats.get("priciest_seen")
        val = f"{priciest:.2f} p" if priciest is not None else "-"
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Most expensive</div><div class='value' style='color:#fb7185;'>{val}</div></div>",
            unsafe_allow_html=True,
        )
    with s4:
        val = f"£{potential_saving:.2f}" if potential_saving is not None else "-"
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Saving vs now</div><div class='value' style='color:#60a5fa;'>{val}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def render_dashboard() -> None:
    left, right = st.columns([2.65, 0.95], gap="large")

    with right:
        render_askai_panel()

    with left:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Electricity prices")

        c1, c2, c3 = st.columns([1.55, 1.0, 1.0])
        with c1:
            mode = st.selectbox(
                "Load",
                ["Last 48h + next 24h", "Custom date/time"],
                index=0,
                key="dash_mode",
            )
        with c2:
            refresh = st.button("Refresh", width="stretch")
        with c3:
            load_clicked = st.button("Load prices", width="stretch")

        custom_from_utc = None
        custom_to_utc = None
        if mode == "Custom date/time":
            r1, r2 = st.columns(2)
            with r1:
                from_date = st.date_input("From date", value=datetime.now(UK_TZ).date(), key="dash_from_date")
                from_time = st.time_input("From time", value=time(0, 0), key="dash_from_time")
            with r2:
                to_date = st.date_input("To date", value=datetime.now(UK_TZ).date(), key="dash_to_date")
                to_time = st.time_input("To time", value=time(23, 30), key="dash_to_time")

            from_local = datetime.combine(from_date, from_time).replace(tzinfo=UK_TZ)
            to_local = datetime.combine(to_date, to_time).replace(tzinfo=UK_TZ)
            if to_local <= from_local:
                to_local = from_local + timedelta(hours=1)

            custom_from_utc = from_local.astimezone(timezone.utc)
            custom_to_utc = to_local.astimezone(timezone.utc)

        df = st.session_state.df_prices
        if load_clicked or refresh or (isinstance(df, pd.DataFrame) and df.empty):
            try:
                with st.spinner("Loading prices..."):
                    if mode == "Last 48h + next 24h":
                        df = load_last_48h_next_24h()
                    else:
                        df = load_prices(custom_from_utc, custom_to_utc)
                update_session_stats(df)
            except Exception as exc:
                st.error(f"Could not load prices: {exc}")
                df = st.session_state.df_prices

        if df is None or df.empty:
            st.info("Press Load prices to fetch data.")
            st.markdown("</div>", unsafe_allow_html=True)
            return

        best_window = find_cheapest_window(df, float(st.session_state.run_hours))
        try:
            plot_prices_matplotlib(df, best_window)
        except Exception:
            plot_prices_plotly(df, best_window)

        st.download_button(
            "Download prices CSV",
            data=to_csv_bytes(df),
            file_name="prices.csv",
            mime="text/csv",
            width="stretch",
        )

        snapshot = build_price_snapshot(df)
        vol = price_volatility_score(df)
        stat1, stat2, stat3, stat4, stat5 = st.columns(5)
        with stat1:
            st.markdown(
                f"<div class='mini-metric'><div class='label'>Current</div><div class='value'>{snapshot['current']:.2f} p/kWh</div></div>",
                unsafe_allow_html=True,
            )
        with stat2:
            st.markdown(
                f"<div class='mini-metric'><div class='label'>Lowest</div><div class='value'>{snapshot['lowest']:.2f} p/kWh</div></div>",
                unsafe_allow_html=True,
            )
        with stat3:
            st.markdown(
                f"<div class='mini-metric'><div class='label'>Spread</div><div class='value'>{snapshot['spread']:.2f} p</div></div>",
                unsafe_allow_html=True,
            )
        with stat4:
            st.markdown(
                f"<div class='mini-metric'><div class='label'>Neg. slots</div><div class='value'>{snapshot['negative_slots']}</div></div>",
                unsafe_allow_html=True,
            )
        with stat5:
            color_map = {"green": "#4ade80", "yellow": "#fbbf24", "red": "#fb7185"}
            vc = color_map.get(vol["color"], "#fff")
            st.markdown(
                f"<div class='mini-metric'><div class='label'>Volatility</div><div class='value' style='color:{vc}'>{vol['band']} ({vol['score']})</div></div>",
                unsafe_allow_html=True,
            )

        st.markdown(f"<div class='pill'>Status: {snapshot['signal']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='pill'>Next cheap slot: {snapshot['next_cheap_label']}</div>", unsafe_allow_html=True)

        now_uk = datetime.now(tz=UK_TZ)
        dr = demand_response_status(now_uk)
        if dr["active"] and dr["event"]:
            ev = dr["event"]
            st.markdown(
                f"<div class='pill' style='background:rgba(251,113,133,0.18);border-color:rgba(251,113,133,0.4);color:#fb7185;'>"
                f"⚠️ Demand Response: {ev['label']} · {ev['action']}</div>",
                unsafe_allow_html=True,
            )

        if best_window:
            st.markdown(
                (
                    f"<div class='pill'>Cheapest {st.session_state.run_hours:.1f}h: "
                    f"{best_window['start_local'].strftime('%a %H:%M')} -> "
                    f"{best_window['end_local'].strftime('%a %H:%M')} "
                    f"({best_window['avg_price_p_per_kwh']:.2f} p/kWh)</div>"
                ),
                unsafe_allow_html=True,
            )

        if snapshot.get("current") is not None:
            pct = price_percentile_rank(df, snapshot["current"])
            pct_color = "#4ade80" if pct < 30 else ("#fbbf24" if pct < 70 else "#fb7185")
            st.markdown(
                f"<div class='pill' style='color:{pct_color};'>Current price is cheaper than {pct:.0f}% of all slots loaded</div>",
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

        # ── SESSION AT A GLANCE (new card) ──
        render_session_at_a_glance(df)

        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Quick cost estimate")

        power_kw = st.number_input(
            "Device power (kW)",
            min_value=0.1,
            max_value=22.0,
            step=0.1,
            key="device_power_kw",
        )
        run_hours = st.number_input(
            "Run duration (hours)",
            min_value=0.5,
            max_value=12.0,
            step=0.5,
            key="run_hours",
        )

        st.caption("Smart presets")
        p1, p2, p3 = st.columns(3)
        with p1:
            st.button(
                "Washer (1.5 kW / 1.5 h)",
                key="preset_washer",
                width="stretch",
                on_click=set_device_preset,
                args=(1.5, 1.5),
            )
        with p2:
            st.button(
                "Dishwasher (1.2 kW / 1.3 h)",
                key="preset_dishwasher",
                width="stretch",
                on_click=set_device_preset,
                args=(1.2, 1.3),
            )
        with p3:
            st.button(
                "EV (7 kW / 2 h)",
                key="preset_ev",
                width="stretch",
                on_click=set_device_preset,
                args=(7.0, 2.0),
            )

        best_window = find_cheapest_window(df, float(run_hours))
        now_price = current_slot_price(df)
        if now_price is not None:
            cost_now = estimate_cost_gbp(float(power_kw), float(run_hours), now_price)
            st.markdown(f"<div class='pill'>Run now: ~£{cost_now:.2f}</div>", unsafe_allow_html=True)
        else:
            cost_now = None

        if best_window:
            cost_best = estimate_cost_gbp(float(power_kw), float(run_hours), float(best_window["avg_price_p_per_kwh"]))
            st.markdown(f"<div class='pill'>Cheapest window: ~£{cost_best:.2f}</div>", unsafe_allow_html=True)
            if cost_now is not None:
                saving = max(cost_now - cost_best, 0)
                st.markdown(
                    f"<div class='pill'>Potential saving: ~£{saving:.2f}</div>",
                    unsafe_allow_html=True,
                )
                st.caption(budget_guardrail_message(cost_now, cost_best))

            carbon = estimate_carbon_kg(float(power_kw), float(run_hours), st.session_state.region_letter)
            st.markdown(
                f"<div class='pill'>🌱 Carbon estimate: {carbon*1000:.0f}g CO₂ ({carbon:.3f} kg)</div>",
                unsafe_allow_html=True,
            )

            svp = compute_savings_vs_peak(df, float(power_kw), float(run_hours))
            if svp:
                st.markdown(
                    f"<div class='pill' style='color:#4ade80;'>Max possible saving vs peak: ~£{svp['saving']:.2f}</div>",
                    unsafe_allow_html=True,
                )

            st.download_button(
                "Add to calendar (.ics)",
                data=build_ics(
                    best_window["start_local"],
                    best_window["end_local"],
                    "Cheapest energy window",
                    "Run flexible appliances in this window.",
                ),
                file_name="cheapest_window.ics",
                mime="text/calendar",
                width="stretch",
            )

        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Cheapest upcoming slots")

        cheap = get_next_cheapest_slots(df, n=8)
        if cheap.empty:
            st.caption("Load prices to see this list.")
        else:
            for _, row in cheap.iterrows():
                t_uk = row["timestamp_local"].strftime("%a %H:%M")
                price = float(row["price_p_per_kwh"])
                st.write(f"**{t_uk}** - {price:.2f} p/kWh")

        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.subheader("Smart recommendations")

        def rec_card(cls: str, title: str, time_txt: str, why: str, extra: Optional[str] = None) -> None:
            more = f"<div style='opacity:0.78;margin-top:8px;'>{extra}</div>" if extra else ""
            st.markdown(
                f"""
<div class="rec {cls}">
  <div style="font-weight:800;font-size:16px;">{title}</div>
  <div style="opacity:0.78;margin-top:6px;"><b>{time_txt}</b> - {why}</div>
  {more}
</div>
""",
                unsafe_allow_html=True,
            )

        if snapshot["negative_slots"] > 0:
            rec_card("green", "Negative-price opportunity", "Upcoming slots", "You have zero or below-zero price periods in the loaded data.")
        elif snapshot["current"] is not None and snapshot["current"] <= float(df["price_p_per_kwh"].quantile(0.25)):
            rec_card("green", "Good time to run flexible tasks", "Now", "Prices are already on the cheaper side.")
        else:
            rec_card("yellow", "Wait if you can", "Now", "Prices are not at their lowest yet.")

        if best_window:
            rec_card(
                "green",
                f"Best {st.session_state.run_hours:.1f}h window",
                f"{best_window['start_local'].strftime('%a %d %b %H:%M')} -> {best_window['end_local'].strftime('%a %d %b %H:%M')}",
                "Cheapest continuous window in the loaded period.",
                f"Average: {best_window['avg_price_p_per_kwh']:.2f} p/kWh",
            )

        rec_card(
            "red",
            "Watch the expensive slots",
            "Top peaks",
            "The priciest periods in your current data are:",
            "<br>".join(snapshot["peaks"]) if snapshot["peaks"] else "No peak data yet.",
        )

        st.markdown("</div>", unsafe_allow_html=True)


def render_prices_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Prices")

    mode = st.radio("Mode", ["Last 48h + next 24h", "Custom date/time"], horizontal=True)

    if mode == "Last 48h + next 24h":
        if st.button("Load", key="prices_load_standard", width="stretch"):
            with st.spinner("Loading prices..."):
                load_last_48h_next_24h()
            update_session_stats(st.session_state.df_prices)
            st.success("Loaded.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            from_date = st.date_input("From date", value=datetime.now(UK_TZ).date(), key="p_from_date")
            from_time = st.time_input("From time", value=time(0, 0), key="p_from_time")
        with c2:
            to_date = st.date_input("To date", value=datetime.now(UK_TZ).date(), key="p_to_date")
            to_time = st.time_input("To time", value=time(23, 30), key="p_to_time")

        from_local = datetime.combine(from_date, from_time).replace(tzinfo=UK_TZ)
        to_local = datetime.combine(to_date, to_time).replace(tzinfo=UK_TZ)
        if to_local <= from_local:
            to_local = from_local + timedelta(hours=1)

        if st.button("Load", key="prices_load_custom", width="stretch"):
            with st.spinner("Loading prices..."):
                load_prices(from_local.astimezone(timezone.utc), to_local.astimezone(timezone.utc))
            update_session_stats(st.session_state.df_prices)
            st.success("Loaded.")

    df = st.session_state.df_prices
    if df is not None and not df.empty:
        st.download_button(
            "Download CSV",
            data=to_csv_bytes(df),
            file_name="prices.csv",
            mime="text/csv",
            width="stretch",
        )
        st.dataframe(
            df[["timestamp_local", "price_p_per_kwh"]].rename(
                columns={"timestamp_local": "Time (UK)", "price_p_per_kwh": "Price (p/kWh)"}
            ).tail(250),
            width="stretch",
        )
    else:
        st.info("Load prices to see data.")

    st.markdown("</div>", unsafe_allow_html=True)


def render_scheduler_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🗓️ Smart Appliance Scheduler")
    st.caption(
        "Select your appliances and the app automatically finds the cheapest run window for each one — "
        "with cost and carbon estimates. This feature is not available on typical energy apps."
    )

    df = st.session_state.df_prices
    if df is None or df.empty:
        st.info("Load prices from the Dashboard first to use the scheduler.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    all_names = list(APPLIANCE_LIBRARY.keys())
    selected = st.multiselect(
        "Choose appliances to schedule",
        options=all_names,
        default=st.session_state.selected_appliances,
        key="appliance_multiselect",
    )
    st.session_state.selected_appliances = selected

    if not selected:
        st.info("Select at least one appliance above.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    schedule = smart_schedule_appliances(df, selected)
    if not schedule:
        st.warning("Could not compute schedule. Try loading more price data.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    total_cost = sum(s["cost_gbp"] for s in schedule)
    total_carbon = sum(s["carbon_kg"] for s in schedule)
    total_kwh = sum(s["kwh"] for s in schedule)

    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Total scheduled cost</div><div class='value'>£{total_cost:.2f}</div></div>",
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Total energy</div><div class='value'>{total_kwh:.2f} kWh</div></div>",
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Total CO₂</div><div class='value'>{total_carbon*1000:.0f}g</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)

    for s in schedule:
        flex_icon = "✅ Flexible" if s["flexible"] else "🔒 Fixed-time"
        price_rank = price_percentile_rank(df, s["avg_price"])
        if price_rank < 30:
            badge_cls = "badge-ok"
            badge_txt = f"Top {price_rank:.0f}% cheapest ✓"
        elif price_rank < 70:
            badge_cls = "badge-warn"
            badge_txt = f"Mid range ({price_rank:.0f}%)"
        else:
            badge_cls = "badge-bad"
            badge_txt = f"Pricey ({price_rank:.0f}%)"

        st.markdown(
            f"""
<div class='sched-row'>
  <div style='display:flex;align-items:center;gap:10px;flex:1;'>
    <span style='font-size:1.4rem;'>{s['icon']}</span>
    <div>
      <div style='font-weight:800;'>{s['name']}</div>
      <div style='opacity:0.65;font-size:0.8rem;'>{s['power_kw']}kW · {s['run_hours']}h · {flex_icon}</div>
    </div>
  </div>
  <div style='text-align:center;flex:1;'>
    <div style='font-size:0.85rem;opacity:0.72;'>Best window</div>
    <div style='font-weight:700;'>{s['best_start'].strftime('%a %H:%M')} → {s['best_end'].strftime('%H:%M')}</div>
  </div>
  <div style='text-align:center;flex:0.6;'>
    <div style='font-size:0.85rem;opacity:0.72;'>Cost</div>
    <div style='font-weight:700;'>£{s['cost_gbp']:.2f}</div>
  </div>
  <div style='text-align:center;flex:0.6;'>
    <div style='font-size:0.85rem;opacity:0.72;'>CO₂</div>
    <div style='font-weight:700;'>{s['carbon_kg']*1000:.0f}g</div>
  </div>
  <div style='text-align:right;flex:0.7;'>
    <span class='{badge_cls}'>{badge_txt}</span>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        ics_data = build_ics(
            s["best_start"],
            s["best_end"],
            f"Run {s['name']}",
            f"Cheapest window: {s['avg_price']:.2f} p/kWh. Cost: £{s['cost_gbp']:.2f}",
        )
        st.download_button(
            f"📅 Add {s['name']} to calendar",
            data=ics_data,
            file_name=f"{s['name'].lower().replace(' ','_')}_window.ics",
            mime="text/calendar",
            key=f"ics_{s['name']}",
        )

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("⚡ Demand Response Guidance")
    st.caption("Standard UK grid demand-response periods — times to avoid or embrace for flexible appliances.")

    for ev in DEMAND_RESPONSE_EVENTS:
        cls = "red" if ev["action"].startswith("Avoid") else ("yellow" if ev["action"].startswith("Delay") else "green")
        st.markdown(
            f"<div class='rec {cls}'>"
            f"<div style='font-weight:800;'>{ev['label']} ({ev['start_hour']:02d}:00 – {ev['end_hour']:02d}:00)</div>"
            f"<div style='opacity:0.78;margin-top:4px;'>Recommendation: {ev['action']}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def render_carbon_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🌱 Carbon Footprint Tracker")
    st.caption(
        "Track the carbon cost of running your appliances on the Agile tariff. "
        "Includes regional carbon intensity data and a green-shift calculator."
    )

    region_letter = st.session_state.region_letter
    region_name = REGION_DICT.get(region_letter, region_letter)
    carbon_g = get_carbon_intensity(region_letter)

    st.markdown(carbon_badge_html(carbon_g), unsafe_allow_html=True)
    st.markdown(
        f"<div style='opacity:0.65;font-size:0.88rem;margin:6px 0 14px 0;'>Region {region_letter} · {region_name}</div>",
        unsafe_allow_html=True,
    )

    st.markdown("**Carbon intensity by UK region**")
    region_data = [(REGION_DICT[r], CARBON_INTENSITY_BY_REGION[r]) for r in CARBON_INTENSITY_BY_REGION]
    region_data.sort(key=lambda x: x[1])
    for rname, intensity in region_data:
        bar_pct = int((intensity / 250) * 100)
        bar_color = "#4ade80" if intensity < 180 else ("#fbbf24" if intensity < 220 else "#fb7185")
        st.markdown(
            f"""
<div style='display:flex;align-items:center;gap:10px;margin-bottom:6px;font-size:0.85rem;'>
  <div style='width:160px;opacity:0.85;'>{rname}</div>
  <div style='flex:1;background:rgba(255,255,255,0.07);border-radius:999px;height:10px;'>
    <div style='width:{bar_pct}%;background:{bar_color};border-radius:999px;height:10px;'></div>
  </div>
  <div style='width:70px;text-align:right;font-weight:700;color:{bar_color};'>{intensity} g</div>
</div>
""",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Appliance carbon calculator")

    app_choice = st.selectbox("Appliance", list(APPLIANCE_LIBRARY.keys()), key="carbon_app")
    info = APPLIANCE_LIBRARY[app_choice]
    c1, c2 = st.columns(2)
    with c1:
        cp_kw = st.number_input("Power (kW)", value=info["power_kw"], min_value=0.1, max_value=22.0, step=0.1, key="c_power")
    with c2:
        ch_hrs = st.number_input("Hours", value=info["run_hours"], min_value=0.1, max_value=12.0, step=0.1, key="c_hours")

    carbon_kg = estimate_carbon_kg(cp_kw, ch_hrs, region_letter)
    kwh_used = cp_kw * ch_hrs

    st.markdown(
        f"<div class='pill'>Energy used: {kwh_used:.2f} kWh</div>"
        f"<div class='pill'>CO₂ emitted: {carbon_kg*1000:.0f}g ({carbon_kg:.3f} kg)</div>",
        unsafe_allow_html=True,
    )

    trees_minutes = carbon_kg * 1000 / 21.77
    km_equivalent = carbon_kg / 0.170
    st.caption(
        f"Equivalent to driving {km_equivalent:.2f} km in an average UK petrol car, "
        f"or {trees_minutes:.1f} minutes of a tree absorbing CO₂."
    )

    df = st.session_state.df_prices
    if df is not None and not df.empty:
        best_region_carbon = min(CARBON_INTENSITY_BY_REGION.values())
        current_carbon = carbon_g
        if current_carbon > best_region_carbon:
            saved_g = (current_carbon - best_region_carbon) / 1000.0 * kwh_used * 1000
            st.markdown(
                f"<div class='rec green'>"
                f"<div style='font-weight:800;'>If you were in the lowest-carbon UK region</div>"
                f"<div style='opacity:0.78;margin-top:4px;'>You'd emit {saved_g:.0f}g less CO₂ for this task.</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    st.markdown("</div>", unsafe_allow_html=True)


def render_bill_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("💷 Bill Projection & Savings Simulator")
    st.caption(
        "Estimate your monthly and annual electricity bill from live Agile prices "
        "and see how much you could save by shifting usage to off-peak windows."
    )

    df = st.session_state.df_prices
    if df is None or df.empty:
        st.info("Load prices from the Dashboard first.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    daily_kwh = st.number_input(
        "Daily household usage (kWh)",
        min_value=1.0,
        max_value=100.0,
        step=0.5,
        value=float(st.session_state.daily_kwh),
        key="bill_daily_kwh",
    )
    st.session_state.daily_kwh = daily_kwh

    proj = monthly_bill_projection(df, daily_kwh)
    if not proj:
        st.warning("Not enough data.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Avg price</div><div class='value'>{proj['avg_p_per_kwh']:.2f} p/kWh</div></div>",
            unsafe_allow_html=True,
        )
    with m2:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Daily cost</div><div class='value'>£{proj['daily_cost']:.2f}</div></div>",
            unsafe_allow_html=True,
        )
    with m3:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Monthly est.</div><div class='value'>£{proj['monthly']:.2f}</div></div>",
            unsafe_allow_html=True,
        )
    with m4:
        st.markdown(
            f"<div class='mini-metric'><div class='label'>Annual est.</div><div class='value'>£{proj['annual']:.0f}</div></div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

    st.markdown("**Savings simulator: shift X% of usage to cheapest slots**")
    shift_pct = st.slider(
        "% of usage shifted to cheapest windows", min_value=0, max_value=100, value=30, step=5
    )

    prices = df["price_p_per_kwh"].astype(float)
    avg_price = float(prices.mean())
    cheap_avg = float(prices.nsmallest(max(int(len(prices) * 0.25), 1)).mean())
    shifted_kwh = daily_kwh * (shift_pct / 100.0)
    unshifted_kwh = daily_kwh - shifted_kwh

    daily_shifted_cost = (unshifted_kwh * avg_price + shifted_kwh * cheap_avg) / 100.0
    monthly_shifted = daily_shifted_cost * 30
    annual_shifted = daily_shifted_cost * 365
    monthly_saving = proj["monthly"] - monthly_shifted
    annual_saving = proj["annual"] - annual_shifted

    s1, s2 = st.columns(2)
    with s1:
        st.markdown(
            f"<div class='rec green'>"
            f"<div style='font-weight:800;font-size:16px;'>Monthly saving</div>"
            f"<div style='font-size:1.6rem;font-weight:900;color:#4ade80;margin-top:8px;'>£{monthly_saving:.2f}</div>"
            f"<div style='opacity:0.72;margin-top:4px;font-size:0.85rem;'>If {shift_pct}% of usage is shifted</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    with s2:
        st.markdown(
            f"<div class='rec blue'>"
            f"<div style='font-weight:800;font-size:16px;'>Annual saving</div>"
            f"<div style='font-size:1.6rem;font-weight:900;color:#60a5fa;margin-top:8px;'>£{annual_saving:.2f}</div>"
            f"<div style='opacity:0.72;margin-top:4px;font-size:0.85rem;'>Cheapest 25% slots avg: {cheap_avg:.2f} p/kWh</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("**Price distribution summary**")
    quantiles = [0.10, 0.25, 0.50, 0.75, 0.90]
    for q in quantiles:
        val = float(prices.quantile(q))
        st.markdown(
            f"<div class='pill'>{int(q*100)}th percentile: {val:.2f} p/kWh</div>",
            unsafe_allow_html=True,
        )

    # ── PDF REPORT BUTTON ──
    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    if HAS_REPORTLAB:
        pdf_bytes = build_pdf_report(df, daily_kwh)
        if pdf_bytes:
            st.download_button(
                "📄 Download PDF report",
                data=pdf_bytes,
                file_name="smart_energy_report.pdf",
                mime="application/pdf",
                width="stretch",
            )
    else:
        txt_report = build_txt_report(df, daily_kwh)
        st.download_button(
            "📄 Download report (.txt)",
            data=txt_report.encode("utf-8"),
            file_name="smart_energy_report.txt",
            mime="text/plain",
            width="stretch",
        )
        st.caption("Install reportlab (`pip install reportlab`) to enable PDF reports.")

    st.markdown("</div>", unsafe_allow_html=True)


# ─── COMPARE PAGE (NEW) ──────────────────────────────────────────────────────

def render_compare_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("⚖️ Tariff Comparison")
    st.caption(
        "Compare your live Agile costs against Standard Variable (Ofgem cap), Octopus Go, "
        "Cosy, Economy 7, and Fixed Rate tariffs. See which tariff wins and how much smart shifting is worth."
    )

    df = st.session_state.df_prices
    if df is None or df.empty:
        st.info("Load prices from the Dashboard first to compare tariffs.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    daily_kwh = st.number_input(
        "Daily usage (kWh)",
        min_value=1.0,
        max_value=100.0,
        step=0.5,
        value=float(st.session_state.daily_kwh),
        key="compare_daily_kwh",
    )
    st.session_state.daily_kwh = daily_kwh

    comparison = compute_tariff_comparison(df, daily_kwh)
    if not comparison:
        st.warning("Not enough data.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    # Sort by monthly cost
    sorted_tariffs = sorted(comparison.items(), key=lambda x: x[1]["monthly_cost"])

    for tname, tdata in sorted_tariffs:
        is_winner = tdata.get("is_winner", False)
        row_cls = "compare-row winner" if is_winner else "compare-row"
        star = "⭐ " if is_winner else ""
        vs_str = ""
        vs_val = tdata.get("vs_agile", 0)
        if vs_val > 0:
            vs_str = f"<span style='color:#fb7185;font-weight:800;'>+£{vs_val:.2f}/mo vs Agile</span>"
        elif vs_val < 0:
            vs_str = f"<span style='color:#4ade80;font-weight:800;'>£{vs_val:.2f}/mo vs Agile</span>"
        else:
            vs_str = "<span style='opacity:0.5;'>—</span>"

        st.markdown(
            f"""
<div class='{row_cls}'>
  <div style='flex:1.4;'>
    <div style='font-weight:800;font-size:1rem;'>{star}{tname}</div>
    <div style='opacity:0.6;font-size:0.78rem;margin-top:2px;'>{tdata['description']}</div>
  </div>
  <div style='text-align:center;flex:0.6;'>
    <div style='font-size:0.8rem;opacity:0.65;'>Avg rate</div>
    <div style='font-weight:700;'>{tdata['avg_rate']:.2f} p/kWh</div>
  </div>
  <div style='text-align:center;flex:0.6;'>
    <div style='font-size:0.8rem;opacity:0.65;'>Monthly</div>
    <div style='font-weight:700;'>£{tdata['monthly_cost']:.2f}</div>
  </div>
  <div style='text-align:right;flex:0.7;'>
    {vs_str}
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)

    # Smart shift value
    shift_val = compute_smart_shift_value(df, daily_kwh)
    if shift_val:
        st.markdown(
            f"<div class='rec green'>"
            f"<div style='font-weight:800;font-size:16px;'>💡 Value of smart shifting on Agile</div>"
            f"<div style='opacity:0.78;margin-top:6px;'>"
            f"If you shift {shift_val['shift_pct']:.0f}% of your usage to the cheapest 25% of slots, "
            f"you save approximately <b>£{shift_val['monthly_saving']:.2f}/month</b> compared to using energy randomly."
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


# ─── HEATMAP PAGE (NEW) ──────────────────────────────────────────────────────

def render_heatmap_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("🟥 Price Heatmap")
    st.caption(
        "A full colour grid of every 30-minute price slot across all loaded days. "
        "Green = cheap, red = expensive. Spot daily patterns at a glance."
    )

    df = st.session_state.df_prices
    if df is None or df.empty:
        st.info("Load prices from the Dashboard first to view the heatmap.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    ordered = df.sort_values("timestamp_local").copy()
    ordered["date"] = ordered["timestamp_local"].dt.date
    ordered["time_slot"] = ordered["timestamp_local"].dt.strftime("%H:%M")
    pivot = ordered.pivot_table(
        index="time_slot", columns="date", values="price_p_per_kwh", aggfunc="mean"
    )
    pivot = pivot.sort_index()

    # Try Plotly heatmap first
    if go is not None:
        try:
            x_labels = [str(d) for d in pivot.columns]
            y_labels = list(pivot.index)
            z_values = pivot.values.tolist()

            fig = go.Figure(data=go.Heatmap(
                z=z_values,
                x=x_labels,
                y=y_labels,
                colorscale=[
                    [0.0, "#22c55e"],
                    [0.3, "#86efac"],
                    [0.5, "#fbbf24"],
                    [0.7, "#f97316"],
                    [1.0, "#ef4444"],
                ],
                colorbar=dict(title="p/kWh", tickfont=dict(color="white"), titlefont=dict(color="white")),
                hovertemplate="Date: %{x}<br>Time: %{y}<br>Price: %{z:.2f} p/kWh<extra></extra>",
            ))
            fig.update_layout(
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="rgba(255,255,255,0.9)"),
                margin=dict(l=60, r=20, t=20, b=40),
                height=max(400, len(y_labels) * 10),
                xaxis=dict(title="Date", tickfont=dict(size=10)),
                yaxis=dict(title="Time slot", autorange="reversed", tickfont=dict(size=9)),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        except Exception:
            _render_heatmap_matplotlib(pivot)
    else:
        _render_heatmap_matplotlib(pivot)

    # Consistently cheapest / priciest time slot
    st.markdown("<div style='height:14px;'></div>", unsafe_allow_html=True)
    avg_by_slot = pivot.mean(axis=1)
    if not avg_by_slot.empty:
        cheapest_slot = avg_by_slot.idxmin()
        cheapest_price = avg_by_slot.min()
        priciest_slot = avg_by_slot.idxmax()
        priciest_price = avg_by_slot.max()

        st.markdown(
            f"<div class='rec green'>"
            f"<div style='font-weight:800;'>Consistently cheapest slot across all days</div>"
            f"<div style='opacity:0.78;margin-top:4px;'><b>{cheapest_slot}</b> — avg {cheapest_price:.2f} p/kWh</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div class='rec red'>"
            f"<div style='font-weight:800;'>Consistently most expensive slot</div>"
            f"<div style='opacity:0.78;margin-top:4px;'><b>{priciest_slot}</b> — avg {priciest_price:.2f} p/kWh</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_heatmap_matplotlib(pivot: pd.DataFrame) -> None:
    """Fallback matplotlib heatmap if Plotly is unavailable."""
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "energy", ["#22c55e", "#86efac", "#fbbf24", "#f97316", "#ef4444"]
    )
    fig, ax = plt.subplots(figsize=(max(6, len(pivot.columns) * 1.2), max(6, len(pivot.index) * 0.22)), dpi=120)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")

    data = pivot.values.astype(float)
    im = ax.imshow(data, aspect="auto", cmap=cmap, interpolation="nearest")
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([str(d) for d in pivot.columns], rotation=45, ha="right", fontsize=8, color="white")
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(list(pivot.index), fontsize=7, color="white")
    ax.set_xlabel("Date", color="white", fontsize=10)
    ax.set_ylabel("Time slot", color="white", fontsize=10)
    cb = fig.colorbar(im, ax=ax, shrink=0.7)
    cb.set_label("p/kWh", color="white", fontsize=9)
    cb.ax.yaxis.set_tick_params(color="white")
    for label in cb.ax.yaxis.get_ticklabels():
        label.set_color("white")
    fig.tight_layout()
    st.pyplot(fig, use_container_width=True)


def render_evaluation_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Evaluation")

    df = st.session_state.df_prices
    if df is None or df.empty:
        st.info("Load prices first from the Dashboard.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    forecast_rf = make_forecast(df, method="RF")
    forecast_persistence = make_forecast(df, method="Persistence")
    window_hours = float(st.session_state.run_hours)

    def pill(label: str, value: str) -> None:
        st.markdown(f"<div class='pill'><b>{label}</b>: {value}</div>", unsafe_allow_html=True)

    rf_mae = "-"
    rf_regret = "-"
    if not forecast_rf.empty:
        actual = forecast_rf["actual"].to_numpy(dtype=float)
        pred = forecast_rf["forecast"].to_numpy(dtype=float)
        rf_mae = f"{mae(actual, pred):.2f} p/kWh"
        rf_regret = f"{cheapest_window_regret_pence(forecast_rf['timestamp_local'], actual, pred, window_hours):.2f} p"

    pers_mae = "-"
    pers_regret = "-"
    if not forecast_persistence.empty:
        actual = forecast_persistence["actual"].to_numpy(dtype=float)
        pred = forecast_persistence["forecast"].to_numpy(dtype=float)
        pers_mae = f"{mae(actual, pred):.2f} p/kWh"
        pers_regret = f"{cheapest_window_regret_pence(forecast_persistence['timestamp_local'], actual, pred, window_hours):.2f} p"

    left, right = st.columns(2)
    with left:
        pill("MAE RF", rf_mae)
        pill("MAE Persistence", pers_mae)
    with right:
        pill("Regret RF", rf_regret)
        pill("Regret Persistence", pers_regret)

    vol = price_volatility_score(df)
    pill("Volatility score", f"{vol['score']}/100 ({vol['band']})")
    pill("Price spread", f"{vol['spread']:.2f} p/kWh")
    pill("Std deviation", f"{vol['std']:.2f} p/kWh")

    st.caption("MAE is typical forecast error. Regret is the extra paid if you follow the predicted cheapest window.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_subscribe_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Subscribe")
    st.caption("Smart alerts are automatic. The app uses your loaded tariff, region, live prices, and AI connection when available.")

    df = st.session_state.df_prices
    plan = build_auto_alert_plan(df)
    best = plan["best"]
    snapshot = build_price_snapshot(df) if df is not None and not df.empty else {}

    left, right = st.columns([1.3, 1.0], gap="large")

    with left:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.markdown("**One-click setup**")
        email = st.text_input("Email", placeholder="name@example.com", key="sub_email")

        st.markdown(f"<div class='pill'>Mode: {plan['mode']}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='pill'>Auto threshold: {plan['threshold']:.2f} p/kWh</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='pill'>Region: {REGION_DICT.get(st.session_state.region_letter, st.session_state.region_letter)}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='pill'>Tariff: {st.session_state.tariff_code}</div>", unsafe_allow_html=True)

        st.markdown(
            "<div class='rec yellow'>"
            "<div style='font-weight:800;font-size:16px;'>Automatic behavior</div>"
            f"<div style='opacity:0.78;margin-top:6px;'>{plan['message']}</div>"
            "</div>",
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        with c1:
            send_confirmation = st.button("Enable smart alerts", width="stretch")
        with c2:
            send_digest = st.button("Send sample digest", width="stretch")

        if send_confirmation:
            email_clean = (email or "").strip()
            if not email_clean:
                st.error("Enter a valid email.")
            else:
                prompt = (
                    "Create a concise, friendly UK email confirming automatic smart energy alerts.\n"
                    "Keep it under 120 words. Mention auto price tracking, smart digest logic, and unsubscribe.\n"
                    f"User email: {email_clean}\n"
                    f"Auto plan(JSON): {json.dumps({'mode': plan['mode'], 'threshold': plan['threshold'], 'message': plan['message']})}\n"
                    "Return only the plain text body.\n"
                )
                with st.spinner("Preparing email..."):
                    ok_ai, email_text = ai_call_text(prompt)

                if not ok_ai or not (email_text or "").strip():
                    email_text = build_subscription_email(email_clean, df)

                import hashlib

                payload_hash = hashlib.sha1((email_clean + "|" + email_text).encode("utf-8")).hexdigest()
                if payload_hash == st.session_state.sub_last_hash:
                    st.info("This email was just sent. Skipping duplicate.")
                else:
                    ok_send, msg_send = send_email_mailtrap(email_clean, "Smart Energy alerts", email_text)
                    if ok_send:
                        st.session_state.sub_last_hash = payload_hash
                        st.success("Smart alerts enabled.")
                    else:
                        st.error(msg_send)

        if send_digest:
            email_clean = (email or "").strip()
            if not email_clean:
                st.error("Enter an email first.")
            else:
                body = compose_digest_text(df, float(st.session_state.run_hours))
                ok_send, msg_send = send_email_mailtrap(email_clean, "Smart Energy - Smart Digest", body)
                if ok_send:
                    st.success("Sample digest sent.")
                else:
                    st.error(msg_send)

        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("<div class='panel'>", unsafe_allow_html=True)
        st.markdown("**Alert preview**")

        if best:
            st.markdown(
                "<div class='rec green'>"
                "<div style='font-weight:800;font-size:16px;'>Best current window</div>"
                f"<div style='opacity:0.78;margin-top:6px;'><b>{best['start_local'].strftime('%a %d %b %H:%M')} -> {best['end_local'].strftime('%a %d %b %H:%M')}</b></div>"
                f"<div style='opacity:0.78;margin-top:8px;'>Average: {best['avg_price_p_per_kwh']:.2f} p/kWh</div>"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            st.info("Load prices on the Dashboard to generate the live alert preview.")

        st.markdown(
            "<div class='rec yellow'>"
            "<div style='font-weight:800;font-size:16px;'>What gets included</div>"
            "<div style='opacity:0.78;margin-top:6px;'>Cheapest run windows, spike warnings, price volatility notes, and smart digest snapshots.</div>"
            "</div>",
            unsafe_allow_html=True,
        )

        if snapshot:
            st.markdown(
                "<div class='rec green'>"
                "<div style='font-weight:800;font-size:16px;'>Next cheap slot</div>"
                f"<div style='opacity:0.78;margin-top:6px;'>{snapshot['next_cheap_label']}</div>"
                "</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            "<div class='rec red'>"
            "<div style='font-weight:800;font-size:16px;'>Extra feature</div>"
            f"<div style='opacity:0.78;margin-top:6px;'>Negative-price slots detected: {plan['negative_slots']}. These are highlighted automatically whenever they appear.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


def render_settings_page() -> None:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    st.subheader("Settings")
    st.caption("This final version keeps most behavior automatic. You only need connections and tariff settings here.")

    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("**Connections**")
    api_key = get_openai_key()
    if OpenAI is None:
        st.warning("OpenAI package not installed. Run: pip install openai")
    elif not api_key:
        st.warning("Add OPENAI_API_KEY or [openai].api_key to .streamlit/secrets.toml.")
    else:
        st.success("Assistant connected.")

    smtp_ok, _, smtp_msg = _get_mailtrap_smtp_settings()
    api_ok, _, api_msg = _get_mailtrap_sdk_settings()
    if smtp_ok or api_ok:
        st.success("Mail sending configured.")
    else:
        st.warning(f"{smtp_msg} | {api_msg}")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("**Tariff configuration**")
    base_tariff = st.text_input("Tariff base", value=st.session_state.tariff_code_base, key="adv_tariff")
    product_code = st.text_input("Product code", value=st.session_state.product_code, key="adv_product")

    if st.button("Apply", width="stretch"):
        st.session_state.tariff_code_base = base_tariff.strip()
        st.session_state.product_code = product_code.strip()
        st.session_state.tariff_code = normalize_tariff_code(
            st.session_state.tariff_code_base,
            st.session_state.region_letter,
        )
        st.session_state.df_prices = pd.DataFrame(columns=["timestamp_utc", "timestamp_local", "price_p_per_kwh"])
        st.cache_data.clear()
        st.success("Updated. Reload prices.")
        _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='panel'>", unsafe_allow_html=True)
    st.markdown("**Automation mode**")
    st.markdown(
        "<div class='rec green'>"
        "<div style='font-weight:800;font-size:16px;'>Automatic smart mode</div>"
        "<div style='opacity:0.78;margin-top:6px;'>The app now auto-builds alert thresholds, live price digests, and recommended windows from the loaded tariff data. Manual preferences were removed to keep the final version cleaner.</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("Clear cache", width="stretch"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("Cleared.")
        _rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)


if st.session_state.page == "Dashboard":
    render_dashboard()
elif st.session_state.page == "Prices":
    render_prices_page()
elif st.session_state.page == "Scheduler":
    render_scheduler_page()
elif st.session_state.page == "Carbon":
    render_carbon_page()
elif st.session_state.page == "Bill":
    render_bill_page()
elif st.session_state.page == "Compare":
    render_compare_page()
elif st.session_state.page == "Heatmap":
    render_heatmap_page()
elif st.session_state.page == "Evaluation":
    render_evaluation_page()
elif st.session_state.page == "Subscribe":
    render_subscribe_page()
elif st.session_state.page == "Settings":
    render_settings_page()