import sys, os, asyncio, time, ast
import tools, contracts, orders, portfolio
from dotenv import load_dotenv, dotenv_values
from decimal import *
from hexbytes import HexBytes
import price_feeds

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}

pairObj = None
activeOrders = []
market = sys.argv[1]
settings = ast.literal_eval(config[market])
base = tools.getSymbolFromName(market,0)
quote = tools.getSymbolFromName(market,1)
pairStr = base + '/' + quote
pairByte32 = HexBytes(pairStr.encode('utf-8'))

async def start():
  global pairObj
  pairObj = await tools.getPairObj(pairStr,config["apiUrl"]);
  if (pairObj is None):
    print("failed to get pairObj")
    return
    
  await asyncio.gather(
    price_feeds.startPriceFeed(market),
    contracts.getDeployments("TradePairs"),
    contracts.getDeployments("Portfolio"),
    contracts.getDeployments("OrderBooks"),
  )
  await contracts.initializeProviders(market)
  await contracts.initializeContracts(market,pairStr)
  await orderUpdater()

async def orderUpdater():
  lastUpdatePrice = 0
  attempts = 0
  global activeOrders
  
  while True:
    marketPrice = price_feeds.getMarketPrice()
    if marketPrice == 0:
      print("no market data")
      await asyncio.sleep(2)
      continue
    if abs(lastUpdatePrice - marketPrice)/marketPrice > float(settings["refreshTolerance"])/100:
      
      if len(contracts.pendingTransactions) > 0 and attempts < settings["refreshInterval"]:
        attempts = attempts + 1
        continue
      else:
        attempts = 0
        contracts.pendingTransactions = []
        print("\n")

      try: 
        await asyncio.gather(
          contracts.refreshDexalotNonce(),
          portfolio.getBalances(base, quote),
          orders.getBestOrders()
        )
        await orders.cancelAllOrders(pairStr)
      except Exception as error:
        print("error in cancel and get positions calls", error)
        continue
      
      priceChange = 0
      if lastUpdatePrice != 0:
        priceChange = (abs(lastUpdatePrice - marketPrice)/lastUpdatePrice)*100
      
      baseFunds = float(contracts.contracts[base]["portfolioTot"])
      quoteFunds = float(contracts.contracts[quote]["portfolioTot"])
      totalFunds = baseFunds * marketPrice + quoteFunds
      
      
      buyOrders = orders.generateBuyOrders(marketPrice,priceChange,settings,quoteFunds,totalFunds, pairObj)
      sellOrders = orders.generateSellOrders(marketPrice,priceChange,settings,baseFunds,totalFunds, pairObj)
      
      limit_orders = []
      limit_orders = buyOrders + sellOrders
      
      if len(limit_orders) > 0:
        try:
          await orders.addLimitOrderList(limit_orders, pairObj, pairByte32)
          
          lastUpdatePrice = marketPrice
          continue
        except Exception as error:
          print("failed to place orders",error)
          continue
      else:
        print("no orders to place")
    await asyncio.sleep(1)
      

  
  # fundingRate = await client.get_funding_rate(marketID,time.time())
  # fundingRate = fundingRate["fundingRate"]
  # print("last funding rate:", fundingRate)
  
  # nextFundingRate = await client.get_predicted_funding_rate(marketID)
  # nextFundingRate = nextFundingRate["fundingRate"]
  # print("next funding rate:",nextFundingRate)
  
def generateBuyOrders(marketID, midPrice, settings, availableMargin, defensiveSkew, currentSize):
  orders = []
  amountOnOrder = 0
  leverage = float(settings["leverage"])
  for level in settings["orderLevels"]:
    l = settings["orderLevels"][level]
    spread = float(l["spread"])/100 + defensiveSkew
    bidPrice = midPrice * (1 - spread)
    roundedBidPrice = round(bidPrice,get_price_precision(marketID))
    
    amtToTrade = (availableMargin * leverage)/roundedBidPrice
    qty = getQty(l,amtToTrade,marketID)
    reduceOnly = False
    if qty == 0:
      continue
    elif currentSize < 0 and qty * -1 > currentSize + amountOnOrder:
      reduceOnly = True
      amountOnOrder = amountOnOrder + qty
    availableMargin = availableMargin - ((qty * roundedBidPrice)/leverage)
    order = LimitOrder.new(marketID,qty,roundedBidPrice,reduceOnly,True)
    orders.append(order)
  return orders
  
def generateSellOrders(marketID, midPrice, settings, availableMargin, defensiveSkew, currentSize):
  orders = []
  amountOnOrder = 0
  leverage = float(settings["leverage"])
  for level in settings["orderLevels"]:
    l = settings["orderLevels"][level]
    spread = float(l["spread"])/100 + defensiveSkew
    askPrice = midPrice * (1 + spread)
    roundedAskPrice = round(askPrice,get_price_precision(marketID))
    amtToTrade = (availableMargin * leverage)/roundedAskPrice
    qty = getQty(l,amtToTrade,marketID) * -1
    reduceOnly = False
    if qty == 0:
      continue
    elif currentSize > 0 and qty * -1 < currentSize + amountOnOrder:
      reduceOnly = True
      amountOnOrder = amountOnOrder + qty
    availableMargin = availableMargin - ((qty * roundedAskPrice)/leverage)
    order = LimitOrder.new(marketID,qty,roundedAskPrice,reduceOnly,True)
    orders.append(order)
  return orders
      
def getQty(level, amtToTrade,marketID):
  if float(level["qty"]) < amtToTrade:
    return float(level["qty"])
  elif amtToTrade > get_minimum_quantity(marketID):
    return float(Decimal(str(amtToTrade)).quantize(Decimal(str(get_minimum_quantity(marketID))), rounding=ROUND_DOWN))
  else:
    return 0
  
async def handleOrderUpdates(pointer, response):
  if response.EventName == 'OrderMatched':
    print(response)
    print("ORDER FILLED - QTY:",response.Args["fillAmount"], " PRICE:",response.Args["price"])
    