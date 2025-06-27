#!/usr/bin/env python3
"""
Simple test to verify KeyboardInterrupt handling.
"""

import asyncio
import logging
import signal
import contracts

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global shutdown flag
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle shutdown signals."""
    global shutdown_requested
    logger.info(f"Received signal {signum}")
    shutdown_requested = True
    contracts.status = False

async def test_loop():
    """Test loop that should respond to Ctrl+C."""
    global shutdown_requested
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("Starting test loop...")
    logger.info("Press Ctrl+C to test shutdown")
    
    contracts.status = True
    iteration = 0
    
    while not shutdown_requested and contracts.status:
        try:
            iteration += 1
            logger.info(f"Test iteration {iteration}")
            
            # Simulate work with frequent shutdown checks
            for _ in range(10):  # Check every 0.1s for 1s total
                if shutdown_requested or not contracts.status:
                    break
                await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt caught in test loop")
            shutdown_requested = True
            break
        except Exception as e:
            logger.error(f"Error in test loop: {e}")
            break
    
    logger.info("Test loop stopped")

if __name__ == "__main__":
    try:
        asyncio.run(test_loop())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt caught at top level")
    finally:
        logger.info("Test completed") 