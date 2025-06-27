#!/usr/bin/env python3
import sys
import asyncio
import signal
import logging
from typing import Optional

import analytics
import marketMaker
import orders
import contracts

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BotManager:
    """Manages bot lifecycle with proper shutdown and restart handling."""
    
    def __init__(self):
        self.shutdown_requested = False
        self.keyboard_interrupt = False
        
    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            self.shutdown_requested = True
            if signum == signal.SIGINT:
                self.keyboard_interrupt = True
                
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    
    async def graceful_shutdown(self, market_pair: str):
        """Perform graceful shutdown by canceling all orders."""
        try:
            logger.info("Performing graceful shutdown...")
            contracts.status = False
            await orders.cancelAllOrders(market_pair, True)
            logger.info("All orders cancelled successfully")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
    
    async def run_analytics(self):
        """Run analytics mode."""
        try:
            await analytics.start()
        except Exception as e:
            logger.error(f"Analytics error: {e}")
            raise
    
    async def run_market_maker(self, network: str = 'm'):
        """Run market maker with restart logic."""
        restart_delay = 15
        
        while not self.shutdown_requested:
            try:
                logger.info(f"Starting market maker on network: {network}")
                await marketMaker.start(network)
                
                # If we reach here, the market maker completed normally
                # Check if it was a graceful shutdown
                if (hasattr(marketMaker, 'market_maker_instance') and 
                    marketMaker.market_maker_instance and 
                    marketMaker.market_maker_instance.shutdown_requested):
                    logger.info("Market maker requested shutdown, not restarting")
                    break
                
                if self.shutdown_requested:
                    break
                    
                logger.info(f"Market maker stopped, restarting in {restart_delay}s...")
                await asyncio.sleep(restart_delay)
                
            except asyncio.CancelledError:
                logger.info("Market maker cancelled")
                if not self.shutdown_requested:
                    await asyncio.sleep(restart_delay)
                    continue
                break
                
            except Exception as e:
                logger.error(f"Market maker error: {e}")
                
                if self.shutdown_requested:
                    break
                
                logger.info(f"Restarting in {restart_delay}s...")
                await asyncio.sleep(restart_delay)
        
        # Perform cleanup
        if hasattr(marketMaker, 'pairStr'):
            await self.graceful_shutdown(marketMaker.pairStr)
    
    async def run(self):
        """Main run method."""
        self.setup_signal_handlers()
        
        try:
            # Parse command line arguments
            if len(sys.argv) < 2:
                logger.error("Usage: python main.py <MARKET_PAIR> [network] [analytics]")
                logger.error("Example: python main.py AVAX_USDC")
                logger.error("Example: python main.py AVAX_USDC fuji")
                logger.error("Example: python main.py AVAX_USDC m analytics")
                return
            
            # Check for analytics mode
            if len(sys.argv) > 2 and sys.argv[2] == "analytics":
                await self.run_analytics()
                return
            
            # Determine network
            network = 'fuji' if len(sys.argv) > 2 and sys.argv[2] == "fuji" else 'm'
            
            # Run market maker
            await self.run_market_maker(network)
            
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            self.keyboard_interrupt = True
            self.shutdown_requested = True
        except Exception as e:
            logger.error(f"Unexpected error in main: {e}")
            raise
        finally:
            logger.info("Bot manager shutdown complete")

async def main():
    """Entry point."""
    bot_manager = BotManager()
    await bot_manager.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        logger.info("Program finished")