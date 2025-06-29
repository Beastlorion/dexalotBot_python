#!/usr/bin/env python3
"""
Shutdown Manager for DexalotBot - Coordinates graceful shutdown across all components.
Handles KeyboardInterrupt, timeouts, and cleanup operations.
"""

import asyncio
import signal
import logging
import threading
from typing import Set, Callable, List, Optional, Dict, Any
import time
from enum import Enum


logger = logging.getLogger(__name__)


class ShutdownReason(Enum):
    """Reasons for shutdown"""
    NONE = "none"
    KEYBOARD_INTERRUPT = "keyboard_interrupt"
    SIGNAL_TERM = "signal_term"
    SIGNAL_INT = "signal_int"
    ERROR_LIMIT = "error_limit"
    MANUAL = "manual"
    CIRCUIT_BREAKER = "circuit_breaker"


class ShutdownManager:
    """Centralized shutdown coordination for all bot components"""
    
    def __init__(self, shutdown_timeout: int = 10):
        # Core shutdown coordination
        self.shutdown_event = asyncio.Event()
        self.shutdown_timeout = shutdown_timeout
        self.shutdown_reason = ShutdownReason.NONE
        self.shutdown_time: Optional[float] = None
        
        # Task and cleanup management
        self.managed_tasks: Set[asyncio.Task] = set()
        self.cleanup_callbacks: List[Callable] = []
        self.websocket_connections: Set[Any] = set()
        
        # State tracking
        self.is_shutting_down = False
        self.signal_handlers_installed = False
        self._lock = threading.RLock()
        
        logger.info("ShutdownManager initialized")
    
    def install_signal_handlers(self):
        """Install signal handlers for graceful shutdown"""
        if self.signal_handlers_installed:
            return
            
        def signal_handler(signum, frame):
            reason = ShutdownReason.SIGNAL_INT if signum == signal.SIGINT else ShutdownReason.SIGNAL_TERM
            logger.info(f"Received signal {signum} - requesting shutdown")
            self.request_shutdown(reason)
        
        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            self.signal_handlers_installed = True
            logger.info("Signal handlers installed")
        except Exception as e:
            logger.error(f"Failed to install signal handlers: {e}")
    
    def request_shutdown(self, reason: ShutdownReason = ShutdownReason.MANUAL):
        """Request graceful shutdown with specified reason"""
        with self._lock:
            if self.is_shutting_down:
                return
                
            self.is_shutting_down = True
            self.shutdown_reason = reason
            self.shutdown_time = time.time()
            
        logger.info(f"Shutdown requested: {reason.value}")
        
        # Set the event to wake up all waiting tasks
        if not self.shutdown_event.is_set():
            self.shutdown_event.set()
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested"""
        return self.is_shutting_down
    
    async def wait_with_timeout(self, timeout: float) -> bool:
        """
        Wait for shutdown with timeout - replaces asyncio.sleep().
        Returns True if shutdown was requested, False if timeout elapsed.
        """
        if self.is_shutdown_requested():
            return True
            
        try:
            await asyncio.wait_for(self.shutdown_event.wait(), timeout=timeout)
            return True  # Shutdown requested
        except asyncio.TimeoutError:
            return False  # Continue running
    
    async def sleep_interruptible(self, duration: float, check_interval: float = 0.1):
        """
        Sleep that can be interrupted by shutdown request.
        Checks for shutdown every check_interval seconds.
        """
        end_time = time.time() + duration
        
        while time.time() < end_time and not self.is_shutdown_requested():
            remaining = end_time - time.time()
            sleep_time = min(check_interval, remaining)
            
            if sleep_time <= 0:
                break
                
            try:
                await asyncio.wait_for(self.shutdown_event.wait(), timeout=sleep_time)
                return  # Shutdown requested
            except asyncio.TimeoutError:
                continue  # Keep sleeping
    
    def register_task(self, task: asyncio.Task, name: str = ""):
        """Register a task for managed shutdown"""
        self.managed_tasks.add(task)
        if name:
            task.set_name(name)
        
        # Add done callback to remove from set when completed
        def task_done_callback(t):
            self.managed_tasks.discard(t)
        task.add_done_callback(task_done_callback)
        
        logger.debug(f"Registered task: {name or task.get_name()}")
    
    def register_cleanup(self, callback: Callable, description: str = ""):
        """Register cleanup function to run on shutdown"""
        self.cleanup_callbacks.append((callback, description))
        logger.debug(f"Registered cleanup: {description or 'unnamed'}")
    
    def register_websocket(self, websocket):
        """Register WebSocket connection for proper cleanup"""
        self.websocket_connections.add(websocket)
        logger.debug("Registered WebSocket connection")
    
    def unregister_websocket(self, websocket):
        """Unregister WebSocket connection"""
        self.websocket_connections.discard(websocket)
    
    async def recv_with_timeout(
        self, 
        websocket, 
        timeout: float = 1.0,
        description: str = ""
    ) -> Optional[str]:
        """
        Receive from WebSocket with timeout and shutdown checking.
        Returns None if shutdown requested or on error.
        """
        while not self.is_shutdown_requested():
            try:
                message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                return message
            except asyncio.TimeoutError:
                # Check shutdown and continue
                continue
            except Exception as e:
                logger.error(f"WebSocket error {description}: {e}")
                return None
        
        logger.debug(f"WebSocket recv interrupted by shutdown {description}")
        return None
    
    async def cancel_tasks(self):
        """Cancel all managed tasks"""
        if not self.managed_tasks:
            return
            
        logger.info(f"Cancelling {len(self.managed_tasks)} managed tasks")
        
        for task in self.managed_tasks.copy():
            if not task.done():
                task.cancel()
        
        # Wait for tasks to complete cancellation
        if self.managed_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self.managed_tasks, return_exceptions=True),
                    timeout=self.shutdown_timeout
                )
            except asyncio.TimeoutError:
                logger.warning("Some tasks did not cancel within timeout")
    
    async def close_websockets(self):
        """Close all registered WebSocket connections"""
        if not self.websocket_connections:
            return
            
        logger.info(f"Closing {len(self.websocket_connections)} WebSocket connections")
        
        close_tasks = []
        for ws in self.websocket_connections.copy():
            if hasattr(ws, 'close') and not ws.closed:
                close_tasks.append(ws.close())
        
        if close_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*close_tasks, return_exceptions=True),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("Some WebSocket connections did not close within timeout")
    
    async def run_cleanup_callbacks(self):
        """Run all registered cleanup callbacks"""
        if not self.cleanup_callbacks:
            return
            
        logger.info(f"Running {len(self.cleanup_callbacks)} cleanup callbacks")
        
        for callback, description in self.cleanup_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback()
                else:
                    callback()
                logger.debug(f"Completed cleanup: {description}")
            except Exception as e:
                logger.error(f"Cleanup error {description}: {e}")
    
    async def shutdown(self):
        """Coordinate graceful shutdown of all components"""
        if not self.is_shutting_down:
            self.request_shutdown(ShutdownReason.MANUAL)
        
        logger.info("Starting graceful shutdown sequence")
        start_time = time.time()
        
        try:
            # 1. Cancel all managed tasks
            await self.cancel_tasks()
            
            # 2. Close WebSocket connections
            await self.close_websockets()
            
            # 3. Run cleanup callbacks
            await self.run_cleanup_callbacks()
            
            duration = time.time() - start_time
            logger.info(f"Graceful shutdown completed in {duration:.2f}s")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise
    
    def get_status(self) -> Dict[str, Any]:
        """Get current shutdown manager status"""
        return {
            "is_shutting_down": self.is_shutting_down,
            "shutdown_reason": self.shutdown_reason.value,
            "shutdown_time": self.shutdown_time,
            "managed_tasks": len(self.managed_tasks),
            "cleanup_callbacks": len(self.cleanup_callbacks),
            "websocket_connections": len(self.websocket_connections),
            "signal_handlers_installed": self.signal_handlers_installed
        }


class InterruptibleLoop:
    """Helper class for creating loops that respond to shutdown requests"""
    
    def __init__(self, shutdown_manager: ShutdownManager, name: str = ""):
        self.shutdown = shutdown_manager
        self.name = name
        self.iteration_count = 0
        self.error_count = 0
        
    async def run(
        self,
        loop_func: Callable,
        interval: float = 1.0,
        max_errors: int = 5,
        error_backoff: float = 1.0
    ):
        """
        Run a function in a loop until shutdown is requested.
        
        Args:
            loop_func: Function to call each iteration (can be async)
            interval: Seconds between iterations
            max_errors: Maximum consecutive errors before giving up
            error_backoff: Additional delay after errors
        """
        logger.info(f"Starting interruptible loop: {self.name}")
        consecutive_errors = 0
        
        try:
            while not self.shutdown.is_shutdown_requested():
                try:
                    # Call the loop function
                    if asyncio.iscoroutinefunction(loop_func):
                        await loop_func()
                    else:
                        loop_func()
                    
                    self.iteration_count += 1
                    consecutive_errors = 0  # Reset on success
                    
                    # Wait for next iteration or shutdown
                    if await self.shutdown.wait_with_timeout(interval):
                        break  # Shutdown requested
                        
                except Exception as e:
                    self.error_count += 1
                    consecutive_errors += 1
                    
                    logger.error(f"Error in loop {self.name} (iteration {self.iteration_count}): {e}")
                    
                    if consecutive_errors >= max_errors:
                        logger.error(f"Too many consecutive errors in {self.name}, stopping")
                        self.shutdown.request_shutdown(ShutdownReason.ERROR_LIMIT)
                        break
                    
                    # Backoff on error
                    if await self.shutdown.wait_with_timeout(error_backoff):
                        break  # Shutdown requested during backoff
                        
        except Exception as e:
            logger.error(f"Fatal error in loop {self.name}: {e}")
            self.shutdown.request_shutdown(ShutdownReason.ERROR_LIMIT)
            raise
        finally:
            logger.info(f"Loop {self.name} stopped after {self.iteration_count} iterations")


# Example usage and testing
if __name__ == "__main__":
    async def test_shutdown_manager():
        logging.basicConfig(level=logging.INFO)
        
        shutdown = ShutdownManager()
        shutdown.install_signal_handlers()
        
        print("Testing ShutdownManager...")
        print("Press Ctrl+C to test keyboard interrupt handling")
        
        # Test interruptible sleep
        print("Sleeping for 5 seconds (interruptible)...")
        interrupted = await shutdown.wait_with_timeout(5.0)
        
        if interrupted:
            print("Sleep was interrupted!")
        else:
            print("Sleep completed normally")
        
        # Test cleanup
        def cleanup_func():
            print("Cleanup function called!")
        
        shutdown.register_cleanup(cleanup_func, "test cleanup")
        
        # Perform shutdown
        await shutdown.shutdown()
        print("Shutdown complete")
    
    # Run the test
    asyncio.run(test_shutdown_manager())