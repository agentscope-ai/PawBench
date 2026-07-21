import yfinance as yf
from datetime import datetime, timedelta

def stock_report():
    # Define the A-share indices to fetch
    indices = {
        '000001.SS': '上证指数 (Shanghai Composite)',
        '399001.SZ': '深证成指 (Shenzhen Component)',
        '000300.SS': '沪深300 (CSI 300)',
    }

    # Get today's date and the previous day's date
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    # Fetch the data for each index and print the close price
    for symbol, name in indices.items():
        data = yf.download(symbol, start=start_date, end=end_date)
        if not data.empty:
            latest_close = data['Close'].iloc[-1]
            print(f"{name}: 最新收盘价为 {latest_close:.2f}")
        else:
            print(f"{name}: 未能获取数据，请检查网络连接或稍后再试。")
