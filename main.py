import sys, os, asyncio, time, ast
import tools
import price_feeds
import marketMaker
import orders
import contracts
import analytics

client = {}
marketID = None

async def main():
  if len(sys.argv) > 2 and sys.argv[2] == "analytics":
    await analytics.start()      
  else:
    x = True
    while x is True:
      try:
        net = 'm'
        if sys.argv == 2 and sys.argv[2] == "fuji":
          net = 'fuji'
        await marketMaker.start(net)
      except asyncio.CancelledError:
        print("asyncio.CancelledError")
        await asyncio.sleep(15)
        continue
      except Exception as error:
        print("main error:",error)
        await asyncio.sleep(15)
        continue
      except KeyboardInterrupt:
        print("KeyboardInterrupt")
        print("CANCELLING ORDERS AND SHUTTING DOWN")
        x = False
        contracts.status = False
        await orders.cancelAllOrders(marketMaker.pairStr, True)


# Start and run until complete
# loop = asyncio.get_event_loop()
# task = loop.create_task(main())

# Run until a certain condition or indefinitely
try:
  asyncio.run(main())
except:
  print("FINISHED")
# except KeyboardInterrupt:
#   # Handle other shutdown signals here
#   try:
#     print("CANCELLING ORDERS AND SHUTTING DOWN")
#     loop = asyncio.get_event_loop()
#     task = loop.create_task(orders.cancelAllOrders(marketMaker.pairStr,True))
#     loop.run_until_complete(task)
#   except:
#     print("Stopping. Please confirm that orders have been cancelled.")