import sys, os, asyncio, time, ast, json
from hexbytes import HexBytes
import tools
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen
from web3 import Web3, AsyncWeb3, AsyncHTTPProvider
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
replaceStatus = 0

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

async def initializeProviders(market):
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
    if item["symbol"] in contracts and item["symbol"] != "AVAX":
      contracts[item["symbol"]]["tokenDetails"] = item
      contracts[item["symbol"]]["deployedContract"] = contracts["AvaxcProvider"]["provider"].eth.contract(address=contracts[item["symbol"]]["tokenDetails"]["address"], abi=ERC20ABI["abi"])
    elif item["symbol"] == "AVAX":
      contracts[item["symbol"]]["tokenDetails"] = item
      
      
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
  
async def startBlockFilter():
  block_filter = contracts["SubNetProvider"]["provider"].eth.filter('latest')
  # worker = Thread(target=log_loop, args=(block_filter, .5), daemon=True)
  # worker.start()
  asyncio.create_task(log_loop(block_filter, 0.5))
    
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
  global activeOrders, replaceStatus, status
  try:
    block = contracts["SubNetProvider"]["provider"].eth.get_block(event.hex())
    transactionsProcessed = []
    for hash in block.transactions:
      for tx in pendingTransactions:
        if tx["hash"] == hash:
          receipt = contracts["SubNetProvider"]["provider"].eth.get_transaction_receipt(hash)
          if receipt.status == 1:
            transactionsProcessed.append(tx)
            print('transaction success:', tx['purpose'])
            if tx['purpose'] == 'placeOrder' or tx['purpose'] == 'addOrderList':
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
    
def newPendingTx(purpose,hash,orders = []):
  pendingTransactions.append({'purpose': purpose,'status':'pending','hash': hash,'orders':orders})

