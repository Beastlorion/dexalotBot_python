import os, sys, json, math, csv, re
from datetime import datetime, timezone
from dotenv import dotenv_values
from urllib.request import Request, urlopen
import tools, contracts, settings
import asyncio
from pprint import pprint

config = {
  **dotenv_values(".env.shared"),
  **dotenv_values(".env.secret")
}

market = sys.argv[1]
base = tools.getSymbolFromName(market,0)
quote = tools.getSymbolFromName(market,1)
pairStr = base + '/' + quote
settings = settings.settings[market]

async def start():
  if (sys.argv[3] == '0'):
    getDataFromFiles()
    return

  apiUrl = config["apiUrl"]
  pairObj = await tools.getPairObj(pairStr,apiUrl)
  await contracts.initializeProviders(market,settings,False, base)
  signedApiUrl = config["signedApiUrl"]
  startDate = int(datetime.utcnow().timestamp()) - 604800
  endDate = int(datetime.utcnow().timestamp())
  if len(sys.argv) > 4:
    startDate = int(sys.argv[4])
  if len(sys.argv) > 5:
    endDate = int(sys.argv[5])

  print("startDate=",datetime.fromtimestamp(startDate))
  print("endDate=",datetime.fromtimestamp(endDate))

  ordersList = []
  for i in range(math.ceil((endDate-startDate)/2592000)):
    start = endDate - 2592000 * (i+1)
    if start < startDate:
      start = startDate
    end = endDate - 2592000 * i
    if end < startDate:
      break

    print("start=",datetime.fromtimestamp(start))
    print("end=",datetime.fromtimestamp(end))
    itemsperpage = 20
    category = '1'
    url = signedApiUrl + "orders?pair=" + pairStr + "&category="+ category + "&periodfrom=" + datetime.fromtimestamp(start, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&periodto=" + datetime.fromtimestamp(end, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&itemsperpage="+str(itemsperpage)+"&pageno=1"
    try:
      req = Request(url)
      req.add_header('x-signature', contracts.signature)
      ordersJson = urlopen(req).read()
      orders = json.loads(ordersJson)
      if int(orders['count']) > 0:
        ordersList = ordersList + orders['rows']
    except Exception as err:
      print('err in first orders pull in analytics', err)
    rows = int(ordersList[0]['nbrof_rows'])
    print('orders filled:',rows)
    
    if len(ordersList) < 1:
      print('no orders. shutting down.')
      sys.exit()
    if rows > itemsperpage:
      for x in range(1,math.ceil(rows/itemsperpage)):
        url = signedApiUrl + "orders?pair=" + pairStr + "&category="+ category + "&periodfrom=" + datetime.fromtimestamp(start, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&periodto=" + datetime.fromtimestamp(end, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&itemsperpage="+str(itemsperpage)+"&pageno="+str(x+1)
        try:
          req = Request(url)
          req.add_header('x-signature', contracts.signature)
          ordersJson = urlopen(req).read()
          orders = json.loads(ordersJson)
          if int(orders['count']) > 0:
            ordersList = ordersList + orders['rows']
          print(len(ordersList),rows)
        except Exception as err:
          print(err)
  print("ordersList:",len(ordersList))
  runAnalytics(ordersList)

def runAnalytics(ordersList):
  try:
    data = {
      'totalCost' : 0,
      'totalSold' : 0,
      'qtyOutstanding' : 0,
      'totalQtyBought':0,
      'totalQtySold':0,
      'totalFees' : 0,
      'buyFills' : 0,
      'sellFills' : 0,
      'totalVolumeBase': 0,
      'totalVolumeQuote': 0
    };
    for order in ordersList:
      if order['id'] == "0xf82ab7d84f27d8d2e7a6b2859b3f7835550e14f0cf10ea2ae00c500000000000":
        continue
      qtyFilled = float(order['quantityfilled'])
      totalAmount = float(order['totalamount'])
      price = float(order['price'])
      if int(order['side']) == 0:
        data['buyFills'] += 1
        data['totalCost'] += totalAmount
        data['totalQtyBought'] += qtyFilled
      else:
        data['sellFills'] += 1
        data['totalSold'] += totalAmount
        data['totalQtySold'] += qtyFilled
      data['totalFees'] += float(order['totalfee'])
      data['totalVolumeBase'] += qtyFilled
      data['totalVolumeQuote'] += totalAmount
      # if data['buyFills']%10000 == 0:
      #   print(data['totalQtyBought'], data['totalQtySold'])

    data['qtyOutstanding'] = data['totalQtyBought'] - data['totalQtySold']
    data['avgBuyPrice'] = data['totalCost']/data['totalQtyBought']
    data['avgSellPrice'] = data['totalSold']/data['totalQtySold']
    pprint(data)
  except Exception as err:
    print('err in runAnalytics', err)
    pprint(data)

def getDataFromFiles():
  try:
    directory = 'fillData/'+base.lower()+'_'+quote.lower()+'/'
    # List to store all records
    all_records = []

    # Regex pattern to extract date (YYYYMM) from filenames
    filename_pattern = re.compile(rf"{base.lower()}_[a-z]+_(\d{{6}})\.csv")

    # Iterate through files in the directory
    for filename in os.listdir(directory):
        match = filename_pattern.match(filename)
        if match:
            file_date = match.group(1)  # Extracted YYYYMM string
            file_datetime = datetime.strptime(file_date, "%Y%m")  # Convert to datetime object
            
            file_path = os.path.join(directory, filename)
            print('openFile:', file_path)
            with open(file_path, "r", newline="", encoding="utf-8") as csv_file:
              print('readFile:', file_path)
              reader = csv.DictReader(csv_file)  # Read CSV as dictionary
              for row in reader:
                if (row['ts'] > '2025-02-01 00:00:50+00'):
                  all_records.append(row)
                    # sys.exit()
                    # all_records.append({
                    #   'type': row[1],
                    #   'type2': row[2],
                    #   'side': row[3], 
                    #   'price': row[4], 
                    #   'quantity': row[5], 
                    #   'totalamount': row[6], 
                    #   'ts': row[7], 
                    #   'quantityfilled': row[8],
                    #   'totalfee': row[9], 
                    #   'cumgas_cost': row[10]
                    # })

    # Sort all records by date
    all_records.sort(key=lambda x: x['ts'])

    # Print or use the sorted data
    runAnalytics(all_records)
  except Exception as err:
    print('error in getData from files:', err)
  sys.exit()
