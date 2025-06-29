# Safety Bounds Configuration Guide

## Overview
Safety bounds protect your market maker from placing orders at dangerous prices. They are configured in the `settings.py` file under the `SAFETY_BOUNDS` section.

## Configuration Location
All safety bounds are defined in `settings.py`:

```python
settings = {
    'SAFETY_BOUNDS': {
        'AVAX_USDC': {
            'min_price': 17,         # Minimum allowed price
            'max_price': 19,         # Maximum allowed price
            'max_spread': 0.0005,    # Maximum spread (0.05%)
            'max_deviation': 0.001,  # Maximum price deviation (0.1%)
            'stale_timeout': 30      # Price staleness timeout (seconds)
        }
    },
    # ... rest of settings
}
```

## Parameters Explained

### `min_price` and `max_price`
- **Purpose**: Absolute price bounds for the trading pair
- **Function**: Orders outside this range will be rejected
- **Example**: For AVAX at $18, bounds of [17, 19] allow ±$1 movement

### `max_spread`
- **Purpose**: Maximum allowed spread between order price and market price
- **Function**: Prevents placing orders too far from current market
- **Example**: 0.0005 = 0.05% maximum spread
- **Note**: This is different from the `spread` in order levels

### `max_deviation`
- **Purpose**: Maximum allowed price change between updates
- **Function**: Triggers circuit breaker if price moves too fast
- **Example**: 0.001 = 0.1% maximum price change per update

### `stale_timeout` (optional)
- **Purpose**: Maximum age for price data in seconds
- **Function**: Rejects prices older than this threshold
- **Default**: 30 seconds if not specified

## Current AVAX_USDC Configuration Analysis

Your current settings:
```python
'AVAX_USDC': {
    'min_price': 17,
    'max_price': 19,
    'max_spread': 0.0005,   # 0.05%
    'max_deviation': 0.001  # 0.1%
}
```

**⚠️ WARNING**: These bounds are VERY tight:
- Price range: $17-$19 (only ~11% range)
- Max spread: 0.05% (very restrictive)
- Max deviation: 0.1% (will trigger often in volatile markets)

## Recommended Configurations

### Conservative (Safe for Testing)
```python
'AVAX_USDC': {
    'min_price': 10,        # ~50% below current price
    'max_price': 30,        # ~50% above current price
    'max_spread': 0.01,     # 1% maximum spread
    'max_deviation': 0.05,  # 5% price movement allowed
    'stale_timeout': 30
}
```

### Normal Operations
```python
'AVAX_USDC': {
    'min_price': 5,         # Wider range for volatility
    'max_price': 50,
    'max_spread': 0.02,     # 2% maximum spread
    'max_deviation': 0.10,  # 10% price movement allowed
    'stale_timeout': 30
}
```

### Volatile Markets
```python
'AVAX_USDC': {
    'min_price': 1,
    'max_price': 100,
    'max_spread': 0.05,     # 5% maximum spread
    'max_deviation': 0.15,  # 15% price movement allowed
    'stale_timeout': 60     # Longer timeout for unstable feeds
}
```

## Adding Bounds for Other Pairs

Add new trading pairs to the `SAFETY_BOUNDS` section:

```python
'SAFETY_BOUNDS': {
    'AVAX_USDC': {
        'min_price': 10,
        'max_price': 30,
        'max_spread': 0.01,
        'max_deviation': 0.05
    },
    'BTC_USDC': {
        'min_price': 20000,
        'max_price': 100000,
        'max_spread': 0.005,    # Tighter spread for BTC
        'max_deviation': 0.03   # Less deviation allowed
    },
    'ETH_USDC': {
        'min_price': 1000,
        'max_price': 5000,
        'max_spread': 0.01,
        'max_deviation': 0.05
    }
}
```

## How Safety Bounds Work

1. **Price Validation**: Before placing any order, the system checks:
   - Is the market price within [min_price, max_price]?
   - Is the order price within max_spread of market price?
   - Has the price moved more than max_deviation since last update?

2. **Circuit Breaker**: If price moves too fast (exceeds max_deviation):
   - Trading halts temporarily
   - System waits for cooldown period
   - Prevents trading during flash crashes or price manipulation

3. **Stale Price Protection**: If price data is older than stale_timeout:
   - Orders are not placed
   - System waits for fresh price data
   - Prevents trading on outdated information

## Monitoring and Alerts

When bounds are violated, you'll see log messages like:
```
WARNING - Price 25.5 above maximum 19
WARNING - Buy order spread 2.1% exceeds maximum 0.05%
WARNING - Price for AVAX_USDC changed 5.2% in one update
ERROR - Circuit breaker triggered: price_deviation
```

## Testing Your Configuration

1. **Start with tight bounds** to test the safety system
2. **Monitor logs** for bound violations
3. **Gradually widen bounds** based on market conditions
4. **Review circuit breaker triggers** to fine-tune deviation limits

## Default Fallbacks

If no bounds are configured for a pair, the system uses defaults:
- Most pairs: min=0.001, max=1000000, spread=10%, deviation=25%
- BTC pairs: min=1000, max=200000, spread=3%, deviation=15%
- ETH pairs: min=100, max=20000, spread=3%, deviation=15%

## Best Practices

1. **Set realistic bounds** based on historical price ranges
2. **Consider volatility** when setting deviation limits
3. **Monitor circuit breaker** triggers and adjust if too frequent
4. **Update bounds** periodically as market conditions change
5. **Test thoroughly** before using in production

Remember: Safety bounds are your protection against extreme market conditions and potential bugs. It's better to miss some trades than to place orders at dangerous prices.