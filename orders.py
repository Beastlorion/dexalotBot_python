import sys, os, asyncio, time, ast, json, shortuuid
import contracts, tools
from hexbytes import HexBytes
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}

openOrders = None
bestBid = None
bestAsk = None

failedReplaceAttempts = 0

def getOpenOrders(pair):
  global openOrders
  try:
    url = config["signedApiUrl"] + "orders?pair=" + pair + "&category=0"
    req = Request(url)
    req.add_header('x-signature', contracts.signature)
    openOrdersJson = urlopen(req).read()
    # print("open orders:",openOrders)
    openOrders = json.loads(openOrdersJson)
  except Exception as error:
    print("error in getOpenOrders:", error)
  print("finished getting open orders:",time.time())
  return openOrders

def getBestOrders():
  global bestBid, bestAsk
  orderBooks = contracts.contracts["OrderBooks"]
  try:
    currentBestBid = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id0"]).call();
    currentBestAsk = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id1"]).call();
    bestBid = currentBestBid[0]/1000000
    bestAsk = currentBestAsk[0]/1000000
  except Exception as error:
    print("error in getBestOrders:", error)
  print("finished getting best orders:",time.time())
  return
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
  try:
    cancelTxGasest = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).estimate_gas();
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':round(cancelTxGasest * 1.2)});
    contracts.incrementNonce()
    response = contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
  except Exception as error:
    print("error in cancelOrderList", error)
  # print("cancelOrderList response:", response.hex(), round(time.time()))
  
async def cancelOrderLevels(pairStr, levelsToUpdate):
  for i in range(5):
    openOrders = await asyncio.to_thread(getOpenOrders, pairStr)
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
        if a.replace('\x00','') == record["clientOrderID"].decode('utf-8'):
          matched = True
          if record['level'] <= levelsToUpdate:
            print('cancel level:', record['level'], levelsToUpdate)
            orderIDs.append(order["id"])
            ordersToCancel.append(record)
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
    openOrders = await asyncio.to_thread(getOpenOrders, pairStr)
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
  
def generateBuyOrders(marketPrice,settings,totalQuoteFunds,totalFunds,pairObj, levelsToUpdate, availQuoteFunds):
  try:
    orders = []
    availableFunds = availQuoteFunds
    for level in settings["levels"]:
      if int(level['level']) <= levelsToUpdate:
        spread = tools.getSpread(marketPrice,settings,totalQuoteFunds,totalFunds,level,0)
        price = round(marketPrice * (1 - spread),pairObj["quotedisplaydecimals"])
        if price > contracts.bestAsk:
          price = contracts.bestAsk - tools.getIncrement(pairObj["quotedisplaydecimals"])
        qty = round(tools.getQty(price,0,level,availableFunds,pairObj),pairObj["basedisplaydecimals"])
        if qty * marketPrice < float(pairObj["mintrade_amnt"]):
          continue
        if qty > float(pairObj["maxtrade_amnt"]) :
          qty = round(float(pairObj["maxtrade_amnt"]) * 0.999,pairObj["basedisplaydecimals"])
        availableFunds = availableFunds - (qty * price)
        orders.append({'side':0,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8')),'timestamp':time.time(), 'tracked':False})
  except Exception as error:
    print("ERROR DURING GENERATE BUY ORDERS:",error)
  return orders

def generateSellOrders(marketPrice,settings,totalBaseFunds,totalFunds,pairObj, levelsToUpdate, availBaseFunds):
  orders = []
  availableFunds = availBaseFunds
  for level in settings["levels"]:
    if int(level['level']) <= levelsToUpdate:
      spread = tools.getSpread(marketPrice,settings,totalBaseFunds,totalFunds,level,1)
      price = round(marketPrice * (1 + spread),pairObj["quotedisplaydecimals"])
      if price < contracts.bestBid:
        price = contracts.bestBid + tools.getIncrement(pairObj["quotedisplaydecimals"])
      qty = round(tools.getQty(price,1,level,availableFunds,pairObj),pairObj["basedisplaydecimals"])
      if qty * marketPrice < float(pairObj["mintrade_amnt"]):
        continue
      if qty > float(pairObj["maxtrade_amnt"]) :
        qty = round(float(pairObj["maxtrade_amnt"]) * 0.999,pairObj["basedisplaydecimals"])
      availableFunds = availableFunds - qty
      orders.append({'side':1,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8')),'timestamp':time.time(), 'tracked':False})
  return orders

async def cancelReplaceOrders(base, quote, marketPrice,settings, pairObj, pairStr, pairByte32, levelsToUpdate):
  replaceOrders = []
  newOrders = []
  orderIDsToCancel = []
  ordersToUpdate = []
  
  global failedReplaceAttempts, openOrders
  contracts.replaceStatus = 0
  contracts.addStatus = 0
  print("begin cancelReplace:",time.time())
  
  # await asyncio.gather(
  #   asyncio.to_thread(contracts.getBalances,base, quote),
  #   asyncio.to_thread(getBestOrders),
  #   asyncio.to_thread(getOpenOrders, pairStr)
  # )
  
  totalBaseFunds = float(contracts.contracts[base]["portfolioTot"])
  totalQuoteFunds = float(contracts.contracts[quote]["portfolioTot"])
  totalFunds = totalBaseFunds * marketPrice + totalQuoteFunds
  availBaseFunds = float(contracts.contracts[base]["portfolioAvail"])
  availQuoteFunds = float(contracts.contracts[quote]["portfolioAvail"])
  
  # for order in openOrders["rows"]:
  #   a = bytes(HexBytes(order["clientordid"])).decode('utf-8')
  #   matches = []
  #   for record in contracts.activeOrders:
  #     print(a.replace('\x00',''), record["clientOrderID"].decode('utf-8'))
  #     if a.replace('\x00','') == record["clientOrderID"].decode('utf-8'):
  #       matches.append({'orderID':order["id"], 'clientOrderID':record["clientOrderID"], 'level':int(record['level']), 'qtyLeft': float(order['quantity']) - float(order['quantityfilled']), 'price': float(order['price']),'side': int(order['side'])})
  #   if len(matches) == 0: # if no record, cancel order
  #     print("UNMATCHED ORDER:", order, "\n", "record")
  #     orderIDsToCancel.append(order["id"])
  #   elif len(matches) > 1: # if more than one record, cancel order
  #     print("DUPLICATE ORDER:", order, "\n", "record")
  #     orderIDsToCancel.append(order["id"])
  #   else:
  #     for matched in matches:
  #       if matched['level'] <= levelsToUpdate:
  #         ordersToUpdate.append(matched)
  
  # if len(orderIDsToCancel) > 0 and failedReplaceAttempts < 2:
  #   print("----------- Wait for order updates -----------", failedReplaceAttempts)
  #   failedReplaceAttempts = failedReplaceAttempts + 1
  #   return False
  # elif failedReplaceAttempts >= 2 or len(orderIDsToCancel) == 0:
  #   failedReplaceAttempts = 0
  
  for order in contracts.activeOrders:
    if order['level'] <= levelsToUpdate:
      ordersToUpdate.append(order)
  for order in ordersToUpdate:
    if order['side'] == 0:
      availQuoteFunds = availQuoteFunds + order['qtyLeft'] * order['price']
    elif order['side'] == 1:
      availBaseFunds = availBaseFunds + order['qtyLeft']
      
  buyOrders = generateBuyOrders(marketPrice,settings,totalQuoteFunds,totalFunds,pairObj, levelsToUpdate, availQuoteFunds)
  sellOrders = generateSellOrders(marketPrice,settings,totalBaseFunds,totalFunds,pairObj, levelsToUpdate, availBaseFunds)
  
  limit_orders = buyOrders + sellOrders
  
  if len(limit_orders) == 0:
    await asyncio.sleep(2)
    return False
  
  for newOrder in limit_orders:
    matches = []
    for oldOrder in ordersToUpdate:
      if newOrder['side'] == oldOrder['side'] and newOrder['level'] == oldOrder['level']:
        newOrder['orderID'] = oldOrder['orderID']
        newOrder['oldClientOrderID'] = oldOrder['clientOrderID']
        if newOrder['side'] == 0:
          newOrder['costDif'] = (newOrder['qty'] * newOrder['price']) - (oldOrder['qtyLeft'] * oldOrder['price'])
        else:
          newOrder['costDif'] = newOrder['qty'] - oldOrder['qtyLeft']
        matches.append(newOrder)
    if len(matches) == 0:
      newOrders.append(newOrder)
    elif len(matches) == 1:
      replaceOrders = replaceOrders + matches
    elif len(matches) > 1:
      print("MATCHES with duplicate:",matches)
      for orderToCancel in matches:
        orderIDsToCancel = orderIDsToCancel + matches['orderID']
    # if len(matches) > 1:
    #   sortedMatches = sorted(matches, key = lambda d: d['timestamp'])
    #   matches = sortedMatches[-1]
    # if len(matches) == 0:
    #   newOrders.append(newOrder)
    # elif len(matches) == 1:
    #   replaceOrders = replaceOrders + matches
  if len(orderIDsToCancel) > 0:
    print("ORDERS TO CANCEL:", orderIDsToCancel)
    await cancelOrderList(orderIDsToCancel)
  
  replaceTx = False
  if len(replaceOrders) > 0:
    replaceTx = replaceOrderList(replaceOrders, pairObj)
  
  addTx = False
  if len(newOrders) > 0:
    addTx = addLimitOrderList(newOrders,pairObj,pairByte32)
  
  if replaceTx or addTx:
    for x in range(100):
      if (contracts.replaceStatus == 1 or not replaceTx) and (contracts.addStatus == 1 or not addTx):
        return True
      elif (contracts.replaceStatus == 2 and replaceTx) or (contracts.addStatus == 2 and addTx):
        return False
      await asyncio.sleep(0.01)
  return True

def replaceOrderList(orders, pairObj):
  sortedOrders = sorted(orders, key = lambda d: d['costDif'])
  print('replace orders:',time.time(), 'orders:', sortedOrders)
  
  updateIDs = []
  clientOrderIDs = []
  prices = []
  quantities = []
  
  for order in sortedOrders:
    updateIDs.append(order['orderID'])
    clientOrderIDs.append(order['clientOrderID'])
    prices.append(int(order["price"]*pow(10, int(pairObj["quote_evmdecimals"]))))
    quantities.append(int(order["qty"]*pow(10, int(pairObj["base_evmdecimals"]))))
  
  try:
    gasest = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelReplaceList(
      updateIDs,
      clientOrderIDs,
      prices,
      quantities
    ).estimate_gas()
    
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelReplaceList(
      updateIDs,
      clientOrderIDs,
      prices,
      quantities
    ).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':round(gasest * 1.2)});
    contracts.incrementNonce()
    replaceTx = contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
    contracts.newPendingTx('replaceOrderList',replaceTx,sortedOrders)
    return True
  except Exception as error:
    print('error in replaceOrderList:', error)
  return replaceTx

def addLimitOrderList(limit_orders,pairObj,pairByte32):
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

  print('New Orders:', len(limit_orders),'time:',time.time(),limit_orders)
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
  return True