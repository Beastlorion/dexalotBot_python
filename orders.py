import sys, os, asyncio, time, ast, json, shortuuid
from decimal import Decimal
from eth_utils.units import units, decimal
import contracts, tools, price_feeds
from web3 import Web3
from hexbytes import HexBytes
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}
units.update(
    {
        "8_dec": decimal.Decimal("100000000"),  # Add in 8 decimals
    }
)
openOrders = None
totalQtyFilled = 0
totalQtyFilled2 = 0
totalQtyFilled3 = 0
totalQtyFilledLastUpdate = 0
totalQtyFilled2LastUpdate = 0
totalQtyFilled3LastUpdate = 0

failedReplaceAttempts = 0

async def getOpenOrders(pair,refreshActiveOrders = False):
  global openOrders
  try:
    url = config["signedApiUrl"] + "orders?pair=" + pair + "&category=0"
    req = Request(url)
    req.add_header('x-signature', contracts.signature)
    openOrdersJson = urlopen(req).read()
    # print("open orders:",openOrders)
    openOrders = json.loads(openOrdersJson)
    if len(contracts.activeOrders)>0 and refreshActiveOrders:
      orderIDsToCancel = []
      for order in openOrders["rows"]:
        a = bytes(HexBytes(order["clientordid"])).decode('utf-8')
        matches = []
        for record in contracts.activeOrders:
          if a.replace('\x00','') == record["clientOrderID"].decode('utf-8'):
            matches.append({'orderID':order["id"], 'clientOrderID':record["clientOrderID"], 'level':int(record['level']), 'qtyLeft': float(order['quantity']) - float(order['quantityfilled']), 'price': float(order['price']),'side': int(order['side'])})
            record['orderID'] = order["id"]
            record['orderID'] = order["id"]
            record['orderID'] = order["id"]
            
        if len(matches) == 0: # if no record, cancel order
          print("UNMATCHED ORDER:", order, "\n", "record")
          orderIDsToCancel.append(order["id"])
        elif len(matches) > 1: # if more than one record, cancel order
          print("DUPLICATE ORDER:", order, "\n", "record")
          orderIDsToCancel.append(order["id"])
        if len(orderIDsToCancel) > 0:
          print("ORDERS TO CANCEL:", orderIDsToCancel)
          await cancelOrderList(orderIDsToCancel)
  except Exception as error:
    print("error in getOpenOrders:", error)
  print("finished getting open orders:",time.time())
  return openOrders

def getBestOrders():
  orderBooks = contracts.contracts["OrderBooks"]
  try:
    currentBestBid = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id0"]).call();
    currentBestAsk = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id1"]).call();
    contracts.bestBid = currentBestBid[0]/1000000
    contracts.bestAsk = currentBestAsk[0]/1000000
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
    # cancelTxGasest = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).estimate_gas();
    gas = len(orderIDs) * 500000
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas});
    contracts.incrementNonce()
    response = contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
  except Exception as error:
    print("error in cancelOrderList", error)
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
  if shuttingDown:
    await asyncio.sleep(4)
  for i in range(3):
    openOrders = await getOpenOrders(pairStr)
    if (len(openOrders["rows"])>0 or len(contracts.activeOrders) == 0) and not shuttingDown:
      break
    elif(len(openOrders["rows"]) == len(contracts.activeOrders)):
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
          price = round(contracts.bestAsk - tools.getIncrement(pairObj["quotedisplaydecimals"]),pairObj["quotedisplaydecimals"])
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
  try:
    orders = []
    availableFunds = availBaseFunds
    for level in settings["levels"]:
      if int(level['level']) <= levelsToUpdate:
        spread = tools.getSpread(marketPrice,settings,totalBaseFunds,totalFunds,level,1)
        price = round(marketPrice * (1 + spread),pairObj["quotedisplaydecimals"])
        if price < contracts.bestBid:
          price = round(contracts.bestBid + tools.getIncrement(pairObj["quotedisplaydecimals"]),pairObj["quotedisplaydecimals"])
        qty = round(tools.getQty(price,1,level,availableFunds,pairObj),pairObj["basedisplaydecimals"])
        if qty * marketPrice < float(pairObj["mintrade_amnt"]):
          continue
        if qty > float(pairObj["maxtrade_amnt"]) :
          qty = round(float(pairObj["maxtrade_amnt"]) * 0.999,pairObj["basedisplaydecimals"])
        availableFunds = availableFunds - qty
        orders.append({'side':1,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8')),'timestamp':time.time(), 'tracked':False})
  except Exception as error:
    print("ERROR DURING GENERATE BUY ORDERS:",error)
  return orders

async def cancelReplaceOrders(base, quote, marketPrice,settings, pairObj, pairStr, pairByte32, levelsToUpdate, taker):
  global totalQtyFilled,totalQtyFilled2,totalQtyFilled3,totalQtyFilledLastUpdate,totalQtyFilled2LastUpdate,totalQtyFilled3LastUpdate
  replaceOrders = []
  newOrders = []
  ordersToUpdate = []
  orderIDsToCancel = []
  
  if taker:
    print("try taker")
    qtyFilled = 0
    if (time.time() - totalQtyFilledLastUpdate > 60):
      if price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold']/100) > contracts.bestAsk:
        executePrice = price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold']/100)
        qtyFilled = tools.getTakerFill(settings, marketPrice,executePrice,contracts.asks,price_feeds.bybitBids,0)
      elif price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold']/100) < contracts.bestBid:
        executePrice = price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold']/100)
        qtyFilled = tools.getTakerFill(settings, marketPrice,executePrice,contracts.bids,price_feeds.bybitAsks,1)
      totalQtyFilled = totalQtyFilled + qtyFilled
      totalQtyFilledLastUpdate = time.time()
    
    if (time.time() - totalQtyFilled2LastUpdate > 60):
      qtyFilled2 = 0
      if price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold2']/100) > contracts.bestAsk:
        executePrice = price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold2']/100)
        qtyFilled2 = tools.getTakerFill(settings, marketPrice,executePrice,contracts.asks,price_feeds.bybitBids,0)
      elif price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold2']/100) < contracts.bestBid:
        executePrice = price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold2']/100)
        qtyFilled2 = tools.getTakerFill(settings, marketPrice,executePrice,contracts.bids,price_feeds.bybitAsks,1)
      totalQtyFilled2 = totalQtyFilled2 + qtyFilled2
      totalQtyFilled2LastUpdate = time.time()
    
    if (time.time() - totalQtyFilled3LastUpdate > 60):
      qtyFilled3 = 0
      if price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold3']/100) > contracts.bestAsk:
        executePrice = price_feeds.bybitBids[0][0] * (1 - settings['takerThreshold3']/100)
        qtyFilled3 = tools.getTakerFill(settings, marketPrice,executePrice,contracts.asks,price_feeds.bybitBids,0)
      elif price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold3']/100) < contracts.bestBid:
        executePrice = price_feeds.bybitAsks[0][0] * (1 + settings['takerThreshold3']/100)
        qtyFilled3 = tools.getTakerFill(settings, marketPrice,executePrice,contracts.bids,price_feeds.bybitAsks,1)
      totalQtyFilled3 = totalQtyFilled3 + qtyFilled3
      totalQtyFilled3LastUpdate = time.time()
  
  print('totalQtyFilled',totalQtyFilled)
  print('totalQtyFilled2',totalQtyFilled2)
  print('totalQtyFilled3',totalQtyFilled3)
  
  contracts.replaceStatus = 0
  contracts.addStatus = 0
  
  totalBaseFunds = float(contracts.contracts[base]["portfolioTot"])
  totalQuoteFunds = float(contracts.contracts[quote]["portfolioTot"])
  totalFunds = totalBaseFunds * marketPrice + totalQuoteFunds
  # availBaseFunds = float(contracts.contracts[base]["portfolioAvail"])
  # availQuoteFunds = float(contracts.contracts[quote]["portfolioAvail"])
  availBaseFunds = totalBaseFunds
  availQuoteFunds = totalQuoteFunds
  
  for order in contracts.activeOrders:
    if order['side'] == 0:
      availQuoteFunds = availQuoteFunds - order['qtyLeft'] * order['price']
    elif order['side'] == 1:
      availBaseFunds = availBaseFunds - order['qtyLeft']
  
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
    return True
  
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
  
  quoteDecimals = pairObj["quote_evmdecimals"]
  shiftPrice = 'ether'
  match quoteDecimals:
    case 6:
      shiftPrice = "lovelace"
    case 8:
      shiftPrice = "8_dec"
  baseDecimals = pairObj["base_evmdecimals"]
  shiftQty = 'ether'
  match baseDecimals:
    case 6:
      shiftQty = "lovelace"
    case 8:
      shiftQty = "8_dec"
        
  replaceTx = False
  if len(replaceOrders) > 0:
    replaceTx = True
    asyncio.create_task(replaceOrderList(replaceOrders, pairObj,shiftPrice,shiftQty))
  
  addTx = False
  if len(newOrders) > 0:
    addTx = True
    asyncio.create_task(addLimitOrderList(newOrders,pairObj,pairByte32,shiftPrice,shiftQty))
  
  if replaceTx or addTx:
    for x in range(100):
      if (contracts.replaceStatus == 1 or not replaceTx) and (contracts.addStatus == 1 or not addTx):
        return True
      elif (contracts.replaceStatus == 2 and replaceTx) or (contracts.addStatus == 2 and addTx):
        return False
      await asyncio.sleep(0.05)
    return False
  return True

async def replaceOrderList(orders, pairObj, shiftPrice, shiftQty):
  sortedOrders = sorted(orders, key = lambda d: d['costDif'])
  # print('replace orders:',time.time(), 'orders:', sortedOrders)
  
  updateIDs = []
  clientOrderIDs = []
  prices = []
  quantities = []
  
  for order in sortedOrders:
    updateIDs.append(order['orderID'])
    clientOrderIDs.append(order['clientOrderID'])
    prices.append(Web3.to_wei(order["price"], shiftPrice))
    quantities.append(Web3.to_wei(order["qty"], shiftQty))
  
  print('replaceOrderList - Prices:', prices, 'quantities',quantities)
  try:
    contracts.newPendingTx('replaceOrderList',sortedOrders)
    gas = len(sortedOrders) * 1000000
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelReplaceList(
      updateIDs,
      clientOrderIDs,
      prices,
      quantities
    ).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas});
    contracts.incrementNonce()
    await asyncio.to_thread(contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction,contract_data)
    return
  except Exception as error:
    print('error in replaceOrderList:', error)
    for tx in contracts.pendingTransactions:
      if tx['purpose'] == 'replaceOrderList':
        contracts.pendingTransactions.remove(tx)
  return

async def addLimitOrderList(limit_orders,pairObj,pairByte32, shiftPrice, shiftQty):
  prices = []
  quantities = []
  sides = []
  clientOrderIDs = []
  type2s = []
        
  for order in limit_orders:
    
    prices.append(Web3.to_wei(order["price"], shiftPrice))
    quantities.append(Web3.to_wei(order["qty"], shiftQty))
    sides.append(order["side"])
    clientOrderIDs.append(order['clientOrderID'])
    type2s.append(3)

  print('addLimitOrderList - Prices:', prices, 'quantities',quantities)
  # print('New Orders:', len(limit_orders),'time:',time.time(),limit_orders)
  try:
    contracts.newPendingTx('addOrderList',limit_orders)
    gas = len(limit_orders) * 700000
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.addLimitOrderList(
      pairByte32,
      clientOrderIDs,
      prices,
      quantities,
      sides,
      type2s
    ).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas});
    contracts.incrementNonce()
    await asyncio.to_thread(contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction,contract_data)
  except Exception as error:
    print('error in addLimitOrderList:', error)
    for tx in contracts.pendingTransactions:
      if tx['purpose'] == 'addOrderList':
        contracts.pendingTransactions.remove(tx)
  return