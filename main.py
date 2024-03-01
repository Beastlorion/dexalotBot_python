import sys, os, asyncio, time, ast
from dotenv import load_dotenv, dotenv_values
import tools
import price_feeds
import marketMaker
import orders
import contracts

client = {}
marketID = None

async def main():
  try:
    await marketMaker.start()
  except asyncio.CancelledError:
    print("asyncio.CancelledError")
  except KeyboardInterrupt:
    print("KeyboardInterrupt")
  finally:
    print("CANCELLING ORDERS AND SHUTTING DOWN")
    await orders.cancelAllOrders(marketMaker.pairStr, True)

# Start and run until complete
loop = asyncio.get_event_loop()
task = loop.create_task(main())

# Run until a certain condition or indefinitely
try:
  loop.run_until_complete(task)
except KeyboardInterrupt:
  # Handle other shutdown signals here
  try:
    print("CANCELLING ORDERS AND SHUTTING DOWN")
    loop = asyncio.get_event_loop()
    task = loop.create_task(orders.cancelAllOrders(marketMaker.pairStr,True))
    loop.run_until_complete(task)
  except:
    print("Stopping. Please confirm that orders have been cancelled.")