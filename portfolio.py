import sys, os, asyncio, time, ast, json
from web3 import Web3
import contracts
from dotenv import load_dotenv, dotenv_values
import urllib.request
from urllib.request import Request, urlopen
from eth_utils.units import units, decimal

units.update(
    {
        "8_dec": decimal.Decimal("100000000"),  # Add in 8 decimals
    }
)

config = {
    **dotenv_values(".env.shared"),
    **dotenv_values(".env.secret")
}

def getBalances(base, quote):
  address = contracts.address
  portfolio = contracts.contracts["PortfolioSub"]["deployedContract"]
  
  try:
    # get AVAX balances
    avaxC = contracts.contracts["AvaxcProvider"]["provider"].eth.get_balance(address)
    contracts.contracts["AVAX"]["mainnetBal"] = Web3.from_wei(avaxC, 'ether')
    
    avaxD = portfolio.functions.getBalance(address, "AVAX".encode('utf-8')).call()
    contracts.contracts["AVAX"]["portfolioTot"] = Web3.from_wei(avaxD[0], 'ether')
    contracts.contracts["AVAX"]["portfolioAvail"] = Web3.from_wei(avaxD[1], 'ether')
    
    # get ALOT balances
    alotC = contracts.contracts["ALOT"]["deployedContract"].functions.balanceOf(address).call()
    contracts.contracts["ALOT"]["mainnetBal"] = Web3.from_wei(alotC, 'ether')
    
    alotD = portfolio.functions.getBalance(address, "AVAX".encode('utf-8')).call()
    contracts.contracts["AVAX"]["portfolioTot"] = Web3.from_wei(alotD[0], 'ether')
    contracts.contracts["AVAX"]["portfolioAvail"] = Web3.from_wei(alotD[1], 'ether')
    
    # print("BALANCES AVAX:",contracts.contracts["AVAX"]["mainnetBal"], contracts.contracts["AVAX"]["portfolioTot"], contracts.contracts["AVAX"]["portfolioAvail"])
    # print("BALANCES ALOT:",contracts.contracts["ALOT"]["mainnetBal"], contracts.contracts["ALOT"]["portfolioTot"], contracts.contracts["ALOT"]["portfolioAvail"])
    
    if base != "ALOT" and base != "AVAX":
      decimals = contracts.contracts[base]["tokenDetails"]["evmdecimals"]
      shift = 'ether'
      match decimals:
        case 6:
          shift = "lovelace"
        case 8:
          shift = "8_dec"
      basec = contracts.contracts[base]["deployedContract"].functions.balanceOf(address).call()
      contracts.contracts[base]["mainnetBal"] = Web3.from_wei(basec, shift)
      
      baseD = portfolio.functions.getBalance(address, base.encode('utf-8')).call()
      contracts.contracts[base]["portfolioTot"] = Web3.from_wei(baseD[0], shift)
      contracts.contracts[base]["portfolioAvail"] = Web3.from_wei(baseD[1], shift)
      # print("BALANCES:",base,contracts.contracts[base]["mainnetBal"], contracts.contracts[base]["portfolioTot"], contracts.contracts[base]["portfolioAvail"])
    
    if quote != "ALOT" and quote != "AVAX":
      decimals = contracts.contracts[quote]["tokenDetails"]["evmdecimals"]
      shift = 'ether'
      match decimals:
        case 6:
          shift = "lovelace"
        case 8:
          shift = "8_dec"
      quoteC = contracts.contracts[quote]["deployedContract"].functions.balanceOf(address).call()
      contracts.contracts[quote]["mainnetBal"] = Web3.from_wei(quoteC, shift)
      
      quoteD = portfolio.functions.getBalance(address, quote.encode('utf-8')).call()
      contracts.contracts[quote]["portfolioTot"] = Web3.from_wei(quoteD[0], shift)
      contracts.contracts[quote]["portfolioAvail"] = Web3.from_wei(quoteD[1], shift)
      # print("BALANCES:",quote,contracts.contracts[quote]["mainnetBal"], contracts.contracts[quote]["portfolioTot"], contracts.contracts[quote]["portfolioAvail"])
  except Exception as error:
    print("error in getBalances:", error)
  print("finished getting balances:",time.time())
  return
  