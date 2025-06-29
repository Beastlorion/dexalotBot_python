# DexalotBot Python Refactoring Plan

## Overview
This document outlines a comprehensive refactoring plan to address critical issues found in the codebase:
1. **KeyboardInterrupt/Shutdown Issues**: Blocking operations that prevent graceful shutdown
2. **Price Safety Vulnerabilities**: No validation allowing orders at dangerous prices
3. **Code Organization**: Inconsistent structure and global state management

## Critical Issues to Fix

### 1. Shutdown/Interrupt Handling Issues
- **Blocking WebSocket recv()**: No timeouts on WebSocket operations
- **Uninterruptible sleeps**: Long sleep operations without shutdown checks
- **No graceful WebSocket closure**: Connections not properly closed
- **Race conditions**: Multiple threads checking global state without synchronization

### 2. Price Safety Vulnerabilities
- **No price bounds validation**: Orders can be placed at any price
- **Stale price trading**: Continues using old prices when feeds disconnect
- **Uncapped spreads**: No maximum spread limits
- **No circuit breakers**: No halt conditions for extreme markets
- **Vulnerable price sources**: Custom price server has no authentication

### 3. Code Organization Issues
- **Global state scattered**: Variables spread across modules
- **Inconsistent error handling**: Mix of patterns and bare excepts
- **Poor type safety**: Limited type hints
- **Mixed naming conventions**: camelCase and snake_case

## Refactoring Structure

### Phase 1: Core Safety Infrastructure

#### 1.1 Create Safety Module (`safety.py`)
```python
from dataclasses import dataclass
from typing import Optional
import asyncio

@dataclass
class PriceBounds:
    min_price: float
    max_price: float
    max_spread_percent: float
    stale_price_timeout: int = 30  # seconds

class PriceSafetyValidator:
    """Validates all prices before order placement"""
    
    def validate_market_price(self, price: float, bounds: PriceBounds) -> bool:
        """Check if market price is within acceptable bounds"""
        
    def validate_spread(self, spread: float, max_spread: float) -> bool:
        """Ensure spread is not too wide"""
        
    def check_price_staleness(self, last_update: float) -> bool:
        """Check if price data is fresh"""

class CircuitBreaker:
    """Halts trading during extreme conditions"""
    
    def check_price_movement(self, old_price: float, new_price: float) -> bool:
        """Detect extreme price movements"""
        
    def check_consecutive_errors(self, error_count: int) -> bool:
        """Stop trading after too many errors"""
```

#### 1.2 Create Shutdown Manager (`shutdown_manager.py`)
```python
import asyncio
from typing import Set, Callable

class ShutdownManager:
    """Centralized shutdown coordination"""
    
    def __init__(self):
        self.shutdown_event = asyncio.Event()
        self.tasks: Set[asyncio.Task] = set()
        self.cleanup_callbacks: List[Callable] = []
        
    async def wait_with_timeout(self, timeout: float) -> bool:
        """Wait for shutdown with timeout - replaces sleep"""
        try:
            await asyncio.wait_for(self.shutdown_event.wait(), timeout)
            return True  # Shutdown requested
        except asyncio.TimeoutError:
            return False  # Continue running
            
    def register_cleanup(self, callback: Callable):
        """Register cleanup functions to run on shutdown"""
        
    async def shutdown(self):
        """Coordinate graceful shutdown of all components"""
```

### Phase 2: Refactored Core Components

#### 2.1 Enhanced Market Maker (`market_maker_v2.py`)
```python
from typing import Optional, Dict, Any
import asyncio
from dataclasses import dataclass

@dataclass
class MarketMakerConfig:
    """Configuration with validation"""
    market_pair: str
    network: str
    price_bounds: PriceBounds
    max_position_size: float
    
class MarketMakerV2:
    """Refactored market maker with safety features"""
    
    def __init__(self, config: MarketMakerConfig, shutdown_manager: ShutdownManager):
        self.config = config
        self.shutdown = shutdown_manager
        self.safety_validator = PriceSafetyValidator()
        self.circuit_breaker = CircuitBreaker()
        
    async def place_order_with_validation(self, side: str, price: float, quantity: float):
        """Place order only after all safety checks pass"""
        
        # Validate price bounds
        if not self.safety_validator.validate_market_price(price, self.config.price_bounds):
            raise ValueError(f"Price {price} outside acceptable bounds")
            
        # Check circuit breakers
        if self.circuit_breaker.is_tripped():
            raise RuntimeError("Circuit breaker active - trading halted")
            
        # Place order...
```

#### 2.2 Safe WebSocket Handler (`websocket_manager.py`)
```python
class WebSocketManager:
    """WebSocket connections with proper timeout and shutdown handling"""
    
    async def recv_with_timeout(self, websocket, timeout: float = 1.0) -> Optional[str]:
        """Receive with timeout and shutdown check"""
        while not self.shutdown.shutdown_event.is_set():
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout)
                return message
            except asyncio.TimeoutError:
                continue  # Check shutdown and try again
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                return None
        return None  # Shutdown requested
```

#### 2.3 Price Feed Manager V2 (`price_feed_v2.py`)
```python
class PriceFeedV2:
    """Enhanced price feeds with validation and staleness detection"""
    
    def __init__(self, shutdown_manager: ShutdownManager):
        self.prices: Dict[str, PriceData] = {}
        self.shutdown = shutdown_manager
        
    async def update_price(self, symbol: str, price: float, source: str):
        """Update price with validation and timestamp"""
        
        # Validate price is reasonable
        if not self._is_price_reasonable(symbol, price):
            logger.warning(f"Rejected unreasonable price: {symbol} @ {price}")
            return
            
        # Update with timestamp
        self.prices[symbol] = PriceData(
            price=price,
            timestamp=time.time(),
            source=source
        )
        
    def get_price(self, symbol: str, max_age: int = 30) -> Optional[float]:
        """Get price only if fresh"""
        if symbol not in self.prices:
            return None
            
        price_data = self.prices[symbol]
        age = time.time() - price_data.timestamp
        
        if age > max_age:
            logger.warning(f"Price for {symbol} is stale ({age}s old)")
            return None
            
        return price_data.price
```

### Phase 3: Configuration and State Management

#### 3.1 Centralized Configuration (`config_manager.py`)
```python
from pydantic import BaseModel, validator

class TradingPairConfig(BaseModel):
    """Validated configuration for each trading pair"""
    
    pair_name: str
    min_spread: float = 0.001  # 0.1%
    max_spread: float = 0.05   # 5%
    max_order_size: float
    price_tolerance: float = 0.10  # 10% from market
    
    @validator('max_spread')
    def validate_spread(cls, v, values):
        if v <= values.get('min_spread', 0):
            raise ValueError('max_spread must be greater than min_spread')
        return v

class GlobalConfig(BaseModel):
    """Global configuration with validation"""
    
    network: str
    max_price_age: int = 30  # seconds
    shutdown_timeout: int = 10
    enable_circuit_breakers: bool = True
```

#### 3.2 State Manager (`state_manager.py`)
```python
from typing import Dict, Any
import threading

class StateManager:
    """Thread-safe state management"""
    
    def __init__(self):
        self._state: Dict[str, Any] = {}
        self._lock = threading.RLock()
        
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)
            
    def set(self, key: str, value: Any):
        with self._lock:
            self._state[key] = value
            
    def update_atomic(self, key: str, updater: Callable[[Any], Any]):
        """Atomic read-modify-write"""
        with self._lock:
            old_value = self._state.get(key)
            new_value = updater(old_value)
            self._state[key] = new_value
            return new_value
```

### Phase 4: Implementation Priority

1. **Immediate (Critical Safety)**:
   - Implement PriceSafetyValidator
   - Add WebSocket timeouts
   - Create ShutdownManager
   - Add price bounds checking

2. **High Priority**:
   - Implement CircuitBreaker
   - Add stale price detection
   - Fix shutdown handling in all loops
   - Add max spread validation

3. **Medium Priority**:
   - Refactor to class-based architecture
   - Implement proper state management
   - Add comprehensive logging
   - Improve error handling

4. **Lower Priority**:
   - Add metrics collection
   - Implement backtesting hooks
   - Add web dashboard
   - Create unit tests

## Migration Strategy

1. **Create new modules alongside existing code**
2. **Implement safety features first (can be added to existing code)**
3. **Gradually migrate functionality to new architecture**
4. **Run both versions in parallel for testing**
5. **Switch over once new version is stable**

## Testing Requirements

- Unit tests for all safety validators
- Integration tests for shutdown scenarios
- Stress tests for extreme market conditions
- Mock price feed tests with bad data
- Circuit breaker trigger tests

This refactoring plan addresses all critical issues while maintaining backward compatibility during the transition.