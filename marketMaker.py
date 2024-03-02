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
    contracts.getDeployments("TradePairs"),
    contracts.getDeployments("Portfolio"),
    contracts.getDeployments("OrderBooks"),
  )
  await contracts.initializeProviders(market)
  await contracts.initializeContracts(market,pairStr)
  await contracts.refreshDexalotNonce()
  await price_feeds.startPriceFeed(market)
  await contracts.startBlockFilter()
  await orderUpdater()

async def orderUpdater():
  lastUpdatePrice = 0
  attempts = 0
  global activeOrders
  
  while contracts.status:
    marketPrice = price_feeds.getMarketPrice()
    if marketPrice == 0:
      print("waiting for market data")
      await asyncio.sleep(2)
      continue
    if abs(lastUpdatePrice - marketPrice)/marketPrice > float(settings["refreshTolerance"])/100:
      
      if len(contracts.pendingTransactions) > 0 and attempts < settings["refreshInterval"]:
        attempts = attempts + 1
        await asyncio.sleep(1)
        continue
      else:
        print("New market price:", marketPrice)
        attempts = 0
        contracts.pendingTransactions = []
        print("\n")

      try: 
        await asyncio.gather(
          orders.cancelAllOrders(pairStr),
          portfolio.getBalances(base, quote),
          orders.getBestOrders()
        )
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
          results = await orders.addLimitOrderList(limit_orders, pairObj, pairByte32)
          if not results:
            continue
          lastUpdatePrice = marketPrice
          continue
        except Exception as error:
          print("failed to place orders",error)
          await contracts.refreshDexalotNonce()
          continue
      else:
        print("no orders to place")
    await asyncio.sleep(1)
