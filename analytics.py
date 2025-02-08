import sys, json, math
from datetime import datetime, timezone
from dotenv import dotenv_values
from urllib.request import Request, urlopen
import tools, contracts, settings
import asyncio

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
  apiUrl = config["apiUrl"]
  pairObj = await tools.getPairObj(pairStr,apiUrl)
  
  await contracts.initializeProviders(market,settings,False)
  signedApiUrl = config["signedApiUrl"]

  startDate = int(datetime.utcnow().timestamp()) - 604800
  endDate = int(datetime.utcnow().timestamp())
  if len(sys.argv) > 3:
    startDate = int(sys.argv[3])
  if len(sys.argv) > 4:
    endDate = int(sys.argv[4])

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
    url = signedApiUrl + "orders?pair=" + pairStr + "&category=3" + "&periodfrom=" + datetime.fromtimestamp(start, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&periodto=" + datetime.fromtimestamp(end, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&itemsperpage="+itemsperpage+"&pageno=1"
    try:
      req = Request(url)
      req.add_header('x-signature', contracts.signature)
      ordersJson = urlopen(req).read()
      orders = json.loads(ordersJson)
      # print("orders:",orders)
      if int(orders['count']) > 0:
        ordersList = ordersList + orders['rows']
    except Exception as err:
      print(err)
    if orders['nbrof_rows'] > itemsperpage:
      for x in range(1,math.ceil(orders['nbrof_rows']/itemsperpage)):
        url = signedApiUrl + "orders?pair=" + pairStr + "&category=3" + "&periodfrom=" + datetime.fromtimestamp(start, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&periodto=" + datetime.fromtimestamp(end, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z') + "&itemsperpage="+itemsperpage+"&pageno="+(x+1)
        try:
          req = Request(url)
          req.add_header('x-signature', contracts.signature)
          ordersJson = urlopen(req).read()
          orders = json.loads(ordersJson)
          if int(orders['count']) > 0:
            ordersList = ordersList + orders['rows']
        except Exception as err:
          print(err)
        await asyncio.sleep(0.1)
  print("ordersList:",len(ordersList))
