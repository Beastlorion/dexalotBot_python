#!/usr/bin/env python3
"""
Price feed management for Dexalot Bot.
Handles real-time price data from various sources including Binance, Bybit, and custom feeds.
"""

import time
import asyncio
import aiohttp
import json
import logging
from typing import Dict, Any, Optional
import urllib.request

from binance import AsyncClient, BinanceSocketManager
from pybit.unified_trading import WebSocket
import tools
import contracts

logger = logging.getLogger(__name__)

class PriceFeedManager:
    """Manages price feeds from multiple sources."""
    
    def __init__(self):
        # Price data
        self.market_price = 0
        self.eth_usdt_price = 0
        self.vol_spread = 0
        self.usdt_usd = 0
        self.usdc_usdt = 0
        
        # Order book data
        self.bybit_bids = []
        self.bybit_asks = []
        
        # Timestamps
        self.last_update = 0
        self.last_update_eth = 0
        
        # Configuration
        self.global_base = None
        self.market_settings = None
        
        # API credentials
        self.api_key = ''
        self.api_secret = ''
    
    async def start_price_feed(self, market: str, settings: Dict[str, Any]):
        """Start price feed for the specified market."""
        try:
            self.market_settings = settings
            base = tools.getSymbolFromName(market, 0)
            quote = tools.getSymbolFromName(market, 1)
            self.global_base = base
            
            logger.info(f"Starting price feed for {base}/{quote}")
            
            # Start various price feeds based on configuration
            tasks = []
            
            if settings.get('useVolSpread', False):
                tasks.append(self._get_vol_spread(base, quote))
            
            if settings.get('useCustomPrice', False):
                tasks.append(self._get_custom_price(base, quote))
            elif not settings.get('useBybitPrice', False):
                tasks.extend(await self._setup_binance_feeds(base, quote))
            
            if settings.get('useBybitPrice', False):
                tasks.extend(self._setup_bybit_feeds(base, quote))
            
            # Start all price feed tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                
        except Exception as e:
            logger.error(f"Failed to start price feed: {e}")
            raise
    
    async def _setup_binance_feeds(self, base: str, quote: str) -> list:
        """Setup Binance price feeds."""
        tasks = []
        
        try:
            client = await AsyncClient.create()
            bm = BinanceSocketManager(client)
            
            if quote == "USDC" and base not in ["EUROC", "USDT"]:
                tasks.append(self._start_binance_ticker(client, bm, base, quote))
                tasks.append(self._usdc_usdt_ticker(client, bm, base, quote))
            elif quote == "USDT":
                tasks.append(self._start_binance_ticker(client, bm, base, quote))
            elif base == "USDT" and quote == "USDC":
                tasks.append(self._usdc_usdt_ticker(client, bm, base, quote))
            elif base == "sAVAX":
                tasks.append(self._savax_feed())
                
        except Exception as e:
            logger.error(f"Failed to setup Binance feeds: {e}")
            
        return tasks
    
    def _setup_bybit_feeds(self, base: str, quote: str) -> list:
        """Setup Bybit price feeds."""
        tasks = []
        
        if quote == "USDC":
            tasks.append(self._bybit_feed('USDC', 'USDT'))
        
        if base not in ["USDT", "EURC"]:
            tasks.append(self._bybit_feed(base, quote))
            
        return tasks
    
    async def _start_binance_ticker(self, client, bm, base: str, quote: str):
        """Start Binance ticker stream."""
        symbol = base + quote
        
        if quote == "USDC":
            symbol = base + 'USDT'
        if base == 'WBTC':
            base = 'BTC'
            symbol = base + "USDT"
        
        logger.info(f"Starting Binance ticker: {symbol}")
        
        try:
            ts = bm.depth_socket(symbol, 5, 100)
            
            async with ts as tscm:
                while contracts.status:
                    try:
                        res = await asyncio.wait_for(tscm.recv(), timeout=1.0)
                        binance_price = (float(res["bids"][0][0]) + float(res['asks'][0][0])) / 2
                        
                        await self._process_binance_price(binance_price, symbol, quote, base)
                        
                    except asyncio.TimeoutError:
                        # Timeout is normal - just check if we should continue
                        if not contracts.status:
                            break
                        continue
                    except Exception as e:
                        logger.error(f"Error processing Binance data: {e}")
                        await asyncio.sleep(1)
                        
        except Exception as e:
            logger.error(f"Binance ticker error: {e}")
        finally:
            await client.close_connection()
    
    async def _process_binance_price(self, binance_price: float, symbol: str, quote: str, base: str):
        """Process Binance price update."""
        if quote == "USDC" and self.usdc_usdt:
            self.market_price = binance_price / self.usdc_usdt
            self.last_update = time.time()
        elif symbol == 'ETHUSDT' and self.global_base == "WBTC":
            self.eth_usdt_price = binance_price
            self.last_update_eth = time.time()
        elif symbol == 'BTCUSDT' and self.global_base == 'WBTC' and self.eth_usdt_price:
            self.market_price = binance_price / self.eth_usdt_price
            self.last_update = time.time()
        elif quote == 'USDT' and self.global_base != 'WBTC':
            self.market_price = binance_price
            self.last_update = time.time()
    
    async def _usdc_usdt_ticker(self, client, bm, base: str, quote: str):
        """Handle USDC/USDT ticker."""
        symbol = 'USDCUSDT'
        
        try:
            ts = bm.depth_socket(symbol, 5, 100)
            
            async with ts as tscm:
                while contracts.status:
                    try:
                        res = await asyncio.wait_for(tscm.recv(), timeout=1.0)
                        self.usdc_usdt = (float(res["bids"][0][0]) + float(res['asks'][0][0])) / 2
                        
                        if base == "USDT" and quote == "USDC":
                            self.market_price = 1 / self.usdc_usdt
                            self.last_update = time.time()
                            
                    except asyncio.TimeoutError:
                        # Timeout is normal - just check if we should continue
                        if not contracts.status:
                            break
                        continue
                    except Exception as e:
                        logger.error(f"Error processing USDC/USDT data: {e}")
                        await asyncio.sleep(1)
                        
        except Exception as e:
            logger.error(f"USDC/USDT ticker error: {e}")
        finally:
            await client.close_connection()
    
    async def _savax_feed(self):
        """Handle sAVAX price feed."""
        logger.info("Starting sAVAX feed")
        
        while contracts.status:
            try:
                if "sAVAX" in contracts.contracts and "proxy" in contracts.contracts["sAVAX"]:
                    shares_to_avax = contracts.contracts["sAVAX"]["proxy"].functions.getPooledAvaxByShares(1000000).call()
                    self.market_price = float(shares_to_avax / 1000000)
                    self.last_update = time.time()
                else:
                    logger.warning("sAVAX contract not available")
                    
            except Exception as e:
                logger.error(f"Error in sAVAX feed: {e}")
                
            await asyncio.sleep(5)
    
    async def _get_custom_price(self, base: str, quote: str):
        """Get prices from custom price server."""
        logger.info("Starting custom price feed")
        
        async with aiohttp.ClientSession() as session:
            url = 'http://localhost:3000/prices'
            
            while contracts.status:
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            response.raise_for_status()
                            
                        prices_data = await response.json()
                        
                        if quote == "AVAX":
                            self.market_price = prices_data[f"{base}-AVAX"]
                            self.last_update = time.time()
                        elif base == "EURC" and self.usdc_usdt:
                            self.market_price = prices_data['EURC-USD'] / self.usdc_usdt
                        elif quote == "USDC":
                            self.market_price = prices_data[f"{base}-USDC"]
                            self.last_update = time.time()
                            
                except Exception as e:
                    logger.error(f"Error in custom price feed: {e}")
                    
                await asyncio.sleep(0.1)
    
    async def _get_vol_spread(self, base: str, quote: str):
        """Get volatility spread data."""
        if base == 'WBTC':
            base = 'BTC'
            
        logger.info(f"Starting volatility spread feed for {base}")
        
        async with aiohttp.ClientSession() as session:
            url = 'http://localhost:3000/spreads'
            
            while contracts.status:
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            response.raise_for_status()
                            
                        spreads_data = await response.json()
                        self.vol_spread = spreads_data[f"{base}-USD"]
                        
                except Exception as e:
                    logger.error(f"Error in volatility spread feed: {e}")
                    
                await asyncio.sleep(1)
    
    async def _bybit_feed(self, base: str, quote: str):
        """Handle Bybit price feed."""
        logger.info(f"Starting Bybit feed for {base}/{quote}")
        
        try:
            # Determine channel type based on settings
            channel_type = "linear" if self.market_settings.get('perps', False) else "spot"
            
            ws = WebSocket(
                testnet=False,
                channel_type=channel_type,
                ping_interval=10,
                ping_timeout=5,
                retries=0,
                restart_on_error=True
            )
            
            # Adjust symbols for Bybit
            convert = False
            if quote == "USDC":
                quote = "USDT"
                convert = True
            if base == "WBTC":
                base = "BTC"
            
            def handle_orderbook(message):
                try:
                    self._process_bybit_orderbook(message, convert)
                except Exception as e:
                    logger.error(f"Error processing Bybit orderbook: {e}")
            
            # Subscribe to orderbook
            symbol = f"{base}{quote}"
            ws.orderbook_stream(depth=1, symbol=symbol, callback=handle_orderbook)
            
        except Exception as e:
            logger.error(f"Bybit feed error: {e}")
    
    def _process_bybit_orderbook(self, message: Dict, convert: bool):
        """Process Bybit orderbook data."""
        try:
            if message.get('topic', '').startswith('orderbook') and 'data' in message:
                data = message['data']
                
                if 'b' in data and 'a' in data and data['b'] and data['a']:
                    bid_price = float(data['b'][0][0])
                    ask_price = float(data['a'][0][0])
                    
                    mid_price = (bid_price + ask_price) / 2
                    
                    if convert and self.usdc_usdt > 0:
                        self.market_price = mid_price / self.usdc_usdt
                    else:
                        self.market_price = mid_price
                        
                    self.last_update = time.time()
                    
        except Exception as e:
            logger.error(f"Error processing Bybit orderbook: {e}")
    
    def get_market_price(self) -> float:
        """Get current market price."""
        return self.market_price
    
    def get_vol_price(self) -> float:
        """Get volatility-adjusted price."""
        return self.market_price

# Global instance for backward compatibility
_price_feed_manager = PriceFeedManager()

# Module-level variables for backward compatibility
marketPrice = 0
ethUsdtPrice = 0
volSpread = 0
bybitBids = []
bybitAsks = []
lastUpdate = 0
lastUpdateEth = 0

def update_globals():
    """Update global variables from manager instance."""
    global marketPrice, ethUsdtPrice, volSpread, lastUpdate, lastUpdateEth
    marketPrice = _price_feed_manager.market_price
    ethUsdtPrice = _price_feed_manager.eth_usdt_price
    volSpread = _price_feed_manager.vol_spread
    lastUpdate = _price_feed_manager.last_update
    lastUpdateEth = _price_feed_manager.last_update_eth

async def startPriceFeed(market: str, settings: Dict[str, Any]):
    """Legacy function for backward compatibility."""
    await _price_feed_manager.start_price_feed(market, settings)
    
    # Update global variables periodically
    async def update_loop():
        while contracts.status:
            update_globals()
            await asyncio.sleep(0.1)
    
    asyncio.create_task(update_loop())

def getMarketPrice() -> float:
    """Legacy function for backward compatibility."""
    return _price_feed_manager.get_market_price()

def getVolPrice() -> float:
    """Legacy function for backward compatibility."""
    return _price_feed_manager.get_vol_price()
  
  
