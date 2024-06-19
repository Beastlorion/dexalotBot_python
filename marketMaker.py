import sys, os, asyncio, time, ast, aiohttp
import settings, tools, contracts, orders, price_feeds
from dotenv import dotenv_values
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
settings = settings.settings[market]
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
  # print(pairObj)
  async with aiohttp.ClientSession() as s:
    tasks = []
    tasks = [contracts.getDeployments("TradePairs",s),contracts.getDeployments("Portfolio",s),contracts.getDeployments("OrderBooks",s),contracts.getDeployments("PortfolioSubHelper",s)]
    res = await asyncio.gather(*tasks)
  await aiohttp.ClientSession().close()
    
    
  await contracts.initializeProviders(market,settings)
  await contracts.initializeContracts(market,pairObj)
  contracts.getRates(pairObj,pairByte32)
  await contracts.refreshDexalotNonce()
  await orders.cancelAllOrders(pairStr)
  await asyncio.sleep(4)
  
  contracts.getBalances(base,quote,pairObj)
  orders.getBestOrders()
  await asyncio.gather(price_feeds.startPriceFeed(market,settings),contracts.startDataFeeds(pairObj),orderUpdater(base,quote))
  contracts.status = False
  await asyncio.sleep(2)

async def orderUpdater(base,quote):
  levels = []
  lastUpdatePrice = 0
  lastUpdateTime = 0
  failedCount = 0
  count = 0
  resetOrders = False
  startTime = time.time()
  
  for i in settings['levels']:
    level = i
    level['lastUpdatePrice'] = 0
    if level['refreshTolerance'] is None:
      level['refreshTolerance'] = settings['refreshTolerance']
    levels.append(level)
  global activeOrders
  
  while contracts.status and (time.time() - price_feeds.lastUpdate < 30 or price_feeds.lastUpdate == 0):
    marketPrice = price_feeds.marketPrice
    if marketPrice == 0 or contracts.bestAsk is None or contracts.bestBid is None:
      print("waiting for market data")
      await asyncio.sleep(2)
      continue
    if len(contracts.orderIDsToCancel) > 0:
      await orders.cancelOrderList(contracts.orderIDsToCancel)
      await asyncio.sleep(3)
      contracts.orderIDsToCancel = []
    if contracts.refreshActiveOrders:
      await orders.getOpenOrders(pairStr,True)
      contracts.refreshActiveOrders = False
      await asyncio.sleep(3)
      continue
    if contracts.refreshBalances:
      contracts.getBalances(base,quote,pairObj)
    levelsToUpdate = 0
    for level in levels:
      if (abs(level['lastUpdatePrice'] - marketPrice)/marketPrice > float(level["refreshTolerance"])/100 or resetOrders or (settings['pairType'] == "stable" and contracts.retrigger)) and int(level['level']) > levelsToUpdate:
        levelsToUpdate = int(level['level'])
    resetOrders = False
    if levelsToUpdate == 0 and (contracts.retrigger or contracts.refreshOrderLevel):
      levelsToUpdate = 1
    else:
      contracts.retrigger = False
    contracts.refreshOrderLevel= False 
          
    takerBuy = False
    takerSell = False
    if settings['takerEnabled']:
      takerBuy = marketPrice * (1 - settings['takerThreshold']/100) > contracts.bestAsk
      takerSell = marketPrice * (1 + settings['takerThreshold']/100) < contracts.bestBid
    if levelsToUpdate > 0 or takerBuy or takerSell:
      print("New market price:", marketPrice, "volatility spread:",round(price_feeds.volSpread*100,6), 'Run Time:',time.time() - startTime)
      print('BEST BID:', contracts.bestBid, "BEST ASK:", contracts.bestAsk)
      print('cancelReplaceCount:',orders.cancelReplaceCount,'addOrderCount:',orders.addOrderCount,'cancelOrderCount:',orders.cancelOrderCount, 'time:',time.time())
      for order in contracts.activeOrders:
        if order['status'] == 'CANCELED':
          contracts.activeOrders.remove(order)
      success = await orders.cancelReplaceOrders(base, quote, marketPrice, settings, pairObj, pairStr, pairByte32, levelsToUpdate, takerBuy, takerSell)
      if success:
        failedCount = 0
        lastUpdateTime = time.time()
        lastUpdatePrice = marketPrice
        for level in levels:
          if level['level'] <= levelsToUpdate:
            level['lastUpdatePrice'] = lastUpdatePrice
        print("\n")
        continue
      else:
        contracts.pendingTransactions = []
        failedCount = failedCount + 1
        if failedCount >= 2:
          contracts.reconnect = True
          await orders.cancelAllOrders(pairStr)
          resetOrders = True
        if failedCount > 5:
          print('5 failed transactions. Cancel all orders and shutdown')
          await orders.cancelAllOrders(pairStr)
          contracts.status = False
        contracts.refreshBalances = True
        # contracts.refreshActiveOrders = True
        await contracts.refreshDexalotNonce()
        print("\n")
        continue 
    await asyncio.sleep(1)
  contracts.status = False