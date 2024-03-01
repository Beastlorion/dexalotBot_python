import time, ast, asyncio
import urllib.request
from binance import AsyncClient, BinanceSocketManager
import tools

api_key = ''
api_secret = ''
usdt = 0
usdcUsdt = 0
marketPrice = 0

async def startPriceFeed(market):
  base = tools.getSymbolFromName(market,0)
  quote = tools.getSymbolFromName(market,1)
  
  client = await AsyncClient.create()
  bm = BinanceSocketManager(client)
  if (quote == "USDC" and base != "EUROC" and base != "USDt"):
    tickerTask = asyncio.create_task(startTicker(client, bm, base, quote))
    usdc_usdtTickerTask = asyncio.create_task(usdc_usdtTicker(client, bm, base, quote))
  elif(base == "AVAX" and quote == "USDt"):
    tickerTask = asyncio.create_task(startTicker(client, bm, base, quote))
  elif(base == "USDt" and quote == "USDC"):
    usdc_usdtTickerTask = asyncio.create_task(usdc_usdtTicker(client, bm, base, quote))
  
  # usdtUpdaterTask = asyncio.create_task(usdtUpdater())
  
async def usdtUpdater():
  while 1:
    await updateUSDT()
    await asyncio.sleep(1)

async def startTicker(client, bm, base, quote):
  global marketPrice
  if base == "BTC.b":
    base = "BTC"
  symbol = base + 'USDT'

  # start any sockets here, i.e a trade socket
  ts = bm.trade_socket(symbol)
  # then start receiving messages
  async with ts as tscm:
    while True:
      res = await tscm.recv()
      priceUsdt = float(res["p"])
      if quote == "USDC" and usdcUsdt:
        marketPrice = priceUsdt * usdcUsdt
      elif (quote == "USDt"):
        marketPrice = priceUsdt
  await client.close_connection()
  
async def usdc_usdtTicker(client, bm, base, quote):
  global usdcUsdt,marketPrice
  symbol = 'USDCUSDT'

  # start any sockets here, i.e a trade socket
  ts = bm.trade_socket(symbol)
  # then start receiving messages
  async with ts as tscm:
    while True:
      res = await tscm.recv()
      usdcUsdt = float(res["p"])
      if (base == "USDt" and quote == "USDC"):
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

# def getTickerPrice():
