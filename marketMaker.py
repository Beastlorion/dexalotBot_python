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
  strikes = 0
  count = 0
  for i in settings['levels']:
    level = i
    level['lastUpdatePrice'] = 0
    if level['refreshTolerance'] is None:
      level['refreshTolerance'] = settings['refreshTolerance']
    levels.append(level)
  global activeOrders
  
  while contracts.status:
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
      if (abs(level['lastUpdatePrice'] - marketPrice)/marketPrice > float(level["refreshTolerance"])/100 and int(level['level']) > levelsToUpdate) or (contracts.retrigger and int(level['level']) == 1):
        levelsToUpdate = int(level['level'])
    taker = False
    if(settings['takerEnabled']):
      taker = price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold']/100) > contracts.bestAsk or price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold']/100) < contracts.bestBid
    if levelsToUpdate > 0 or taker:
      if time.time() - lastUpdateTime < 5 and len(contracts.pendingTransactions) > 0:
        print('waiting on pending transactions')
        await asyncio.sleep(0.2)
        continue
      elif time.time() - lastUpdateTime > 5 and len(contracts.pendingTransactions) > 0:
        await orders.getOpenOrders(pairStr,True)
        await asyncio.sleep(4)
        contracts.pendingTransactions = []
        continue
      else:
        print("New market price:", marketPrice, "volatility spread:",price_feeds.volSpread, time.time())
        print('BEST BID:', contracts.bestBid, "BEST ASK:", contracts.bestAsk)
      if (settings['useCancelReplace']):
        count = count+1
        print('Replace orders count:',count, 'time:',time.time())
        for order in contracts.activeOrders:
          if order['status'] == 'CANCELED':
            contracts.activeOrders.remove(order)
        success = await orders.cancelReplaceOrders(base, quote, marketPrice, settings, pairObj, pairStr, pairByte32, levelsToUpdate, taker, lastUpdatePrice)
        if success:
          strikes = 0
          failedCount = 0
          lastUpdateTime = time.time()
          lastUpdatePrice = marketPrice
          for level in levels:
            if level['level'] <= levelsToUpdate:
              level['lastUpdatePrice'] = lastUpdatePrice
          print("\n")
          continue
        else:
          failedCount = failedCount + 1
          if failedCount > 3:
            print('3 failed transactions. Cancel all orders...')
            strikes = strikes + 1
            if strikes == 3:
              contracts.status = False
              break
            await orders.cancelAllOrders(pairStr)
            await asyncio.sleep(2)
          contracts.refreshBalances = True
          contracts.refreshActiveOrders = True
          await contracts.refreshDexalotNonce()
          print("\n")
          continue 
    await asyncio.sleep(1)
