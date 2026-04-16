# The below script takes different API endpoints and 
# turns them into readable dfs and CSV files.

import requests
import base64
import pandas as pd
from keys_config import api_key, secret_key

# Define and encode credentials

credentials = f"{api_key}:{secret_key}"
encoded = base64.b64encode(credentials.encode()).decode()
headers = {"Authorization": f"Basic {encoded}"}

# Define API endpoints

# === POSITIONS ============================================
positions = requests.get(
    "https://live.trading212.com/api/v0/equity/positions",
    headers=headers)
positions = positions.json()

df_pos = pd.json_normalize(positions)
df_pos.to_csv("positions.csv", index=False)
# ==========================================================


# === ACCOUNT SUMMARY ======================================
account_sum = requests.get(
    "https://live.trading212.com/api/v0/equity/account/summary",
    headers=headers)
account_sum = account_sum.json()

df_sum = pd.json_normalize(account_sum)
df_sum.to_csv("account_summary.csv", index=False)
# ==========================================================


# === ORDERS ===============================================
orders = requests.get(
    "https://live.trading212.com/api/v0/equity/orders",
    headers=headers)
orders = orders.json()

df_orders = pd.json_normalize(orders)
df_orders.to_csv("orders.csv", index=False)
# ==========================================================


# === HISTORICAL ORDERS ====================================
hist_orders = requests.get(
    "https://live.trading212.com/api/v0/equity/history/orders",
    headers=headers)
hist_orders = hist_orders.json()

df_hist_orders = pd.json_normalize(hist_orders["items"])
df_hist_orders.to_csv("historical_orders.csv", index=False)
# ==========================================================


# === HISTORICAL TRANSACTIONS ==============================
hist_transactions = requests.get(
    "https://live.trading212.com/api/v0/equity/history/transactions",
    headers=headers)
hist_transactions = hist_transactions.json()

df_transactions = pd.json_normalize(hist_transactions["items"])
df_transactions.to_csv("historical_transactions.csv", index=False)
# ==========================================================


# === METADATA INSTRUMENTS =================================
meta_instruments = requests.get(
    "https://live.trading212.com/api/v0/equity/metadata/instruments",
    headers=headers)
meta_instruments = meta_instruments.json()

df_meta_instr = pd.json_normalize(meta_instruments)
df_meta_instr.to_csv("instruments.csv", index=False)
# ==========================================================

endpoints = [positions, account_sum, orders, hist_orders, 
             hist_transactions, meta_instruments]
