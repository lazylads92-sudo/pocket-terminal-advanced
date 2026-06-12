import numpy as np
import pandas as pd

def calc_indicators(df):
    df = df.copy()
    df["MA_20"] = df["Close"].rolling(20).mean()
    df["MA_50"] = df["Close"].rolling(50).mean()
    df["MA_200"] = df["Close"].rolling(200).mean()
    std20 = df["Close"].rolling(20).std()
    df["BB_Upper"] = df["MA_20"] + 2 * std20
    df["BB_Lower"] = df["MA_20"] - 2 * std20
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    df["RSI"] = 100 - (100 / (1 + rs))
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    df["ATR"] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()
    obv = [0]
    for i in range(1, len(df)):
        if df["Close"].iloc[i] > df["Close"].iloc[i - 1]:
            obv.append(obv[-1] + df["Volume"].iloc[i])
        elif df["Close"].iloc[i] < df["Close"].iloc[i - 1]:
            obv.append(obv[-1] - df["Volume"].iloc[i])
        else:
            obv.append(obv[-1])
    df["OBV"] = obv
    low14 = df["Low"].rolling(14).min()
    high14 = df["High"].rolling(14).max()
    df["Stoch_K"] = 100 * (df["Close"] - low14) / (high14 - low14 + 1e-9)
    df["Stoch_D"] = df["Stoch_K"].rolling(3).mean()
    x = np.arange(len(df))
    z = np.polyfit(x, df["Close"].values, 1)
    df["Auto_Trend"] = np.poly1d(z)(x)
    return df

def find_support_resistance(df, n=5):
    closes = df["Close"].values
    highs, lows = [], []
    for i in range(n, len(closes) - n):
        if closes[i] == max(closes[i-n:i+n+1]): highs.append((i, closes[i]))
        if closes[i] == min(closes[i-n:i+n+1]): lows.append((i, closes[i]))
    def cluster(levels, tol=0.015):
        result, used = [], set()
        for i, (idx, val) in enumerate(levels):
            if i in used: continue
            group = [val]
            for j, (jdx, jval) in enumerate(levels):
                if j != i and j not in used and abs(val - jval)/val < tol:
                    group.append(jval); used.add(j)
            result.append(np.mean(group)); used.add(i)
        return sorted(set(result))
    return cluster(highs[-20:]), cluster(lows[-20:])

def fibonacci_levels(high, low):
    diff = high - low
    keys = ["0.0%", "23.6%", "38.2%", "50.0%", "61.8%", "78.6%", "100.0%"]
    ratios = [0, 0.236, 0.382, 0.500, 0.618, 0.786, 1.0]
    return {k: high - r * diff for k, r in zip(keys, ratios)}
