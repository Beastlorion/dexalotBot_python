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
  await orders.cancelAllOrders(pairStr)
  await orderUpdater()

async def orderUpdater():
  levels = []
  lastUpdatePrice = 0
  for i in settings['levels']:
    level = i
    level['lastUpdatePrice'] = 0
    if level['refreshTolerance'] is None:
      level['refreshTolerance'] = settings['refreshTolerance']
    levels.append(level)
  attempts = 0
  global activeOrders
  
  while contracts.status:
    marketPrice = price_feeds.getMarketPrice()
    if marketPrice == 0:
      print("waiting for market data")
      await asyncio.sleep(2)
      continue
    levelsToUpdate = 0
    for level in levels:
      if abs(level['lastUpdatePrice'] - marketPrice)/marketPrice > float(level["refreshTolerance"])/100 and int(level['level']) > levelsToUpdate:
        levelsToUpdate = int(level['level'])
    if levelsToUpdate > 0:
      if len(contracts.pendingTransactions) > 0 and attempts < settings["refreshInterval"]:
        attempts = attempts + 1
        await asyncio.sleep(1)
        continue
      else:
        marketPrice = price_feeds.getMarketPrice()
        print("New market price:", marketPrice)
        attempts = 0
        contracts.pendingTransactions = []
        print("\n")
      priceChange = 0
      if lastUpdatePrice != 0:
        priceChange = (abs(lastUpdatePrice - marketPrice)/lastUpdatePrice)*100
      if (settings['useCancelReplace']):
        orders.cancelReplaceOrders(marketPrice,priceChange,settings,baseFundsAvail,quoteFundsAvail,totalFunds, pairObj, pairStr, levelsToUpdate)
      else:
        try:
          success = await orders.cancelOrderLevels(pairStr, levelsToUpdate)
          if not success:
            continue
        except Exception as error:
          print("error in cancelOrderLevels", error)
          continue
        try: 
          await asyncio.gather(
            portfolio.getBalances(base, quote),
            orders.getBestOrders()
          )
        except Exception as error:
          print("error in getBalances and getBestOrders calls", error)
          continue
        
        baseFunds = float(contracts.contracts[base]["portfolioTot"])
        quoteFunds = float(contracts.contracts[quote]["portfolioTot"])
        totalFunds = baseFunds * marketPrice + quoteFunds
        
        
        buyOrders = orders.generateBuyOrders(marketPrice,priceChange,settings,quoteFunds,totalFunds, pairObj, levelsToUpdate)
        sellOrders = orders.generateSellOrders(marketPrice,priceChange,settings,baseFunds,totalFunds, pairObj, levelsToUpdate)
        
        limit_orders = []
        limit_orders = buyOrders + sellOrders
        
        if len(limit_orders) > 0:
          try:
            results = await orders.addLimitOrderList(limit_orders, pairObj, pairByte32)
            if not results:
              continue
            lastUpdatePrice = marketPrice
            for level in levels:
              if (int(level['level']) <= levelsToUpdate):
                level['lastUpdatePrice'] = marketPrice
            continue
          except Exception as error:
            print("failed to place orders",error)
            await contracts.refreshDexalotNonce()
            continue
        else:
          print("no orders to place")
    await asyncio.sleep(1)
