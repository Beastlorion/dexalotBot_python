import sys, os, asyncio, time, ast, aiohttp
import tools, contracts, orders
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
  
  
  async with aiohttp.ClientSession() as s:
    tasks = []
    tasks = [contracts.getDeployments("TradePairs",s),contracts.getDeployments("Portfolio",s),contracts.getDeployments("OrderBooks",s)]
    res = await asyncio.gather(*tasks)
  await aiohttp.ClientSession().close()
    
    
  await contracts.initializeProviders(market)
  await contracts.initializeContracts(market,pairStr)
  await contracts.refreshDexalotNonce()
  await orders.cancelAllOrders(pairStr)
  await asyncio.sleep(6)
  contracts.getBalances(base,quote)
  await asyncio.gather(price_feeds.startPriceFeed(market),contracts.startDataFeeds(pairObj),orderUpdater())
  contracts.status = False
  asincio.sleep(2)

async def orderUpdater():
  levels = []
  lastUpdatePrice = 0
  lastUpdateTime = 0
  for i in settings['levels']:
    level = i
    level['lastUpdatePrice'] = 0
    if level['refreshTolerance'] is None:
      level['refreshTolerance'] = settings['refreshTolerance']
    levels.append(level)
  global activeOrders
  
  while contracts.status:
    marketPrice = price_feeds.getMarketPrice()
    if marketPrice == 0 or contracts.bestAsk is None or contracts.bestBid is None:
      print("waiting for market data")
      await asyncio.sleep(2)
      continue
    levelsToUpdate = 0
    for level in levels:
      if abs(level['lastUpdatePrice'] - marketPrice)/marketPrice > float(level["refreshTolerance"])/100 and int(level['level']) > levelsToUpdate:
        levelsToUpdate = int(level['level'])
    if levelsToUpdate > 0:
      if time.time() - lastUpdateTime < 3 and len(contracts.pendingTransactions) > 0:
        await asyncio.sleep(0.2)
        continue
      else:
        contracts.pendingTransactions = []
        print("\n")
        print("New market price:", marketPrice, time.time())
      if (settings['useCancelReplace']):
        success = await orders.cancelReplaceOrders(base, quote, marketPrice, settings, pairObj, pairStr, pairByte32, levelsToUpdate)
        if success:
          lastUpdateTime = time.time()
          lastUpdatePrice = marketPrice
          for level in levels:
            if level['level'] <= levelsToUpdate:
              level['lastUpdatePrice'] = lastUpdatePrice
          continue
        else:
          await asyncio.sleep(2)
          continue
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
            contracts.getBalances(base, quote),
            orders.getBestOrders()
          )
        except Exception as error:
          print("error in getBalances and getBestOrders calls", error)
          continue
        
        totalBaseFunds = float(contracts.contracts[base]["portfolioTot"])
        totalQuoteFunds = float(contracts.contracts[quote]["portfolioTot"])
        availBaseFunds = float(contracts.contracts[base]["portfolioAvail"])
        availQuoteFunds = float(contracts.contracts[quote]["portfolioAvail"])
        totalFunds = totalBaseFunds * marketPrice + totalQuoteFunds
        
        
        buyOrders = orders.generateBuyOrders(marketPrice,settings,totalQuoteFunds,totalFunds, pairObj, levelsToUpdate, availQuoteFunds)
        sellOrders = orders.generateSellOrders(marketPrice,settings,totalBaseFunds,totalFunds, pairObj, levelsToUpdate, availBaseFunds)
        
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
