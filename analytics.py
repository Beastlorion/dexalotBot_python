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
from typing import Dict, Any, List
from config import config

logger = logging.getLogger(__name__)

# Get market information from command line arguments
market = sys.argv[1] if len(sys.argv) > 1 else None
base = tools.getSymbolFromName(market, 0) if market else None
quote = tools.getSymbolFromName(market, 1) if market else None
pairStr = base + '/' + quote if base and quote else None
settings_config = settings.settings[market] if market else None

async def start():
    """Start analytics mode."""
    logger.info("Starting analytics mode")
    
    try:
        # Get API URLs from config
        api_url = config.get_api_url("mainnet")  # Default to mainnet for analytics
        signed_api_url = config.get("signedApiUrl")
        
        if not signed_api_url:
            logger.error("signedApiUrl not configured")
            return
        
        # Start analytics tasks
        await asyncio.gather(
            _analytics_task(api_url, signed_api_url),
            return_exceptions=True
        )
        
    except Exception as e:
        logger.error(f"Analytics error: {e}")
        raise

async def _analytics_task(api_url: str, signed_api_url: str):
    """Main analytics task."""
    logger.info("Analytics task started")
    
    # Your analytics logic here
    # This is a placeholder - implement your specific analytics needs
    
    while True:
        try:
            # Example: Fetch some analytics data
            async with aiohttp.ClientSession() as session:
                # Add your analytics API calls here
                pass
                
        except Exception as e:
            logger.error(f"Analytics task error: {e}")
            
        await asyncio.sleep(60)  # Update every minute

def runAnalytics(ordersList,startTime):
  try:
    data = {
      'totalCost' : 0,
      'totalSold' : 0,
      'qtyOutstanding' : 0,
      'totalQtyBought':0,
      'totalQtySold':0,
      'totalFees' : 0,
      'buyFills' : 0,
      'sellFills' : 0,
      'totalVolumeBase': 0,
      'totalVolumeQuote': 0
    };
    if len(sys.argv) > 4 and not startTime:
      startDate = int(sys.argv[4])
    elif startTime:
      startDate = startTime
    for order in ordersList:
      if order['id'] == "0xf82ab7d84f27d8d2e7a6b2859b3f7835550e14f0cf10ea2ae00c500000000000" or order['ts'] < startDate:
        continue
      qtyFilled = float(order['quantityfilled'])
      totalAmount = float(order['totalamount'])
      price = float(order['price'])
      if int(order['side']) == 0:
        data['buyFills'] += 1
        data['totalCost'] += totalAmount
        data['totalQtyBought'] += qtyFilled
      else:
        data['sellFills'] += 1
        data['totalSold'] += totalAmount
        data['totalQtySold'] += qtyFilled
      data['totalFees'] += float(order['totalfee'])
      data['totalVolumeBase'] += qtyFilled
      data['totalVolumeQuote'] += totalAmount

    data['qtyOutstanding'] = data['totalQtyBought'] - data['totalQtySold']
    data['avgBuyPrice'] = data['totalCost']/data['totalQtyBought']
    data['avgSellPrice'] = data['totalSold']/data['totalQtySold']
    pprint(data)
  except Exception as err:
    print('err in runAnalytics', err)
    pprint(data)

def getDataFromFiles():
  try:
    directory = 'fillData/'+base.lower()+'_'+quote.lower()+'/'
    # List to store all records
    all_records = []

    # Regex pattern to extract date (YYYYMM) from filenames
    filename_pattern = re.compile(rf"{base.lower()}_[a-z]+_(\d{{6}})\.csv")

    # Iterate through files in the directory
    for filename in os.listdir(directory):
        match = filename_pattern.match(filename)
        if match:
            file_date = match.group(1)  # Extracted YYYYMM string
            file_datetime = datetime.strptime(file_date, "%Y%m")  # Convert to datetime object
            
            file_path = os.path.join(directory, filename)
            print('openFile:', file_path)
            with open(file_path, "r", newline="", encoding="utf-8") as csv_file:
              print('readFile:', file_path)
              reader = csv.DictReader(csv_file)  # Read CSV as dictionary
              for row in reader:
                all_records.append(row)
                    # sys.exit()
                    # all_records.append({
                    #   'type': row[1],
                    #   'type2': row[2],
                    #   'side': row[3], 
                    #   'price': row[4], 
                    #   'quantity': row[5], 
                    #   'totalamount': row[6], 
                    #   'ts': row[7], 
                    #   'quantityfilled': row[8],
                    #   'totalfee': row[9], 
                    #   'cumgas_cost': row[10]
                    # })

    # Sort all records by date
    all_records.sort(key=lambda x: x['ts'])

    startDate = '1'
    if len(sys.argv) > 4:
      startDate = formatted_time = datetime.fromtimestamp(int(sys.argv[4]), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S+00')
    # Print or use the sorted data
    runAnalytics(all_records, startDate)
  except Exception as err:
    print('error in getData from files:', err)
  sys.exit()
