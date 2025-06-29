# Inventory Management Guide

## Overview
The V2 refactoring now includes the defensive/offensive skew functionality from the original `tools.py`, but with an improved, modular architecture through the `InventoryManager` class.

## How It Works

### Original Implementation (tools.py)
The original `getSpread()` function calculated spread adjustments based on:
```python
# Defensive skew: Widen spread when low on funds
if funds < total_funds / 2:
    multiple = ((funds / total_funds) - 0.5) * 20 * -1
    defensive_skew = multiple * settings.get("defensiveSkew", 0)

# Offensive skew: Tighten spread when high on funds    
if funds > total_funds / 2:
    multiple = ((funds / total_funds) - 0.5) * 20 * -1
    offensive_skew = multiple * settings["offensiveSkew"]
```

### Enhanced V2 Implementation (inventory_manager.py)
The new `InventoryManager` provides the same functionality with additional features:

1. **Inventory Status Tracking**
   - Real-time portfolio balance monitoring
   - Health score calculation (0-100)
   - State detection (balanced, long base, long quote, critical)

2. **Dynamic Spread Adjustment**
   - Implements the same skew logic as original
   - Better organized with clear separation of concerns
   - Detailed logging of adjustment reasons

3. **Automatic Rebalancing Suggestions**
   - Detects critical imbalances
   - Suggests rebalancing trades
   - Configurable thresholds

## Configuration

### In settings.py
```python
'AVAX_USDC': {
    "defensiveSkew": 0.01,    # 1% - Widen spread when low on funds
    "offensiveSkew": 0.005,   # 0.5% - Tighten spread when high on funds (optional)
    # ... other settings
}
```

### How Skew Values Work

#### Defensive Skew (Always Active)
- **Purpose**: Protect remaining funds when inventory is imbalanced
- **When Applied**:
  - Buy orders: When you're low on quote currency (high base ratio)
  - Sell orders: When you're low on base currency (low base ratio)
- **Effect**: Widens spread to reduce chance of execution
- **Formula**: `skew = multiplier * defensiveSkew`
  - multiplier ranges from 0 to 10 based on imbalance

#### Offensive Skew (Optional)
- **Purpose**: Capture more spread when well-funded
- **When Applied**:
  - Buy orders: When you have excess quote currency
  - Sell orders: When you have excess base currency
- **Effect**: Tightens spread to increase execution chance
- **Formula**: `skew = multiplier * offensiveSkew`

### Example Scenarios

#### Scenario 1: Balanced Portfolio (50/50)
```
Base: 100 AVAX @ $18 = $1800
Quote: $1800 USDC
Ratio: 50%

Buy spread adjustment: 0% (no skew)
Sell spread adjustment: 0% (no skew)
```

#### Scenario 2: Long Base (75% AVAX, 25% USDC)
```
Base: 150 AVAX @ $18 = $2700
Quote: $900 USDC
Ratio: 75%

Buy spread adjustment: +0.5% (defensive - low on USDC)
Sell spread adjustment: -0.25% (offensive - high on AVAX)
```

#### Scenario 3: Long Quote (25% AVAX, 75% USDC)
```
Base: 50 AVAX @ $18 = $900
Quote: $2700 USDC
Ratio: 25%

Buy spread adjustment: -0.25% (offensive - high on USDC)
Sell spread adjustment: +0.5% (defensive - low on AVAX)
```

## Integration with Market Maker

The inventory manager is automatically initialized in `main_v2.py`:

```python
# Reads defensive/offensive skew from settings
self.inventory_manager = InventoryManager(
    target_ratio=0.5,
    defensive_skew=market_settings.get('defensiveSkew', 0.01),
    offensive_skew=market_settings.get('offensiveSkew', None)
)
```

To use in your market making logic:

```python
# Calculate inventory status
inventory_status = inventory_manager.calculate_inventory_status(
    base_balance=base_balance,
    quote_balance=quote_balance,
    current_price=market_price
)

# Get adjusted spread for orders
spread, reason = inventory_manager.calculate_spread_adjustment(
    side=0,  # 0 for buy, 1 for sell
    inventory_status=inventory_status,
    base_spread=level['spread'] / 100,
    vol_spread=volatility_spread
)
```

## Monitoring

The inventory manager provides detailed logging:

```
INFO - Inventory Status - Base: 150.0000 (75.0%), Quote: 900.00 (25.0%), Health: 50/100, State: long_base
INFO - Buy spread: 0.0150 (1.50%) - base: 0.0100 + defensive: 0.0050 = 0.0150
INFO - Sell spread: 0.0075 (0.75%) - base: 0.0100 + offensive: -0.0025 = 0.0075
```

## Health Metrics

The inventory manager tracks portfolio health:

```python
health = inventory_manager.get_inventory_health(status)
# Returns:
{
    'health_score': 75,              # 0-100 score
    'state': 'long_base',            # Current state
    'inventory_ratio': 0.75,         # Base value ratio
    'deviation_from_target': 0.25,   # Distance from 50/50
    'base_value_pct': 75.0,         # Base percentage
    'quote_value_pct': 25.0,        # Quote percentage
    'needs_rebalance': False,        # Critical imbalance?
    'skew_multiplier': 5.0          # Current multiplier
}
```

## Benefits Over Original Implementation

1. **Better Organization**: Inventory logic separated from spread calculation
2. **Enhanced Monitoring**: Track portfolio health and history
3. **Automatic Rebalancing**: Suggests trades to maintain balance
4. **Detailed Logging**: Understand why spreads are adjusted
5. **Configurable Thresholds**: Customize when rebalancing triggers
6. **Type Safety**: Proper type hints and data structures

## Backward Compatibility

The new system maintains the exact same skew calculation logic as the original `tools.getSpread()` function, ensuring identical behavior while providing additional features and better structure.

## Recommended Settings

### Conservative (Lower Risk)
```python
"defensiveSkew": 0.02,    # 2% - Stronger protection
"offensiveSkew": None,    # Disabled - Don't chase trades
```

### Balanced
```python
"defensiveSkew": 0.01,    # 1% - Moderate protection
"offensiveSkew": 0.005,   # 0.5% - Slight aggression when funded
```

### Aggressive (Higher Risk)
```python
"defensiveSkew": 0.005,   # 0.5% - Minimal protection
"offensiveSkew": 0.01,    # 1% - Aggressive when funded
```

The inventory manager ensures your market maker maintains a balanced portfolio while maximizing spread capture opportunities based on current inventory levels.