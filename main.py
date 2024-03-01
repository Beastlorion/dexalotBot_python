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
  # try:
  await marketMaker.start()
  # except asyncio.CancelledError:
  #   print("asyncio.CancelledError")
  # finally:
  #   print("finished")
  #   await marketMaker.cancelAllOrders(client, marketID)

# Start and run until complete
loop = asyncio.get_event_loop()
task = loop.create_task(main())

# Run until a certain condition or indefinitely
try:
  loop.run_until_complete(task)
except KeyboardInterrupt:
  # Handle other shutdown signals here
  print("CANCELLING ORDERS AND SHUTTING DOWN")
  task = loop.create_task(orders.cancelAllOrders(marketMaker.pairStr))
  loop.run_until_complete(task)
