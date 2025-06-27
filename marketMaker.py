#!/usr/bin/env python3
"""
Market Maker Bot for Dexalot Exchange.
Handles order placement, updates, and market making logic.
"""

import sys
import asyncio
import time
import random
import logging
from decimal import Decimal
from hexbytes import HexBytes
from typing import Dict, List, Any, Optional

import aiohttp
import settings
import tools
import contracts
import orders
import price_feeds
from config import config

logger = logging.getLogger(__name__)

class MarketMaker:
    """Main market maker class with improved structure."""
    
    def __init__(self, market: str, network: str = 'm', shutdown_event: asyncio.Event = None):
        self.market = market
        self.network = network
        self.testnet = network == 'fuji'
        self.shutdown_event = shutdown_event or asyncio.Event()
        
        # Market data
        self.pair_obj: Optional[Dict] = None
        self.active_orders: List = []
        self.base = tools.getSymbolFromName(market, 0)
        self.quote = tools.getSymbolFromName(market, 1)
        self.pair_str = f"{self.base}/{self.quote}"
        self.pair_byte32 = HexBytes(self.pair_str.encode('utf-8'))
        
        # Settings
        self.response_time = settings.settings['responseTime']
        self.market_settings = settings.settings[market]
        
        # State tracking
        self.start_time = time.time()
        self.last_update_price = 0
        self.last_update_time = 0
        
        # Failure handling
        self.consecutive_failures = 0
        self.max_consecutive_failures = 3
        self.shutdown_requested = False
        
        logger.info(f"MarketMaker initialized for {self.pair_str} on {'testnet' if self.testnet else 'mainnet'}")
    
    def request_shutdown(self):
        """Request graceful shutdown."""
        self.shutdown_requested = True
        contracts.status = False
        self.shutdown_event.set()
        logger.info("Shutdown requested")
    
    async def initialize(self):
        """Initialize market maker components."""
        try:
            logger.info("Initializing market maker...")
            
            # Get pair object
            api_url = config.get_api_url(self.network)
            self.pair_obj = await tools.getPairObj(self.pair_str, api_url)
            
            if not self.pair_obj:
                raise ValueError("Failed to get pair object")
            
            # Initialize contracts and providers
            await self._initialize_contracts()
            await self._initialize_providers()
            
            # Setup initial state
            contracts.getRates(self.pair_obj, self.pair_byte32)
            await contracts.refreshDexalotNonce()
            await orders.cancelAllOrders(self.pair_str)
            await asyncio.sleep(2)
            
            contracts.getBalances(self.base, self.quote, self.pair_obj)
            orders.getBestOrders()
            
            logger.info("Market maker initialization complete")
            
        except Exception as e:
            logger.error(f"Failed to initialize market maker: {e}")
            raise
    
    async def _initialize_contracts(self):
        """Initialize contract deployments."""
        async with aiohttp.ClientSession() as session:
            tasks = [
                contracts.getDeployments("TradePairs", session, self.testnet),
                contracts.getDeployments("Portfolio", session, self.testnet),
                contracts.getDeployments("OrderBooks", session, self.testnet),
                contracts.getDeployments("PortfolioSubHelper", session, self.testnet)
            ]
            await asyncio.gather(*tasks)
    
    async def _initialize_providers(self):
        """Initialize blockchain providers."""
        await contracts.initializeProviders(self.market, self.market_settings, self.testnet, self.base)
        await contracts.initializeContracts(self.market, self.pair_obj, self.testnet)
    
    def _create_levels(self) -> List[Dict]:
        """Create trading levels configuration."""
        levels = []
        
        # Add configured levels
        for level_config in self.market_settings['levels']:
            level = level_config.copy()
            level['lastUpdatePrice'] = 0
            if level.get('refreshTolerance') is None:
                level['refreshTolerance'] = self.market_settings['refreshTolerance']
            levels.append(level)
        
        # Add filler orders if configured
        if 'fillerOrders' in self.market_settings:
            for i in range(self.market_settings['fillerOrders']):
                level_num = len(levels) + 1
                random_factor = random.uniform(0.5, 1)
                qty = random_factor * levels[0]['qty'] * 0.1
                
                if 'fillerSize' in self.market_settings:
                    qty *= self.market_settings['fillerSize']
                
                spread = levels[-1]['spread'] + random_factor
                
                levels.append({
                    "level": level_num,
                    "spread": spread,
                    "qty": qty,
                    "refreshTolerance": spread * 0.9,
                    "lastUpdatePrice": 0
                })
        
        logger.info(f"Created {len(levels)} trading levels: {levels}")
        return levels
    
    async def run_order_updater(self):
        """Main order update loop with improved failure handling."""
        levels = self._create_levels()
        reset_orders = False
        last_priority_gwei = 0
        
        timeout = self.market_settings.get('timeout', 30)
        
        logger.info('Starting order updater')
        
        while not self.shutdown_requested and contracts.status and not self.shutdown_event.is_set():
            try:
                # Check data freshness
                if not self._is_data_fresh(timeout):
                    logger.warning("Market data is stale, waiting...")
                    # Use shutdown event for more responsive shutdown
                    try:
                        await asyncio.wait_for(self.shutdown_event.wait(), timeout=1.0)
                        break  # Shutdown event was set
                    except asyncio.TimeoutError:
                        continue
                
                market_price = self._get_adjusted_market_price()
                
                if not self._is_market_data_ready(market_price):
                    logger.info("Waiting for market data...")
                    # Use shutdown event for more responsive shutdown
                    try:
                        await asyncio.wait_for(self.shutdown_event.wait(), timeout=2.0)
                        break  # Shutdown event was set
                    except asyncio.TimeoutError:
                        continue
                
                # Handle pending operations
                await self._handle_pending_operations()
                
                # Check for shutdown after each major operation
                if self.shutdown_requested or not contracts.status or self.shutdown_event.is_set():
                    break
                
                # Determine what needs updating
                levels_to_update, priority_gwei = self._calculate_updates(
                    levels, market_price, reset_orders, last_priority_gwei
                )
                
                # Handle taker opportunities
                taker_buy, taker_sell = self._check_taker_opportunities(market_price)
                
                # Execute updates if needed
                if levels_to_update > 0 or taker_buy or taker_sell:
                    success = await self._execute_order_updates(
                        market_price, levels, levels_to_update, taker_buy, taker_sell, priority_gwei
                    )
                    
                    if success:
                        self._handle_successful_update(levels, levels_to_update, market_price)
                        self.consecutive_failures = 0  # Reset failure counter on success
                        last_priority_gwei = 0
                        reset_orders = False
                    else:
                        reset_orders = await self._handle_failed_update(last_priority_gwei, priority_gwei)
                        last_priority_gwei = priority_gwei
                
                # Use shutdown event for more responsive shutdown
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=1.0)
                    break  # Shutdown event was set
                except asyncio.TimeoutError:
                    pass  # Continue with next iteration
                
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt received in order updater, initiating graceful shutdown")
                self.request_shutdown()
                break
                
            except Exception as e:
                logger.error(f"Error in order updater: {e}")
                self.consecutive_failures += 1
                
                if self.consecutive_failures >= self.max_consecutive_failures:
                    logger.error(f"Too many consecutive failures ({self.consecutive_failures}), shutting down")
                    self.request_shutdown()
                    break
                
                # Use shutdown event for more responsive shutdown
                try:
                    await asyncio.wait_for(self.shutdown_event.wait(), timeout=1.0)
                    break  # Shutdown event was set
                except asyncio.TimeoutError:
                    pass  # Continue with next iteration
        
        logger.info("Order updater stopped")
    
    def _is_data_fresh(self, timeout: int) -> bool:
        """Check if price data is fresh enough."""
        time_since_update = time.time() - price_feeds.lastUpdate
        time_since_eth_update = time.time() - price_feeds.lastUpdateEth
        
        price_fresh = time_since_update < timeout or price_feeds.lastUpdate == 0
        eth_fresh = time_since_eth_update < timeout or price_feeds.lastUpdateEth == 0 or self.base != "WBTC"
        
        return price_fresh and eth_fresh
    
    def _get_adjusted_market_price(self) -> float:
        """Get market price with any configured adjustments."""
        market_price = price_feeds.marketPrice
        
        if 'priceAdjust' in self.market_settings and self.market_settings['priceAdjust'] != 0:
            adjustment = 1 + float(self.market_settings['priceAdjust'] / 100)
            market_price *= adjustment
        
        return market_price
    
    def _is_market_data_ready(self, market_price: float) -> bool:
        """Check if all required market data is available."""
        return (market_price > 0 and 
                contracts.bestAsk is not None and 
                contracts.bestBid is not None)
    
    async def _handle_pending_operations(self):
        """Handle any pending contract operations."""
        if len(contracts.orderIDsToCancel) > 0:
            await orders.cancelOrderList(contracts.orderIDsToCancel, 1)
            await asyncio.sleep(2)
            contracts.orderIDsToCancel = []
        
        if contracts.refreshActiveOrders:
            await orders.getOpenOrders(self.pair_str, True)
            contracts.refreshActiveOrders = False
            await asyncio.sleep(2)
        
        if contracts.refreshBalances:
            contracts.getBalances(self.base, self.quote, self.pair_obj)
    
    def _calculate_updates(self, levels: List[Dict], market_price: float, 
                          reset_orders: bool, last_priority_gwei: float) -> tuple:
        """Calculate which levels need updating and priority gas."""
        levels_to_update = 0
        priority_gwei = 0
        
        for level in levels:
            price_change = abs(level['lastUpdatePrice'] - market_price) / market_price
            tolerance = float(level["refreshTolerance"]) / 100
            
            needs_update = (price_change > tolerance or 
                          reset_orders or 
                          (self.market_settings['pairType'] == "stable" and contracts.retrigger))
            
            if needs_update and int(level['level']) > levels_to_update:
                levels_to_update = int(level['level'])
                
                # Calculate priority gas for urgent updates
                if level['level'] == 1 and self.last_update_price != 0:
                    threshold = tolerance + self.market_settings['priorityGweiThreshold'] / 100
                    if price_change > threshold:
                        urgency = price_change - threshold
                        new_priority = urgency * self.market_settings['priorityGwei']
                        priority_gwei = max(priority_gwei, round(new_priority, 2))
        
        # Handle failed transaction escalation
        if self.consecutive_failures > 0 and priority_gwei < last_priority_gwei * 1.2:
            priority_gwei = last_priority_gwei * 1.2
        
        priority_gwei = min(priority_gwei, 100)  # Cap at 100
        
        # Handle retrigger conditions
        if levels_to_update == 0 and (contracts.retrigger or contracts.refreshOrderLevel):
            levels_to_update = 1
        
        contracts.retrigger = False
        contracts.refreshOrderLevel = False
        
        return levels_to_update, priority_gwei
    
    def _check_taker_opportunities(self, market_price: float) -> tuple:
        """Check for taker trading opportunities."""
        if not self.market_settings.get('takerEnabled', False):
            return False, False
        
        threshold = self.market_settings['takerThreshold'] / 100
        
        taker_buy = (contracts.bestAsk > 0 and 
                    market_price * (1 - threshold) > contracts.bestAsk)
        taker_sell = market_price * (1 + threshold) < contracts.bestBid
        
        return taker_buy, taker_sell
    
    async def _execute_order_updates(self, market_price: float, levels: List[Dict], 
                                   levels_to_update: int, taker_buy: bool, 
                                   taker_sell: bool, priority_gwei: float) -> bool:
        """Execute order updates."""
        logger.info(f"Market price: {market_price}, volatility spread: {round(price_feeds.volSpread*100, 6)}")
        logger.info(f"Best bid: {contracts.bestBid}, best ask: {contracts.bestAsk}")
        logger.info(f"Runtime: {time.time() - self.start_time:.1f}s")
        
        if priority_gwei > 0:
            logger.info(f"Priority gas: {priority_gwei}")
        
        # Clean up canceled orders
        contracts.activeOrders = [order for order in contracts.activeOrders 
                                if order['status'] != 'CANCELED']
        
        # Execute the order update
        success = await orders.cancelReplaceOrders(
            self.base, self.quote, market_price, self.market_settings,
            self.response_time, self.pair_obj, self.pair_str, self.pair_byte32,
            levels, levels_to_update, taker_buy, taker_sell, priority_gwei
        )
        
        return success
    
    def _handle_successful_update(self, levels: List[Dict], levels_to_update: int, 
                                 market_price: float):
        """Handle successful order update."""
        self.last_update_time = time.time()
        self.last_update_price = market_price
        
        for level in levels:
            if level['level'] <= levels_to_update:
                level['lastUpdatePrice'] = self.last_update_price
        
        # logger.info("Order update successful\n")
    
    async def _handle_failed_update(self, last_priority_gwei: float, 
                                  priority_gwei: float) -> bool:
        """Handle failed order update."""
        self.consecutive_failures += 1
        contracts.pendingTransactions = []
        
        logger.warning(f"Order update failed (attempt {self.consecutive_failures}/{self.max_consecutive_failures})")
        
        if self.consecutive_failures >= self.max_consecutive_failures:
            logger.error("Maximum consecutive failures reached, shutting down")
            self.request_shutdown()
            return False
        
        if self.consecutive_failures >= 1:
            logger.info("Reinitializing providers and contracts...")
            try:
                await self._initialize_providers()
                contracts.reconnect = True
                await contracts.refreshDexalotNonce()
                await orders.cancelAllOrders(self.pair_str)
                await asyncio.sleep(2)
                contracts.refreshBalances = True
                return True  # reset_orders = True
            except Exception as e:
                logger.error(f"Failed to reinitialize: {e}")
                self.consecutive_failures += 1
                if self.consecutive_failures >= self.max_consecutive_failures:
                    self.request_shutdown()
                return False
        
        logger.info("Failed update handled\n")
        return False
    
    async def run(self):
        """Main run method."""
        try:
            await self.initialize()
            
            # Start concurrent tasks
            await asyncio.gather(
                price_feeds.startPriceFeed(self.market, self.market_settings),
                contracts.startDataFeeds(self.pair_obj, self.testnet),
                self.run_order_updater()
            )
            
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received in market maker, initiating graceful shutdown")
            self.request_shutdown()
            
        except Exception as e:
            logger.error(f"Market maker error: {e}")
            self.consecutive_failures += 1
            
            if self.consecutive_failures >= self.max_consecutive_failures:
                logger.error("Maximum consecutive failures reached, shutting down")
                self.request_shutdown()
            else:
                raise  # Re-raise to let main.py handle restart
        finally:
            if not self.shutdown_requested:
                self.request_shutdown()
            await asyncio.sleep(2)

# Module-level variables for backward compatibility
pairObj = None
activeOrders = []
market = sys.argv[1] if len(sys.argv) > 1 else None
pairStr = None
market_maker_instance = None  # Global instance for main.py to access

async def start(net: str, shutdown_event: asyncio.Event = None):
    """Legacy start function for backward compatibility."""
    global pairObj, pairStr, market_maker_instance
    
    if not market:
        raise ValueError("Market not specified")
    
    market_maker_instance = MarketMaker(market, net, shutdown_event)
    pairStr = market_maker_instance.pair_str
    pairObj = market_maker_instance.pair_obj
    
    await market_maker_instance.run()
