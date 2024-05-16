import time, ast, asyncio, aiohttp, json
import urllib.request
from binance import AsyncClient, BinanceSocketManager
from pybit.unified_trading import WebSocket
import tools
import contracts

api_key = ''
api_secret = ''
usdt = 0
usdcUsdt = 0
marketPrice = 0
volSpread = 0
bybitBids = []
bybitAsks = []

async def startPriceFeed(market,settings):
  base = tools.getSymbolFromName(market,0)
  quote = tools.getSymbolFromName(market,1)
  if settings['useVolSpread']:
    asyncio.create_task(getVolSpread(base,quote))
  if settings['useCustomPrice']:
    asyncio.create_task(getCustomPrice(base,quote))
  else:
    client = await AsyncClient.create()
    bm = BinanceSocketManager(client)
    if quote == "USDC" and base != "EUROC" and base != "USDT" and not settings['useBybitPrice']:
      tickerTask = asyncio.create_task(startTicker(client, bm, base, quote))
      usdc_usdtTickerTask = asyncio.create_task(usdc_usdtTicker(client, bm, base, quote))
    elif quote == "USDT":
      tickerTask = asyncio.create_task(startTicker(client, bm, base, quote))
    elif base == "USDT" and quote == "USDC":
      usdc_usdtTickerTask = asyncio.create_task(usdc_usdtTicker(client, bm, base, quote))
    elif base == "sAVAX":
      savaxTickerTask = asyncio.create_task(savaxFeed())
  if (settings['takerEnabled'] or settings['useBybitPrice']):
    asyncio.create_task(bybitFeed(base, quote, settings['useBybitPrice']))
  
  # usdtUpdaterTask = asyncio.create_task(usdtUpdater())
  
async def usdtUpdater():
  while contracts.status:
    await updateUSDT()
    await asyncio.sleep(1)

async def startTicker(client, bm, base, quote):
  global marketPrice
  symbol = base + 'USDT'

  # start any sockets here, i.e a trade socket
  print("starting ticker:", symbol)
  ts = bm.trade_socket(symbol)
  # then start receiving messages
  async with ts as tscm:
    while contracts.status:
      res = await tscm.recv()
      priceUsdt = float(res["p"])
      if quote == "USDC" and usdcUsdt:
        marketPrice = priceUsdt / usdcUsdt
      elif (quote == "USDT"):
        marketPrice = priceUsdt
  await client.close_connection()
  
async def usdc_usdtTicker(client, bm, base, quote):
  global usdcUsdt,marketPrice
  symbol = 'USDCUSDT'

  # start any sockets here, i.e a trade socket
  ts = bm.trade_socket(symbol)
  # then start receiving messages
  async with ts as tscm:
    while contracts.status:
      res = await tscm.recv()
      usdcUsdt = float(res["p"])
      if (base == "USDT" and quote == "USDC"):
        marketPrice = 1/usdcUsdt
  await client.close_connection()

async def updateUSDT():
  try:
    usdtResult = urllib.request.urlopen("https://api.kraken.com/0/public/Ticker?pair=USDTUSD").read()
    usdtResult = ast.literal_eval(usdtResult.decode('utf-8')) 
    global usdt
    usdt = (float(usdtResult["result"]["USDTZUSD"]["a"][0]) + float(usdtResult["result"]["USDTZUSD"]["b"][0]))/2;
  except:
    print("error getting usdt price")

def getMarketPrice():
  return marketPrice
def getVolPrice():
  return marketPrice

# def getTickerPrice():
async def savaxFeed():
  global marketPrice
  while contracts.status:
    marketPrice = float(contracts.contracts["sAVAX"]["proxy"].functions.getPooledAvaxByShares(1000000).call()/1000000)
    await asyncio.sleep(5)

async def getCustomPrice(base,quote):
  global marketPrice
  async with aiohttp.ClientSession() as s:
    url='http://localhost:3000/prices'
    while contracts.status:
      try:
        async with s.get(url) as r:
          if r.status != 200:
            r.raise_for_status()
          prices = await r.read()
          prices = json.loads(prices.decode('utf-8'))
          if (quote == "AVAX"):
            marketPrice = prices[base + '-AVAX']
          else:
            basePrice = prices[base + '-USD']
            quotePrice = prices[quote + '-USD']
            marketPrice = basePrice/quotePrice
      except Exception as error:
        print("error in getCustomPrice:", error)
      await asyncio.sleep(0.1)
      
async def getVolSpread(base,quote):
  global volSpread
  async with aiohttp.ClientSession() as s:
    url='http://localhost:3000/spreads'
    while contracts.status:
      try:
        async with s.get(url) as r:
          if r.status != 200:
            r.raise_for_status()
          spreads = await r.read()
          spreads = json.loads(spreads.decode('utf-8'))
          volSpread = spreads[base + '-USD']
      except Exception as error:
        print("error in getVolSpread:", error)
      await asyncio.sleep(1)
      
async def bybitFeed (base,quote,useBybitPrice):
  print('starting bybit websockets')
  ws = WebSocket(
    testnet=False,
    channel_type="spot",
  )
  convert = False
  if quote == "USDC":
    quote = "USDT"
    convert = True
    
  def handle_orderbook(message):
    global bybitBids,bybitAsks,marketPrice
    try:
      if message['topic'] == "orderbook.50."+base+quote:
        if time.time() - message['ts'] < 5:
          buildBids = []
          buildAsks = []
          if convert and usdcUsdt != 0:
            for bid in message['data']['b']:
              buildBids.append([float(bid[0])/usdcUsdt,float(bid[1])])
            for ask in message['data']['a']:
              buildAsks.append([float(ask[0])/usdcUsdt,float(ask[1])])
          else:
            for bid in message['data']['b']:
              buildBids.append([float(bid[0]),float(bid[1])])
            for ask in message['data']['a']:
              buildAsks.append([float(ask[0]),float(ask[1])])
          bybitBids = sorted(buildBids, key=lambda tup: tup[0], reverse=True)
          bybitAsks = sorted(buildAsks, key=lambda tup: tup[0])
          if useBybitPrice:
            marketPrice = (bybitBids[0][0] + bybitAsks[0][0])/2
    except Exception as error:
      print('error in handle_orderbook bybit:',error)
      
  ws.orderbook_stream(50, base+quote, handle_orderbook)
  
  