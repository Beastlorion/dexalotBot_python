import sys, os, asyncio, time, ast, json
from hexbytes import HexBytes
from websockets.sync.client import connect
import tools
from dotenv import dotenv_values
import urllib.request
from urllib.request import Request, urlopen
from web3 import Web3, AsyncWeb3, AsyncHTTPProvider
from eth_utils.units import units, decimal
from eth_account import Account
from eth_account.signers.local import LocalAccount
from web3.middleware import construct_sign_and_send_raw_middleware, geth_poa_middleware
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

freezeNewOrders = False
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
bestBid = None
bestAsk = None
replaceStatus = 0
addStatus = 0
refreshBalances = False

async def getDeployments(dt, s):
  url = config["apiUrl"] + "deployment?contracttype=" + dt + "&returnabi=true"
  # contract = urllib.request.urlopen(url).read()
  # contract = json.loads(contract)
  async with s.get(url) as r:
    if r.status != 200:
      r.raise_for_status()
    contract = json.loads(await r.read())
    for item in contract :
      contracts[item["contract_name"]] = item

async def getTokenDetails():
  url = config["apiUrl"] + "tokens/"
  tokenDetails = json.loads(urllib.request.urlopen(url).read())
  return tokenDetails

async def initializeProviders(market,settings):
  contracts["SubNetProvider"] = {
    "provider": Web3(Web3.HTTPProvider(config["rpc_url"])),
    # "provider": AsyncWeb3(AsyncHTTPProvider(config["rpc_url"])),
    "nonce": 0
  }
  # await contracts["SubNetProvider"]['provider'].is_connected()
  contracts["SubNetProvider"]["provider"].middleware_onion.inject(geth_poa_middleware, layer=0)
  contracts["AvaxcProvider"] = {
    "provider": Web3(Web3.HTTPProvider(config["avaxc_rpc_url"])),
    # "provider": AsyncWeb3(AsyncHTTPProvider(config["avaxc_rpc_url"])),
    "nonce": 0
  }
  # await contracts["AvaxcProvider"]['provider'].is_connected()
  contracts["AvaxcProvider"]["provider"].middleware_onion.inject(geth_poa_middleware, layer=0)
  if len(settings['secret_name'])>0:
    private_key = tools.getPrivateKey(market,settings)
  else:
    private_key = config[market+"_pk"]
  assert private_key is not None, "You must set PRIVATE_KEY environment variable"
  assert private_key.startswith("0x"), "Private key must start with 0x hex prefix"

  account: LocalAccount = Account.from_key(private_key)
  contracts["SubNetProvider"]["provider"].middleware_onion.add(construct_sign_and_send_raw_middleware(account))
  contracts["AvaxcProvider"]["provider"].middleware_onion.add(construct_sign_and_send_raw_middleware(account))
  
  # set default account
  contracts["SubNetProvider"]["provider"].eth.default_account = account.address
  contracts["AvaxcProvider"]["provider"].eth.default_account = account.address
  
  contracts["SubNetProvider"]["provider"].strict_bytes_type_checking = False
  contracts["AvaxcProvider"]["provider"].strict_bytes_type_checking = False
  global address, signature
  address = account.address
  message = encode_defunct(text="dexalot")
  signedMessage = contracts["SubNetProvider"]["provider"].eth.account.sign_message(message, private_key=private_key)
  global signedHeaders
  signature = address + ':' + signedMessage.signature.hex()
  contracts["SubNetProvider"]["nonce"] = contracts["SubNetProvider"]["provider"].eth.get_transaction_count(address)
  contracts["AvaxcProvider"]["nonce"] = contracts["AvaxcProvider"]["provider"].eth.get_transaction_count(address)
  print('finished initializeProviders')
  
async def initializeContracts(market,pairStr):
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
  
  contracts["PortfolioMain"]["deployedContract"] = contracts["AvaxcProvider"]["provider"].eth.contract(address=contracts["PortfolioMain"]["address"], abi=contracts["PortfolioMain"]["abi"]["abi"])
  contracts["PortfolioSub"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["PortfolioSub"]["address"], abi=contracts["PortfolioSub"]["abi"]["abi"])
  contracts["TradePairs"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["TradePairs"]["address"], abi=contracts["TradePairs"]["abi"]["abi"])
  contracts["OrderBooks"]["deployedContract"] = contracts["SubNetProvider"]["provider"].eth.contract(address=contracts["OrderBooks"]["address"], abi=contracts["OrderBooks"]["abi"]["abi"])
  contracts["OrderBooks"]["id0"] = contracts["TradePairs"]["deployedContract"].functions.getBookId(pairStr.encode('utf-8'), 0).call()
  contracts["OrderBooks"]["id1"] = contracts["TradePairs"]["deployedContract"].functions.getBookId(pairStr.encode('utf-8'), 1).call()
  
  if base == 'sAVAX':
    contracts["sAVAX"]["proxy"] = contracts["AvaxcProvider"]["provider"].eth.contract(address='0x2b2C81e08f1Af8835a78Bb2A90AE924ACE0eA4bE', abi=savaxABI)
  
  tokens = await getTokenDetails()
  for item in tokens:
    if item["subnet_symbol"] in contracts and item["subnet_symbol"] != "AVAX":
      contracts[item["subnet_symbol"]]["tokenDetails"] = item
      contracts[item["subnet_symbol"]]["deployedContract"] = contracts["AvaxcProvider"]["provider"].eth.contract(address=contracts[item["subnet_symbol"]]["tokenDetails"]["address"], abi=ERC20ABI["abi"])
    elif item["subnet_symbol"] == "AVAX":
      contracts[item["subnet_symbol"]]["tokenDetails"] = item
  print('finished initializeContracts')
  return
      
      
def signTransaction(provider,tx):
  return provider.eth.account.sign_transaction(tx, private_key=config[market+"_pk"])

async def refreshDexalotNonce():
  global nonce
  newNonce = contracts["SubNetProvider"]["provider"].eth.get_transaction_count(address)
  if newNonce > nonce:
    nonce = newNonce
    print("newNonce:",newNonce)
  return nonce

def getSubnetNonce():
  return nonce

def incrementNonce():
  global nonce
  nonce = nonce + 1
  
async def startDataFeeds(pairObj):
  block_filter = contracts["SubNetProvider"]["provider"].eth.filter('latest')
  # a = asyncio.create_task(log_loop(block_filter, 0.5))
  b = asyncio.to_thread(dexalotOrderFeed)
  c = asyncio.to_thread(dexalotBookFeed,pairObj)
  d = asyncio.create_task(updateBalancesLoop(pairObj))
  await asyncio.gather(b,c,d)
  
async def updateBalancesLoop(pairObj):
  while status:
    if (refreshBalances):
      await asyncio.to_thread(getBalances,pairObj['pair'].split('/')[0],pairObj['pair'].split('/')[1])
    await asyncio.sleep(0.5)
  return
    
  
def dexalotBookFeed(pairObj):
  global bestAsk,bestBid
  print("dexalotBookFeed START")
  msg = {"data":pairObj['pair'],"pair":pairObj['pair'],"type":"subscribe","decimal":3}
  with connect("wss://api.dexalot.com") as websocket:
    websocket.send(json.dumps(msg))
    while status:
      try:
        message = str(websocket.recv())
        parsed = ast.literal_eval(message)
        if parsed['type'] == 'orderBooks':
          data = parsed['data']
          bestBid = float(data['buyBook'][0]['prices'].split(',')[0])/pow(10,pairObj['quote_evmdecimals'])
          bestAsk = float(data['sellBook'][0]['prices'].split(',')[0])/pow(10,pairObj['quote_evmdecimals'])
          # print('BEST BID:', bestBid, "BEST ASK:",bestAsk)
      except Exception as error:
        print("error in dexalotBookFeed feed:", error)
    msg = {"data":pairStr,"pair":pairStr,"type":"unsubscribe","decimal":3}
    websocket.send(json.dumps(msg))
    return
        
      
def dexalotOrderFeed():
  global addStatus, replaceStatus, refreshBalances
  print("dexalotOrderFeed START")
  msg = {"type":"tradereventsubscribe", "signature":signature}
  with connect("wss://api.dexalot.com") as websocket:
    websocket.send(json.dumps(msg))
    while status:
      try:
        message = str(websocket.recv())
        parsed = ast.literal_eval(message)
        data = parsed['data']
        if parsed['type'] == "orderStatusUpdateEvent":
          hex1 = HexBytes(data["clientOrderId"])
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
          elif data['status'] in ['FILLED','EXPIRED','KILLED']:
            refreshBalances = True
            for order in activeOrders:
              if clientOrderID == order["clientOrderID"].decode('utf-8'):
                print('Order',data['status'],'and removed from activeOrders:',parsed)
                activeOrders.remove(order)
          elif (data['status'] in ['NEW','REJECTED','CANCEL_REJECT']):
            for tx in pendingTransactions:
              if tx['purpose'] in ['addOrderList','replaceOrderList'] :
                for order in tx['orders']:
                  if clientOrderID == order["clientOrderID"].decode('utf-8') and data['status'] == 'NEW' and not order['tracked']:
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
                    order['tracked'] = True
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
          elif data['status'] == 'CANCELED':
            for order in activeOrders:
              if clientOrderID == order["clientOrderID"].decode('utf-8'):
                order['status'] = data['status']
      except Exception as error:
        print("error in dexalotOrderFeed feed:", error)
    msg = {"type":"tradereventsubscribe", "signature":signature}
    websocket.send(json.dumps(msg))
    return
        
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

def getBalances(base, quote):
  print("get balances",time.time())
  global refreshBalances
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
    
    if base != "ALOT" and base != "AVAX":
      decimals = contracts[base]["tokenDetails"]["evmdecimals"]
      shift = 'ether'
      match decimals:
        case 6:
          shift = "lovelace"
        case 8:
          shift = "8_dec"
      basec = contracts[base]["deployedContract"].functions.balanceOf(address).call()
      contracts[base]["mainnetBal"] = Web3.from_wei(basec, shift)
      
      baseD = portfolio.functions.getBalance(address, base.encode('utf-8')).call()
      contracts[base]["portfolioTot"] = Web3.from_wei(baseD[0], shift)
      contracts[base]["portfolioAvail"] = Web3.from_wei(baseD[1], shift)
      # print("BALANCES:",base,contracts[base]["mainnetBal"], contracts[base]["portfolioTot"], contracts[base]["portfolioAvail"])
    
    if quote != "ALOT" and quote != "AVAX":
      decimals = contracts[quote]["tokenDetails"]["evmdecimals"]
      shift = 'ether'
      match decimals:
        case 6:
          shift = "lovelace"
        case 8:
          shift = "8_dec"
      quoteC = contracts[quote]["deployedContract"].functions.balanceOf(address).call()
      contracts[quote]["mainnetBal"] = Web3.from_wei(quoteC, shift)
      
      quoteD = portfolio.functions.getBalance(address, quote.encode('utf-8')).call()
      contracts[quote]["portfolioTot"] = Web3.from_wei(quoteD[0], shift)
      contracts[quote]["portfolioAvail"] = Web3.from_wei(quoteD[1], shift)
      # print("BALANCES:",quote,contracts[quote]["mainnetBal"], contracts[quote]["portfolioTot"], contracts[quote]["portfolioAvail"])
  except Exception as error:
    print("error in getBalances:", error)
  print("finished getting balances:",time.time())
  refreshBalances = False
  return
  