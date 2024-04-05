import sys, os, asyncio, time, ast, json
import tools
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen
from web3 import Web3
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

async def getDeployments(dt):
  url = config["apiUrl"] + "deployment?contracttype=" + dt + "&returnabi=true"
  contract = urllib.request.urlopen(url).read()
  contract = json.loads(contract)
  for item in contract :
    contracts[item["contract_name"]] = item

async def getTokenDetails():
  url = config["apiUrl"] + "tokens/"
  tokenDetails = json.loads(urllib.request.urlopen(url).read())
  return tokenDetails

async def initializeProviders(market):
  contracts["SubNetProvider"] = {
    "provider": Web3(Web3.HTTPProvider(config["rpc_url"])),
    "nonce": 0
  }
  contracts["SubNetProvider"]["provider"].middleware_onion.inject(geth_poa_middleware, layer=0)
  contracts["AvaxcProvider"] = {
    "provider": Web3(Web3.HTTPProvider(config["avaxc_rpc_url"])),
    "nonce": 0
  }
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
    if item["symbol"] in contracts and item["symbol"] != "AVAX" and item["env"] == "production-multi-avax":
      contracts[item["symbol"]]["tokenDetails"] = item
      contracts[item["symbol"]]["deployedContract"] = contracts["AvaxcProvider"]["provider"].eth.contract(address=contracts[item["symbol"]]["tokenDetails"]["address"], abi=ERC20ABI["abi"])
    elif item["symbol"] == "AVAX" and item["env"] == "production-multi-avax":
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
  asyncio.create_task(log_loop(block_filter, 2))
    
async def log_loop(event_filter, poll_interval):
  global activeOrders
  print("start block filter")
  while True:
    try:
      for event in event_filter.get_new_entries():
        block = contracts["SubNetProvider"]["provider"].eth.get_block(event.hex())
        transactionsProcessed = []
        for hash in block.transactions:
          for tx in pendingTransactions:
            if tx["hash"] == hash:
              receipt = contracts["SubNetProvider"]["provider"].eth.get_transaction_receipt(hash)
              if receipt.status == 1:
                transactionsProcessed.append(tx)
                if tx['purpose'] == 'placeOrder' or tx['purpose'] == 'addOrderList':
                  activeOrders = activeOrders + tx['orders']
                  print("ACTIVE ORDERS:", activeOrders)
                print('transaction success:', tx['purpose'])
              elif tx['purpose'] == 'cancel':
                print('cancel tx failed:', tx)
                tx['status'] = 'failed'
              else:
                print('tx failed:', tx)
                transactionsProcessed.append(tx)
          # if tx['to'] == WETH_ADDRESS:
          #     print(f'Found interaction with WETH contract! {tx}')
        for tx in transactionsProcessed:
          pendingTransactions.remove(tx)
    except:
      global status
      print("exception, closing block filter")
      status = False
    await asyncio.sleep(poll_interval)
    
def newPendingTx(purpose,hash,orders = []):
  pendingTransactions.append({'purpose': purpose,'status':'pending','hash': hash,'orders':orders})
