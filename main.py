#!/usr/bin/env python3
"""
Enhanced Main entry point for DexalotBot with improved safety and shutdown handling.
Integrates safety validators and shutdown manager for reliable operation.
"""

import sys
import asyncio
import logging
from typing import Optional

# Import enhanced components
from shutdown_manager import ShutdownManager, ShutdownReason
from safety import PriceSafetyValidator, CircuitBreaker, PriceBounds
from inventory_manager import InventoryManager
import analytics
import marketMaker
import orders
import contracts
import tools

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class EnhancedBotManager:
    """Enhanced bot manager with safety features and reliable shutdown."""
    
    def __init__(self):
        # Core components
        self.shutdown = ShutdownManager(shutdown_timeout=15)
        self.price_validator = PriceSafetyValidator()
        self.circuit_breaker = CircuitBreaker(
            max_price_change_percent=0.15,  # 15% price change threshold
            max_consecutive_errors=3,
            cooldown_seconds=120
        )
        self.inventory_manager = None  # Will be initialized with market settings
        
        # State
        self.market_pair: Optional[str] = None
        self.network: str = 'm'
        
    async def initialize_safety_bounds(self, market_pair: str):
        """Initialize safety bounds for the trading pair"""
        # Import settings to get bounds configuration
        import settings
        
        # Check if bounds are defined in settings file
        if 'SAFETY_BOUNDS' in settings.settings and market_pair in settings.settings['SAFETY_BOUNDS']:
            bounds_config = settings.settings['SAFETY_BOUNDS'][market_pair]
            bounds = PriceBounds(
                min_price=bounds_config.get('min_price', 0.001),
                max_price=bounds_config.get('max_price', 1000000),
                max_spread_percent=bounds_config.get('max_spread', 0.05),
                max_price_deviation_percent=bounds_config.get('max_deviation', 0.20),
                stale_price_timeout=bounds_config.get('stale_timeout', 30)
            )
            logger.info(f"Loaded safety bounds from settings for {market_pair}")
        else:
            # Fallback to default bounds based on market pair type
            default_bounds = {
                'AVAX_USDC': PriceBounds(0.1, 1000, 0.05, 0.20),
                'BTC_USDC': PriceBounds(1000, 200000, 0.03, 0.15),
                'ETH_USDC': PriceBounds(100, 20000, 0.03, 0.15),
                'WBTC_USDC': PriceBounds(1000, 200000, 0.03, 0.15),
                'AVAX_USDT': PriceBounds(0.1, 1000, 0.05, 0.20),
                'ETH_USDT': PriceBounds(100, 20000, 0.03, 0.15),
            }
            
            # Use default if pair not configured
            bounds = default_bounds.get(market_pair, PriceBounds(
                min_price=0.001,
                max_price=1000000,
                max_spread_percent=0.10,  # 10% default for unknown pairs
                max_price_deviation_percent=0.25
            ))
            logger.info(f"Using default safety bounds for {market_pair}")
        
        self.price_validator.default_bounds = bounds
        logger.info(f"Safety bounds configured for {market_pair}: "
                   f"price range [{bounds.min_price}, {bounds.max_price}], "
                   f"max spread {bounds.max_spread_percent:.1%}")
    
    async def initialize_inventory_manager(self, market_pair: str):
        """Initialize inventory manager with market settings"""
        import settings
        
        # Get market settings
        market_settings = settings.settings.get(market_pair, {})
        
        # Initialize inventory manager with defensive/offensive skew
        self.inventory_manager = InventoryManager(
            target_ratio=0.5,  # Target 50/50 balance
            defensive_skew=market_settings.get('defensiveSkew', 0.01),
            offensive_skew=market_settings.get('offensiveSkew', None)
        )
        
        logger.info(f"Inventory manager initialized for {market_pair} with "
                   f"defensive skew: {self.inventory_manager.defensive_skew}, "
                   f"offensive skew: {self.inventory_manager.offensive_skew}")
    
    async def graceful_shutdown(self):
        """Enhanced graceful shutdown with order cancellation"""
        try:
            logger.info("Performing enhanced graceful shutdown...")
            
            # Stop market maker instance if exists
            if (hasattr(marketMaker, 'market_maker_instance') and 
                marketMaker.market_maker_instance):
                marketMaker.market_maker_instance.request_shutdown()
                logger.info("Market maker shutdown requested")
            
            # Note: Order cancellation is now handled in the finally block of run()
            # before shutdown.shutdown() is called, ensuring connections are still alive
            # contracts.status is set to False later to keep connections alive
                
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
    
    async def run_analytics(self):
        """Run analytics mode with enhanced error handling"""
        try:
            logger.info("Starting analytics mode")
            await analytics.start()
        except KeyboardInterrupt:
            logger.info("Analytics interrupted by user")
            self.shutdown.request_shutdown(ShutdownReason.KEYBOARD_INTERRUPT)
        except Exception as e:
            logger.error(f"Analytics error: {e}")
            self.circuit_breaker.record_error()
            raise
    
    async def run_market_maker(self, network: str = 'm'):
        """Run market maker with enhanced safety and restart logic"""
        self.network = network
        restart_delay = 15
        max_restarts = 5
        restart_count = 0
        
        while not self.shutdown.is_shutdown_requested() and restart_count < max_restarts:
            try:
                logger.info(f"Starting market maker on network: {network} (attempt {restart_count + 1})")
                
                # Check circuit breaker before starting
                if self.circuit_breaker.is_tripped():
                    breaker_status = self.circuit_breaker.get_status()
                    logger.warning(f"Circuit breaker active: {breaker_status}")
                    
                    # Wait for cooldown with interruptible sleep
                    cooldown = breaker_status.get('cooldown_remaining', 30)
                    logger.info(f"Waiting {cooldown}s for circuit breaker cooldown")
                    
                    if await self.shutdown.wait_with_timeout(cooldown):
                        break  # Shutdown requested during cooldown
                    continue
                
                # Initialize safety bounds and inventory manager
                if self.market_pair:
                    await self.initialize_safety_bounds(self.market_pair)
                    await self.initialize_inventory_manager(self.market_pair)
                
                # Start market maker with shutdown event
                await marketMaker.start(network, self.shutdown.shutdown_event)
                
                # If we reach here, market maker completed normally
                self.circuit_breaker.record_success()
                
                # Check if it was a graceful shutdown
                if (hasattr(marketMaker, 'market_maker_instance') and 
                    marketMaker.market_maker_instance and 
                    marketMaker.market_maker_instance.shutdown_requested):
                    logger.info("Market maker requested shutdown, not restarting")
                    break
                
                if self.shutdown.is_shutdown_requested():
                    break
                
                # Normal completion - wait before restart
                logger.info(f"Market maker stopped normally, restarting in {restart_delay}s...")
                if await self.shutdown.wait_with_timeout(restart_delay):
                    break  # Shutdown requested during delay
                
                restart_count += 1
                
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt in market maker loop")
                self.shutdown.request_shutdown(ShutdownReason.KEYBOARD_INTERRUPT)
                break
                
            except Exception as e:
                logger.error(f"Market maker error: {e}")
                self.circuit_breaker.record_error()
                restart_count += 1
                
                if self.shutdown.is_shutdown_requested():
                    break
                
                # Check if circuit breaker trips
                if self.circuit_breaker.is_tripped():
                    logger.error("Circuit breaker tripped due to errors")
                    continue  # Will be handled at start of next loop
                
                logger.info(f"Restarting in {restart_delay}s... (attempt {restart_count + 1}/{max_restarts})")
                if await self.shutdown.wait_with_timeout(restart_delay):
                    break  # Shutdown requested during delay
        
        # Check if we exceeded max restarts
        if restart_count >= max_restarts:
            logger.error(f"Maximum restart attempts ({max_restarts}) exceeded")
            self.shutdown.request_shutdown(ShutdownReason.ERROR_LIMIT)
        
        # Perform graceful shutdown
        await self.graceful_shutdown()
    
    async def run(self):
        """Enhanced main run method with comprehensive error handling"""
        # Check for analytics mode early
        is_analytics_mode = len(sys.argv) > 2 and 'analytics' in sys.argv
        
        # Only install signal handlers and register cleanup for market maker mode
        if not is_analytics_mode:
            self.shutdown.install_signal_handlers()
            self.shutdown.register_cleanup(self.graceful_shutdown, "bot cleanup")
        
        try:
            # Parse command line arguments
            if len(sys.argv) < 2:
                logger.error("Usage: python main_v2.py <MARKET_PAIR> [network] [analytics]")
                logger.error("Example: python main_v2.py AVAX_USDC")
                logger.error("Example: python main_v2.py AVAX_USDC fuji")
                logger.error("Example: python main_v2.py AVAX_USDC m analytics")
                return
            
            self.market_pair = sys.argv[1]
            logger.info(f"Market pair: {self.market_pair}")
            
            # Check for analytics mode
            if is_analytics_mode:
                await self.run_analytics()
                return
            
            # Determine network
            self.network = 'fuji' if len(sys.argv) > 2 and sys.argv[2] == "fuji" else 'm'
            logger.info(f"Network: {'testnet (fuji)' if self.network == 'fuji' else 'mainnet'}")
            
            # Run market maker
            await self.run_market_maker(self.network)
            
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received in main")
            self.shutdown.request_shutdown(ShutdownReason.KEYBOARD_INTERRUPT)
        except Exception as e:
            logger.error(f"Unexpected error in main: {e}")
            self.shutdown.request_shutdown(ShutdownReason.ERROR_LIMIT)
            raise
        finally:
            # Skip cleanup for analytics mode
            if is_analytics_mode:
                logger.info("Analytics mode completed, skipping market maker cleanup")
                return
            
            # Ensure cleanup runs
            if not self.shutdown.is_shutdown_requested():
                self.shutdown.request_shutdown(ShutdownReason.MANUAL)
            
            # Cancel orders BEFORE shutting down tasks and connections
            if self.market_pair and hasattr(orders, 'cancelAllOrders'):
                # Convert market pair format from AVAX_USDC to AVAX/USDC for API compatibility
                base = tools.getSymbolFromName(self.market_pair, 0)
                quote = tools.getSymbolFromName(self.market_pair, 1)
                pair_str = f"{base}/{quote}"
                logger.info(f"[SHUTDOWN] Starting order cancellation for {pair_str}")
                try:
                    # Give more time for order cancellation during shutdown
                    result = await asyncio.wait_for(orders.cancelAllOrders(pair_str, True), timeout=20.0)
                    if result:
                        logger.info("[SHUTDOWN] All orders cancelled successfully")
                    else:
                        logger.warning("[SHUTDOWN] Order cancellation completed with issues")
                except asyncio.TimeoutError:
                    logger.error("[SHUTDOWN] Order cancellation timed out after 20 seconds")
                except Exception as e:
                    logger.error(f"[SHUTDOWN] Error cancelling orders: {e}", exc_info=True)
                
                # Add a small delay to ensure transaction propagation
                await asyncio.sleep(1)
                logger.info("[SHUTDOWN] Order cancellation phase complete")
            
            # Now stop all market making activities
            if hasattr(contracts, 'status'):
                contracts.status = False
                logger.info("Market making activities stopped")
            
            await self.shutdown.shutdown()
            
            # Final status report
            status = {
                "shutdown": self.shutdown.get_status(),
                "circuit_breaker": self.circuit_breaker.get_status(),
            }
            logger.info(f"Bot manager shutdown complete. Final status: {status}")


async def main():
    """Enhanced entry point with comprehensive error handling"""
    bot_manager = None
    
    try:
        bot_manager = EnhancedBotManager()
        await bot_manager.run()
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
        if bot_manager:
            bot_manager.shutdown.request_shutdown(ShutdownReason.KEYBOARD_INTERRUPT)
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        if bot_manager:
            bot_manager.shutdown.request_shutdown(ShutdownReason.ERROR_LIMIT)
        sys.exit(1)
    finally:
        logger.info("Program finished")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Final keyboard interrupt - exiting immediately")
    except Exception as e:
        logger.error(f"Critical error: {e}")
        sys.exit(1)