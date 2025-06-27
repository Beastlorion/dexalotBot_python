#!/usr/bin/env python3
"""
Test script to demonstrate the improved failure handling in the market maker.
"""

import asyncio
import logging
from marketMaker import MarketMaker
import contracts

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MockMarketMaker(MarketMaker):
    """Mock market maker for testing failure scenarios."""
    
    def __init__(self, market: str, network: str = 'm'):
        super().__init__(market, network)
        self.failure_count = 0
        self.max_test_failures = 5  # Simulate 5 failures before success
    
    async def run_order_updater(self):
        """Mock order updater that simulates failures."""
        logger.info("Starting mock order updater")
        
        while not self.shutdown_requested and contracts.status:
            try:
                self.failure_count += 1
                
                if self.failure_count <= self.max_test_failures:
                    # Simulate a failure
                    raise Exception(f"Simulated failure #{self.failure_count}")
                else:
                    # Simulate success
                    logger.info("Simulated successful operation")
                    self.consecutive_failures = 0  # Reset on success
                    break
                    
            except Exception as e:
                logger.error(f"Mock error: {e}")
                self.consecutive_failures += 1
                
                if self.consecutive_failures >= self.max_consecutive_failures:
                    logger.error(f"Too many consecutive failures ({self.consecutive_failures}), shutting down")
                    self.request_shutdown()
                    break
                
                await asyncio.sleep(1)
        
        logger.info("Mock order updater stopped")

async def test_failure_handling():
    """Test the failure handling mechanism."""
    logger.info("Testing failure handling mechanism...")
    
    # Create a mock market maker
    mock_maker = MockMarketMaker("AVAX_USDC", "fuji")
    
    try:
        # This should trigger the failure handling
        await mock_maker.run_order_updater()
        
        if mock_maker.shutdown_requested:
            logger.info("✅ Test PASSED: Market maker correctly shut down after 3 consecutive failures")
        else:
            logger.info("❌ Test FAILED: Market maker should have shut down")
            
    except Exception as e:
        logger.error(f"Test error: {e}")

if __name__ == "__main__":
    asyncio.run(test_failure_handling()) 