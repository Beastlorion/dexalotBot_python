import sys, os, asyncio, time, ast, json
from threading import Thread
import shortuuid
import contracts, tools
from hexbytes import HexBytes
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}

bestBid = None
bestAsk = None

async def getOpenOrders(pair):
  url = config["signedApiUrl"] + "orders?pair=" + pair + "&category=0"
  req = Request(url)
  req.add_header('x-signature', contracts.signature)
  openOrders = urlopen(req).read()
  # print("open orders:",openOrders)
  return json.loads(openOrders)

async def getBestOrders():
  global bestBid, bestAsk
  orderBooks = contracts.contracts["OrderBooks"]
  currentBestBid = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id0"]).call();
  currentBestAsk = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id1"]).call();
  bestBid = currentBestBid[0]/1000000
  bestAsk = currentBestAsk[0]/1000000
  # if (this.tradePairIdentifier=="sAVAX/AVAX" || this.tradePairIdentifier=="COQ/AVAX"){
  #   this.currentBestBid = currentBestBid.div("1000000000000000000").toNumber()
  #   this.currentBestAsk = currentBestAsk.div("1000000000000000000").toNumber()
  # } else {
  #   this.currentBestBid = currentBestBid.div(1000000).toNumber()
  #   this.currentBestAsk = currentBestAsk.div(1000000).toNumber()
  # }

  return [currentBestBid,currentBestAsk];


async def cancelOrderList(orderIDs):
  print("cancel orders:",orderIDs)
  if len(orderIDs) == 0:
    return False
  
  cancelTxGasest = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).estimate_gas();
  contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':round(cancelTxGasest * 1.2)});
  contracts.incrementNonce()
  response = contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
  contracts.newPendingTx('cancel',response)
  # print("cancelOrderList response:", response.hex(), round(time.time()))
  
async def cancelOrderLevels(pairStr, levelsToUpdate):
  for i in range(5):
    openOrders = await getOpenOrders(pairStr)
    if (len(openOrders["rows"])==len(contracts.activeOrders) or len(contracts.activeOrders) == 0):
      break
    else:
      await asyncio.sleep(1)
  
  if len(openOrders["rows"]) >= 0:
    orderIDs = []
    ordersToCancel = []
    for order in openOrders["rows"]:
      a = bytes(HexBytes(order["clientordid"])).decode('utf-8')
      matched = False
      for record in contracts.activeOrders:
        if a.replace('\x00','') == record["clientOrderID"].decode('utf-8') and record['level'] <= levelsToUpdate:
          print('cancel level:', record['level'], levelsToUpdate)
          orderIDs.append(order["id"])
          ordersToCancel.append(record)
          matched = True
          break
      if matched is False:
        print("CANCELLING MISSING ClientOrderID:", a.replace('\x00',''))
        orderIDs.append(order["id"])
    try:
      await cancelOrderList(orderIDs)
      for order in ordersToCancel:
        contracts.activeOrders.remove(order)
      return True
    except Exception as error:
      print("error during cancelOrderLevels - cancelOrderList")
      return False
      

async def cancelAllOrders(pairStr,shuttingDown = False):
  for i in range(5):
    openOrders = await getOpenOrders(pairStr)
    if (len(openOrders["rows"])>0 or len(contracts.activeOrders) == 0) and not shuttingDown:
      break
    else:
      await asyncio.sleep(1)
  if len(openOrders["rows"]) >= 0:
    orderIDs = []
    for order in openOrders["rows"]:
      orderIDs.append(order["id"])
    await cancelOrderList(orderIDs)
    contracts.activeOrders = []
  else:
    print("no open orders to cancel")
  
def generateBuyOrders(marketPrice,priceChange,settings,funds,totalFunds,pairObj, levelsToUpdate):
  orders = []
  availableFunds = funds
  for level in settings["levels"]:
    if int(level['level']) <= levelsToUpdate:
      spread = tools.getSpread(marketPrice,priceChange,settings,funds,totalFunds,level,0)
      price = round(marketPrice * (1 - spread),pairObj["quotedisplaydecimals"])
      if price > bestAsk:
        price = bestAsk - tools.getIncrement(pairObj["quotedisplaydecimals"])
      qty = round(tools.getQty(price,0,level,availableFunds,pairObj),pairObj["basedisplaydecimals"])
      if qty * marketPrice < float(pairObj["mintrade_amnt"]):
        continue
      if qty > float(pairObj["maxtrade_amnt"]) :
        qty = round(float(pairObj["maxtrade_amnt"]) * 0.999,pairObj["basedisplaydecimals"])
      availableFunds = availableFunds - (qty * price)
      orders.append({'side':0,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8'))})
  return orders

def generateSellOrders(marketPrice,priceChange,settings,funds,totalFunds,pairObj, levelsToUpdate):
  orders = []
  availableFunds = funds
  for level in settings["levels"]:
    if int(level['level']) <= levelsToUpdate:
      spread = tools.getSpread(marketPrice,priceChange,settings,funds,totalFunds,level,1)
      price = round(marketPrice * (1 + spread),pairObj["quotedisplaydecimals"])
      if price < bestBid:
        price = bestBid + tools.getIncrement(pairObj["quotedisplaydecimals"])
      qty = round(tools.getQty(price,1,level,availableFunds,pairObj),pairObj["basedisplaydecimals"])
      if qty * marketPrice < float(pairObj["mintrade_amnt"]):
        continue
      if qty > float(pairObj["maxtrade_amnt"]) :
        qty = round(float(pairObj["maxtrade_amnt"]) * 0.999,pairObj["basedisplaydecimals"])
      availableFunds = availableFunds - qty
      orders.append({'side':1,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8'))})
  return orders
  
async def addLimitOrderList(limit_orders,pairObj,pairByte32):
  prices = []
  quantities = []
  sides = []
  clientOrderIDs = []
  type2s = []
  
  for order in limit_orders:
    prices.append(int(order["price"]*pow(10, int(pairObj["quote_evmdecimals"]))))
    quantities.append(int(order["qty"]*pow(10, int(pairObj["base_evmdecimals"]))))
    sides.append(order["side"])
    clientOrderIDs.append(order['clientOrderID'])
    type2s.append(3)
  
  status = True
  for x in range(8):
    if len(contracts.pendingTransactions) > 0:
      for tx in contracts.pendingTransactions:
        if tx["status"] == 'failed':
          status = False
          contracts.pendingTransactions.remove(tx)
    else:
      break
    if (status is False or contracts.freezeNewOrders):
      return False
    await asyncio.sleep(1)
  print(limit_orders, len(limit_orders), 'time:',time.time())
  gasest = contracts.contracts["TradePairs"]["deployedContract"].functions.addLimitOrderList(
    pairByte32,
    clientOrderIDs,
    prices,
    quantities,
    sides,
    type2s
  ).estimate_gas()
  
  contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.addLimitOrderList(
    pairByte32,
    clientOrderIDs,
    prices,
    quantities,
    sides,
    type2s
  ).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':round(gasest * 1.2)});
  contracts.incrementNonce()
  response = contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
  contracts.newPendingTx('addOrderList',response,limit_orders)
  print("addLimitOrderList:", response.hex(), 'time: ',round(time.time()))
  return True