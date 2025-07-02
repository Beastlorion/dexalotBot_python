import sys, os, asyncio, time, ast, json, shortuuid, math
import logging
from decimal import Decimal
from eth_utils.units import units, decimal
import contracts, tools, price_feed_v2 as price_feeds
from web3 import Web3
from hexbytes import HexBytes
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}

testnet = False
if len(sys.argv) > 2:
  testnet = sys.argv[2] == "fuji"

units.update(
    {
        "8_dec": decimal.Decimal("100000000"),  # Add in 8 decimals
    }
)
cancelReplaceCount = 0
addOrderCount = 0
cancelOrderCount = 0
openOrders = None

async def getOpenOrders(pair,refreshActiveOrders = False, shuttingDown = False):
  global openOrders
  logger.info(f"[ORDERS] Getting open orders for {pair}, refreshActiveOrders={refreshActiveOrders}, shuttingDown={shuttingDown}")
  try:
    
    signedApiUrl = config.get("fuji_signedApiUrl") if testnet else config.get("signedApiUrl")
    url = signedApiUrl + "orders?pair=" + pair + "&category=0"
    
    # Use aiohttp for async HTTP request
    import aiohttp
    async with aiohttp.ClientSession() as session:
      # Use the global signature from contracts module
      headers = {'x-signature': contracts.signature}
      logger.debug(f"[ORDERS] Making API request to: {url}")
      async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as response:
        if response.status != 200:
          logger.error(f"[ORDERS] API returned status {response.status}: {await response.text()}")
          return {"rows": []}
        openOrdersJson = await response.text()
        openOrders = json.loads(openOrdersJson)
    
    logger.info(f"[ORDERS] Retrieved {len(openOrders.get('rows', []))} open orders")
    if len(contracts.activeOrders)>0 and refreshActiveOrders:
      trackedOrderIDs = []
      orderIDsToCancel = []
      for order in openOrders["rows"]:
        a = bytes(HexBytes(order["clientordid"][2:])).decode('utf-8')
        a = str(a.replace('\x00',''))
        matches = []
        for record in contracts.activeOrders:
          if a == record["clientOrderID"].decode('utf-8'):
            trackedOrderIDs.append(record["clientOrderID"])
            matches.append({'orderID':order["id"], 'clientOrderID':record["clientOrderID"], 'level':int(record['level']), 'qtyLeft': float(order['quantity']) - float(order['quantityfilled']), 'price': float(order['price']),'side': int(order['side'])})
            record['orderID'] = order["id"]
            record['qtyLeft'] = float(order['quantity']) - float(order['quantityfilled'])
            record['price'] = float(order['price'])
            record['side'] = float(order['side'])
        if len(matches) == 0: # if no record, cancel order
          print("NO RECORD:", order, "\n")
          orderIDsToCancel.append(order["id"])
        elif len(matches) > 1: # if more than one record, cancel order
          print("DUPLICATE RECORD:", order, "\n")
          orderIDsToCancel.append(order["id"])
      if len(orderIDsToCancel) > 0:
        print("ORDERS TO CANCEL:", orderIDsToCancel)
        await cancelOrderList(orderIDsToCancel)
      for record in contracts.activeOrders:
        if record['clientOrderID'] not in trackedOrderIDs:
          print("ORDER NOT FOUND:", record)
          contracts.activeOrders.remove(record)
  except Exception as error:
    logger.error(f"[ORDERS] Error in getOpenOrders: {error}", exc_info=True)
    print("error in getOpenOrders:", error)
  logger.debug(f"[ORDERS] Finished getting open orders at {time.time()}")
  print("finished getting open orders:",time.time())
  return openOrders

def getBestOrders():
  logger.info("[ORDERS] Getting best orders from order book")
  orderBooks = contracts.contracts["OrderBooks"]
  try:
    currentBestBid = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id0"]).call();
    currentBestAsk = orderBooks["deployedContract"].functions.getTopOfTheBook(orderBooks["id1"]).call();
    contracts.bestBid = currentBestBid[0]/1000000
    contracts.bestAsk = currentBestAsk[0]/1000000
    logger.info(f"[ORDERS] Best bid: {contracts.bestBid}, Best ask: {contracts.bestAsk}")
  except Exception as error:
    logger.error(f"[ORDERS] Error in getBestOrders: {error}", exc_info=True)
    print("error in getBestOrders:", error)
  logger.debug(f"[ORDERS] Finished getting best orders at {time.time()}")
  print("finished getting best orders:",time.time())
  return

  return [currentBestBid,currentBestAsk];


async def cancelOrderList(orderIDs, priorityGwei):
  global cancelOrderCount
  logger.info(f"[ORDERS] Canceling {len(orderIDs)} orders with priority gas {priorityGwei}")
  print("cancel orders:",orderIDs)
  if len(orderIDs) == 0:
    logger.debug("[ORDERS] No orders to cancel")
    return False
  
  # Check if we're shutting down
  if hasattr(contracts, 'status') and not contracts.status:
    logger.warning("[ORDERS] Shutdown in progress, attempting quick cancel")
  
  try:
    # cancelTxGasest = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).estimate_gas();
    gas = len(orderIDs) * 500000
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelOrderList(orderIDs).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas,'maxFeePerGas':Web3.to_wei(priorityGwei + 1 + 20, 'gwei'),'maxPriorityFeePerGas': Web3.to_wei(1 + priorityGwei, 'gwei')});
    contracts.incrementNonce()
    response = contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
    logger.info(f"[ORDERS] Cancel order transaction sent: {response.hex()}")
    print("CANCEL ORDER LIST RESPONSE: ", response)
    
    # During shutdown, wait for transaction receipt to ensure orders are cancelled
    if hasattr(contracts, 'status') and not contracts.status:
      logger.info(f"[ORDERS] Waiting for transaction receipt during shutdown...")
      try:
        receipt = contracts.contracts["SubNetProvider"]["provider"].eth.wait_for_transaction_receipt(response, timeout=10)
        if receipt.status == 1:
          logger.info(f"[ORDERS] Transaction confirmed successfully in block {receipt.blockNumber}")
        else:
          logger.error(f"[ORDERS] Transaction failed with status {receipt.status}")
          return False
      except Exception as e:
        logger.error(f"[ORDERS] Error waiting for receipt: {e}")
        # Continue anyway since transaction was sent
    
    cancelOrderCount = cancelOrderCount + len(orderIDs)
    logger.info(f"[ORDERS] Successfully cancelled {len(orderIDs)} orders. Total cancelled: {cancelOrderCount}")
    return True
  except Exception as error:
    logger.error(f"[ORDERS] Error in cancelOrderList: {error}", exc_info=True)
    print("error in cancelOrderList", error)
    return False
  # print("cancelOrderList response:", response.hex(), round(time.time()))
  
async def cancelOrderLevels(pairStr, levelsToUpdate):
  logger.info(f"[ORDERS] Canceling order levels <= {levelsToUpdate} for {pairStr}")
  for i in range(5):
    openOrders = await getOpenOrders(pairStr)
    if (len(openOrders["rows"])==len(contracts.activeOrders) or len(contracts.activeOrders) == 0):
      break
    else:
      logger.debug(f"[ORDERS] Order count mismatch, waiting... (attempt {i+1}/5)")
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
      await cancelOrderList(orderIDs, 1)
      for order in ordersToCancel:
        contracts.activeOrders.remove(order)
      logger.info(f"[ORDERS] Successfully cancelled {len(orderIDs)} orders at levels <= {levelsToUpdate}")
      return True
    except Exception as error:
      logger.error(f"[ORDERS] Error during cancelOrderLevels - cancelOrderList: {error}", exc_info=True)
      print("error during cancelOrderLevels - cancelOrderList")
      return False
      

async def cancelAllOrders(pairStr,shuttingDown = False):
  logger.info(f"[ORDERS] Canceling all orders for {pairStr}, shuttingDown={shuttingDown}")

  await asyncio.sleep(4)
  
  logger.info(f"[ORDERS] Fetching open orders...")
  openOrders = await getOpenOrders(pairStr, False, shuttingDown)
  
  # Check if we got a valid response
  if not openOrders or "rows" not in openOrders:
    logger.warning(f"[ORDERS] Invalid response from getOpenOrders: {openOrders}")
    return False
  
  orderIDs = []
  for order in openOrders.get("rows", []):
    orderIDs.append(order["id"])
  
  if len(orderIDs) > 0:
    logger.info(f"[ORDERS] Found {len(orderIDs)} orders to cancel: {orderIDs}")
    success = await cancelOrderList(orderIDs, 1)
    
    if shuttingDown:
      logger.info(f"[ORDERS] Order cancellation during shutdown {'succeeded' if success else 'failed'}")
      return success
    else:
      # Normal operation - verify cancellation
      await asyncio.sleep(3)
      openOrders = await getOpenOrders(pairStr)
      remaining = len(openOrders.get('rows', [])) if openOrders else 0
      logger.info(f"[ORDERS] After cancellation, {remaining} orders remain")
      return remaining == 0
  else:
    logger.info(f"[ORDERS] No orders to cancel")
    return True
  
def generateBuyOrders(marketPrice,settings,totalQuoteFunds,totalFunds,pairObj, levels, levelsToUpdate, availQuoteFunds, myAsks):
  logger.debug(f"[ORDERS] Generating buy orders - marketPrice: {marketPrice}, levelsToUpdate: {levelsToUpdate}")
  try:
    orders = []
    availableFunds = availQuoteFunds
    bestAsk = contracts.bestAsk
    if (settings['pairType'] == 'volatile' and len(myAsks) > 0 and bestAsk == myAsks[0]['price']) and len(contracts.asks) > 1:
      bestAsk = contracts.asks[1][0]
    for level in levels:
      retrigger = False
      if int(level['level']) <= levelsToUpdate:
        spread = tools.getSpread(marketPrice,settings,totalQuoteFunds,totalFunds,level,0)
        price = math.floor(marketPrice * (1 - spread) * pow(10,pairObj["quotedisplaydecimals"]))/pow(10,pairObj["quotedisplaydecimals"])
        if price >= bestAsk and ('autoTake' not in settings or not settings['autoTake']):
          price = math.floor((bestAsk * pow(10,pairObj["quotedisplaydecimals"])) - 1)/pow(10,pairObj["quotedisplaydecimals"])
          retrigger = True
        qty = math.floor(tools.getQty(price,0,level,availableFunds,pairObj) * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
        if qty * marketPrice < float(pairObj["mintrade_amnt"]):
          continue
        if qty * price > float(pairObj["maxtrade_amnt"]) :
          qty = math.floor((float(pairObj["maxtrade_amnt"])/price) * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
        availableFunds = availableFunds - (qty * price)
        orders.append({'side':0,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8')),'timestamp':time.time(), 'tracked':False})
        if retrigger:
          contracts.retrigger = True
  except Exception as error:
    logger.error(f"[ORDERS] Error during generateBuyOrders: {error}", exc_info=True)
    print("ERROR DURING GENERATE BUY ORDERS:",error)
  logger.debug(f"[ORDERS] Generated {len(orders)} buy orders")
  return orders

def generateSellOrders(marketPrice,settings,totalBaseFunds,totalFunds,pairObj, levels, levelsToUpdate, availBaseFunds, myBids):
  logger.debug(f"[ORDERS] Generating sell orders - marketPrice: {marketPrice}, levelsToUpdate: {levelsToUpdate}")
  try:
    orders = []
    availableFunds = availBaseFunds
    bestBid = contracts.bestBid
    if (settings['pairType'] == 'volatile' and len(myBids) > 0 and bestBid == myBids[0]['price']) and len(contracts.bids) > 1:
      bestBid = contracts.bids[1][0]
    for level in levels:
      retrigger = False
      if int(level['level']) <= levelsToUpdate:
        spread = tools.getSpread(marketPrice,settings,totalBaseFunds,totalFunds,level,1)
        price = math.ceil(marketPrice * (1 + spread)* pow(10,pairObj["quotedisplaydecimals"]))/pow(10,pairObj["quotedisplaydecimals"])
        if price <= bestBid and ('autoTake' not in settings or not settings['autoTake']):
          price = math.ceil((bestBid * pow(10,pairObj["quotedisplaydecimals"])) + 1)/pow(10,pairObj["quotedisplaydecimals"])
          retrigger = True
        qty = math.floor(tools.getQty(price,1,level,availableFunds,pairObj) * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
        if qty * marketPrice < float(pairObj["mintrade_amnt"]):
          continue
        if qty * price > float(pairObj["maxtrade_amnt"]):
          qty = math.floor((float(pairObj["maxtrade_amnt"])/price) * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
        availableFunds = availableFunds - qty
        orders.append({'side':1,'price':price,'qty':qty,'level':int(level['level']), 'clientOrderID': HexBytes(str(shortuuid.uuid()).encode('utf-8')),'timestamp':time.time(), 'tracked':False})
        if retrigger:
          contracts.retrigger = True
  except Exception as error:
    logger.error(f"[ORDERS] Error during generateSellOrders: {error}", exc_info=True)
    print("ERROR DURING GENERATE SELL ORDERS:",error)
  logger.debug(f"[ORDERS] Generated {len(orders)} sell orders")
  return orders

async def executeTakerBuy(marketPrice,settings,totalQuoteFunds,totalFunds,pairObj,pairByte32,shiftPrice,shiftQty, myAsks,availQuoteFunds):
  logger.info(f"[ORDERS] Executing taker buy - marketPrice: {marketPrice}, bestAsk: {contracts.bestAsk}")
  try:
    bestAsk = contracts.bestAsk
    myBestAsk = myAsks[0]['price'] if len(myAsks) > 0 else marketPrice * 1.1
    if bestAsk == myBestAsk:
      logger.debug(f"[ORDERS] Skipping taker buy - bestAsk equals myBestAsk")
      return False
    spread = tools.getSpread(marketPrice,settings,totalQuoteFunds,totalFunds,{'level':0},0)
    price = math.floor(marketPrice * (1 - spread - settings['takerThreshold']/100) * pow(10,pairObj["quotedisplaydecimals"]))/pow(10,pairObj["quotedisplaydecimals"])
    
    if price > bestAsk:
      qty = math.floor((availQuoteFunds/price) * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
      if qty * price > float(pairObj["maxtrade_amnt"]) :
        qty = (math.floor((float(pairObj["maxtrade_amnt"])/price) * 0.99 * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"]))
      elif qty * price < float(pairObj["mintrade_amnt"]):
        return False
      if price >= myBestAsk:
        price = math.floor(myBestAsk * pow(10,pairObj["quotedisplaydecimals"]) - 1)/pow(10,pairObj["quotedisplaydecimals"])
      gas = 3000000
      logger.info(f"[ORDERS] Execute Taker Buy - Price: {price}, Qty: {qty}")
      print('Execute Taker Buy - Price:',price,'Qty:',qty)
      contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.addNewOrder({
        'clientOrderId': str(shortuuid.uuid()).encode('utf-8'),
        'tradePairId': pairByte32,
        'price': Web3.to_wei(price, shiftPrice),
        'quantity': Web3.to_wei(qty, shiftQty),
        'traderaddress': contracts.address,
        'side': 0,
        'type1': 1,
        'type2': 2,
        'stp': 1
      }).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas})
      contracts.incrementNonce()
      await asyncio.to_thread(contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction,contract_data)
      logger.info(f"[ORDERS] Taker buy order sent successfully")
    else:
      logger.debug(f"[ORDERS] Taker buy skipped - price {price} <= bestAsk {bestAsk}")
      return False
  except Exception as error:
    logger.error(f"[ORDERS] Error in executeTakerBuy: {error}", exc_info=True)
    print("Error in executeTakerBuy:",error)
    
async def executeTakerSell(marketPrice,settings,totalBaseFunds,totalFunds,pairObj,pairByte32,shiftPrice,shiftQty, myBids,availBaseFunds):
  logger.info(f"[ORDERS] Executing taker sell - marketPrice: {marketPrice}, bestBid: {contracts.bestBid}")
  try:
    bestBid = contracts.bestBid
    myBestBid = myBids[0]['price'] if len(myBids) > 0 else marketPrice * 0.9
    if bestBid == myBestBid:
      logger.debug(f"[ORDERS] Skipping taker sell - bestBid equals myBestBid")
      return False
    spread = tools.getSpread(marketPrice,settings,totalBaseFunds,totalFunds,{'level':0},1)
    price = math.ceil(marketPrice * (1 + spread + settings['takerThreshold']/100)* pow(10,pairObj["quotedisplaydecimals"]))/pow(10,pairObj["quotedisplaydecimals"])
    
    if price < bestBid:
      qty = math.floor(availBaseFunds * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
      if qty * price > float(pairObj["maxtrade_amnt"]) :
        qty = math.floor((float(pairObj["maxtrade_amnt"])/price)* 0.99 * pow(10,pairObj["basedisplaydecimals"]))/pow(10,pairObj["basedisplaydecimals"])
      elif qty * price < float(pairObj["mintrade_amnt"]):
        return False
      if price <= myBestBid:
        price = math.ceil(myBestBid * pow(10,pairObj["quotedisplaydecimals"]) + 1)/pow(10,pairObj["quotedisplaydecimals"])
      gas = 2000000
      logger.info(f"[ORDERS] Execute Taker Sell - Price: {price}, Qty: {qty}")
      print('Execute Taker Sell - Price:',price,'Qty:',qty)
      contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.addNewOrder({
        'clientOrderId': str(shortuuid.uuid()).encode('utf-8'),
        'tradePairId': pairByte32,
        'price': Web3.to_wei(price, shiftPrice),
        'quantity': Web3.to_wei(qty, shiftQty),
        'traderaddress': contracts.address,
        'side': 1,
        'type1': 1,
        'type2': 2,
        'stp': 1
      }).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas})
      contracts.incrementNonce()
      await asyncio.to_thread(contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction,contract_data)
      logger.info(f"[ORDERS] Taker sell order sent successfully")
    else:
      logger.debug(f"[ORDERS] Taker sell skipped - price {price} >= bestBid {bestBid}")
      return False
  except Exception as error:
    logger.error(f"[ORDERS] Error in executeTakerSell: {error}", exc_info=True)
    print("Error in executeTakerSell:",error)
  
    

async def cancelReplaceOrders(base, quote, marketPrice,settings,responseTime, pairObj, pairStr, pairByte32, levels, levelsToUpdate, takerBuy, takerSell, priorityGwei):
  global cancelReplaceCount, addOrderCount
  logger.info(f"[ORDERS] Starting cancelReplaceOrders - marketPrice: {marketPrice}, levelsToUpdate: {levelsToUpdate}, takerBuy: {takerBuy}, takerSell: {takerSell}")
  replaceOrders = []
  newOrders = []
  ordersToUpdate = []
  orderIDsToCancel = []
  contracts.replaceStatus = 0
  contracts.addStatus = 0
  contracts.retrigger = False
  
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
      
  myBids, myAsks = tools.getMyOrdersSorted()
    
  totalBaseFunds = float(contracts.contracts[base]["portfolioTot"])
  totalQuoteFunds = float(contracts.contracts[quote]["portfolioTot"])
  totalFunds = totalBaseFunds * marketPrice + totalQuoteFunds
  # availBaseFunds = float(contracts.contracts[base]["portfolioAvail"])
  # availQuoteFunds = float(contracts.contracts[quote]["portfolioAvail"])
  if 'takerReserve' in settings:
    availBaseFunds = totalBaseFunds * .99 * (1 - settings['takerReserve']/100)
    availQuoteFunds = totalQuoteFunds * .99 * (1 - settings['takerReserve']/100)
    availTakerBaseFunds = totalBaseFunds * .99 * (settings['takerReserve']/100)
    availTakerQuoteFunds = totalQuoteFunds * .99 * (settings['takerReserve']/100)
  else:
    availBaseFunds = totalBaseFunds * .99
    availQuoteFunds = totalQuoteFunds * .99

  if settings['takerEnabled'] and not settings['autoTake']:
    logger.debug(f"[ORDERS] Taker enabled - checking opportunities")
    if takerSell:
      logger.info(f"[ORDERS] Executing taker sell")
      await executeTakerSell(marketPrice,settings,totalBaseFunds,totalFunds,pairObj,pairByte32,shiftPrice,shiftQty, myBids,availTakerBaseFunds)
    elif takerBuy:
      logger.info(f"[ORDERS] Executing taker buy")
      await executeTakerBuy(marketPrice,settings,totalQuoteFunds,totalFunds,pairObj,pairByte32,shiftPrice,shiftQty, myAsks,availTakerQuoteFunds)
      
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
      
  buyOrders = generateBuyOrders(marketPrice,settings,totalQuoteFunds,totalFunds,pairObj, levels, levelsToUpdate, availQuoteFunds, myAsks)
  sellOrders = generateSellOrders(marketPrice,settings,totalBaseFunds,totalFunds,pairObj, levels, levelsToUpdate, availBaseFunds, myBids)
  
  limit_orders = buyOrders + sellOrders
  
  for newOrder in limit_orders:
    matches = []
    skip = False
    for oldOrder in ordersToUpdate:
      if newOrder['side'] == oldOrder['side'] and newOrder['level'] == oldOrder['level']:
        if newOrder['price'] == oldOrder['price']:
          skip = True
          break
        newOrder['orderID'] = oldOrder['orderID']
        newOrder['oldClientOrderID'] = oldOrder['clientOrderID']
        if newOrder['side'] == 0:
          newOrder['costDif'] = (newOrder['qty'] * newOrder['price']) - (oldOrder['qtyLeft'] * oldOrder['price'])
        else:
          newOrder['costDif'] = newOrder['qty'] - oldOrder['qtyLeft']
        matches.append(newOrder)
    if len(matches) == 0 and not skip:
      newOrders.append(newOrder)
    elif len(matches) == 1:
      replaceOrders = replaceOrders + matches
    elif len(matches) > 1:
      print("MATCHES with duplicate:",matches)
      for orderToCancel in matches:
        orderIDsToCancel = orderIDsToCancel + orderToCancel['orderID']
        for activeOrder in activeOrders:
          if activeOrder['clientOrderID'] == orderToCancel['clientOrderID']:
            activeOrders.remove(activeOrder)
  if len(orderIDsToCancel) > 0:
    print("ORDERS TO CANCEL:", orderIDsToCancel)
    await cancelOrderList(orderIDsToCancel, priorityGwei)
        
  if len(replaceOrders) == 0 and len(newOrders) == 0 and levelsToUpdate > 0:
    logger.warning(f"No orders to replace or add, but levelsToUpdate is greater than 0. availQuoteFunds: {availQuoteFunds}, availBaseFunds: {availBaseFunds}")
    return False
  
  replaceTx = False
  if len(replaceOrders) > 0:
    replaceTx = True
    sortedOrders = sorted(replaceOrders, key = lambda d: d['costDif'])
    # logger.info(f"[ORDERS] Replacing {len(sortedOrders)} orders")
    asyncio.create_task(replaceOrderList(sortedOrders, pairObj, pairByte32, shiftPrice,shiftQty,priorityGwei,settings))
    cancelReplaceCount = cancelReplaceCount + len(sortedOrders)
  
  addTx = False
  if len(newOrders) > 0:
    await asyncio.sleep(0.1)
    addTx = True
    logger.info(f"[ORDERS] Adding {len(newOrders)} new orders")
    asyncio.create_task(addOrderList(newOrders,pairObj,pairByte32,shiftPrice,shiftQty,settings))
    addOrderCount = addOrderCount + len(newOrders)
  
  if replaceTx or addTx:
    logger.debug(f"[ORDERS] Waiting for order operations to complete - replaceTx: {replaceTx}, addTx: {addTx}")
    for x in range(responseTime*10):
      if (contracts.replaceStatus == 1 or not replaceTx) and (contracts.addStatus == 1 or not addTx):
        logger.info(f"[ORDERS] Order operations completed successfully - replaced: {len(replaceOrders)}, added: {len(newOrders)}")
        return True
      elif (contracts.replaceStatus == 2 and replaceTx) or (contracts.addStatus == 2 and addTx):
        logger.warning(f"[ORDERS] Order operations failed - replaceStatus: {contracts.replaceStatus}, addStatus: {contracts.addStatus}")
        return False
      await asyncio.sleep(0.1)
    logger.warning(f"[ORDERS] Order operations timed out after {responseTime}s")
    return False
  else:
    logger.debug(f"[ORDERS] No order operations needed")
    await asyncio.sleep(1)
  logger.info(f"[ORDERS] cancelReplaceOrders completed - total active orders: {len(contracts.activeOrders)}")
  return True

async def replaceOrderList(orders, pairObj, pairByte32, shiftPrice, shiftQty, priorityGwei,settings):
  logger.info(f"[ORDERS] Starting replaceOrderList with {len(orders)} orders")
  
  updateIDs = []
  clientOrderIDs = []
  prices = []
  quantities = []

  ordersToReplace = []
  
  for order in orders:
    updateIDs.append(order["orderID"])
    
    ordersToReplace.append({
      'traderaddress': contracts.address,
      'clientOrderId': order['clientOrderID'],
      'tradePairId':pairByte32,
      'price':Web3.to_wei(order["price"], shiftPrice),
      'quantity':Web3.to_wei(order["qty"], shiftQty),
      'side':order['side'],
      'type1': 1,
      'type2': 3 if not settings['autoTake'] else 0,
      'stp': 1
    })
  
  # logger.info(f"[ORDERS] Replacing orders with IDs: {updateIDs}")
  print('replaceOrderList -', len(orders), updateIDs)
  try:
    contracts.newPendingTx('replaceOrderList',orders)
    gas = len(orders) * 1000000
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.cancelAddList(
      updateIDs,
      ordersToReplace
    ).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas,'maxFeePerGas':Web3.to_wei(priorityGwei + 20, 'gwei'),'maxPriorityFeePerGas': Web3.to_wei(priorityGwei, 'gwei')})
    contracts.incrementNonce()
    a = await asyncio.to_thread(contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction,contract_data)
    logger.info(f"[ORDERS] Replace order transaction sent at {time.time()}")
    print('replaceOrderList RESPONSE:',time.time())
    return
  except Exception as error:
    logger.error(f"[ORDERS] Error in replaceOrderList: {error}", exc_info=True)
    print('error in replaceOrderList:', error)
    for tx in contracts.pendingTransactions:
      if tx['purpose'] == 'replaceOrderList':
        contracts.pendingTransactions.remove(tx)
  return

async def addOrderList(limit_orders,pairObj,pairByte32, shiftPrice, shiftQty,settings):
  logger.info(f"[ORDERS] Starting addOrderList with {len(limit_orders)} orders")
  prices = []
  quantities = []
  sides = []
  clientOrderIDs = []
  type2s = []

  ordersToSend = []
  for order in limit_orders:
    ordersToSend.append({
      'traderaddress': contracts.address,
      'clientOrderId': order['clientOrderID'],
      'tradePairId':pairByte32,
      'price':Web3.to_wei(order["price"], shiftPrice),
      'quantity':Web3.to_wei(order["qty"], shiftQty),
      'side':order["side"],
      'type1': 1,
      'type2': 3 if not settings['autoTake'] else 0,
      'stp': 1
    })

  logger.info(f"[ORDERS] Adding {len(limit_orders)} orders - {len([o for o in limit_orders if o['side'] == 0])} buys, {len([o for o in limit_orders if o['side'] == 1])} sells")
  print('Add order list - ', len(limit_orders))
  try:
    contracts.newPendingTx('addOrderList',limit_orders)
    gas = len(limit_orders) * 1000000
    contract_data = contracts.contracts["TradePairs"]["deployedContract"].functions.addOrderList(
      ordersToSend
    ).build_transaction({'nonce':contracts.getSubnetNonce(),'gas':gas,'maxFeePerGas':Web3.to_wei(1 + 20, 'gwei'),'maxPriorityFeePerGas': Web3.to_wei(1, 'gwei')})
    contracts.incrementNonce()
    await asyncio.to_thread(contracts.contracts["SubNetProvider"]["provider"].eth.send_transaction,contract_data)
    logger.info(f"[ORDERS] Add order transaction sent")
  except Exception as error:
    logger.error(f"[ORDERS] Error in addOrderList: {error}", exc_info=True)
    print('error in addOrderList:', error)
    for tx in contracts.pendingTransactions:
      if tx['purpose'] == 'addOrderList':
        contracts.pendingTransactions.remove(tx)
  return
