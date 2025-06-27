#!/usr/bin/env python3
"""
Utility functions for Dexalot Bot.
Provides helper functions for trading operations.
"""

import sys
import os
import asyncio
import time
import ast
import json
import boto3
import logging
from botocore.exceptions import ClientError
import price_feeds
import contracts
from config import config
import urllib.request
from urllib.request import Request, urlopen
from multiprocessing import Process

logger = logging.getLogger(__name__)

def getSymbolFromName(market: str, position: int) -> str:
    """Extract symbol from market name."""
    return market.split('_')[position]

def getKey(d: dict, v: any) -> any:
    """Get key by value from dictionary."""
    for key, item in d.items():
        if item == v:
            return key
    return None

async def callback(response):
    """Generic callback function."""
    return response

async def placeOrdersCallback(response):
    """Callback for order placement."""
    logger.info(f"Received response: {response}")
    return response

async def getPairObj(pair: str, api_url: str) -> dict:
    """Get pair object from API."""
    try:
        pairs = json.loads(urlopen(api_url + "pairs").read())
        for item in pairs:
            if item["pair"] == pair:
                return item
        return None
    except Exception as e:
        logger.error(f"Error getting pair object: {e}")
        return None

def getIncrement(quote_display_decimals: int) -> float:
    """Calculate price increment."""
    return float(1 * pow(10, -1 * quote_display_decimals))

def getMyOrdersSorted():
    """Sort active orders by side and price."""
    bids = []
    asks = []
    for order in contracts.activeOrders:
        if order['side'] == 0:
            bids.append(order)
        elif order['side'] == 1:
            asks.append(order)
    sorted_bids = sorted(bids, key=lambda d: d['price'], reverse=True)
    sorted_asks = sorted(asks, key=lambda d: d['price'])
    
    return sorted_bids, sorted_asks

def getSpread(market_price: float, settings: dict, funds: float, total_funds: float, level: dict, side: int) -> float:
    """Calculate spread based on market conditions and settings."""
    defensive_skew = 0
    offensive_skew = 0
    level_spread = 0
    vol_spread = price_feeds.volSpread
    
    if side == 1:
        funds = funds * market_price
        
    if funds < total_funds / 2:
        multiple = ((funds / total_funds) - 0.5) * 20 * -1
        defensive_skew = multiple * settings.get("defensiveSkew", 0)
        
    if 'offensiveSkew' in settings and funds > total_funds / 2:
        multiple = ((funds / total_funds) - 0.5) * 20 * -1
        offensive_skew = multiple * settings["offensiveSkew"]
        
    if level["level"] > 0:
        level_spread = level["spread"] / 100
        
    spread = defensive_skew / 100 + offensive_skew / 100 + level_spread + vol_spread / 2
    return spread

def getQty(price: float, side: int, level: dict, available_funds: float, pair_obj: dict) -> float:
    """Calculate order quantity based on available funds and constraints."""
    if side == 0:  # Buy
        if level["qty"] < available_funds / price:
            return level["qty"]
        elif available_funds > float(pair_obj["mintrade_amnt"]):
            return available_funds / price - pow(10, -1 * pair_obj["basedisplaydecimals"])
        else:
            return 0
    elif side == 1:  # Sell
        if level["qty"] < available_funds:
            return level["qty"]
        elif available_funds * price > float(pair_obj["mintrade_amnt"]):
            return available_funds - pow(10, -1 * pair_obj["basedisplaydecimals"])
        else:
            return 0
    else:
        return 0

def getPrivateKey(market: str, settings: dict) -> str:
    """Get private key from AWS Secrets Manager or environment."""
    secret_name = settings.get('secret_name', '')
    region_name = settings.get('secret_location', '')
    
    if not secret_name or not region_name:
        # Fall back to environment variable
        return config.get_private_key(market)
    
    # Create a Secrets Manager client
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    
    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        logger.error(f"Error getting secret: {e}")
        raise e
    
    secret = ast.literal_eval(get_secret_value_response['SecretString'])
    pk = secret[secret_name]
    if pk[:2] != '0x':
        pk = '0x' + pk
    return pk
