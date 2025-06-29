#!/usr/bin/env python3
"""
Safety module for DexalotBot - Prevents dangerous trading conditions.
Implements price validation, circuit breakers, and risk management.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Tuple
from decimal import Decimal
from enum import Enum

logger = logging.getLogger(__name__)


class TradingHaltReason(Enum):
    """Reasons for halting trading"""
    NONE = "none"
    PRICE_DEVIATION = "price_deviation"
    STALE_PRICE = "stale_price"
    EXTREME_SPREAD = "extreme_spread"
    CONSECUTIVE_ERRORS = "consecutive_errors"
    MANUAL_HALT = "manual_halt"
    CIRCUIT_BREAKER = "circuit_breaker"


@dataclass
class PriceBounds:
    """Defines acceptable price ranges for a trading pair"""
    min_price: float
    max_price: float
    max_spread_percent: float = 0.05  # 5% default
    max_price_deviation_percent: float = 0.20  # 20% from reference
    stale_price_timeout: int = 30  # seconds
    
    def __post_init__(self):
        """Validate bounds on creation"""
        if self.min_price <= 0:
            raise ValueError("min_price must be positive")
        if self.max_price <= self.min_price:
            raise ValueError("max_price must be greater than min_price")
        if self.max_spread_percent <= 0 or self.max_spread_percent > 1:
            raise ValueError("max_spread_percent must be between 0 and 1")


@dataclass
class PriceData:
    """Timestamped price data"""
    price: float
    timestamp: float
    source: str
    
    @property
    def age(self) -> float:
        """Age of price data in seconds"""
        return time.time() - self.timestamp
    
    def is_stale(self, max_age: int) -> bool:
        """Check if price is too old"""
        return self.age > max_age


class PriceSafetyValidator:
    """Validates all prices before order placement"""
    
    def __init__(self, default_bounds: Optional[PriceBounds] = None):
        self.default_bounds = default_bounds or PriceBounds(
            min_price=0.00001,
            max_price=1000000,
            max_spread_percent=0.05
        )
        self.reference_prices: Dict[str, PriceData] = {}
        
    def update_reference_price(self, symbol: str, price: float, source: str = "market"):
        """Update reference price for deviation checks"""
        self.reference_prices[symbol] = PriceData(
            price=price,
            timestamp=time.time(),
            source=source
        )
        
    def validate_market_price(
        self, 
        symbol: str,
        price: float, 
        bounds: Optional[PriceBounds] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if market price is within acceptable bounds.
        Returns (is_valid, error_message)
        """
        bounds = bounds or self.default_bounds
        
        # Check absolute bounds
        if price < bounds.min_price:
            return False, f"Price {price} below minimum {bounds.min_price}"
        if price > bounds.max_price:
            return False, f"Price {price} above maximum {bounds.max_price}"
            
        # Check deviation from reference if available
        if symbol in self.reference_prices:
            ref_data = self.reference_prices[symbol]
            if not ref_data.is_stale(bounds.stale_price_timeout):
                deviation = abs(price - ref_data.price) / ref_data.price
                if deviation > bounds.max_price_deviation_percent:
                    return False, f"Price {price} deviates {deviation:.1%} from reference {ref_data.price}"
                    
        return True, None
    
    def validate_order_price(
        self,
        symbol: str,
        side: str,  # "buy" or "sell"
        order_price: float,
        market_price: float,
        max_spread_percent: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate order price relative to market price.
        Returns (is_valid, error_message)
        """
        max_spread = max_spread_percent or self.default_bounds.max_spread_percent
        
        if side.lower() == "buy":
            # Buy order should not be too far below market
            if order_price > market_price:
                return False, f"Buy order price {order_price} above market {market_price}"
            min_allowed = market_price * (1 - max_spread)
            if order_price < min_allowed:
                spread = (market_price - order_price) / market_price
                return False, f"Buy order spread {spread:.1%} exceeds maximum {max_spread:.1%}"
                
        elif side.lower() == "sell":
            # Sell order should not be too far above market
            if order_price < market_price:
                return False, f"Sell order price {order_price} below market {market_price}"
            max_allowed = market_price * (1 + max_spread)
            if order_price > max_allowed:
                spread = (order_price - market_price) / market_price
                return False, f"Sell order spread {spread:.1%} exceeds maximum {max_spread:.1%}"
                
        else:
            return False, f"Invalid side: {side}"
            
        return True, None
    
    def validate_spread(
        self, 
        bid_price: float, 
        ask_price: float,
        max_spread_percent: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Ensure spread between bid and ask is not too wide.
        Returns (is_valid, error_message)
        """
        max_spread = max_spread_percent or self.default_bounds.max_spread_percent
        
        if bid_price >= ask_price:
            return False, f"Invalid spread: bid {bid_price} >= ask {ask_price}"
            
        spread = (ask_price - bid_price) / ask_price
        if spread > max_spread:
            return False, f"Spread {spread:.1%} exceeds maximum {max_spread:.1%}"
            
        return True, None
    
    def check_price_staleness(
        self, 
        symbol: str,
        last_update: float,
        max_age: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if price data is fresh.
        Returns (is_fresh, error_message)
        """
        max_age = max_age or self.default_bounds.stale_price_timeout
        age = time.time() - last_update
        
        if age > max_age:
            return False, f"Price for {symbol} is {age:.0f}s old (max: {max_age}s)"
            
        return True, None


class CircuitBreaker:
    """Halts trading during extreme conditions"""
    
    def __init__(
        self,
        max_price_change_percent: float = 0.10,  # 10% in one update
        max_consecutive_errors: int = 5,
        cooldown_seconds: int = 60
    ):
        self.max_price_change_percent = max_price_change_percent
        self.max_consecutive_errors = max_consecutive_errors
        self.cooldown_seconds = cooldown_seconds
        
        self.consecutive_errors = 0
        self.last_prices: Dict[str, float] = {}
        self.halt_reason = TradingHaltReason.NONE
        self.halt_time: Optional[float] = None
        
    def record_error(self) -> bool:
        """
        Record an error and check if circuit breaker should trip.
        Returns True if breaker trips.
        """
        self.consecutive_errors += 1
        
        if self.consecutive_errors >= self.max_consecutive_errors:
            self.trip(TradingHaltReason.CONSECUTIVE_ERRORS)
            return True
            
        return False
    
    def record_success(self):
        """Record successful operation, resetting error count"""
        self.consecutive_errors = 0
        
    def check_price_movement(
        self, 
        symbol: str,
        new_price: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Detect extreme price movements.
        Returns (is_ok, error_message)
        """
        if symbol in self.last_prices:
            old_price = self.last_prices[symbol]
            if old_price > 0:
                change = abs(new_price - old_price) / old_price
                if change > self.max_price_change_percent:
                    msg = f"Price for {symbol} changed {change:.1%} in one update"
                    logger.warning(msg)
                    self.trip(TradingHaltReason.PRICE_DEVIATION)
                    return False, msg
                    
        self.last_prices[symbol] = new_price
        return True, None
    
    def trip(self, reason: TradingHaltReason):
        """Trip the circuit breaker"""
        self.halt_reason = reason
        self.halt_time = time.time()
        logger.warning(f"Circuit breaker tripped: {reason.value}")
        
    def reset(self):
        """Manually reset the circuit breaker"""
        self.halt_reason = TradingHaltReason.NONE
        self.halt_time = None
        self.consecutive_errors = 0
        logger.info("Circuit breaker reset")
        
    def is_tripped(self) -> bool:
        """Check if circuit breaker is currently active"""
        if self.halt_reason == TradingHaltReason.NONE:
            return False
            
        # Check if cooldown period has passed
        if self.halt_time and time.time() - self.halt_time > self.cooldown_seconds:
            self.reset()
            return False
            
        return True
    
    def get_status(self) -> Dict[str, any]:
        """Get current circuit breaker status"""
        status = {
            "tripped": self.is_tripped(),
            "reason": self.halt_reason.value,
            "consecutive_errors": self.consecutive_errors
        }
        
        if self.halt_time:
            remaining = max(0, self.cooldown_seconds - (time.time() - self.halt_time))
            status["cooldown_remaining"] = round(remaining)
            
        return status


class PositionLimits:
    """Manages position size limits and exposure"""
    
    def __init__(
        self,
        max_position_value: float,
        max_order_size: float,
        max_open_orders: int = 10
    ):
        self.max_position_value = max_position_value
        self.max_order_size = max_order_size
        self.max_open_orders = max_open_orders
        
        self.open_orders: Dict[str, float] = {}
        self.position_value = 0.0
        
    def can_place_order(
        self, 
        order_id: str,
        size: float,
        price: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if order can be placed within limits.
        Returns (can_place, error_message)
        """
        # Check order size
        if size > self.max_order_size:
            return False, f"Order size {size} exceeds maximum {self.max_order_size}"
            
        # Check number of open orders
        if len(self.open_orders) >= self.max_open_orders:
            return False, f"Maximum open orders ({self.max_open_orders}) reached"
            
        # Check position value
        order_value = size * price
        new_position_value = self.position_value + order_value
        if new_position_value > self.max_position_value:
            return False, f"Position value {new_position_value} would exceed maximum {self.max_position_value}"
            
        return True, None
    
    def add_order(self, order_id: str, size: float, price: float):
        """Record a new order"""
        order_value = size * price
        self.open_orders[order_id] = order_value
        self.position_value += order_value
        
    def remove_order(self, order_id: str):
        """Remove an order from tracking"""
        if order_id in self.open_orders:
            self.position_value -= self.open_orders[order_id]
            del self.open_orders[order_id]
            
    def get_status(self) -> Dict[str, any]:
        """Get current position status"""
        return {
            "open_orders": len(self.open_orders),
            "position_value": self.position_value,
            "max_position_value": self.max_position_value,
            "utilization": self.position_value / self.max_position_value if self.max_position_value > 0 else 0
        }


# Example usage and testing
if __name__ == "__main__":
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create validators
    bounds = PriceBounds(
        min_price=0.01,
        max_price=100000,
        max_spread_percent=0.02,  # 2%
        max_price_deviation_percent=0.15  # 15%
    )
    
    validator = PriceSafetyValidator(bounds)
    breaker = CircuitBreaker()
    
    # Test price validation
    print("\n=== Price Validation Tests ===")
    
    # Test market price validation
    validator.update_reference_price("BTC", 50000)
    
    test_prices = [45000, 50000, 55000, 65000, 0.001, 1000000]
    for price in test_prices:
        valid, error = validator.validate_market_price("BTC", price, bounds)
        print(f"Price {price}: {'Valid' if valid else f'Invalid - {error}'}")
    
    # Test order price validation
    print("\n=== Order Validation Tests ===")
    market_price = 50000
    
    # Buy orders
    for spread in [0.01, 0.02, 0.05, 0.10]:
        order_price = market_price * (1 - spread)
        valid, error = validator.validate_order_price("BTC", "buy", order_price, market_price, 0.03)
        print(f"Buy at {order_price} (spread {spread:.1%}): {'Valid' if valid else f'Invalid - {error}'}")
    
    # Test circuit breaker
    print("\n=== Circuit Breaker Tests ===")
    
    # Simulate errors
    for i in range(6):
        tripped = breaker.record_error()
        status = breaker.get_status()
        print(f"Error {i+1}: Breaker {'TRIPPED' if tripped else 'OK'} - Status: {status}")
    
    # Test price movement detection
    breaker.reset()
    prices = [50000, 51000, 52000, 60000]  # Last one is > 10% jump
    for price in prices:
        ok, error = breaker.check_price_movement("BTC", price)
        print(f"Price change to {price}: {'OK' if ok else f'HALTED - {error}'}")