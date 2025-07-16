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
import settings
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
from odos_api import OdosAPI

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
    reconnect_timeout: int = 30 # seconds before reconnecting stale feeds


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
        self.market_settings = settings.settings.get(self.config.symbol.replace('/', '_'), {})
        
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
        self.odos_api: Optional[OdosAPI] = None
        
        # Heartbeat monitoring
        self._source_last_update: Dict[PriceSource, float] = {}
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_timeout = self.market_settings.get('timeout', config.reconnect_timeout)
        self.max_price_age = self.market_settings.get('timeout', config.max_price_age)
        
        logger.info(f"Enhanced price feed initialized for {config.symbol}")
    
    def _parse_symbol(self) -> Tuple[str, str]:
        """
        Parse symbol into base and quote currencies.
        Handles both underscore and slash separators.
        
        Returns:
            Tuple of (base, quote) or raises ValueError if invalid format
        """
        # Parse base and quote from symbol (handle both _ and / separators)
        if '_' in self.config.symbol:
            parts = self.config.symbol.split('_')
        elif '/' in self.config.symbol:
            parts = self.config.symbol.split('/')
        else:
            raise ValueError(f"Invalid symbol format: {self.config.symbol}")
        
        if len(parts) != 2:
            raise ValueError(f"Invalid symbol format: {self.config.symbol}")
        
        return parts[0], parts[1]
    
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
            
            # Start heartbeat monitoring
            self._heartbeat_task = asyncio.create_task(self._monitor_heartbeat())
            self.shutdown.register_task(self._heartbeat_task, f"heartbeat-{self.config.symbol}")
            
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
            
            # Parse base and quote from symbol
            try:
                base, quote = self._parse_symbol()
            except ValueError as e:
                logger.error(str(e))
                return
            
            # For Bybit, we need to convert USDC quotes to USDT
            if quote == 'USDC' and base != 'USDT' and base != 'WBTC':
                # We'll get base/USDT price and USDC/USDT price
                bybit_symbol = f"{base}USDT"
                need_usdc_conversion = True
            elif quote == 'USDC' and base == 'USDT':
                bybit_symbol = f"{quote}{base}"
                need_usdc_conversion = False
            elif quote == 'USDC' and base == 'WBTC':
                bybit_symbol = "BTCUSDT"
                need_usdc_conversion = True
            else:
                bybit_symbol = f"{base}{quote}"
                need_usdc_conversion = False
            
            # Check if this is a perpetual contract
            is_perps = self.market_settings.get('perps', False)
            
            # Initialize funding rate data storage
            self._funding_rate = 0.0
            self._next_funding_time = 0
            
            # Set up WebSocket connection
            # Use linear endpoint for perps, spot endpoint for spot
            ws_url = "wss://stream.bybit.com/v5/public/linear" if is_perps else "wss://stream.bybit.com/v5/public/spot"
            
            ws_connection = self.websocket_manager.add_connection(
                name=f"bybit-{self.config.symbol}",
                url=ws_url,
                recv_timeout=5.0
            )
            
            # Store subscription data for later use
            self._bybit_subscription_data = {
                'bybit_symbol': bybit_symbol,
                'need_usdc_conversion': need_usdc_conversion,
                'is_perps': is_perps
            }
            
            # Add message handler that also handles subscription
            def handle_bybit_message(message: str):
                try:
                    data = json.loads(message)
                    asyncio.create_task(self._process_bybit_data(data))
                except Exception as e:
                    logger.error(f"Bybit message error: {e}")
                    self._record_source_failure(PriceSource.BYBIT)
            
            ws_connection.add_message_handler(handle_bybit_message)
            
            # Start connection and wait for it to be ready
            logger.info(f"Starting {'perps' if is_perps else 'spot'} WebSocket connection for bybit-{self.config.symbol}")
            await self.websocket_manager.start_connection(f"bybit-{self.config.symbol}")
            
            # Wait for connection to be established
            connected = await ws_connection.wait_for_connection(timeout=5.0)
            
            if not connected:
                logger.error(f"Bybit {'perps' if is_perps else 'spot'} WebSocket failed to connect within 5.0s")
                self._record_source_failure(PriceSource.BYBIT)
                return
            
            logger.info(f"Bybit {'perps' if is_perps else 'spot'} WebSocket connected successfully")
            
            # Now send subscription
            bybit_symbol = self._bybit_subscription_data['bybit_symbol']
            need_usdc_conversion = self._bybit_subscription_data['need_usdc_conversion']
            is_perps = self._bybit_subscription_data['is_perps']
            
            # Check if we should use orderbook or trade data
            # Default to using trades unless explicitly configured to use orderbook
            use_orderbook = False
            
            # Check if useBybitOrderbook is configured in settings
            if 'useBybitOrderbook' in self.market_settings:
                use_orderbook = self.market_settings.get('useBybitOrderbook', False)
            
            if use_orderbook:
                # Subscribe to orderbook depth for main symbol
                subscribe_args = [f"orderbook.1.{bybit_symbol}"]
                logger.info(f"Using orderbook data for Bybit {'perps' if is_perps else 'spot'} {bybit_symbol}")
            else:
                # Subscribe to public trades for main symbol (default)
                subscribe_args = [f"publicTrade.{bybit_symbol}", f"tickers.{bybit_symbol}"]
                logger.info(f"Using trade data and tickers for Bybit {'perps' if is_perps else 'spot'} {bybit_symbol}")
            
            # If we need USDC conversion, always use orderbook for USDCUSDT
            if need_usdc_conversion:
                subscribe_args.append("orderbook.1.USDCUSDT")
                # Store that we need conversion
                self._bybit_needs_usdc_conversion = True
                self._usdc_usdt_price = None
            else:
                self._bybit_needs_usdc_conversion = False
            
            subscribe_msg = {
                "op": "subscribe",
                "args": subscribe_args
            }
            
            logger.info(f"Bybit subscribing to: {subscribe_args}")
            success = await ws_connection.send_message(json.dumps(subscribe_msg))
            
            if not success:
                logger.error("Failed to send Bybit subscription message")
                self._record_source_failure(PriceSource.BYBIT)
                return
            
            logger.info("Bybit subscription message sent successfully")
            
        except Exception as e:
            logger.error(f"Failed to start Bybit feed: {e}")
            self._record_source_failure(PriceSource.BYBIT)
    
    async def _start_custom_feed(self):
        """Start custom price feed - uses Odos API when useCustomPrice is True"""
        # Check if we should use Odos API
        use_odos = self.market_settings.get('useCustomPrice', False)
        
        if use_odos:
            logger.info(f"Starting Odos price feed for {self.config.symbol}")
            # Initialize Odos API
            if not self.odos_api:
                self.odos_api = OdosAPI(chain_id=43114)  # Avalanche C-Chain
                await self.odos_api.start()
            
            async def poll_odos_price():
                while not self.shutdown.is_shutdown_requested():
                    try:
                        await self._fetch_odos_price()
                    except Exception as e:
                        logger.error(f"Odos price feed error: {e}")
                        self._record_source_failure(PriceSource.CUSTOM)
                    
                    # Wait before next poll (shorter interval for Odos)
                    if await self.shutdown.wait_with_timeout(5.0):
                        break
            
            task = asyncio.create_task(poll_odos_price())
            self.shutdown.register_task(task, f"odos-{self.config.symbol}")
            
        elif self.config.custom_price_url:
            # Fall back to original custom price URL behavior
            logger.info(f"Starting custom URL price feed for {self.config.symbol}")
            
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
        else:
            logger.error("Neither Odos nor custom price URL configured")
            return
    
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
        """Process Bybit trade or orderbook data"""
        try:
            topic = data.get('topic', '')
            
            # Handle subscription success
            if data.get('success') == True and data.get('op') == 'subscribe':
                logger.info(f"Bybit subscription confirmed for: {data.get('req_id', 'unknown')}")
                return
            
            # Handle public trade updates
            if topic and topic.startswith('publicTrade.'):
                symbol = topic.split('.')[-1]  # e.g., "AVAXUSDT"
                
                # Get trade data
                trade_data = data.get('data', [])
                if not trade_data:
                    return
                
                # Use the most recent trade price
                # Trade data format: [{"i": "id", "T": timestamp, "p": price, "v": volume, "S": side, ...}]
                latest_trade = trade_data[-1]  # Get the last trade in the array
                trade_price = float(latest_trade.get('p', 0))
                
                if trade_price <= 0:
                    return
                
                # Apply funding adjustment for perpetuals
                if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                    adjusted_price = self._calculate_funding_adjusted_price(trade_price)
                else:
                    adjusted_price = trade_price
                
                # Handle USDC conversion if needed
                if hasattr(self, '_bybit_needs_usdc_conversion') and self._bybit_needs_usdc_conversion:
                    # Convert from USDT to USDC
                    if hasattr(self, '_usdc_usdt_price') and self._usdc_usdt_price:
                        converted_price = adjusted_price / self._usdc_usdt_price
                        if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                            logger.debug(f"Bybit {symbol} perps trade price: {trade_price} -> funding adjusted: {adjusted_price} USDT = {converted_price} USDC")
                        else:
                            logger.debug(f"Bybit {symbol} trade price: {adjusted_price} USDT = {converted_price} USDC")
                        await self._update_price(PriceSource.BYBIT, converted_price)
                    else:
                        logger.warning("Waiting for USDC/USDT price for conversion")
                else:
                    # No conversion needed
                    if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                        logger.debug(f"Bybit {symbol} perps trade price: {trade_price} -> funding adjusted: {adjusted_price}")
                    else:
                        logger.debug(f"Bybit {symbol} trade price: {adjusted_price}")
                    await self._update_price(PriceSource.BYBIT, adjusted_price)
                        
            # Handle tickers updates
            elif topic and topic.startswith('tickers.'):
                symbol = topic.split('.')[-1]  # e.g., "AVAXUSDT"
                
                # Get tickers data
                ticker_data = data.get('data', {})
                if not ticker_data:
                    return
                
                lastPrice = float(ticker_data.get('lastPrice', 0))
                
                if lastPrice <= 0:
                    return
                
                # Extract funding rate for perpetuals
                if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                    funding_rate = float(ticker_data.get('fundingRate', 0))
                    next_funding_time = int(ticker_data.get('nextFundingTime', 0))
                    
                    if funding_rate != 0 and next_funding_time > 0:
                        self._funding_rate = funding_rate
                        self._next_funding_time = next_funding_time / 1000  # Convert ms to seconds
                        logger.debug(f"Bybit {symbol} funding rate: {funding_rate}, next funding: {self._next_funding_time}")
                    
                    # Calculate funding-adjusted price
                    adjusted_price = self._calculate_funding_adjusted_price(lastPrice)
                    
                    # Handle USDC conversion if needed
                    if hasattr(self, '_bybit_needs_usdc_conversion') and self._bybit_needs_usdc_conversion:
                        # Convert from USDT to USDC
                        if hasattr(self, '_usdc_usdt_price') and self._usdc_usdt_price:
                            converted_price = adjusted_price / self._usdc_usdt_price
                            logger.debug(f"Bybit {symbol} perps price: {lastPrice} -> funding adjusted: {adjusted_price} USDT = {converted_price} USDC")
                            await self._update_price(PriceSource.BYBIT, converted_price)
                        else:
                            logger.warning("Waiting for USDC/USDT price for conversion")
                    else:
                        # No conversion needed
                        logger.debug(f"Bybit {symbol} perps price: {lastPrice} -> funding adjusted: {adjusted_price}")
                        await self._update_price(PriceSource.BYBIT, adjusted_price)
                else:
                    # Not perps, use regular price
                    # Handle USDC conversion if needed
                    if hasattr(self, '_bybit_needs_usdc_conversion') and self._bybit_needs_usdc_conversion:
                        # Convert from USDT to USDC
                        if hasattr(self, '_usdc_usdt_price') and self._usdc_usdt_price:
                            converted_price = lastPrice / self._usdc_usdt_price
                            logger.debug(f"Bybit {symbol} tickers price: {lastPrice} USDT = {converted_price} USDC")
                            await self._update_price(PriceSource.BYBIT, converted_price)
                        else:
                            logger.warning("Waiting for USDC/USDT price for conversion")
                    else:
                        # No conversion needed, use lastPrice
                        logger.debug(f"Bybit {symbol} tickers price: {lastPrice}")
                        await self._update_price(PriceSource.BYBIT, lastPrice)
            
            # Handle orderbook updates (for USDCUSDT conversion or if orderbook mode is enabled)
            elif topic and topic.startswith('orderbook.'):
                symbol = topic.split('.')[-1]  # e.g., "AVAXUSDT" or "USDCUSDT"

                try:
                    base, quote = self._parse_symbol()
                except ValueError as e:
                    logger.error(str(e))
                    return
                
                # Get orderbook data
                orderbook_data = data.get('data', {})
                bids = orderbook_data.get('b', [])  # [[price, size], ...]
                asks = orderbook_data.get('a', [])  # [[price, size], ...]
                
                if not bids or not asks:
                    return
                
                # Get best bid and ask
                best_bid = float(bids[0][0]) if bids else 0
                best_ask = float(asks[0][0]) if asks else 0
                
                if best_bid <= 0 or best_ask <= 0:
                    return
                
                # Calculate mid price
                mid_price = (best_bid + best_ask) / 2
                
                # Handle USDC/USDT conversion
                if symbol == 'USDCUSDT':
                    if base == 'USDT' and quote == 'USDC':
                        # No conversion needed
                        logger.debug(f"Bybit {base}{quote} orderbook price: {1/mid_price}")
                        await self._update_price(PriceSource.BYBIT, 1/mid_price)
                    else:
                        self._usdc_usdt_price = mid_price
                        logger.debug(f"Bybit USDC/USDT price: {mid_price}")
                else:
                    # This is our main symbol price (only if using orderbook mode)
                    # Apply funding adjustment for perpetuals
                    if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                        adjusted_price = self._calculate_funding_adjusted_price(mid_price)
                    else:
                        adjusted_price = mid_price
                    
                    if hasattr(self, '_bybit_needs_usdc_conversion') and self._bybit_needs_usdc_conversion:
                        # Convert from USDT to USDC
                        if hasattr(self, '_usdc_usdt_price') and self._usdc_usdt_price:
                            converted_price = adjusted_price / self._usdc_usdt_price
                            if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                                logger.debug(f"Bybit {symbol} perps orderbook price: {mid_price} -> funding adjusted: {adjusted_price} USDT = {converted_price} USDC")
                            else:
                                logger.debug(f"Bybit {symbol} orderbook price: {adjusted_price} USDT = {converted_price} USDC")
                            await self._update_price(PriceSource.BYBIT, converted_price)
                        else:
                            logger.warning("Waiting for USDC/USDT price for conversion")
                    else:
                        # No conversion needed
                        if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
                            logger.debug(f"Bybit {symbol} perps orderbook price: {mid_price} -> funding adjusted: {adjusted_price}")
                        else:
                            logger.debug(f"Bybit {symbol} orderbook price: {adjusted_price}")
                        await self._update_price(PriceSource.BYBIT, adjusted_price)
                        
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
                else:
                    # Still update heartbeat even if price is invalid
                    self._source_last_update[PriceSource.CUSTOM] = time.time()
        except Exception as e:
            logger.error(f"Error processing custom data: {e}")
            self._record_source_failure(PriceSource.CUSTOM)
    
    async def _fetch_odos_price(self):
        """Fetch price from Odos API using buy/sell quotes"""
        try:
            # Parse base and quote from symbol
            base, quote = self._parse_symbol()
            
            # Get token details from contracts
            if base not in contracts.contracts or quote not in contracts.contracts:
                logger.error(f"Token details not found for {base} or {quote}")
                return
            
            base_token = contracts.contracts[base]
            quote_token = contracts.contracts[quote]
            
            # Get token addresses and decimals
            base_address = base_token.get('tokenDetails', {}).get('address')
            quote_address = quote_token.get('tokenDetails', {}).get('address')
            base_decimals = base_token.get('tokenDetails', {}).get('evmdecimals', 18)
            quote_decimals = quote_token.get('tokenDetails', {}).get('evmdecimals', 18)
            
            if not base_address or not quote_address:
                logger.error(f"Token addresses not found for {base} or {quote}")
                return
            
            # Get a fallback base price (e.g., from Binance/Bybit if available)
            base_price_usd = 1.0  # Default for stablecoins
            if PriceSource.BINANCE in self.prices:
                base_price_usd = self.prices[PriceSource.BINANCE].price
            elif PriceSource.BYBIT in self.prices:
                base_price_usd = self.prices[PriceSource.BYBIT].price
            
            # Special handling for USDC/USDT pairs
            if base in ['USDC', 'USDT'] and quote in ['USDC', 'USDT']:
                base_price_usd = 1.0
            
            # Get user address from market settings or use a default
            user_address = self.market_settings.get('address', contracts.address or "0x0000000000000000000000000000000000000000")
            
            # Fetch price from Odos
            price = await self.odos_api.get_price_from_quotes(
                base_token_address=base_address,
                quote_token_address=quote_address,
                base_decimals=base_decimals,
                quote_decimals=quote_decimals,
                amount_usd=100.0,  # Use $100 worth for quotes
                user_address=user_address,
                base_price_usd=base_price_usd
            )
            
            if price and price > 0:
                logger.info(f"Odos price for {self.config.symbol}: {price:.4f}")
                await self._update_price(PriceSource.CUSTOM, price)
            else:
                logger.warning(f"Failed to get Odos price for {self.config.symbol}")
                # Still update heartbeat to prevent reconnection attempts
                self._source_last_update[PriceSource.CUSTOM] = time.time()
                
        except Exception as e:
            logger.error(f"Error fetching Odos price: {e}")
            self._record_source_failure(PriceSource.CUSTOM)
    
    async def _update_price(self, source: PriceSource, price: float):
        """Update price from a specific source with validation"""
        try:
            # Validate price if enabled and validator exists
            if self.config.enable_validation and self.validator:
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
                        if self.circuit_breaker:
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
            
            # Update heartbeat timestamp
            self._source_last_update[source] = time.time()
            
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
                
                # Update validator reference price if validator exists
                if self.validator:
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
    
    def _calculate_funding_adjusted_price(self, perps_price: float) -> float:
        """
        Calculate funding-adjusted price for perpetual contracts.
        
        The funding premium is calculated as:
        funding_premium = funding_rate * time_to_funding / funding_interval
        
        We subtract the funding premium from the perps price to get the adjusted price.
        """
        if self._funding_rate == 0 or self._next_funding_time == 0:
            return perps_price
        
        current_time = time.time()
        time_to_funding = max(0, self._next_funding_time - current_time)
        
        # Funding happens every 8 hours (28800 seconds)
        funding_interval = 8 * 60 * 60  # 8 hours in seconds
        
        # Calculate the funding premium
        # The funding rate is for 8 hours, so we need to prorate it based on time remaining
        funding_premium = self._funding_rate * (time_to_funding / funding_interval)
        
        # Subtract funding premium from perps price
        # If funding rate is positive, longs pay shorts, so we subtract from perps price
        # If funding rate is negative, shorts pay longs, so we add to perps price (subtracting negative)
        adjusted_price = perps_price * (1 - funding_premium)
        
        logger.debug(f"Funding adjustment: perps={perps_price:.4f}, rate={self._funding_rate:.6f}, "
                    f"time_to_funding={time_to_funding:.0f}s, premium={funding_premium:.6f}, "
                    f"adjusted={adjusted_price:.4f}")
        
        return adjusted_price
    
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
        
        # If both sources are fresh and from same provider, prefer trade over ticker
        # This is handled by the order of processing in _process_bybit_data
        
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
        
        status = {
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
        
        # Add funding rate info if this is a perps feed
        if hasattr(self, '_bybit_subscription_data') and self._bybit_subscription_data.get('is_perps', False):
            status["funding_rate"] = self._funding_rate
            status["next_funding_time"] = self._next_funding_time
            if self._next_funding_time > 0:
                time_to_funding = max(0, self._next_funding_time - time.time())
                status["time_to_funding_seconds"] = time_to_funding
                status["funding_premium"] = self._funding_rate * (time_to_funding / (8 * 60 * 60))
        
        return status
    
    async def _monitor_heartbeat(self):
        """Monitor price feed heartbeat and reconnect if stale"""
        logger.info(f"Starting heartbeat monitor for {self.config.symbol}")
        
        while not self.shutdown.is_shutdown_requested():
            try:
                current_time = time.time()
                
                # Check each active source
                for source in self.config.sources:
                    last_update = self._source_last_update.get(source, 0)
                    time_since_update = current_time - last_update
                    
                    # If we haven't received an update in reconnect_timeout seconds
                    if last_update > 0 and time_since_update > self._reconnect_timeout:
                        logger.warning(f"{source.value} feed stale ({time_since_update:.1f}s since last update), reconnecting...")
                        
                        # Attempt to reconnect based on source
                        try:
                            if source == PriceSource.BINANCE:
                                await self._reconnect_binance()
                            elif source == PriceSource.BYBIT:
                                await self._reconnect_bybit()
                            elif source == PriceSource.CUSTOM:
                                # Custom feed uses polling, so just reset the timestamp
                                self._source_last_update[source] = current_time
                        except Exception as e:
                            logger.error(f"Failed to reconnect {source.value}: {e}")
                            self._record_source_failure(source)
                
                # Check every 5 seconds
                await asyncio.sleep(5.0)
                
            except Exception as e:
                logger.error(f"Error in heartbeat monitor: {e}")
                await asyncio.sleep(5.0)
        
        logger.info(f"Heartbeat monitor stopped for {self.config.symbol}")
    
    async def _reconnect_binance(self):
        """Reconnect Binance WebSocket"""
        logger.info("Reconnecting Binance feed...")
        
        # Close existing client
        if self.binance_client:
            try:
                await self.binance_client.close_connection()
            except:
                pass
            self.binance_client = None
        
        # Reset last update time to prevent immediate reconnect loop
        self._source_last_update[PriceSource.BINANCE] = time.time()
        
        # Restart the feed
        await self._start_binance_feed()
    
    async def _reconnect_bybit(self):
        """Reconnect Bybit WebSocket"""
        logger.info("Reconnecting Bybit feed...")
        
        # Stop existing connection
        ws_name = f"bybit-{self.config.symbol}"
        await self.websocket_manager.stop_connection(ws_name)
        
        # Reset last update time to prevent immediate reconnect loop
        self._source_last_update[PriceSource.BYBIT] = time.time()
        
        # Small delay before reconnecting
        await asyncio.sleep(1.0)
        
        # Restart the feed
        await self._start_bybit_feed()
    
    async def stop(self):
        """Stop price feed"""
        logger.info(f"Stopping price feed for {self.config.symbol}")
        
        # Stop heartbeat monitor
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Stop WebSocket connections
        await self.websocket_manager.stop_all()
        
        # Close Binance client
        if self.binance_client:
            await self.binance_client.close_connection()
        
        # Close Odos API session
        if self.odos_api:
            await self.odos_api.stop()
        
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


# ============================================================================
# Backward Compatibility Layer
# ============================================================================

# Global instance for backward compatibility
_global_price_feed: Optional[EnhancedPriceFeed] = None
_global_shutdown: Optional[ShutdownManager] = None

# Module-level variables for backward compatibility with old price_feeds.py
marketPrice = 0.0
volSpread = 0.0
lastUpdate = 0


async def startPriceFeed(market: str, settings: Dict[str, Any]):
    """Backward compatibility function for starting price feed"""
    global _global_price_feed, _global_shutdown, marketPrice, lastUpdate
    
    logger.info(f"[PRICE_FEEDS] Starting enhanced price feed for {market}")
    
    # Extract base and quote from market string
    import tools
    base = tools.getSymbolFromName(market, 0)
    quote = tools.getSymbolFromName(market, 1)
    
    # Determine price sources based on settings
    sources = []
    primary_source = PriceSource.BINANCE  # Default
    
    if settings.get('useBybitPrice', False):
        sources.append(PriceSource.BYBIT)
        primary_source = PriceSource.BYBIT
    elif settings.get('useCustomPrice', False):
        sources.append(PriceSource.CUSTOM)
        primary_source = PriceSource.CUSTOM
    else:
        sources.append(PriceSource.BINANCE)
    
    # Create configuration (keep underscore format for consistency)
    config = PriceFeedConfig(
        symbol=f"{base}_{quote}",
        sources=sources,
        primary_source=primary_source,
        max_price_age=settings.get('timeout', 30),
        enable_validation=True,
        custom_price_url=settings.get('customPriceUrl', 'http://localhost:3000/prices'),
        reconnect_timeout=settings.get('priceFeedReconnectTimeout', 30)
    )
    
    # Create components
    from safety import PriceBounds, PriceSafetyValidator, CircuitBreaker
    
    bounds = None
    if 'SAFETY_BOUNDS' in settings:
        safety_config = settings['SAFETY_BOUNDS']
        bounds = PriceBounds(
            min_price=safety_config.get('min_price', 0.01),
            max_price=safety_config.get('max_price', 100000),
            max_spread_percent=safety_config.get('max_spread', 0.01)
        )
    
    # Create validator and circuit breaker
    if bounds:
        validator = PriceSafetyValidator(bounds)
    else:
        # Create a validator with permissive default bounds
        default_bounds = PriceBounds(
            min_price=0.00001,
            max_price=1000000,
            max_spread_percent=0.50,  # 50% max spread
            max_price_deviation_percent=0.50,  # 50% max deviation
            stale_price_timeout=60  # 60 seconds
        )
        validator = PriceSafetyValidator(default_bounds)
        logger.warning(f"No safety bounds configured for {market}, using permissive defaults")
    
    circuit_breaker = CircuitBreaker()
    
    # Create shutdown manager
    _global_shutdown = ShutdownManager()
    
    # Create and start price feed
    _global_price_feed = EnhancedPriceFeed(config, _global_shutdown, validator, circuit_breaker)
    await _global_price_feed.start()
    
    # Start background task to update global variables
    asyncio.create_task(_update_globals_loop())
    
    logger.info(f"[PRICE_FEEDS] Enhanced price feed started for {market}")


async def _update_globals_loop():
    """Update global variables for backward compatibility"""
    global marketPrice, lastUpdate, volSpread
    
    while _global_price_feed and contracts.status:
        try:
            price, is_fresh = _global_price_feed.get_current_price()
            if is_fresh and price > 0:
                marketPrice = price
                lastUpdate = time.time()
            
            # Get volatility spread if available
            status = _global_price_feed.get_status()
            volSpread = status.get('volatility_spread', 0.0)
            
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error updating globals: {e}")
            await asyncio.sleep(1)
    
    logger.info("[PRICE_FEEDS] Global update loop exited")