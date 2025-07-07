#!/usr/bin/env python3
"""
Blockchain contracts and provider management for Dexalot Bot.
Handles Web3 connections, contract interactions, and blockchain state.
"""

import sys
import asyncio
import time
import json
import logging
from hexbytes import HexBytes
from typing import Dict, Any, Optional

import websockets
import tools
import orders
from config import config
import urllib.request
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)
from web3 import Web3, AsyncWeb3, AsyncHTTPProvider
from eth_utils.units import units, decimal
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.middleware import SignAndSendRawMiddlewareBuilder, ExtraDataToPOAMiddleware
from eth_account.messages import encode_defunct

logger = logging.getLogger(__name__)

# Load ABIs
try:
    with open('./ABIs/ERC20ABI.json', 'r') as f:
        ERC20ABI = json.load(f)
    with open('./ABIs/savax_ABI.json', 'r') as f:
        savaxABI = json.load(f)
except Exception as e:
    logger.error(f"Failed to load ABI files: {e}")
    raise

units.update(
    {
        "8_dec": decimal.Decimal("100000000"),  # Add in 8 decimals
    }
)

contracts = {}
tokenDetails = None
address = None
signature = None
# Global state variables
nonce = 0
status = True
pendingTransactions = []
activeOrders = []
makerRate = None
takerRate = None
bestBid = None
bestAsk = None
replaceStatus = 0
addStatus = 0
refreshBalances = False
bids = []
asks = []
baseShift = 'ether'
quoteShift = 'ether'
retrigger = False
refreshActiveOrders = False
reconnect = False
orderIDsToCancel = []
takerFilled = 0
makerFilled = 0
refreshOrderLevel = False

async def getDeployments(dt, s, testnet):
  apiUrl = config.get_api_url("fuji" if testnet else "mainnet")
  url = apiUrl + "deployment?contracttype=" + dt + "&returnabi=true"
  # contract = urllib.request.urlopen(url).read()
  # contract = json.loads(contract)
  async with s.get(url) as r:
    if r.status != 200:
      r.raise_for_status()
    contract = json.loads(await r.read())
    for item in contract :
      contracts[item["contract_name"]] = item

async def getTokenDetails(testnet):
  apiUrl = config.get_api_url("fuji" if testnet else "mainnet")
  url = apiUrl + "tokens/"
  tokenDetails = json.loads(urllib.request.urlopen(url).read())
  return tokenDetails

async def initializeProviders(market: str, settings: Dict, testnet: bool, base: str):
    """Initialize blockchain providers with improved error handling."""
    global address, signature

    try:
        # Get private key
        if len(settings.get('secret_name', '')) > 0:
            private_key = tools.getPrivateKey(market, settings)
        else:
            private_key = config.get_private_key(market)
        
        account: LocalAccount = Account.from_key(private_key)
        address = account.address
        logger.info(f"Initialized account: {address}")

        # Initialize subnet provider
        rpc_url = config.get_rpc_url("fuji" if testnet else "mainnet", "subnet")
        contracts["SubNetProvider"] = await _create_provider(rpc_url, private_key, account.address)
        
        # Create signature for API authentication
        message = encode_defunct(text="dexalot")
        signed_message = contracts["SubNetProvider"]["provider"].eth.account.sign_message(message, private_key=private_key)
        signature = address + ':0x' + signed_message.signature.hex()

        # Initialize AVAX-C provider
        try:
            avaxc_rpc_url = config.get_rpc_url("fuji" if testnet else "mainnet", "avaxc")
            contracts["AvaxcProvider"] = await _create_provider(avaxc_rpc_url, private_key, account.address)
            logger.info("AVAX-C provider initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize AVAX-C provider: {e}")

        # Initialize Arbitrum provider
        if not testnet:  # Only for mainnet
            try:
                arb_rpc_url = config.get_rpc_url("mainnet", "arbitrum")
                contracts["ArbProvider"] = await _create_provider(arb_rpc_url, private_key, account.address)
                logger.info("Arbitrum provider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Arbitrum provider: {e}")

        # Initialize Base provider for specific tokens
        if base in ['TOSHI', 'ETH'] and not testnet:
            try:
                base_rpc_url = config.get_rpc_url("mainnet", "base")
                contracts["BaseProvider"] = await _create_provider(base_rpc_url, private_key, account.address)
                logger.info("Base provider initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Base provider: {e}")

        logger.info("Provider initialization complete")
        
    except Exception as e:
        logger.error(f"Failed to initialize providers: {e}")
        raise

async def _create_provider(rpc_url: str, private_key: str, account_address: str) -> Dict:
    """Create a Web3 provider with standard configuration."""
    provider_config = {
        "provider": Web3(Web3.HTTPProvider(rpc_url)),
        "nonce": 0
    }
    
    # Add middleware
    provider_config["provider"].middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    provider_config["provider"].middleware_onion.inject(
        SignAndSendRawMiddlewareBuilder.build(private_key), layer=0
    )
    
    # Configure provider
    provider_config["provider"].eth.default_account = account_address
    provider_config["provider"].strict_bytes_type_checking = False
    
    # Get nonce
    provider_config["nonce"] = provider_config["provider"].eth.get_transaction_count(account_address)
    
    return provider_config
  
async def initializeContracts(market,pairObj,testnet):
  base = tools.getSymbolFromName(market,0)
  quote = tools.getSymbolFromName(market,1)
  
  contracts["AVAX"] = {
    "contractName": "AVAX",
    "mainnetBal": 0,
    "subnetBal": 0,
    "portfolioTot": 0,
    "portfolioAvail": 0,
    "tokenDetails": None,
    "deployedContract": None
  }
  contracts["ALOT"] = {
    "contractName": "ALOT",
    "mainnetBal": 0,
    "subnetBal": 0,
    "portfolioTot": 0,
    "portfolioAvail": 0,
    "tokenDetails": None,
    "deployedContract": None
  }
  contracts[base] = {
    "contractName": base,
    "mainnetBal": 0,
    "subnetBal": 0,
    "portfolioTot": 0,
    "portfolioAvail": 0,
    "tokenDetails": None,
    "deployedContract": None
  }
  contracts[quote] = {
    "contractName": quote,
    "mainnetBal": 0,
    "subnetBal": 0,
    "portfolioTot": 0,
    "portfolioAvail": 0,
    "tokenDetails": None,
    "deployedContract": None
  }
  contracts["PortfolioSub"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["PortfolioSub"]["address"], abi=contracts["PortfolioSub"]["abi"]["abi"])
  contracts["PortfolioSubHelper"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["PortfolioSubHelper"]["address"], abi=contracts["PortfolioSubHelper"]["abi"]["abi"])
  contracts["TradePairs"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["TradePairs"]["address"], abi=contracts["TradePairs"]["abi"]["abi"])
  contracts["OrderBooks"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["OrderBooks"]["address"], abi=contracts["OrderBooks"]["abi"]["abi"])
  contracts["OrderBooks"]["id0"] = contracts["TradePairs"]["deployedContract"].functions.getBookId(pairObj['pair'].encode('utf-8'), 0).call()
  contracts["OrderBooks"]["id1"] = contracts["TradePairs"]["deployedContract"].functions.getBookId(pairObj['pair'].encode('utf-8'), 1).call()
  
  if base == 'sAVAX':
    contracts["sAVAX"]["proxy"] = contracts["AvaxcProvider"]["provider"].eth.contract(address='0x2b2C81e08f1Af8835a78Bb2A90AE924ACE0eA4bE', abi=savaxABI)
  
  tokens = await getTokenDetails(testnet)
  for item in tokens:
    if item["subnet_symbol"] in contracts and item["subnet_symbol"] != "AVAX":
      if item['env'] == "production-multi-avax" or (testnet and item['env'] == "fuji-multi-avax"):
        contracts[item["subnet_symbol"]]["tokenDetails"] = item
        contracts[item["subnet_symbol"]]["deployedContract"] = contracts["AvaxcProvider"]["provider"].eth.contract(address=Web3.to_checksum_address(contracts[item["subnet_symbol"]]["tokenDetails"]["address"]), abi=ERC20ABI["abi"])
      elif item['env'] == "production-multi-arb" or (testnet and item['env'] == "fuji-multi-arb" and item["subnet_symbol"] != "ALOT"):
        contracts[item["subnet_symbol"]]["tokenDetails"] = item
        contracts[item["subnet_symbol"]]["deployedContract"] = contracts["ArbProvider"]["provider"].eth.contract(address=contracts[item["subnet_symbol"]]["tokenDetails"]["address"], abi=ERC20ABI["abi"])
      elif (item['env'] == "production-multi-base" or (testnet and item['env'] == "fuji-multi-base" and item["subnet_symbol"] != "ALOT")) and base in ['TOSHI','ETH']:
        contracts[item["subnet_symbol"]]["tokenDetails"] = item
        contracts[item["subnet_symbol"]]["deployedContract"] = contracts["BaseProvider"]["provider"].eth.contract(address=contracts[item["subnet_symbol"]]["tokenDetails"]["address"], abi=ERC20ABI["abi"])
    elif item["subnet_symbol"] == "AVAX":
      contracts[item["subnet_symbol"]]["tokenDetails"] = item 
  print('finished initializeContracts')
  return
      
      
def signTransaction(provider,tx):
  return provider.eth.account.sign_transaction(tx, private_key=config.get_private_key(market))

async def refreshDexalotNonce():
  global nonce
  newNonce = contracts["SubNetProvider"]["provider"].eth.get_transaction_count(address)
  nonce = newNonce
  print("newNonce:",newNonce)
  return nonce

def getSubnetNonce():
  return nonce

def incrementNonce():
  global nonce
  nonce = nonce + 1
  
def getRates(pairObj,pairByte32):
  global makerRate, takerRate
  rates = contracts['PortfolioSubHelper']["deployedContract"].functions.getRates(address,address,pairByte32,int(pairObj['maker_rate_bps']),int(pairObj['taker_rate_bps'])).call()
  makerRate = rates[0]/10
  takerRate = rates[1]/10
  print('Maker Rate BP:',makerRate)
  print('Taker Rate BP:',takerRate)
  
async def startDataFeeds(pairObj, testnet):
  logger.info(f"[DATA_FEED] Starting data feeds for pair: {pairObj['pair']}")
  try:
    # Create the websocket handler task but don't wait for it
    # It will run continuously in the background
    c = asyncio.create_task(handleWebscokets(pairObj, testnet))
    logger.info(f"[DATA_FEED] Created websocket handler task: {c}")
    
    # Give it a moment to start up
    await asyncio.sleep(1.0)
    
    # Check if it failed immediately
    if c.done():
      if c.exception():
        logger.error(f"[DATA_FEED] WebSocket handler failed immediately: {c.exception()}")
        raise c.exception()
    
    logger.info("[DATA_FEED] Data feeds started successfully")
    # Return without waiting for the task to complete
    return
    
  except Exception as e:
    logger.error(f"[DATA_FEED] Failed to start data feeds: {e}", exc_info=True)
    raise
    
async def handleWebscokets(pairObj, testnet):
  global status, reconnect, bestAsk, bestBid, bids, asks, addStatus, replaceStatus, refreshBalances, retrigger, orderIDsToCancel, takerFilled, makerFilled, refreshOrderLevel
  
  logger.info(f"[WEBSOCKET] Starting WebSocket handler for {pairObj['pair']}")
  base = pairObj['pair'].split('/')[0]
  quote = pairObj['pair'].split('/')[1]
  baseDecimals = pairObj['basedisplaydecimals']
  quoteDecimals = pairObj['quotedisplaydecimals']
  subscribeBook = {"data":pairObj['pair'],"pair":pairObj['pair'],"type":"subscribe","decimal":pairObj["quotedisplaydecimals"]}
  tradereventsubscribe = {"type":"tradereventsubscribe", "signature":signature}
  unsubscribeBook = {"data":pairObj['pair'],"pair":pairObj['pair'],"type":"unsubscribe"}
  tradereventunsubscribe = {"type":"tradereventunsubscribe", "signature":signature}
  wsUrl = "wss://api.dexalot-test.com" if testnet else "wss://api.dexalot.com"
  
  logger.info(f"[WEBSOCKET] WebSocket URL: {wsUrl}")
  logger.info(f"[WEBSOCKET] Initial status: {status}")
  
  connection_attempts = 0
  last_pending_tx_time = 0
  last_order_status_update_time = time.time()
  force_reconnect = False
  
  while status:
    
    reconnect = False
    force_reconnect = False
    try:
      if 'wsKey' in config.all_config and len(config.get('wsKey', '')) > 1:
        url = 'https://api.dexalot.com/privapi/auth/getwstoken'
        req = Request(url)
        req.add_header('x-apikey', config.get('wsKey'))
        token = json.loads(urlopen(req).read())['token']
        #wsUrl = "wss://api.dexalot.com/api/ws?wstoken=" + token
        wsUrl = "wss://api.dexalot.com?wstoken=" + token

      connection_attempts += 1
      logger.info(f"[WEBSOCKET] Attempting WebSocket connection (attempt #{connection_attempts})")
      
      async with websockets.connect(wsUrl) as websocket:
        logger.info(f"[WEBSOCKET] Connected successfully to {wsUrl}")
        
        await websocket.send(json.dumps(subscribeBook))
        logger.info(f"[WEBSOCKET] Sent subscribe book: {subscribeBook}")
        
        await websocket.send(json.dumps(tradereventsubscribe))
        logger.info(f"[WEBSOCKET] Sent trade event subscribe")
        
        print("dexalotOrderFeed and dexalotBookFeed START")
        
        message_count = 0
        while status and not reconnect and not force_reconnect:
          message_count += 1
          try:
            # Check if we need to force reconnect due to pending tx timeout
            current_time = time.time()
            if pendingTransactions:
              # Find the oldest pending transaction
              oldest_pending_time = float('inf')
              for tx in pendingTransactions:
                if tx.get('timestamp'):
                  oldest_pending_time = min(oldest_pending_time, tx['timestamp'])
              
              # If we have a pending tx and haven't received orderStatusUpdateEvent in 5 seconds
              if oldest_pending_time < float('inf') and current_time - oldest_pending_time > 5.0:
                if current_time - last_order_status_update_time > 5.0:
                  logger.warning(f"[WEBSOCKET] No orderStatusUpdateEvent received for 5 seconds after pending tx, forcing reconnect")
                  force_reconnect = True
                  break
            
            # Add timeout to make recv interruptible
            message = str(await asyncio.wait_for(websocket.recv(), timeout=1.0))
            parsed = json.loads(message)
            
            if parsed['type'] == 'orderBooks':
              data = parsed['data']
              if data['buyBook'][0]['prices'].split(',')[0] != '':
                bestBid = float(Web3.from_wei(float(data['buyBook'][0]['prices'].split(',')[0]), quoteShift))
              else:
                bestBid = 0
              if data['sellBook'][0]['prices'].split(',')[0] != '':
                bestAsk = float(Web3.from_wei(float(data['sellBook'][0]['prices'].split(',')[0]), quoteShift))
              else:
                bestAsk = float('inf')
              #print(bestBid,bestAsk)
              bidPrices = data['buyBook'][0]['prices'].split(',')
              if bidPrices[0] == '':
                bidPrices = []
              askPrices = data['sellBook'][0]['prices'].split(',')
              if askPrices[0] == '':
                askPrices = []
              bidQtys = data['buyBook'][0]['quantities'].split(',')
              askQtys = data['sellBook'][0]['quantities'].split(',')
              buildBids = []
              buildAsks = []
              
              for i,price in enumerate(bidPrices):
                buildBids.append([round(float(Web3.from_wei(float(price), quoteShift)),quoteDecimals),round(float(Web3.from_wei(float(bidQtys[i]), baseShift)),baseDecimals)])
              for i,price in enumerate(askPrices):
                buildAsks.append([round(float(Web3.from_wei(float(price), quoteShift)),quoteDecimals),round(float(Web3.from_wei(float(askQtys[i]), baseShift)),baseDecimals)])
              bids = buildBids
              asks = buildAsks
              
              
            if parsed['type'] == "orderStatusUpdateEvent":
              last_order_status_update_time = current_time
              data = parsed['data']
              hex1 = HexBytes(data["clientOrderId"][2:])
              a = bytes(hex1).decode('utf-8')
              clientOrderID = str(a.replace('\x00',''))
              if (data['status'] in ['PARTIAL']):
                refreshBalances = True
                for order in activeOrders:
                  if clientOrderID == order["clientOrderID"].decode('utf-8'):
                      print("PARTIAL FILL:",data)
                      order['orderID'] = data['orderId']
                      order['qty'] = float(data['quantity'])
                      order['qtyFilled'] = float(data['quantityfilled'])
                      order['qtyLeft'] = float(data['quantity']) - float(data['quantityfilled'])
                      order['price'] = float(data['price'])
                      order['side'] = int(data['sideId'])
                      order['status'] = data['status']
              if data['status'] in ['FILLED','EXPIRED','KILLED']:
                # print("order closed:",data)
                refreshBalances = True
                if (data['status'] == 'FILLED'):
                  if data['type2Id'] == 2:
                    takerFilled = takerFilled + float(data['quantityfilled'])
                  if data['type2Id'] == 3:
                    makerFilled = makerFilled + float(data['quantityfilled'])
                for order in activeOrders:
                  if clientOrderID == order["clientOrderID"].decode('utf-8'):
                    refreshOrderLevel = True
                    print('Order',data['status'],'and removed from activeOrders:',parsed)
                    activeOrders.remove(order)
              if data['status'] in ['NEW','PARTIAL','FILLED','REJECTED','CANCEL_REJECT']:
                for tx in pendingTransactions:
                  if tx['purpose'] in ['addOrderList','replaceOrderList'] :
                    for order in tx['orders']:
                      if clientOrderID == order["clientOrderID"].decode('utf-8') and data['status'] in ['NEW','PARTIAL'] and not order['tracked']:
                        print("NEW ORDER:",clientOrderID)
                        order['orderID'] = data['orderId']
                        order['qty'] = float(data['quantity'])
                        order['qtyFilled'] = float(data['quantityfilled'])
                        order['qtyLeft'] = float(data['quantity']) - float(data['quantityfilled'])
                        order['price'] = float(data['price'])
                        order['side'] = int(data['sideId'])
                        order['status'] = data['status']
                        if tx['purpose'] in ['replaceOrderList']:
                          for oldOrder in activeOrders:
                            if order["oldClientOrderID"] == oldOrder["clientOrderID"]:
                              activeOrders.remove(oldOrder)
                        activeOrders.append(order)
                        order['tracked'] = True
                      elif clientOrderID == order["clientOrderID"].decode('utf-8') and data['status'] in ['REJECTED','CANCEL_REJECT']:
                        print("REJECTED ORDER:",parsed)#clientOrderID, 'reason:', data['code'])
                        if data['code'] == "T-T2PO-01":
                          retrigger = True
                        order['tracked'] = True
                        if tx['purpose'] in ['replaceOrderList']:
                          for oldOrder in activeOrders:
                            if order["oldClientOrderID"] == oldOrder["clientOrderID"]:
                              activeOrders.remove(oldOrder)
                      elif clientOrderID == order["clientOrderID"].decode('utf-8') and data['status'] in ['FILLED']:
                        print("FILLED NEW ORDER:",parsed)
                        order['tracked'] = True
                        if tx['purpose'] in ['replaceOrderList']:
                          for oldOrder in activeOrders:
                            if order["oldClientOrderID"] == oldOrder["clientOrderID"]:
                              activeOrders.remove(oldOrder)
                    tracked = 0
                    for order in tx['orders']:
                      if order['tracked']:
                        tracked = tracked+1
                    if tracked == len(tx['orders']):
                      print("COMPLETED",tx['purpose'],"ORDER TRACKING:", time.time())
                      pendingTransactions.remove(tx)
                      if (tx['purpose'] == 'addOrderList'):
                        addStatus = 1
                      elif (tx['purpose'] == 'replaceOrderList'):
                        replaceStatus = 1
              if data['status'] == 'CANCELED':
                for order in activeOrders:
                  if clientOrderID == order["clientOrderID"].decode('utf-8'):
                    order['status'] = data['status']
                    activeOrders.remove(order)
                  if data['type2Id'] == 2:
                    takerFilled = takerFilled + float(data['quantityfilled'])
                  if data['type2Id'] == 3:
                    makerFilled = makerFilled + float(data['quantityfilled'])
              for tx in pendingTransactions:
                for order in tx['orders']:
                  if clientOrderID == order["clientOrderID"].decode('utf-8') and not order['tracked']:
                    print('UNTRACKED ORDER:',data)
                    order['tracked'] = True
              activeOrderIDs = []
              for activeOrder in activeOrders:
                activeOrderIDs.append(activeOrder['clientOrderID'].decode('utf-8'))
              if clientOrderID not in activeOrderIDs and data['status'] in ['NEW','PARTIAL'] and data['type2Id'] == 3:
                print(activeOrderIDs, clientOrderID)
                orderIDsToCancel.append(data['orderId'])
          except asyncio.TimeoutError:
            # Timeout is normal - just check if we should shutdown
            if not status:
              logger.info(f"[WEBSOCKET] Exiting loop due to status=False after {message_count} messages")
              break
            continue
          except websockets.ConnectionClosed as e:
            logger.warning(f"[WEBSOCKET] Connection closed after {message_count} messages: {e}")
            break
          except websockets.ConnectionClosedError as e:
            logger.warning(f"[WEBSOCKET] Connection closed error after {message_count} messages: {e}")
            break
          except Exception as error:
            logger.error(f"[WEBSOCKET] Error processing message #{message_count}: {error}", exc_info=True)
            if 'parsed' in locals() and parsed.get('type') == "orderStatusUpdateEvent":
              logger.error(f"[WEBSOCKET] FAILED ORDER TRACKING: {parsed.get('data', 'unknown')}")
              print("FAILED ORDER TRACKING:", parsed.get('data', 'unknown'), error)
              # Don't set status = False here - let the bot handle the error gracefully
              # status = False
            continue
        
        logger.info(f"[WEBSOCKET] Exiting inner loop - status={status}, reconnect={reconnect}, force_reconnect={force_reconnect}, messages={message_count}")
        
        # Unsubscribe before closing
        try:
          await websocket.send(json.dumps(unsubscribeBook))
          await websocket.send(json.dumps(tradereventunsubscribe))
          await asyncio.sleep(0.15)
        except Exception as e:
          logger.warning(f"[WEBSOCKET] Error during unsubscribe: {e}")
    except Exception as error:
      logger.error(f'[WEBSOCKET] Error during handleWebscokets: {error}', exc_info=True)
      print('error during handleWebscokets:',error)
      await asyncio.sleep(0.15)
  
  logger.info(f"[WEBSOCKET] Exiting handleWebscokets - final status={status}, attempts={connection_attempts}")
  
  # Only exit if there's a fatal error or shutdown requested
  # Otherwise, this should never exit
  if status:
    logger.error("[WEBSOCKET] handleWebscokets exited unexpectedly while status is still True")
    raise Exception("WebSocket handler exited unexpectedly")
      
async def log_loop(event_filter, poll_interval):
  print("start block filter")
  while status:
    events = event_filter.get_new_entries()
    eventsToWatch = []
    if (len(events)>1):
      for e in reversed(events):
        eventsToWatch.append(e)
        if len(eventsToWatch)>=3:
          break
    else:
      eventsToWatch = events
    tasks = []
    for event in eventsToWatch:
      tasks.append(asyncio.to_thread(handleEvents,event))
    if len(tasks)>0:
      await asyncio.gather(*tasks)
      # handleEvents(event)
    await asyncio.sleep(poll_interval)
  return

def handleEvents(event):
  global status, activeOrders, replaceStatus, addStatus
  try:
    block = contracts["SubNetProvider"]["provider"].eth.get_block(event.hex())
    transactionsProcessed = []
    for hash in block.transactions:
      for tx in pendingTransactions:
        if tx["hash"] == hash:
          receipt = contracts["SubNetProvider"]["provider"].eth.get_transaction_receipt(hash)
          if receipt.status == 1:
            transactionsProcessed.append(tx)
            print('transaction success:', tx['purpose'], time.time())
            if tx['purpose'] == 'placeOrder' or tx['purpose'] == 'addOrderList':
              addStatus = 1
              activeOrders = activeOrders + tx['orders']
            elif tx['purpose'] == 'replaceOrderList':
              replaceStatus = 1
              for newOrder in tx['orders']:
                for oldOrder in activeOrders:
                  if (newOrder['oldClientOrderID'] == oldOrder['clientOrderID']):
                    activeOrders.remove(oldOrder)
                    activeOrders.append(newOrder)
                    break
            elif tx['purpose'] == 'cancel':
              for id in tx['orders']:
                for order in activeOrders:
                  if (id == order['orderID']):
                    activeOrders.remove(order)
                    print('REMOVE ORDER:', order)
          elif tx['purpose'] == 'cancel':
            print('cancel tx failed:', tx)
            tx['status'] = 'failed'
          else:
            print('tx failed:', tx)
            if tx['purpose'] == 'replaceOrderList':
              replaceStatus = 2
            transactionsProcessed.append(tx)
    for tx in transactionsProcessed:
      print("ACTIVE ORDERS:",activeOrders)
      pendingTransactions.remove(tx)
  except Exception as error:
    logger.error(f"[CONTRACTS] Error in blockfilter handleEvents: {error}", exc_info=True)
    print("error in blockfilter handleEvents:", error)
    status = False
    return
  return
    
def newPendingTx(purpose,orders = []):
  timestamp = time.time()
  print('New pending transaction:', purpose, len(orders), timestamp)
  pendingTransactions.append({'purpose': purpose,'status':'pending','orders':orders, 'timestamp': timestamp})

def getBalances(base, quote, pairObj):
  global refreshBalances, baseShift, quoteShift, status
  portfolio = contracts["PortfolioSub"]["deployedContract"]
  
  try:
    # get AVAX balances
    avaxC = contracts["AvaxcProvider"]["provider"].eth.get_balance(address)
    contracts["AVAX"]["mainnetBal"] = Web3.from_wei(avaxC, 'ether')
    avaxD = portfolio.functions.getBalance(address, "AVAX".encode('utf-8')).call()
    contracts["AVAX"]["portfolioTot"] = Web3.from_wei(avaxD[0], 'ether')
    contracts["AVAX"]["portfolioAvail"] = Web3.from_wei(avaxD[1], 'ether')
    
    # get ALOT balances
    alotC = contracts["ALOT"]["deployedContract"].functions.balanceOf(address).call()
    contracts["ALOT"]["mainnetBal"] = Web3.from_wei(alotC, 'ether')
    
    alotD = portfolio.functions.getBalance(address, "ALOT".encode('utf-8')).call()
    contracts["ALOT"]["portfolioTot"] = Web3.from_wei(alotD[0], 'ether')
    contracts["ALOT"]["portfolioAvail"] = Web3.from_wei(alotD[1], 'ether')

    alotGas = contracts["SubNetProvider"]["provider"].eth.get_balance(address)
    alotGas = Web3.from_wei(alotGas, 'ether')
    print('alotGas:', alotGas)
    if alotGas < 15 and contracts["ALOT"]["portfolioAvail"] > 100:
      print('adding 100 alot gas')
      contract_data = portfolio.functions.withdrawNative(
        address,
        Web3.to_wei(100, 'ether')
      ).build_transaction({'nonce':getSubnetNonce(),'gas':500000,'maxFeePerGas':Web3.to_wei(20, 'gwei')})
      incrementNonce()
      contracts["SubNetProvider"]["provider"].eth.send_transaction(contract_data)
    elif alotGas < 15 and contracts["ALOT"]["portfolioAvail"] < 100:
      logger.critical("[CONTRACTS] OUT OF GAS AND ALOT IN PORTFOLIO - STOPPING BOT")
      print("OUT OF GAS AND ALOT IN PORTFOLIO")
      status = False
    
    # print("BALANCES AVAX:",contracts["AVAX"]["mainnetBal"], contracts["AVAX"]["portfolioTot"], contracts["AVAX"]["portfolioAvail"])
    # print("BALANCES ALOT:",contracts["ALOT"]["mainnetBal"], contracts["ALOT"]["portfolioTot"], contracts["ALOT"]["portfolioAvail"])
    
    decimals = contracts[base]["tokenDetails"]["evmdecimals"]
    baseShift = 'ether'
    match decimals:
      case 6:
        baseShift = "lovelace"
      case 8:
        baseShift = "8_dec"
    # basec = contracts[base]["deployedContract"].functions.balanceOf(address).call()
    # contracts[base]["mainnetBal"] = Web3.from_wei(basec, baseShift)
    
    baseD = portfolio.functions.getBalance(address, base.encode('utf-8')).call()
    contracts[base]["portfolioTot"] = Web3.from_wei(baseD[0], baseShift)
    contracts[base]["portfolioAvail"] = Web3.from_wei(baseD[1], baseShift)
    # print("BALANCES:",base,contracts[base]["mainnetBal"], contracts[base]["portfolioTot"], contracts[base]["portfolioAvail"])
    
    if quote != "ALOT" and quote != "AVAX":
      decimals = contracts[quote]["tokenDetails"]["evmdecimals"]
      quoteShift = 'ether'
      match decimals:
        case 6:
          quoteShift = "lovelace"
        case 8:
          quoteShift = "8_dec"
      quoteC = contracts[quote]["deployedContract"].functions.balanceOf(address).call()
      contracts[quote]["mainnetBal"] = Web3.from_wei(quoteC, quoteShift)
      
      quoteD = portfolio.functions.getBalance(address, quote.encode('utf-8')).call()
      contracts[quote]["portfolioTot"] = Web3.from_wei(quoteD[0], quoteShift)
      contracts[quote]["portfolioAvail"] = Web3.from_wei(quoteD[1], quoteShift)
      # print("BALANCES:",quote,contracts[quote]["mainnetBal"], contracts[quote]["portfolioTot"], contracts[quote]["portfolioAvail"])
  except Exception as error:
    print("error in getBalances:", error)
  print("finished getting balances:",time.time())
  refreshBalances = False
  return
  
