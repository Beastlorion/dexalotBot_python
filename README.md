# Dexalot Bot - Python

This is an open source market maker bot for automated trading on Dexalot, a decentralized CLOB (Central Limit Order Book) exchange.

## Features

- ✅ **Automated Market Making**: Places and manages buy/sell orders with configurable spreads
- ✅ **Multi-Network Support**: Works on both mainnet and Fuji testnet
- ✅ **Multiple Price Feeds**: Supports Binance, Bybit, and custom price sources
- ✅ **Robust Error Handling**: Automatic reconnection and graceful shutdown
- ✅ **Configurable Strategy**: Flexible settings for different trading pairs
- ✅ **Analytics Mode**: Built-in analytics and monitoring capabilities

## Installation

### Prerequisites

If this is your first time using Python, set up your build environment:

```bash
sudo apt-get update
sudo apt-get install build-essential python3-pip python3-venv
```

### Setup

1. **Clone the repository**:
```bash
git clone https://github.com/Beastlorion/dexalotBot_python.git
cd dexalotBot_python
```

2. **Create and activate virtual environment**:
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. **Install dependencies**:
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**:
```bash
cp .env.shared.example .env.shared  # if example exists
vi .env.secret
```

Add your private key:
```bash
export AVAX_USDC_pk="0xYOUR_PRIVATE_KEY_HERE"
```

## Configuration

The bot uses a layered configuration system:

- **`.env.shared`**: Shared configuration (API URLs, general settings)
- **`.env.secret`**: Private keys and sensitive data (never commit this!)
- **`settings.py`**: Trading pair specific settings (spreads, quantities, etc.)

### Trading Pair Settings

Each trading pair in `settings.py` can be configured with:

- `levels`: Array of order levels with spread and quantity
- `refreshTolerance`: Price movement threshold to trigger order updates
- `takerEnabled`: Enable taker orders for arbitrage opportunities
- `useBybitPrice`: Use Bybit as price source instead of Binance
- `priceAdjust`: Percentage adjustment to market price

## Usage

### Basic Market Making

```bash
# Activate virtual environment
source .venv/bin/activate

# Run on mainnet
python3 main.py AVAX_USDC

# Run on testnet
python3 main.py AVAX_USDC fuji
```

### Analytics Mode

```bash
python3 main.py AVAX_USDC m analytics
```

### Available Trading Pairs

- AVAX_USDC, AVAX_USDT
- BTC_USDC, ETH_USDC, ETH_USDT
- WBTC_USDC, WBTC_ETH
- ARB_USDC, GMX_USDC
- USDT_USDC (stablecoin pair)
- sAVAX_AVAX (liquid staking)

## Architecture

The refactored codebase follows a modular architecture:

```
├── main.py              # Entry point with improved shutdown handling
├── config.py            # Centralized configuration management
├── marketMaker.py       # Core market making logic (class-based)
├── contracts.py         # Blockchain interaction and Web3 providers
├── price_feeds.py       # Multi-source price feed management
├── orders.py            # Order management and execution
├── settings.py          # Trading pair configurations
├── tools.py             # Utility functions
├── analytics.py         # Analytics and monitoring
└── requirements.txt     # Python dependencies
```

## Key Improvements

### 🔄 Better Shutdown Handling
- **Graceful shutdown** with order cancellation
- **Automatic restart** unless interrupted by user (Ctrl+C)
- **Proper signal handling** for clean exits
- **KeyboardInterrupt support** - Ctrl+C triggers graceful shutdown
- **Consecutive failure protection** - stops after 3 failures to prevent infinite loops

### 🏗️ Improved Architecture
- Class-based design for better organization
- Centralized configuration management
- Proper logging throughout the application
- Type hints for better code clarity

### 🛡️ Enhanced Error Handling
- Retry logic with exponential backoff
- Connection recovery for price feeds
- Detailed error logging and monitoring
- **Smart failure counting** - resets on success, stops after 3 consecutive failures

### ⚡ Performance Optimizations
- Reduced sleep times in critical paths
- Better async task management
- Optimized provider initialization

## Workflow

1. **Initialization**: Sets up blockchain providers and contracts
2. **Price Feeds**: Starts real-time price feeds from configured sources
3. **Order Management**: Places initial orders based on current market price
4. **Monitoring Loop**: Continuously monitors price movements and updates orders
5. **Risk Management**: Handles failed transactions and connection issues
6. **Graceful Shutdown**: Cancels all orders before stopping

## Code Analysis

Use the included cleanup analyzer to identify potential improvements:

```bash
python3 cleanup_analyzer.py
```

This will analyze the codebase for:
- Unused imports and variables
- Commented-out code
- Long functions that could be refactored
- General code quality issues

## Security Notes

- **Never commit private keys** to version control
- Use environment variables for all sensitive data
- Consider using hardware wallets for production trading
- Monitor your positions and set appropriate risk limits

## Disclaimer

This is an open source project provided as-is. The authors take no responsibility for any financial losses incurred by using this code. Always test thoroughly on testnet before using real funds, and never risk more than you can afford to lose.

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes with proper tests
4. Submit a pull request

## License

Open source - see LICENSE file for details.
