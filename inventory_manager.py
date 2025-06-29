#!/usr/bin/env python3
"""
Inventory Manager for DexalotBot - Manages portfolio balance through dynamic spread adjustment.
Implements defensive and offensive skew based on inventory levels.
"""

import logging
import time
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class InventoryState(Enum):
    """Current inventory balance state"""
    BALANCED = "balanced"
    LONG_BASE = "long_base"     # Too much base asset
    LONG_QUOTE = "long_quote"   # Too much quote asset
    CRITICAL_LONG_BASE = "critical_long_base"
    CRITICAL_LONG_QUOTE = "critical_long_quote"


@dataclass
class InventoryStatus:
    """Current inventory status and metrics"""
    base_balance: float
    quote_balance: float
    base_value_in_quote: float
    total_value: float
    inventory_ratio: float  # base_value / total_value
    state: InventoryState
    skew_multiplier: float


class InventoryManager:
    """
    Manages inventory balance through spread adjustments.
    Implements defensive and offensive skew from original tools.py.
    """
    
    def __init__(
        self,
        target_ratio: float = 0.5,  # Target 50/50 balance
        defensive_skew: float = 0.01,  # Default 1% defensive skew
        offensive_skew: Optional[float] = None,  # Optional offensive skew
        critical_threshold: float = 0.8  # 80% in one asset is critical
    ):
        self.target_ratio = target_ratio
        self.defensive_skew = defensive_skew
        self.offensive_skew = offensive_skew
        self.critical_threshold = critical_threshold
        
        # Tracking
        self.inventory_history = []
        self.rebalance_trades = 0
        
        logger.info(f"Inventory manager initialized with target ratio: {target_ratio}")
    
    def calculate_inventory_status(
        self,
        base_balance: float,
        quote_balance: float,
        current_price: float
    ) -> InventoryStatus:
        """Calculate current inventory status and imbalance"""
        
        # Calculate values
        base_value_in_quote = base_balance * current_price
        total_value = base_value_in_quote + quote_balance
        
        # Prevent division by zero
        if total_value == 0:
            inventory_ratio = 0.5
        else:
            inventory_ratio = base_value_in_quote / total_value
        
        # Determine state
        if inventory_ratio > self.critical_threshold:
            state = InventoryState.CRITICAL_LONG_BASE
        elif inventory_ratio < (1 - self.critical_threshold):
            state = InventoryState.CRITICAL_LONG_QUOTE
        elif inventory_ratio > 0.6:
            state = InventoryState.LONG_BASE
        elif inventory_ratio < 0.4:
            state = InventoryState.LONG_QUOTE
        else:
            state = InventoryState.BALANCED
        
        # Calculate skew multiplier (matches original logic)
        skew_multiplier = (inventory_ratio - 0.5) * 20
        
        status = InventoryStatus(
            base_balance=base_balance,
            quote_balance=quote_balance,
            base_value_in_quote=base_value_in_quote,
            total_value=total_value,
            inventory_ratio=inventory_ratio,
            state=state,
            skew_multiplier=skew_multiplier
        )
        
        # Track history
        self.inventory_history.append({
            'timestamp': time.time(),
            'ratio': inventory_ratio,
            'state': state.value
        })
        
        return status
    
    def calculate_spread_adjustment(
        self,
        side: int,  # 0 = buy, 1 = sell
        inventory_status: InventoryStatus,
        base_spread: float,
        vol_spread: float = 0
    ) -> Tuple[float, str]:
        """
        Calculate spread adjustment based on inventory.
        Reimplements logic from tools.py getSpread function.
        
        Returns: (adjusted_spread, reason)
        """
        
        # Start with base spread
        total_spread = base_spread
        
        # Calculate defensive skew (when low on funds)
        defensive_adjustment = 0
        offensive_adjustment = 0
        
        if side == 0:  # Buy order
            # Check quote balance for buy orders
            if inventory_status.inventory_ratio > 0.5:
                # Low on quote (too much base), widen buy spread defensively
                multiplier = inventory_status.skew_multiplier
                defensive_adjustment = multiplier * self.defensive_skew
                
        else:  # Sell order (side == 1)
            # Check base balance for sell orders
            if inventory_status.inventory_ratio < 0.5:
                # Low on base (too much quote), widen sell spread defensively
                multiplier = -inventory_status.skew_multiplier
                defensive_adjustment = multiplier * self.defensive_skew
        
        # Calculate offensive skew (when high on funds)
        if self.offensive_skew is not None:
            if side == 0 and inventory_status.inventory_ratio < 0.5:
                # High on quote, can be aggressive with buys
                multiplier = -inventory_status.skew_multiplier
                offensive_adjustment = multiplier * self.offensive_skew
                
            elif side == 1 and inventory_status.inventory_ratio > 0.5:
                # High on base, can be aggressive with sells
                multiplier = inventory_status.skew_multiplier
                offensive_adjustment = multiplier * self.offensive_skew
        
        # Add volatility spread
        total_spread += vol_spread / 2
        
        # Apply adjustments
        total_spread += defensive_adjustment
        total_spread += offensive_adjustment
        
        # Generate reason for logging
        adjustments = []
        if defensive_adjustment != 0:
            adjustments.append(f"defensive: {defensive_adjustment:.4f}")
        if offensive_adjustment != 0:
            adjustments.append(f"offensive: {offensive_adjustment:.4f}")
        if vol_spread > 0:
            adjustments.append(f"volatility: {vol_spread/2:.4f}")
            
        reason = f"base: {base_spread:.4f}"
        if adjustments:
            reason += f" + {' + '.join(adjustments)}"
        reason += f" = {total_spread:.4f}"
        
        return total_spread, reason
    
    def should_rebalance(self, inventory_status: InventoryStatus) -> bool:
        """Determine if inventory needs rebalancing"""
        return inventory_status.state in [
            InventoryState.CRITICAL_LONG_BASE,
            InventoryState.CRITICAL_LONG_QUOTE
        ]
    
    def calculate_rebalance_order(
        self,
        inventory_status: InventoryStatus,
        current_price: float,
        max_order_size: float
    ) -> Optional[Dict[str, any]]:
        """
        Calculate rebalancing order if needed.
        Returns order details or None if no rebalance needed.
        """
        
        if not self.should_rebalance(inventory_status):
            return None
        
        # Calculate target balances
        target_base_value = inventory_status.total_value * self.target_ratio
        target_quote_value = inventory_status.total_value * (1 - self.target_ratio)
        
        current_base_value = inventory_status.base_value_in_quote
        current_quote_value = inventory_status.quote_balance
        
        if inventory_status.state == InventoryState.CRITICAL_LONG_BASE:
            # Need to sell base
            value_to_rebalance = current_base_value - target_base_value
            size = min(value_to_rebalance / current_price, max_order_size)
            
            return {
                'side': 'sell',
                'size': size,
                'reason': f'Rebalancing: {inventory_status.inventory_ratio:.1%} base',
                'urgency': 'high'
            }
            
        elif inventory_status.state == InventoryState.CRITICAL_LONG_QUOTE:
            # Need to buy base
            value_to_rebalance = target_base_value - current_base_value
            size = min(value_to_rebalance / current_price, max_order_size)
            
            return {
                'side': 'buy',
                'size': size,
                'reason': f'Rebalancing: {inventory_status.inventory_ratio:.1%} base',
                'urgency': 'high'
            }
        
        return None
    
    def get_inventory_health(self, inventory_status: InventoryStatus) -> Dict[str, any]:
        """Get inventory health metrics"""
        
        deviation = abs(inventory_status.inventory_ratio - self.target_ratio)
        health_score = max(0, 100 - (deviation * 200))  # 0-100 score
        
        return {
            'health_score': health_score,
            'state': inventory_status.state.value,
            'inventory_ratio': inventory_status.inventory_ratio,
            'deviation_from_target': deviation,
            'base_value_pct': inventory_status.inventory_ratio * 100,
            'quote_value_pct': (1 - inventory_status.inventory_ratio) * 100,
            'needs_rebalance': self.should_rebalance(inventory_status),
            'skew_multiplier': inventory_status.skew_multiplier
        }
    
    def log_inventory_status(self, inventory_status: InventoryStatus):
        """Log current inventory status"""
        health = self.get_inventory_health(inventory_status)
        
        logger.info(
            f"Inventory Status - "
            f"Base: {inventory_status.base_balance:.4f} "
            f"({health['base_value_pct']:.1f}%), "
            f"Quote: {inventory_status.quote_balance:.2f} "
            f"({health['quote_value_pct']:.1f}%), "
            f"Health: {health['health_score']:.0f}/100, "
            f"State: {health['state']}"
        )


# Example integration with enhanced spread calculation
def calculate_order_spread(
    market_price: float,
    side: int,  # 0 = buy, 1 = sell
    level: dict,
    inventory_manager: InventoryManager,
    inventory_status: InventoryStatus,
    vol_spread: float = 0
) -> Tuple[float, str]:
    """
    Enhanced spread calculation using inventory manager.
    Replaces tools.getSpread() with better structure.
    """
    
    # Get base spread from level
    base_spread = level.get('spread', 0) / 100
    
    # Calculate adjusted spread based on inventory
    adjusted_spread, reason = inventory_manager.calculate_spread_adjustment(
        side=side,
        inventory_status=inventory_status,
        base_spread=base_spread,
        vol_spread=vol_spread
    )
    
    return adjusted_spread, reason


# Example usage
if __name__ == "__main__":
    import time
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create inventory manager with settings
    inventory_mgr = InventoryManager(
        target_ratio=0.5,
        defensive_skew=0.01,  # 1% from settings
        offensive_skew=0.005  # 0.5% offensive (optional)
    )
    
    # Simulate inventory scenarios
    scenarios = [
        # (base_balance, quote_balance, current_price, description)
        (100, 1800, 18, "Balanced inventory"),
        (150, 900, 18, "Long base (need to sell)"),
        (50, 2700, 18, "Long quote (need to buy)"),
        (180, 360, 18, "Critical long base"),
        (20, 3240, 18, "Critical long quote")
    ]
    
    for base, quote, price, desc in scenarios:
        print(f"\n=== {desc} ===")
        
        # Calculate inventory status
        status = inventory_mgr.calculate_inventory_status(base, quote, price)
        
        # Log status
        inventory_mgr.log_inventory_status(status)
        
        # Calculate spread adjustments for buy and sell
        for side, side_name in [(0, "Buy"), (1, "Sell")]:
            level = {'spread': 0.5}  # 0.5% base spread
            spread, reason = calculate_order_spread(
                market_price=price,
                side=side,
                level=level,
                inventory_manager=inventory_mgr,
                inventory_status=status,
                vol_spread=0.002  # 0.2% volatility spread
            )
            
            print(f"{side_name} spread: {spread:.4f} ({spread*100:.2f}%) - {reason}")
        
        # Check for rebalancing needs
        rebalance = inventory_mgr.calculate_rebalance_order(status, price, 10)
        if rebalance:
            print(f"REBALANCE NEEDED: {rebalance}")
        
        # Show health metrics
        health = inventory_mgr.get_inventory_health(status)
        print(f"Health Score: {health['health_score']:.0f}/100")