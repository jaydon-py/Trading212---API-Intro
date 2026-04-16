import requests
import base64
import json
import pandas as pd
import re
#import os
from keys_config import api_key, secret_key
#from datetime import datetime

credentials = f"{api_key}:{secret_key}"
encoded = base64.b64encode(credentials.encode()).decode()


headers = {"Authorization": f"Basic {encoded}"}

response = requests.get(
    "https://live.trading212.com/api/v0/equity/positions",
    headers=headers
)

print(response.status_code)
print(response.text)

data = response.json()
print(json.dumps(data, indent=4))

# A list [ ] contains multiple positions
# Each position is a dictionary { } with key:value pairs
# data = [
#     {"ticker": "AAPL", "quantity": 10, "currentPrice": 185.5},
#     {"ticker": "TSLA", "quantity": 5, "currentPrice": 240.0},]

# data[0]                    > first position (whole dictionary)
# data[0]["ticker"]          > "AAPL"
# data[0]["currentPrice"]    > 185.5

# Pulling out cleaner data

clean_data = []

def clean_ticker(ticker):
    ticker = ticker.replace("_EQ", "")
    ticker = ticker.replace("_US", "")
    ticker = re.sub(r'[a-z]+$', '', ticker)
    return ticker

for position in data:
    clean_data.append({
        "Stock": clean_ticker(position["instrument"]["ticker"]),
        "Name": position["instrument"]["name"],
        "Currency": position["instrument"]["currency"],
        "Quantity": position["quantity"],
        "Avg Price Paid": position["averagePricePaid"],
        "Current Price": position["currentPrice"],
        "Current Value": position["walletImpact"]["currentValue"],
        "Total Cost": position["walletImpact"]["totalCost"],
        "Profit/Loss": position["walletImpact"]["unrealizedProfitLoss"],
        "FX Impact": position["walletImpact"]["fxImpact"]})

df = pd.DataFrame(clean_data)
df.to_csv('current_investments.csv', index=False)

hist_response = requests.get(
    "https://live.trading212.com/api/v0/equity/history/orders",
    headers=headers
)

hist_data = hist_response.json()
print(json.dumps(hist_data, indent=4))

hist_clean = []

for item in hist_data["items"]:
    order = item["order"]
    fill = item["fill"]
    
    hist_clean.append({
        "Date": fill.get("filledAt"),
        "Stock": clean_ticker(order["instrument"]["ticker"]),
        "Name": order["instrument"]["name"],
        "Currency": order["instrument"]["currency"],
        "Side": order.get("side"),
        "Quantity": fill.get("quantity"),
        "Price": fill.get("price"),
        "Value": order.get("filledValue"),
        "Profit/Loss": fill["walletImpact"].get("realisedProfitLoss"),
        "FX Rate": fill["walletImpact"].get("fxRate"),
        "Type": order.get("initiatedFrom")})

hist_df = pd.DataFrame(hist_clean)
hist_df.to_csv("trade_history.csv", index=False)
print(hist_df)