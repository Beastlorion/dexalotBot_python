#!/usr/bin/env python3
"""
Enhanced Price Feed Manager for DexalotBot with safety validation and staleness detection.
Prevents trading on stale or invalid price data.
"""

import time
import asyncio
import aiohttp
import json
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

from binance import AsyncClient, BinanceSocketManager
from pybit.unified_trading import WebSocket as BybitWebSocket

from safety import PriceSafetyValidator, PriceData, CircuitBreaker
from shutdown_manager import ShutdownManager
from websocket_manager import WebSocketManager, WebSocketConnection
import tools
import contracts

logger = logging.getLogger(__name__)


class PriceSource(Enum):
    """Available price sources"""
    BINANCE = "binance"
    BYBIT = "bybit"
    CUSTOM = "custom"
    DEXALOT = "dexalot"


@dataclass
class PriceFeedConfig:
    """Configuration for price feeds"""
    symbol: str
    sources: List[PriceSource]
    primary_source: PriceSource
    max_price_age: int = 30  # seconds
    price_deviation_threshold: float = 0.05  # 5%
    enable_validation: bool = True
    custom_price_url: Optional[str] = None


class EnhancedPriceFeed:
    """Enhanced price feed with validation and safety features"""
    
    def __init__(
        self,
        config: PriceFeedConfig,
        shutdown_manager: ShutdownManager,
        safety_validator: PriceSafetyValidator,
        circuit_breaker: CircuitBreaker
    ):
        self.config = config
        self.shutdown = shutdown_manager
        self.validator = safety_validator
        self.circuit_breaker = circuit_breaker
        
        # Price data storage
        self.prices: Dict[PriceSource, PriceData] = {}
        self.current_price = 0.0
        self.last_update_time = 0.0
        self.price_source = None
        
        # Statistics
        self.price_updates = 0
        self.validation_failures = 0
        self.source_failures: Dict[PriceSource, int] = {}
        
        # Components
        self.websocket_manager = WebSocketManager(shutdown_manager)
        self.binance_client: Optional[AsyncClient] = None
        
        logger.info(f"Enhanced price feed initialized for {config.symbol}")
    
    async def start(self):
        """Start all configured price sources"""
        logger.info(f"Starting price feed for {self.config.symbol}")
        
        try:
            # Start price sources based on configuration
            start_tasks = []
            
            if PriceSource.BINANCE in self.config.sources:
                start_tasks.append(self._start_binance_feed())
            
            if PriceSource.BYBIT in self.config.sources:
                start_tasks.append(self._start_bybit_feed())
            
            if PriceSource.CUSTOM in self.config.sources:
                start_tasks.append(self._start_custom_feed())
            
            if PriceSource.DEXALOT in self.config.sources:
                start_tasks.append(self._start_dexalot_feed())
            
            # Start all feeds concurrently
            if start_tasks:
                await asyncio.gather(*start_tasks, return_exceptions=True)
            
            logger.info(f"Price feed started for {self.config.symbol}")
            
        except Exception as e:
            logger.error(f"Failed to start price feed for {self.config.symbol}: {e}")
            raise
    
    async def _start_binance_feed(self):
        """Start Binance price feed"""
        try:
            logger.info(f"Starting Binance feed for {self.config.symbol}")
            
            # Create Binance client
            self.binance_client = await AsyncClient.create()
            
            # Set up WebSocket connection
            bsm = BinanceSocketManager(self.binance_client)
            
            # Create symbol for Binance (e.g., AVAX_USDC -> AVAXUSDC)
            binance_symbol = self.config.symbol.replace('_', '').upper()
            
            # Start ticker stream
            ticker_socket = bsm.ticker_socket(symbol=binance_symbol)
            
            # Create task for handling Binance data
            async def handle_binance_stream():
                async with ticker_socket as stream:
                    while not self.shutdown.is_shutdown_requested():
                        try:
                            # Receive with timeout
                            res = await asyncio.wait_for(stream.recv(), timeout=2.0)
                            if res:
                                await self._process_binance_data(res)
                        except asyncio.TimeoutError:
                            continue  # Check shutdown and continue
                        except Exception as e:
                            logger.error(f"Binance stream error: {e}")
                            self._record_source_failure(PriceSource.BINANCE)
                            break
            
            # Register and start task
            task = asyncio.create_task(handle_binance_stream())
            self.shutdown.register_task(task, f"binance-{self.config.symbol}")
            
        except Exception as e:
            logger.error(f"Failed to start Binance feed: {e}")
            self._record_source_failure(PriceSource.BINANCE)
    
    async def _start_bybit_feed(self):
        """Start Bybit price feed"""
        try:
            logger.info(f"Starting Bybit feed for {self.config.symbol}")
            
            # Create Bybit symbol (e.g., AVAX_USDC -> AVAXUSDC)
            bybit_symbol = self.config.symbol.replace('_', '').upper()
            
            # Set up WebSocket connection
            ws_connection = self.websocket_manager.add_connection(
                name=f"bybit-{self.config.symbol}",
                url=f"wss://stream.bybit.com/v5/public/spot",
                recv_timeout=2.0
            )
            
            # Add message handler
            def handle_bybit_message(message: str):
                try:
                    data = json.loads(message)
                    asyncio.create_task(self._process_bybit_data(data))
                except Exception as e:
                    logger.error(f"Bybit message error: {e}")
                    self._record_source_failure(PriceSource.BYBIT)
            
            ws_connection.add_message_handler(handle_bybit_message)
            
            # Start connection
            await self.websocket_manager.start_connection(f"bybit-{self.config.symbol}")
            
            # Subscribe to ticker
            subscribe_msg = {
                "op": "subscribe",
                "args": [f"tickers.{bybit_symbol}"]
            }
            await ws_connection.send_message(json.dumps(subscribe_msg))
            
        except Exception as e:
            logger.error(f"Failed to start Bybit feed: {e}")
            self._record_source_failure(PriceSource.BYBIT)
    
    async def _start_custom_feed(self):
        """Start custom price feed"""
        if not self.config.custom_price_url:
            logger.error("Custom price URL not configured")
            return
        
        async def poll_custom_price():
            while not self.shutdown.is_shutdown_requested():
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(self.config.custom_price_url) as response:
                            if response.status == 200:
                                data = await response.json()
                                await self._process_custom_data(data)
                            else:
                                logger.error(f"Custom price feed HTTP {response.status}")
                                self._record_source_failure(PriceSource.CUSTOM)
                except Exception as e:
                    logger.error(f"Custom price feed error: {e}")
                    self._record_source_failure(PriceSource.CUSTOM)
                
                # Wait before next poll
                if await self.shutdown.wait_with_timeout(10.0):
                    break
        
        task = asyncio.create_task(poll_custom_price())
        self.shutdown.register_task(task, f"custom-{self.config.symbol}")
    
    async def _start_dexalot_feed(self):
        """Start Dexalot native price feed"""
        # This would connect to Dexalot's WebSocket for order book data
        # Implementation depends on Dexalot's WebSocket API
        logger.info("Dexalot feed not yet implemented")
    
    async def _process_binance_data(self, data: Dict[str, Any]):
        """Process Binance ticker data"""
        try:
            price = float(data.get('c', 0))  # Current price
            if price > 0:
                await self._update_price(PriceSource.BINANCE, price)
        except Exception as e:
            logger.error(f"Error processing Binance data: {e}")
            self._record_source_failure(PriceSource.BINANCE)
    
    async def _process_bybit_data(self, data: Dict[str, Any]):
        """Process Bybit ticker data"""
        try:
            if data.get('topic', '').startswith('tickers.'):
                ticker_data = data.get('data', {})
                price = float(ticker_data.get('lastPrice', 0))
                if price > 0:
                    await self._update_price(PriceSource.BYBIT, price)
        except Exception as e:
            logger.error(f"Error processing Bybit data: {e}")
            self._record_source_failure(PriceSource.BYBIT)
    
    async def _process_custom_data(self, data: Dict[str, Any]):
        """Process custom price feed data"""
        try:
            # Assume custom feed returns {"symbol": "AVAX_USDC", "price": 12.34}
            if data.get('symbol') == self.config.symbol:
                price = float(data.get('price', 0))
                if price > 0:
                    await self._update_price(PriceSource.CUSTOM, price)
        except Exception as e:
            logger.error(f"Error processing custom data: {e}")
            self._record_source_failure(PriceSource.CUSTOM)
    
    async def _update_price(self, source: PriceSource, price: float):
        """Update price from a specific source with validation"""
        try:
            # Validate price if enabled
            if self.config.enable_validation:
                is_valid, error = self.validator.validate_market_price(
                    self.config.symbol, 
                    price
                )
                
                if not is_valid:
                    logger.warning(f"Price validation failed for {source.value}: {error}")
                    self.validation_failures += 1
                    return
                
                # Check for extreme price movements
                if source in self.prices:
                    old_price_data = self.prices[source]
                    if not old_price_data.is_stale(self.config.max_price_age):
                        ok, error = self.circuit_breaker.check_price_movement(
                            f"{self.config.symbol}_{source.value}",
                            price
                        )
                        if not ok:
                            logger.error(f"Circuit breaker triggered: {error}")
                            return
            
            # Store price data
            price_data = PriceData(
                price=price,
                timestamp=time.time(),
                source=source.value
            )
            self.prices[source] = price_data
            
            # Update current price if this is primary source or better
            should_update = (
                source == self.config.primary_source or
                self.price_source is None or
                self._is_better_source(source, self.price_source)
            )
            
            if should_update:
                self.current_price = price
                self.last_update_time = time.time()
                self.price_source = source
                self.price_updates += 1
                
                # Update validator reference price
                self.validator.update_reference_price(
                    self.config.symbol,
                    price,
                    source.value
                )
                
                logger.debug(f"Price updated for {self.config.symbol}: "
                           f"{price} from {source.value}")
            
            # Record successful update
            self.circuit_breaker.record_success()
            
        except Exception as e:
            logger.error(f"Error updating price from {source.value}: {e}")
            self._record_source_failure(source)
    
    def _is_better_source(self, new_source: PriceSource, current_source: PriceSource) -> bool:
        """Determine if new source is better than current source"""
        # Prefer primary source
        if new_source == self.config.primary_source:
            return True
        if current_source == self.config.primary_source:
            return False
        
        # Check if current source is stale
        if current_source in self.prices:
            current_data = self.prices[current_source]
            if current_data.is_stale(self.config.max_price_age):
                return True
        
        return False
    
    def _record_source_failure(self, source: PriceSource):
        """Record failure for a price source"""
        self.source_failures[source] = self.source_failures.get(source, 0) + 1
        self.circuit_breaker.record_error()
    
    def get_current_price(self) -> Tuple[float, bool]:
        """
        Get current price and whether it's fresh.
        Returns (price, is_fresh)
        """
        if self.current_price == 0:
            return 0.0, False
        
        age = time.time() - self.last_update_time
        is_fresh = age <= self.config.max_price_age
        
        if not is_fresh:
            logger.warning(f"Price for {self.config.symbol} is stale ({age:.1f}s old)")
        
        return self.current_price, is_fresh
    
    def get_price_from_source(self, source: PriceSource) -> Optional[PriceData]:
        """Get price data from specific source"""
        return self.prices.get(source)
    
    def get_status(self) -> Dict[str, Any]:
        """Get price feed status"""
        price, is_fresh = self.get_current_price()
        
        return {
            "symbol": self.config.symbol,
            "current_price": price,
            "is_fresh": is_fresh,
            "price_source": self.price_source.value if self.price_source else None,
            "last_update": self.last_update_time,
            "price_updates": self.price_updates,
            "validation_failures": self.validation_failures,
            "source_failures": dict(self.source_failures),
            "available_sources": list(self.prices.keys())
        }
    
    async def stop(self):
        """Stop price feed"""
        logger.info(f"Stopping price feed for {self.config.symbol}")
        
        # Stop WebSocket connections
        await self.websocket_manager.stop_all()
        
        # Close Binance client
        if self.binance_client:
            await self.binance_client.close_connection()
        
        logger.info(f"Price feed stopped for {self.config.symbol}")


# Example usage
async def example_usage():
    """Example of using the enhanced price feed"""
    
    from safety import PriceBounds
    
    # Create components
    shutdown = ShutdownManager()
    shutdown.install_signal_handlers()
    
    validator = PriceSafetyValidator(PriceBounds(
        min_price=1.0,
        max_price=100.0,
        max_spread_percent=0.05
    ))
    
    breaker = CircuitBreaker()
    
    # Configure price feed
    config = PriceFeedConfig(
        symbol="AVAX_USDC",
        sources=[PriceSource.BINANCE, PriceSource.BYBIT],
        primary_source=None,
        max_price_age=30,
        enable_validation=True
    )
    
    # Create price feed
    price_feed = EnhancedPriceFeed(config, shutdown, validator, breaker)
    
    try:
        # Start price feed
        await price_feed.start()
        
        # Monitor prices
        while not shutdown.is_shutdown_requested():
            await shutdown.wait_with_timeout(5.0)
            
            price, is_fresh = price_feed.get_current_price()
            status = price_feed.get_status()
            
            logger.info(f"Current price: {price} (fresh: {is_fresh})")
            logger.info(f"Status: {status}")
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        await price_feed.stop()
        await shutdown.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(example_usage())