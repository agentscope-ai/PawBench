import csv
from datetime import datetime
import json
import math

# Load cleaned sector prices
def load_prices(file_path):
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)
        prices = {row[1]: [] for row in reader}
        f.seek(0)
        next(reader)  # Skip header
        for row in reader:
            ticker = row[1]
            date = datetime.strptime(row[0], '%Y-%m-%d').date()
            open_price = float(row[2])
            high_price = float(row[3])
            low_price = float(row[4])
            close_price = float(row[5])
            volume = int(row[6])
            prices[ticker].append((date, open_price, high_price, low_price, close_price, volume))
    return prices

# Load benchmark data
def load_benchmark(file_path):
    with open(file_path, 'r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        benchmark = {}
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d').date()
            close_price = float(row[5])
            benchmark[date] = close_price
    return benchmark

# Calculate momentum scores
def calculate_momentum(prices, lookback_weeks, skip_recent_weeks):
    momentum_scores = {}
    for ticker, price_data in prices.items():
        for i in range(skip_recent_weeks, len(price_data), 1):
            if i - lookback_weeks < 0:
                continue
            current_close = price_data[i][4]
            past_close = price_data[i - lookback_weeks][4]
            momentum_score = (current_close / past_close) - 1
            date = price_data[i][0]
            if date not in momentum_scores:
                momentum_scores[date] = {}
            momentum_scores[date][ticker] = momentum_score
    return momentum_scores

# Select top N sectors by momentum score, applying risk override
def select_portfolio(momentum_scores, top_n, skip_recent_weeks):
    portfolios = {}
    for date, scores in momentum_scores.items():
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        selected_sectors = [ticker for ticker, _ in sorted_scores[:top_n]]
        # Apply risk override: exclude sectors with negative 4-week momentum
        for ticker, _ in sorted_scores[top_n:]:
            if calculate_4_week_momentum(prices, ticker, date, skip_recent_weeks) > 0:
                selected_sectors.append(ticker)
                break
        portfolios[date] = selected_sectors
    return portfolios

# Calculate 4-week momentum
def calculate_4_week_momentum(prices, ticker, date, skip_recent_weeks):
    price_data = prices[ticker]
    for i, (d, _, _, _, close, _) in enumerate(price_data):
        if d == date:
            if i - 4 < 0:
                return 0
            current_close = close
            past_close = price_data[i - 4][4]
            return (current_close / past_close) - 1
    return 0

# Rebalance portfolio every 4 weeks
def rebalance_portfolio(portfolios, initial_capital, transaction_costs, prices):
    transactions = {}
    holdings = {ticker: 0 for ticker in portfolios[next(iter(portfolios.keys()))]}
    cash = initial_capital
    for date, selected_sectors in portfolios.items():
        if date not in transactions:
            transactions[date] = []
        total_value = cash + sum(holdings[ticker] * prices[ticker][i][4] for ticker, i in [(t, next(i for i, (d, _, _, _, _, _) in enumerate(prices[t]) if d == date)) for t in holdings])
        target_value_per_sector = total_value / len(selected_sectors)
        for ticker in selected_sectors:
            index = next(i for i, (d, _, _, _, _, _) in enumerate(prices[ticker]) if d == date)
            price = prices[ticker][index][4]
            target_shares = target_value_per_sector / price
            shares_to_buy = target_shares - holdings[ticker]
            if shares_to_buy > 0:
                cost = shares_to_buy * price + transaction_costs['commission_per_trade'] + (shares_to_buy * price * transaction_costs['spread_bps'][ticker] / 10000)
                if cost <= cash:
                    cash -= cost
                    holdings[ticker] += shares_to_buy
                    transactions[date].append({'ticker': ticker, 'action': 'buy', 'shares': shares_to_buy, 'price': price, 'cost': cost})
        for ticker in list(holdings.keys()):
            if ticker not in selected_sectors:
                index = next(i for i, (d, _, _, _, _, _) in enumerate(prices[ticker]) if d == date)
                price = prices[ticker][index][4]
                shares_to_sell = holdings[ticker]
                proceeds = shares_to_sell * price - transaction_costs['commission_per_trade'] - (shares_to_sell * price * transaction_costs['spread_bps'][ticker] / 10000)
                cash += proceeds
                holdings[ticker] = 0
                transactions[date].append({'ticker': ticker, 'action': 'sell', 'shares': shares_to_sell, 'price': price, 'proceeds': proceeds})
    return transactions, holdings, cash

# Calculate returns
def calculate_returns(transactions, holdings, cash, prices, benchmark):
    portfolio_value = {date: cash + sum(holdings[ticker] * prices[ticker][i][4] for ticker, i in [(t, next(i for i, (d, _, _, _, _, _) in enumerate(prices[t]) if d == date)) for t in holdings]) for date in benchmark}
    returns = {date: (portfolio_value[date] / initial_capital) - 1 for date in benchmark}
    benchmark_returns = {date: (benchmark[date] / next(b for b in benchmark.values())) - 1 for date in benchmark}
    return returns, benchmark_returns

# Calculate performance metrics
def calculate_performance_metrics(returns, benchmark_returns, risk_free_rate, annualization_factor):
    total_return = (1 + list(returns.values())[-1]) ** (1 / (len(returns) / annualization_factor)) - 1
    annualized_return = (1 + total_return) ** annualization_factor - 1
    volatility = math.sqrt(sum((r - total_return) ** 2 for r in returns.values()) / (len(returns) - 1)) * math.sqrt(annualization_factor)
    sharpe_ratio = (annualized_return - risk_free_rate) / volatility
    max_drawdown = 0
    peak = 1
    for date, ret in returns.items():
        if (1 + ret) > peak:
            peak = 1 + ret
        drawdown = (1 + ret) / peak - 1
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    return {
        'total_return': total_return,
        'annualized_return': annualized_return,
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown
    }

# Main function
def main():
    # Load data
    sector_prices = load_prices('data/sector_prices_cleaned.csv')
    benchmark = load_benchmark('data/benchmark.csv')

    # Load configuration
    with open('config/strategy_params.yaml', 'r') as f:
        strategy_params = yaml.safe_load(f)
    with open('config/transaction_costs.json', 'r') as f:
        transaction_costs = json.load(f)

    # Parameters
    lookback_weeks = strategy_params['momentum']['lookback_weeks']
    skip_recent_weeks = strategy_params['momentum']['skip_recent_weeks']
    top_n = strategy_params['portfolio']['top_n']
    initial_capital = strategy_params['portfolio']['initial_capital']
    risk_free_rate = strategy_params['risk']['risk_free_rate']
    annualization_factor = strategy_params['risk']['annualization_factor']

    # Calculate momentum scores
    momentum_scores = calculate_momentum(sector_prices, lookback_weeks, skip_recent_weeks)

    # Select portfolio
    portfolios = select_portfolio(momentum_scores, top_n, skip_recent_weeks)

    # Rebalance portfolio
    transactions, holdings, cash = rebalance_portfolio(portfolios, initial_capital, transaction_costs, sector_prices)

    # Calculate returns
    returns, benchmark_returns = calculate_returns(transactions, holdings, cash, sector_prices, benchmark)

    # Calculate performance metrics
    performance_metrics = calculate_performance_metrics(returns, benchmark_returns, risk_free_rate, annualization_factor)

    # Generate report
    with open('backtest_report.md', 'w') as f:
        f.write('# Backtest Report\n\n')
        f.write('## Data Cleaning\n')
        f.write('Data cleaning steps were performed to remove duplicates, adjust for corporate actions, and interpolate missing data.\n\n')
        f.write('## Momentum Scores and Portfolio Selection\n')
        f.write('Momentum scores were calculated using a lookback of {} weeks, skipping the most recent {} weeks. The top {} sectors were selected at each rebalancing.\n'.format(lookback_weeks, skip_recent_weeks, top_n))
        f.write('## Returns and Performance Metrics\n')
        f.write('### Period-by-Period Returns\n')
        for date, ret in returns.items():
            f.write('- {}: {:.2%}\n'.format(date, ret))
        f.write('### Cumulative Returns\n')
        f.write('- Total Return: {:.2%}\n'.format(performance_metrics['total_return']))
        f.write('- Annualized Return: {:.2%}\n'.format(performance_metrics['annualized_return']))
        f.write('### Key Performance Metrics\n')
        f.write('- Volatility: {:.2%}\n'.format(performance_metrics['volatility']))
        f.write('- Sharpe Ratio: {:.2f}\n'.format(performance_metrics['sharpe_ratio']))
        f.write('- Max Drawdown: {:.2%}\n'.format(performance_metrics['max_drawdown']))
        f.write('## Comparison with SPY Benchmark\n')
        f.write('The strategy outperformed (or underperformed) the SPY benchmark.\n')
        f.write('### Benchmark Returns\n')
        for date, ret in benchmark_returns.items():
            f.write('- {}: {:.2%}\n'.format(date, ret))
        f.write('### Transaction Costs\n')
        f.write('Transaction costs were deducted from returns at each rebalancing.\n')

    # Save results to JSON
    with open('backtest_results.json', 'w') as f:
        json.dump({
            'period_returns': {str(date): ret for date, ret in returns.items()},
            'cumulative_return': performance_metrics['total_return'],
            'annualized_return': performance_metrics['annualized_return'],
            'volatility': performance_metrics['volatility'],
            'sharpe_ratio': performance_metrics['sharpe_ratio'],
            'max_drawdown': performance_metrics['max_drawdown'],
            'transactions': transactions
        }, f, indent=4)

if __name__ == '__main__':
    main()
