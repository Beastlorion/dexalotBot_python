import sys, os, asyncio, time, ast, json
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen

def getSymbolFromName(market,position):
  return market.split('_')[position]

def getKey(d,v):
  for key,item in d.items():
    if item == v:
      return key

async def callback(response):
  # print(f"Received response: {response}")
  return response

async def placeOrdersCallback(response):
  print(f"Received response: {response}")
  return response

async def getPairObj(pair, apiUrl):
  pairs = json.loads(urlopen(apiUrl + "pairs").read())
  for item in pairs:
    if item["pair"] == pair:
      return item

def getIncrement(quoteDisplayDecimals):
  increment = '0.'
  for i in range(quoteDisplayDecimals):
    if i < quoteDisplayDecimals - 1:
      increment += '0'
    else:
      increment += '1'
  return float(increment);
    
def getSpread(marketPrice,priceChange,settings,funds,totalFunds,level,side):
  slip = 0
  defensiveSkew = 0
  if side == 1:
    funds = funds * marketPrice
  if priceChange > settings["refreshTolerance"] * 2:
    slip = (priceChange/2)
  if (funds > totalFunds/2):
    multiple = ((funds/totalFunds) - .5) * 20
    defensiveSkew = multiple * settings["defensiveSkew"];
  spread = slip/100 + defensiveSkew/100 + level["spread"]/100
  return spread

def getQty(price, side, level, availableFunds,pairObj):
  if side == 0:
    # print("AVAILABLE FUNDS IN QUOTE BID: ",availableFunds, "AMOUNT: ",level["qty"])
    if level["qty"] < availableFunds/price:
      return level["qty"]
    elif availableFunds > float(pairObj["mintrade_amnt"]):
      return availableFunds/price - pow(10,-1 *pairObj["quotedisplaydecimals"])
    else: 
      return 0
  elif side == 1:
    # print("AVAILABLE FUNDS ASK: ",availableFunds, "AMOUNT: ",level["qty"])
    if level["qty"] < availableFunds:
        return level["qty"]
    elif availableFunds * price > float(pairObj["mintrade_amnt"]):
      return availableFunds - pow(10,-1 *pairObj["quotedisplaydecimals"])
    else:
      return 0
  else: 
    return 0
  