import sys, os, asyncio, time, ast, json, boto3
from botocore.exceptions import ClientError
import price_feeds
from dotenv import dotenv_values
import urllib.request
from urllib.request import Request, urlopen
from multiprocessing import Process

def getSymbolFromName(market,position):
  return market.split('_')[position]

def getKey(d,v):
  for key,item in d.items():
    if item == v:
      return key

async def callback(response):
  # print(f"Received response: {response}")
  return response

async def placeOrdersCallback(response):
  print(f"Received response: {response}")
  return response

async def getPairObj(pair, apiUrl):
  pairs = json.loads(urlopen(apiUrl + "pairs").read())
  for item in pairs:
    if item["pair"] == pair:
      return item

def getIncrement(quoteDisplayDecimals):
  return float(1 * pow(10,-1 * quoteDisplayDecimals));
    
def getSpread(marketPrice,settings,funds,totalFunds,level,side):
  defensiveSkew = 0
  volSpread = price_feeds.volSpread
  if side == 1:
    funds = funds * marketPrice
  if (funds > totalFunds/2):
    multiple = ((funds/totalFunds) - .5) * 20
    defensiveSkew = multiple * settings["defensiveSkew"];
  spread = defensiveSkew/100 + level["spread"]/100 + volSpread/2
  return spread

def getQty(price, side, level, availableFunds,pairObj):
  if side == 0:
    # print("AVAILABLE FUNDS IN QUOTE BID: ",availableFunds, "AMOUNT: ",level["qty"])
    if level["qty"] < availableFunds/price:
      return level["qty"]
    elif availableFunds > float(pairObj["mintrade_amnt"]):
      return availableFunds/price - pow(10,-1 *pairObj["quotedisplaydecimals"])
    else: 
      return 0
  elif side == 1:
    # print("AVAILABLE FUNDS ASK: ",availableFunds, "AMOUNT: ",level["qty"])
    if level["qty"] < availableFunds:
        return level["qty"]
    elif availableFunds * price > float(pairObj["mintrade_amnt"]):
      return availableFunds - pow(10,-1 *pairObj["quotedisplaydecimals"])
    else:
      return 0
  else: 
    return 0

def getPrivateKey(market,settings):

  secret_name = settings['secret_name']
  region_name = settings['secret_location']

  # Create a Secrets Manager client
  session = boto3.session.Session()
  client = session.client(
      service_name='secretsmanager',
      region_name=region_name
  )

  try:
      get_secret_value_response = client.get_secret_value(
          SecretId=secret_name
      )
  except ClientError as e:
      # For a list of exceptions thrown, see
      # https://docs.aws.amazon.com/secretsmanager/latest/apireference/API_GetSecretValue.html
      raise e

  secret = ast.literal_eval(get_secret_value_response['SecretString'])
  pk = secret[secret_name]
  if pk[:2] != '0x':
    pk = '0x' + pk
  return pk

def getTakerFill(settings,marketPrice,executePrice,book,bybitBook,side):
  qtyFilled = 0
  qtyAvailable = 0
  if side == 0:
    for order in book:
      if order[0] < executePrice:
        qtyFilled = qtyFilled + order[1]
      else:
        break
    for order in bybitBook:
      if order[0] > marketPrice * (1 - settings['maxSlippage']):
        qtyAvailable = qtyAvailable + order[1]
      else:
        break
  if side == 1:
    for order in book:
      if order[0] > executePrice:
        qtyFilled = qtyFilled + order[1]
      else:
        break
    for order in bybitBook:
      if order[0] < marketPrice * (1 + settings['maxSlippage']):
        qtyAvailable = qtyAvailable + order[1]
      else:
        break
  if qtyFilled > qtyAvailable:
    qtyFilled = qtyAvailable
  print(qtyFilled,qtyAvailable)
  return qtyFilled