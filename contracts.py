import sys, os, asyncio, time, ast, json
from hexbytes import HexBytes
import websockets
import tools, orders
from dotenv import dotenv_values
import urllib.request
from urllib.request import Request, urlopen
from web3 import Web3, AsyncWeb3, AsyncHTTPProvider
from eth_utils.units import units, decimal
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.middleware import SignAndSendRawMiddlewareBuilder, ExtraDataToPOAMiddleware
from eth_account.messages import encode_defunct
ERC20ABIf = open('./ABIs/ERC20ABI.json')
savaxABIf = open('./ABIs/savax_ABI.json')

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}

units.update(
    {
        "8_dec": decimal.Decimal("100000000"),  # Add in 8 decimals
    }
)

contracts = {}
tokenDetails = None
address = None
signature = None
ERC20ABI = json.load(ERC20ABIf)
savaxABI = json.load(savaxABIf)
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
  apiUrl = config['fuji_apiUrl'] if testnet else config["apiUrl"]
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
  apiUrl = config["fuji_apiUrl"] if testnet else config["apiUrl"]
  url = apiUrl + "tokens/"
  tokenDetails = json.loads(urllib.request.urlopen(url).read())
  return tokenDetails

async def initializeProviders(market,settings, testnet, base):
  global address, signature

  if len(settings['secret_name'])>0:
    private_key = tools.getPrivateKey(market,settings)
  else:
    private_key = config[market+"_pk"]
  assert private_key is not None, "You must set PRIVATE_KEY environment variable"
  assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"

  account: LocalAccount = Account.from_key(private_key)
  address = account.address
  


  rpc_url = config["fuji_rpc_url"] if testnet else config["dexalot_rpc_url"]
  contracts["SubNetProvider"] = {
    "provider": Web3(Web3.HTTPProvider(rpc_url)),
    "nonce": 0
  }
  contracts["SubNetProvider"]["provider"].middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
  contracts["SubNetProvider"]["provider"].middleware_onion.inject(SignAndSendRawMiddlewareBuilder.build(private_key),layer=0)
  contracts["SubNetProvider"]["provider"].eth.default_account = account.address
  contracts["SubNetProvider"]["provider"].strict_bytes_type_checking = False
  contracts["SubNetProvider"]["nonce"] = contracts["SubNetProvider"]["provider"].eth.get_transaction_count(address)

  message = encode_defunct(text="dexalot")
  signedMessage = contracts["SubNetProvider"]["provider"].eth.account.sign_message(message, private_key=private_key)
  signature = address + ':0x' + signedMessage.signature.hex()

  try:
    avaxc_rpc_url = config["fuji_avaxc_rpc_url"] if testnet else config["avaxc_rpc_url"]
    contracts["AvaxcProvider"] = {
      "provider": Web3(Web3.HTTPProvider(avaxc_rpc_url)),
      "nonce": 0
    }
    contracts["AvaxcProvider"]["provider"].middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    contracts["AvaxcProvider"]["provider"].middleware_onion.inject(SignAndSendRawMiddlewareBuilder.build(private_key),layer=0)
    contracts["AvaxcProvider"]["provider"].eth.default_account = account.address
    contracts["AvaxcProvider"]["provider"].strict_bytes_type_checking = False
    contracts["AvaxcProvider"]["nonce"] = contracts["AvaxcProvider"]["provider"].eth.get_transaction_count(address)
  except Exception as error:
    print('error setting avaxc provider:', error)

  try:
    contracts["ArbProvider"] = {
      "provider": Web3(Web3.HTTPProvider(config["arb_rpc_url"])),
      "nonce": 0
    }
    contracts["ArbProvider"]["provider"].middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    contracts["ArbProvider"]["provider"].middleware_onion.inject(SignAndSendRawMiddlewareBuilder.build(private_key),layer=0)
    contracts["ArbProvider"]["provider"].eth.default_account = account.address
    contracts["ArbProvider"]["provider"].strict_bytes_type_checking = False
    contracts["ArbProvider"]["nonce"] = contracts["ArbProvider"]["provider"].eth.get_transaction_count(address)
  except Exception as error:
    print('error setting arbitrum provider:', error)
    
  if base in ['TOSHI', 'ETH']:
    try:
      contracts["BaseProvider"] = {
      "provider": Web3(Web3.HTTPProvider(config["base_rpc_url"])),
      "nonce": 0
      }
      contracts["BaseProvider"]["provider"].middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
      contracts["BaseProvider"]["provider"].middleware_onion.inject(SignAndSendRawMiddlewareBuilder.build(private_key),layer=0)
      contracts["BaseProvider"]["provider"].eth.default_account = account.address
      contracts["BaseProvider"]["provider"].strict_bytes_type_checking = False
      contracts["BaseProvider"]["nonce"] = contracts["BaseProvider"]["provider"].eth.get_transaction_count(address)
    except Exception:
      print('error setting base provider:', error)
  print('finished initializeProviders')
  
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
  return provider.eth.account.sign_transaction(tx, private_key=config[market+"_pk"])

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
  # block_filter = contracts["SubNetProvider"]["provider"].eth.filter('latest')
  # a = asyncio.create_task(log_loop(block_filter, 0.5))
  c = asyncio.create_task(handleWebscokets(pairObj, testnet))
  # d = asyncio.create_task(updateBalancesLoop(pairObj))
  await asyncio.gather(c)
  
# async def updateBalancesLoop(pairObj):
#   while status:
#     if (refreshBalances):
#       await asyncio.to_thread(getBalances,pairObj['pair'].split('/')[0],pairObj['pair'].split('/')[1],pairObj)
#     await asyncio.sleep(0.5)
#   return
    
async def handleWebscokets(pairObj, testnet):
  global status, reconnect, bestAsk, bestBid, bids, asks, addStatus, replaceStatus, refreshBalances, retrigger, orderIDsToCancel, takerFilled, makerFilled, refreshOrderLevel
  base = pairObj['pair'].split('/')[0]
  quote = pairObj['pair'].split('/')[1]
  baseDecimals = pairObj['basedisplaydecimals']
  quoteDecimals = pairObj['quotedisplaydecimals']
  subscribeBook = {"data":pairObj['pair'],"pair":pairObj['pair'],"type":"subscribe","decimal":pairObj["quotedisplaydecimals"]}
  tradereventsubscribe = {"type":"tradereventsubscribe", "signature":signature}
  unsubscribeBook = {"data":pairObj['pair'],"pair":pairObj['pair'],"type":"unsubscribe"}
  tradereventunsubscribe = {"type":"tradereventunsubscribe", "signature":signature}
  wsUrl = "wss://api.dexalot-test.com" if testnet else "wss://api.dexalot.com"
  
  while status:
    reconnect = False
    try:
      if 'wsKey' in config and len(config['wsKey'])>1:
        url = 'https://api.dexalot.com/privapi/auth/getwstoken'
        req = Request(url)
        req.add_header('x-apikey', config['wsKey'])
        token = json.loads(urlopen(req).read())['token']
        wsUrl = "wss://api.dexalot.com/api/ws?wstoken=" + token
        #wsUrl = "wss://api.dexalot.com?wstoken=" + token

      async with websockets.connect(wsUrl) as websocket:
        await websocket.send(json.dumps(subscribeBook))
        await websocket.send(json.dumps(tradereventsubscribe))
        print("dexalotOrderFeed and dexalotBookFeed START")
        while status and not reconnect:
          try:
            message = str(await websocket.recv())
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
              if askPrices[0] == '':
                askPrices = []
              bidQtys = data['buyBook'][0]['quantities'].split(',')
              askPrices = data['sellBook'][0]['prices'].split(',')
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
              if data['status'] in ['NEW','PARTIAL','REJECTED','CANCEL_REJECT']:
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
          except websockets.ConnectionClosed:
            break
          except websockets.ConnectionClosedError:
            break
          except Exception as error:
            if parsed['type'] == "orderStatusUpdateEvent":
              print("FAILED ORDER TRACKING:", parsed['data'], error)
              status = False
            continue
        asyncio.create_task(websocket.send(json.dumps(unsubscribeBook)))
        asyncio.create_task(websocket.send(json.dumps(tradereventunsubscribe)))
        await asyncio.sleep(0.05)
    except Exception as error:
      print('error during handleWebscokets:',error)
      await asyncio.sleep(0.1)
      
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
      # if tx['to'] == WETH_ADDRESS:
      #     print(f'Found interaction with WETH contract! {tx}')
    for tx in transactionsProcessed:
      print("ACTIVE ORDERS:",activeOrders)
      pendingTransactions.remove(tx)
  except Exception as error:
    print("error in blockfilter handleEvents:", error)
    status = False
    return
  return
    
def newPendingTx(purpose,orders = []):
  print('New pending transaction:', purpose, len(orders), time.time())
  pendingTransactions.append({'purpose': purpose,'status':'pending','orders':orders})

def getBalances(base, quote, pairObj):
  global refreshBalances, baseShift, quoteShift
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
    
    alotD = portfolio.functions.getBalance(address, "AVAX".encode('utf-8')).call()
    contracts["AVAX"]["portfolioTot"] = Web3.from_wei(alotD[0], 'ether')
    contracts["AVAX"]["portfolioAvail"] = Web3.from_wei(alotD[1], 'ether')
    
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
    #print("BALANCES:",base,contracts[base]["mainnetBal"], contracts[base]["portfolioTot"], contracts[base]["portfolioAvail"])
    
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
      #print("BALANCES:",quote,contracts[quote]["mainnetBal"], contracts[quote]["portfolioTot"], contracts[quote]["portfolioAvail"])
  except Exception as error:
    print("error in getBalances:", error)
  print("finished getting balances:",time.time())
  refreshBalances = False
  return
  
