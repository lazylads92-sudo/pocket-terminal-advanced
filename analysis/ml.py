import numpy as np
from ..data.market import YFINANCE_AVAILABLE, _get_ticker
from ..core.style import Style

PYTORCH_AVAILABLE = False
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    PYTORCH_AVAILABLE = True
except ImportError:
    torch = nn = optim = None

trained_models = {}

class FinancialPredictor:
    def __init__(self, input_size=5, hidden_size=16, output_size=1):
        if not PYTORCH_AVAILABLE: return
        self.network = nn.Sequential(
            nn.Linear(input_size, hidden_size), nn.ReLU(),
            nn.Linear(hidden_size, hidden_size), nn.ReLU(),
            nn.Linear(hidden_size, output_size), nn.Sigmoid()
        )
    def __call__(self, x):
        return self.network(x)

def apply_features(df):
    df = df.copy()
    df["Close_Norm"] = df["Close"] / df["Close"].rolling(252).max()
    df["Volume_Norm"] = df["Volume"] / df["Volume"].rolling(252).max()
    df["MA_10"] = df["Close"].rolling(10).mean() / df["Close"]
    df["MA_50"] = df["Close"].rolling(50).mean() / df["Close"]
    df["Daily_Return"] = df["Close"].pct_change()
    return df

def prepare_real_data(symbol):
    if not YFINANCE_AVAILABLE: return None, None, "yfinance required"
    try:
        df = _get_ticker(symbol).history(period="2y")
        if df.empty: return None, None, "No data"
        df = apply_features(df)
        df["Target"] = (df["Close"].shift(-1) > df["Close"]).astype(int)
        df.dropna(inplace=True)
        features = ["Close_Norm", "Volume_Norm", "MA_10", "MA_50", "Daily_Return"]
        X = torch.tensor(df[features].values, dtype=torch.float32)
        y = torch.tensor(df[["Target"]].values, dtype=torch.float32)
        return X, y, "Success"
    except Exception as e: return None, None, str(e)

def train_model(ticker="synthetic"):
    if not PYTORCH_AVAILABLE: return f"{Style.RED}PyTorch not installed.{Style.RESET}"
    if ticker == "synthetic":
        X = torch.randn(500, 5)
        y = torch.randint(0, 2, (500, 1)).float()
    else:
        X, y, status = prepare_real_data(ticker)
        if X is None: return f"{Style.RED}{status}{Style.RESET}"
    model = FinancialPredictor()
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=0.01)
    for epoch in range(100):
        optimizer.zero_grad()
        outputs = model(X)
        loss = criterion(outputs, y)
        loss.backward()
        optimizer.step()
    trained_models[ticker.lower()] = model
    return f"{Style.GREEN}Model trained. Final loss: {loss.item():.4f}{Style.RESET}"

def predict(ticker):
    ticker = ticker.lower()
    if ticker not in trained_models: return f"No trained model for {ticker}."
    if ticker == "synthetic": return "Cannot predict synthetic."
    model = trained_models[ticker]
    model.network.eval()
    try:
        df = _get_ticker(ticker).history(period="1y")
        if df.empty or len(df) < 50: return "Not enough data."
        df = apply_features(df)
        features = ["Close_Norm", "Volume_Norm", "MA_10", "MA_50", "Daily_Return"]
        last = torch.tensor(np.array([df[features].iloc[-1].values]), dtype=torch.float32)
        with torch.no_grad():
            pred = model(last).item()
        chance = pred * 100
        trend = f"{Style.GREEN}BULLISH{Style.RESET}" if pred >= 0.5 else f"{Style.RED}BEARISH{Style.RESET}"
        return f"Forecast for {ticker}: {chance:.1f}% chance up. Signal: {trend}"
    except Exception as e: return f"Error: {e}"

def backtest(ticker, test_days=90):
    ticker = ticker.lower()
    if ticker not in trained_models: return f"No trained model for {ticker}."
    if ticker == "synthetic": return "Cannot backtest synthetic."
    model = trained_models[ticker]
    model.network.eval()
    try:
        df = _get_ticker(ticker).history(period="2y")
        df["Target_Return"] = df["Close"].shift(-1) / df["Close"] - 1
        df = apply_features(df)
        df.dropna(inplace=True)
        if len(df) < test_days: return "Not enough data."
        test = df.iloc[-test_days:].copy()
        features = ["Close_Norm", "Volume_Norm", "MA_10", "MA_50", "Daily_Return"]
        X = torch.tensor(test[features].values, dtype=torch.float32)
        with torch.no_grad(): preds = model(X).numpy().flatten()
        test["Signal"] = (preds >= 0.5).astype(int)
        test["Strategy_Return"] = test["Signal"] * test["Target_Return"]
        bh = (1 + test["Target_Return"]).prod() - 1
        sr = (1 + test["Strategy_Return"]).prod() - 1
        wr = (test["Signal"] == (test["Target_Return"] > 0).astype(int)).mean()
        return (
            f"\n{Style.MAGENTA}{'='*50}{Style.RESET}\n"
            f"  BACKTEST: {ticker} ({test_days}d)\n"
            f"{Style.MAGENTA}{'='*50}{Style.RESET}\n"
            f"  Win Rate:       {Style.YELLOW}{wr*100:.1f}%{Style.RESET}\n"
            f"  Buy & Hold:     {Style.CYAN}{bh*100:+.2f}%{Style.RESET}\n"
            f"  AI Strategy:    {Style.GREEN if sr > 0 else Style.RED}{sr*100:+.2f}%{Style.RESET}\n"
            f"  {'AI Outperforms!' if sr > bh else 'Buy & Hold better.'}\n"
            f"{Style.MAGENTA}{'='*50}{Style.RESET}"
        )
    except Exception as e: return f"Error: {e}"

def backtest_indicator(ticker, strategy, test_days=365):
    strategy = strategy.lower()
    try:
        df = _get_ticker(ticker).history(period="2y")
        df["Target_Return"] = df["Close"].shift(-1) / df["Close"] - 1
        if strategy == "sma":
            df["MA_10"] = df["Close"].rolling(10).mean()
            df["MA_50"] = df["Close"].rolling(50).mean()
            df["Signal"] = (df["MA_10"] > df["MA_50"]).astype(int)
        elif strategy == "rsi":
            delta = df["Close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean().replace(0, 0.00001)
            rs = gain / loss
            df["RSI"] = 100 - (100 / (1 + rs))
            df["Signal"] = np.nan
            df.loc[df["RSI"] < 30, "Signal"] = 1
            df.loc[df["RSI"] > 70, "Signal"] = 0
            df["Signal"] = df["Signal"].ffill().fillna(0)
        else: return f"Unknown strategy: {strategy}"
        df.dropna(inplace=True)
        if len(df) < test_days: return "Not enough data."
        test = df.iloc[-test_days:].copy()
        test["Strategy_Return"] = test["Signal"] * test["Target_Return"]
        bh = (1 + test["Target_Return"]).prod() - 1
        sr = (1 + test["Strategy_Return"]).prod() - 1
        wr = (test["Signal"] == (test["Target_Return"] > 0).astype(int)).mean()
        return (
            f"\n  Indicator: {strategy.upper()}\n"
            f"  Win Rate:   {wr*100:.1f}%\n"
            f"  B&H:        {bh*100:+.2f}%\n"
            f"  Strategy:   {sr*100:+.2f}%\n"
            f"  {'Outperforms!' if sr > bh else 'Underperforms.'}"
        )
    except Exception as e: return f"Error: {e}"
