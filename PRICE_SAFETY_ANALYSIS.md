# Price Safety Analysis - dexalotBot_python

## Overview
This analysis examines the price safety mechanisms in the dexalotBot_python market maker bot, identifying potential vulnerabilities where orders might be placed at dangerous prices.

## Key Findings

### 1. Price Feed Management

#### Sources
- **Binance**: Primary price source via WebSocket streams
- **Bybit**: Alternative price source for certain pairs
- **Custom Price Server**: Local server at `http://localhost:3000/prices`
- **sAVAX**: Direct on-chain price from proxy contract

#### Critical Issues Found:

1. **No Price Bounds Checking**
   - The bot does not validate that received prices are within reasonable bounds
   - No checks for extreme price movements (e.g., 90% drops or 10x increases)
   - Could accept prices of 0 or extremely high values

2. **Stale Price Data Handling**
   - Uses `timeout` parameter (default 30s, configurable per pair)
   - However, if price feeds disconnect, `lastUpdate` timestamps are not reset
   - The bot continues using the last known price indefinitely after timeout

3. **Price Feed Disconnection**
   - WebSocket connections can fail silently
   - No automatic reconnection logic in price feeds
   - Error handling catches exceptions but doesn't alert or stop the bot

### 2. Order Pricing Logic

#### Spread Calculation (`tools.getSpread()`)
```python
spread = defensive_skew/100 + offensive_skew/100 + level_spread + vol_spread/2
```

Components:
- **defensive_skew**: Increases spread when low on funds (max ~10% with default 0.01 setting)
- **offensive_skew**: Reduces spread when high on funds (optional)
- **level_spread**: Base spread from configuration (0.07% - 1.5% typical)
- **vol_spread**: External volatility component (uncapped)

#### Dangerous Scenarios:

1. **Negative Spread Possible**
   - If `offensive_skew` is set too high, total spread can become negative
   - This would place buy orders above market price or sells below
   - Example: With 0.09 offensive_skew (WBTC_USDC), spread could go negative

2. **No Maximum Spread Cap**
   - Defensive skew can push spreads extremely wide (10%+)
   - Combined with volatility, spreads could exceed 20%
   - No upper bound validation

3. **Price Adjustment Without Validation**
   ```python
   if 'priceAdjust' in self.market_settings:
       market_price *= adjustment
   ```
   - Manual price adjustments are applied without bounds checking
   - Could push prices far from market

### 3. Order Placement Safety

#### Limited Protections:
1. **Best Order Comparison**
   ```python
   if price >= bestAsk and not settings['autoTake']:
       price = bestAsk - increment
   ```
   - Prevents crossing the spread only if `autoTake` is disabled
   - But `bestAsk`/`bestBid` could be stale or manipulated

2. **Min/Max Trade Amounts**
   - Checks against exchange limits but not price reasonableness
   - A $0.01 BTC order would pass if quantity is valid

#### Missing Protections:
1. **No Absolute Price Bounds**
   - No checks like "BTC price must be between $1,000 and $1,000,000"
   - No percentage deviation limits from recent averages

2. **No Order Book Depth Analysis**
   - Places orders based on top of book only
   - Thin order books could have manipulated best prices

3. **No Circuit Breaker**
   - No mechanism to halt trading on extreme price movements
   - No daily loss limits or position limits

### 4. Specific Vulnerabilities

1. **Custom Price Server Manipulation**
   - No authentication on `http://localhost:3000/prices`
   - No validation of received prices
   - Local server could be compromised

2. **Volatility Spread Manipulation**
   - `volSpread` from external source is uncapped
   - Could push spreads to extreme values
   - No validation or sanity checks

3. **Race Conditions**
   - Multiple async price feed updates
   - No locking mechanism
   - Orders could be placed during price updates

4. **Stale Data After Reconnection**
   - When providers reconnect, old price data isn't cleared
   - Bot might trade on hours-old prices

5. **No Heartbeat Mechanism**
   - Price feeds don't have keepalive/heartbeat
   - Silent failures leave bot trading on stale data

## Recommendations

### Critical (Implement Immediately)
1. Add price bounds checking for each asset
2. Implement maximum spread caps
3. Add staleness checks that halt trading
4. Validate all external price data
5. Add circuit breaker for extreme movements

### High Priority
1. Implement price feed heartbeats
2. Add order book depth validation  
3. Create position and loss limits
4. Add authentication to custom price server
5. Implement proper WebSocket reconnection

### Medium Priority
1. Add price smoothing/averaging
2. Implement spread volatility caps
3. Create alert system for anomalies
4. Add redundant price sources
5. Implement gradual position building

## Example Attack Scenarios

1. **Flash Crash Exploitation**
   - Manipulate thin order book to show $1 bestAsk for BTC
   - Bot places buy orders at $0.93 (7% spread)
   - Attacker fills orders and profits

2. **Stale Price Attack**
   - Disconnect price feeds during market crash
   - Bot continues trading at old high prices
   - Sells assets far below market value

3. **Volatility Manipulation**
   - Inject high volSpread value via compromised server
   - Force bot to place orders 50%+ away from market
   - Collect spread as profit

## Conclusion

The bot lacks fundamental price safety mechanisms and could place orders at dangerous prices in multiple scenarios. The most critical issues are:
1. No validation of price data
2. Trading on stale prices
3. No bounds on spreads or prices
4. No circuit breakers or safety limits

These vulnerabilities could lead to significant financial losses, especially during market volatility or targeted attacks.