#!/usr/bin/env python3
"""
Analytics module for Dexalot Bot.
Provides trading analytics and performance monitoring.
"""

import os
import sys
import json
import math
import csv
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen
import tools
import contracts
import settings
import asyncio
from pprint import pprint
import aiohttp
import logging
from typing import Dict, Any, List, Optional, Tuple
from config import config
import time
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)


@dataclass
class AnalyticsConfig:
    """Configuration for analytics run"""
    market: str
    start_time: int
    end_time: int
    base: str
    quote: str
    pair_str: str
    settings_config: Dict[str, Any]


class DexalotAnalytics:
    """Main analytics class for fetching and processing trade data"""
    
    def __init__(self, analytics_config: AnalyticsConfig):
        self.config = analytics_config
        self.session: Optional[aiohttp.ClientSession] = None
        # Use the global config module for API URLs
        from config import config as global_config
        self.api_url = global_config.get_api_url("mainnet")
        self.signed_api_url = global_config.get("signedApiUrl")
        self.fills_data: List[Dict] = []
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def fetch_filled_orders(self) -> List[Dict]:
        """Fetch filled orders from Dexalot API with pagination"""
        # Convert Unix timestamps to ISO format
        from_date = datetime.fromtimestamp(self.config.start_time, tz=timezone.utc).isoformat().replace('+00:00', '.000Z')
        to_date = datetime.fromtimestamp(self.config.end_time, tz=timezone.utc).isoformat().replace('+00:00', '.000Z')
        
        logger.info(f"Fetching filled orders for {self.config.pair_str} from {from_date} to {to_date}")
        
        all_orders = []
        page_no = 1
        items_per_page = 20  # Max items per page
        has_more = True
        
        while has_more:
            try:
                # Construct URL with parameters
                url = (f"{self.signed_api_url}orders?"
                      f"periodfrom={from_date}&"
                      f"periodto={to_date}&"
                      f"itemsperpage={items_per_page}&"
                      f"pageno={page_no}&"
                      f"pair={self.config.pair_str}&"
                      f"category=1")
                
                logger.debug(f"Fetching page {page_no}: {url}")
                
                # Add x-signature header
                headers = {}
                if hasattr(contracts, 'signature') and contracts.signature:
                    headers['x-signature'] = contracts.signature
                
                async with self.session.get(url, headers=headers) as response:
                    if response.status != 200:
                        text = await response.text()
                        logger.error(f"Failed to fetch orders: HTTP {response.status} - {text}")
                        break
                    
                    data = await response.json()
                    
                    # Handle response format: {"count": N, "rows": [...]}
                    if not isinstance(data, dict) or 'rows' not in data:
                        logger.error(f"Unexpected response format: {data}")
                        break
                    
                    orders = data['rows']
                    total_count = int(data.get('count', '0'))
                    
                    all_orders.extend(orders)
                    
                    logger.info(f"Page {page_no}: fetched {len(orders)} orders")
                    
                    # Check if we should continue pagination
                    current_total = len(all_orders)
                    if current_total >= total_count or len(orders) < items_per_page:
                        has_more = False
                    else:
                        page_no += 1
                        
                    # Add a small delay to avoid rate limiting
                    await asyncio.sleep(0.1)
                    
            except Exception as e:
                logger.error(f"Error fetching orders page {page_no}: {e}")
                break
        
        logger.info(f"Fetched {len(all_orders)} filled orders")
        return all_orders
    
    def process_analytics(self, orders: List[Dict]) -> Dict[str, Any]:
        """Process fetched orders to calculate analytics"""
        data = {
            'totalCost': 0,
            'totalSold': 0,
            'qtyOutstanding': 0,
            'totalQtyBought': 0,
            'totalQtySold': 0,
            'totalFees': 0,
            'buyFills': 0,
            'sellFills': 0,
            'totalVolumeBase': 0,
            'totalVolumeQuote': 0,
            'avgBuyPrice': 0,
            'avgSellPrice': 0,
            'pnl': 0,
            'startTime': self.config.start_time,
            'endTime': self.config.end_time,
            'duration_hours': (self.config.end_time - self.config.start_time) / 3600
        }
        
        for order in orders:
            
            # Parse timestamp if needed (already filtered by API)
            # ts format: "2023-02-22T18:29:02.000Z"
            ts_str = order.get('ts', '')
            if ts_str:
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    order_timestamp = ts_dt.timestamp()
                    # Double-check time range
                    if order_timestamp < self.config.start_time or order_timestamp > self.config.end_time:
                        continue
                except:
                    pass
            
            qty_filled = float(order.get('quantityfilled', '0'))
            total_amount = float(order.get('totalamount', '0'))
            price = float(order.get('price', '0'))
            side = int(order.get('side', -1))
            
            # If totalamount is 0, calculate it from price and quantity
            if total_amount == 0 and price > 0 and qty_filled > 0:
                total_amount = price * qty_filled
            
            if side == 0:  # Buy order
                data['buyFills'] += 1
                data['totalCost'] += total_amount
                data['totalQtyBought'] += qty_filled
            elif side == 1:  # Sell order
                data['sellFills'] += 1
                data['totalSold'] += total_amount
                data['totalQtySold'] += qty_filled
            
            data['totalFees'] += float(order.get('totalfee', '0'))
            data['totalVolumeBase'] += qty_filled
            data['totalVolumeQuote'] += total_amount
        
        # Calculate derived metrics
        data['qtyOutstanding'] = data['totalQtyBought'] - data['totalQtySold']
        
        if data['totalQtyBought'] > 0:
            data['avgBuyPrice'] = data['totalCost'] / data['totalQtyBought']
        
        if data['totalQtySold'] > 0:
            data['avgSellPrice'] = data['totalSold'] / data['totalQtySold']
        
        # Calculate PnL
        data['pnl'] = data['totalSold'] - data['totalCost'] - data['totalFees']
        
        # Add performance metrics
        if data['duration_hours'] > 0:
            data['volume_per_hour'] = data['totalVolumeQuote'] / data['duration_hours']
            data['trades_per_hour'] = (data['buyFills'] + data['sellFills']) / data['duration_hours']
        
        return data
    
    def print_analytics_report(self, analytics: Dict[str, Any]):
        """Print formatted analytics report"""
        print("\n" + "=" * 60)
        print(f"ANALYTICS REPORT - {self.config.market}")
        print("=" * 60)
        print(f"Period: {datetime.fromtimestamp(analytics['startTime'])} to {datetime.fromtimestamp(analytics['endTime'])}")
        print(f"Duration: {analytics['duration_hours']:.2f} hours")
        print("\nTRADING SUMMARY:")
        print(f"  Total Buy Fills:  {analytics['buyFills']}")
        print(f"  Total Sell Fills: {analytics['sellFills']}")
        print(f"  Total Fills:      {analytics['buyFills'] + analytics['sellFills']}")
        print("\nVOLUME:")
        print(f"  Base Volume:  {analytics['totalVolumeBase']:.4f} {self.config.base}")
        print(f"  Quote Volume: {analytics['totalVolumeQuote']:.2f} {self.config.quote}")
        print(f"  Volume/Hour:  {analytics.get('volume_per_hour', 0):.2f} {self.config.quote}")
        print("\nPRICES:")
        if analytics['avgBuyPrice'] > 0:
            print(f"  Avg Buy Price:  {analytics['avgBuyPrice']:.4f}")
        else:
            print(f"  Avg Buy Price:  N/A (no buy fills)")
            
        if analytics['avgSellPrice'] > 0:
            print(f"  Avg Sell Price: {analytics['avgSellPrice']:.4f}")
        else:
            print(f"  Avg Sell Price: N/A (no sell fills)")
            
        if analytics['avgBuyPrice'] > 0 and analytics['avgSellPrice'] > 0:
            spread = analytics['avgSellPrice'] - analytics['avgBuyPrice']
            spread_pct = (spread / analytics['avgBuyPrice']) * 100
            print(f"  Spread:         {spread:.4f} ({spread_pct:.2f}%)")
        else:
            print(f"  Spread:         N/A")
        print("\nPOSITION:")
        print(f"  Quantity Bought:      {analytics['totalQtyBought']:.4f} {self.config.base}")
        print(f"  Quantity Sold:        {analytics['totalQtySold']:.4f} {self.config.base}")
        print(f"  Outstanding Position: {analytics['qtyOutstanding']:.4f} {self.config.base}")
        print("\nFINANCIALS:")
        print(f"  Total Cost:   {analytics['totalCost']:.2f} {self.config.quote}")
        print(f"  Total Sold:   {analytics['totalSold']:.2f} {self.config.quote}")
        print(f"  Total Fees:   {analytics['totalFees']:.2f} {self.config.quote}")
        print(f"  Net PnL:      {analytics['pnl']:.2f} {self.config.quote}")
        print("=" * 60 + "\n")
    
    async def run(self):
        """Run the complete analytics process"""
        try:
            # Fetch filled orders
            orders = await self.fetch_filled_orders()
            
            if not orders:
                logger.warning("No filled orders found for the specified period")
                return
            
            # Process analytics
            analytics = self.process_analytics(orders)
            
            # Print report
            self.print_analytics_report(analytics)
            
            # Optionally save to file
            if getattr(self.config, 'save_to_file', False):
                self.save_analytics_to_file(analytics, orders)
            
        except Exception as e:
            logger.error(f"Error running analytics: {e}")
            raise
    
    def save_analytics_to_file(self, analytics: Dict[str, Any], orders: List[Dict]):
        """Save analytics data to CSV file"""
        try:
            # Create directory if it doesn't exist
            directory = f'analytics_reports/{self.config.base.lower()}_{self.config.quote.lower()}/'
            os.makedirs(directory, exist_ok=True)
            
            # Create filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{directory}analytics_{timestamp}.json"
            
            # Save analytics summary
            with open(filename, 'w') as f:
                json.dump({
                    'summary': analytics,
                    'orders': orders
                }, f, indent=2)
            
            logger.info(f"Analytics saved to {filename}")
            
        except Exception as e:
            logger.error(f"Error saving analytics to file: {e}")

async def start():
    """Start analytics mode with command line parameters."""
    logger.info("Starting analytics mode")
    
    try:
        # Parse command line arguments
        # Expected format: python3 main.py AVAX_USDC analytics <start_time> <end_time>
        if len(sys.argv) < 5:
            logger.error("Usage: python3 main.py <MARKET> analytics <start_time> <end_time>")
            logger.error("Example: python3 main.py AVAX_USDC analytics 1752671718 1752673881")
            sys.exit(1)
        
        market = sys.argv[1]
        start_time = int(sys.argv[3])
        end_time = int(sys.argv[4])
        
        # Parse market symbols
        base = tools.getSymbolFromName(market, 0)
        quote = tools.getSymbolFromName(market, 1)
        pair_str = f"{base}/{quote}"
        
        # Get settings for this market
        market_settings = settings.settings.get(market, {})
        
        # Create analytics configuration
        analytics_config = AnalyticsConfig(
            market=market,
            start_time=start_time,
            end_time=end_time,
            base=base,
            quote=quote,
            pair_str=pair_str,
            settings_config=market_settings
        )
        
        logger.info(f"Running analytics for {market} from {datetime.fromtimestamp(start_time)} to {datetime.fromtimestamp(end_time)}")
        
        # Initialize contracts to get token details and signature
        await contracts.initializeProviders(market, market_settings, False, base)
        
        # Ensure we have a signature for API calls
        if not hasattr(contracts, 'signature') or not contracts.signature:
            logger.error("No signature available for API authentication")
            sys.exit(1)
        
        logger.info("Contracts initialized, signature available")
        
        # Run analytics
        async with DexalotAnalytics(analytics_config) as analytics:
            await analytics.run()
        
        logger.info("Analytics completed successfully")
        
    except KeyboardInterrupt:
        logger.info("Analytics interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        sys.exit(1)
    
    # Exit gracefully without going through market maker shutdown
    sys.exit(0)

# Legacy function for reading from CSV files - kept for backward compatibility
def read_historical_csv_data(base: str, quote: str, start_time: Optional[int] = None) -> List[Dict]:
    """Read historical data from CSV files if available"""
    try:
        directory = f'fillData/{base.lower()}_{quote.lower()}/'
        if not os.path.exists(directory):
            logger.info(f"No historical CSV data found at {directory}")
            return []
        
        all_records = []
        filename_pattern = re.compile(rf"{base.lower()}_[a-z]+_(\d{{6}})\.csv")
        
        for filename in os.listdir(directory):
            match = filename_pattern.match(filename)
            if match:
                file_path = os.path.join(directory, filename)
                with open(file_path, "r", newline="", encoding="utf-8") as csv_file:
                    reader = csv.DictReader(csv_file)
                    for row in reader:
                        if start_time and int(row.get('ts', 0)) < start_time:
                            continue
                        all_records.append(row)
        
        all_records.sort(key=lambda x: int(x.get('ts', 0)))
        return all_records
        
    except Exception as e:
        logger.error(f"Error reading CSV files: {e}")
        return []
