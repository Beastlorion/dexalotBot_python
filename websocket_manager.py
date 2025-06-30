#!/usr/bin/env python3
"""
WebSocket Manager for DexalotBot - Handles WebSocket connections with proper timeouts and shutdown.
Prevents blocking recv() operations that don't respond to shutdown signals.
"""

import asyncio
import logging
import json
from typing import Optional, Dict, Any, Callable, List
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException
from shutdown_manager import ShutdownManager

logger = logging.getLogger(__name__)


class WebSocketConnection:
    """Manages a single WebSocket connection with timeout and shutdown handling"""
    
    def __init__(
        self,
        url: str,
        shutdown_manager: ShutdownManager,
        name: str = "",
        recv_timeout: float = 1.0,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 5
    ):
        self.url = url
        self.shutdown = shutdown_manager
        self.name = name or url
        self.recv_timeout = recv_timeout
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_attempts = max_reconnect_attempts
        
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.is_connected = False
        self.reconnect_count = 0
        self.message_handlers: List[Callable[[str], None]] = []
        
        # Statistics
        self.messages_received = 0
        self.connection_errors = 0
        
    def add_message_handler(self, handler: Callable[[str], None]):
        """Add a message handler function"""
        self.message_handlers.append(handler)
        
    async def connect(self) -> bool:
        """Establish WebSocket connection"""
        try:
            logger.info(f"Connecting to WebSocket: {self.name} at {self.url}")
            self.websocket = await websockets.connect(
                self.url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            self.shutdown.register_websocket(self.websocket)
            self.is_connected = True
            self.reconnect_count = 0
            logger.info(f"Successfully connected to WebSocket: {self.name}")
            return True
            
        except Exception as e:
            self.connection_errors += 1
            logger.error(f"Failed to connect to WebSocket {self.name}: {e}")
            self.is_connected = False
            return False
    
    async def disconnect(self):
        """Gracefully disconnect from WebSocket"""
        if self.websocket and self.is_connected:
            try:
                logger.info(f"Disconnecting from WebSocket: {self.name}")
                await self.websocket.close()
            except Exception as e:
                logger.error(f"Error disconnecting from WebSocket {self.name}: {e}")
            finally:
                self.shutdown.unregister_websocket(self.websocket)
                self.is_connected = False
                self.websocket = None
    
    async def send_message(self, message: str) -> bool:
        """Send message to WebSocket"""
        if not self.is_connected or not self.websocket:
            logger.warning(f"Cannot send message - WebSocket {self.name} not connected")
            return False
            
        try:
            await self.websocket.send(message)
            logger.debug(f"Sent message to {self.name}: {message[:100]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to WebSocket {self.name}: {e}")
            self.is_connected = False
            return False
    
    async def recv_message(self) -> Optional[str]:
        """
        Receive message with timeout and shutdown checking.
        Returns None if shutdown requested or on error.
        """
        if not self.is_connected or not self.websocket:
            return None
        
        return await self.shutdown.recv_with_timeout(
            self.websocket,
            timeout=self.recv_timeout,
            description=self.name
        )
    
    async def handle_messages(self):
        """Message handling loop with proper shutdown support"""
        while not self.shutdown.is_shutdown_requested() and self.is_connected:
            try:
                message = await self.recv_message()
                
                if message is None:
                    # Either shutdown requested or error occurred
                    if self.shutdown.is_shutdown_requested():
                        logger.info(f"WebSocket {self.name} stopping due to shutdown")
                        break
                    else:
                        # Connection error - will reconnect if needed
                        self.is_connected = False
                        break
                
                self.messages_received += 1
                
                # Process message with handlers
                for handler in self.message_handlers:
                    try:
                        handler(message)
                    except Exception as e:
                        logger.error(f"Error in message handler for {self.name}: {e}")
                        
            except Exception as e:
                logger.error(f"Error in message loop for {self.name}: {e}")
                self.is_connected = False
                break
    
    async def run_with_reconnect(self):
        """Run WebSocket with automatic reconnection"""
        while not self.shutdown.is_shutdown_requested():
            # Connect if not connected
            if not self.is_connected:
                connected = await self.connect()
                if not connected:
                    self.reconnect_count += 1
                    
                    if self.reconnect_count >= self.max_reconnect_attempts:
                        logger.error(f"Max reconnection attempts reached for {self.name}")
                        break
                    
                    # Wait before retry with shutdown check
                    logger.info(f"Retrying connection to {self.name} in {self.reconnect_delay}s (attempt {self.reconnect_count}/{self.max_reconnect_attempts})")
                    if await self.shutdown.wait_with_timeout(self.reconnect_delay):
                        break  # Shutdown requested
                    continue
                else:
                    # Connection successful, give it a moment to stabilize
                    await asyncio.sleep(0.1)
            
            # Handle messages
            await self.handle_messages()
            
            # If we're here, either connection lost or shutdown requested
            if self.shutdown.is_shutdown_requested():
                break
            
            logger.warning(f"WebSocket {self.name} connection lost, will reconnect")
            await self.disconnect()
            
            # Brief delay before reconnecting
            if await self.shutdown.wait_with_timeout(1.0):
                break
        
        # Cleanup
        await self.disconnect()
        logger.info(f"WebSocket {self.name} stopped")
    
    async def wait_for_connection(self, timeout: float = 5.0) -> bool:
        """Wait for connection to be established"""
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout:
            if self.is_connected:
                return True
            await asyncio.sleep(0.1)
        
        return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get connection statistics"""
        return {
            "name": self.name,
            "url": self.url,
            "is_connected": self.is_connected,
            "messages_received": self.messages_received,
            "connection_errors": self.connection_errors,
            "reconnect_count": self.reconnect_count
        }


class WebSocketManager:
    """Manages multiple WebSocket connections"""
    
    def __init__(self, shutdown_manager: ShutdownManager):
        self.shutdown = shutdown_manager
        self.connections: Dict[str, WebSocketConnection] = {}
        self.running_tasks: Dict[str, asyncio.Task] = {}
        
    def add_connection(
        self,
        name: str,
        url: str,
        recv_timeout: float = 1.0,
        reconnect_delay: float = 5.0,
        max_reconnect_attempts: int = 5
    ) -> WebSocketConnection:
        """Add a WebSocket connection"""
        connection = WebSocketConnection(
            url=url,
            shutdown_manager=self.shutdown,
            name=name,
            recv_timeout=recv_timeout,
            reconnect_delay=reconnect_delay,
            max_reconnect_attempts=max_reconnect_attempts
        )
        
        self.connections[name] = connection
        logger.info(f"Added WebSocket connection: {name}")
        return connection
    
    def get_connection(self, name: str) -> Optional[WebSocketConnection]:
        """Get connection by name"""
        return self.connections.get(name)
    
    async def start_connection(self, name: str):
        """Start a WebSocket connection"""
        if name not in self.connections:
            logger.error(f"WebSocket connection '{name}' not found")
            return
        
        if name in self.running_tasks:
            logger.warning(f"WebSocket connection '{name}' already running")
            return
        
        connection = self.connections[name]
        
        # Create and start task
        task = asyncio.create_task(connection.run_with_reconnect())
        task.set_name(f"websocket-{name}")
        
        self.running_tasks[name] = task
        self.shutdown.register_task(task, f"websocket-{name}")
        
        logger.info(f"Started WebSocket connection task: {name}")
        
        # Wait a bit for initial connection attempt
        await asyncio.sleep(0.2)
    
    async def stop_connection(self, name: str):
        """Stop a WebSocket connection"""
        if name in self.running_tasks:
            task = self.running_tasks[name]
            task.cancel()
            
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            del self.running_tasks[name]
        
        if name in self.connections:
            await self.connections[name].disconnect()
        
        logger.info(f"Stopped WebSocket connection: {name}")
    
    async def start_all(self):
        """Start all registered connections"""
        for name in self.connections:
            await self.start_connection(name)
    
    async def stop_all(self):
        """Stop all connections"""
        stop_tasks = []
        for name in list(self.running_tasks.keys()):
            stop_tasks.append(self.stop_connection(name))
        
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all connections"""
        return {name: conn.get_stats() for name, conn in self.connections.items()}


# Example usage for Dexalot bot
async def example_dexalot_websockets():
    """Example of how to use WebSocketManager for Dexalot bot"""
    
    # Create shutdown manager
    shutdown = ShutdownManager()
    shutdown.install_signal_handlers()
    
    # Create WebSocket manager
    ws_manager = WebSocketManager(shutdown)
    
    # Add Dexalot WebSocket connections
    mainnet_ws = ws_manager.add_connection(
        name="dexalot_mainnet",
        url="wss://api.dexalot.com/ws",
        recv_timeout=2.0,
        reconnect_delay=5.0
    )
    
    # Add message handler
    def handle_dexalot_message(message: str):
        try:
            data = json.loads(message)
            logger.info(f"Received Dexalot message: {data.get('type', 'unknown')}")
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON received: {message[:100]}")
    
    mainnet_ws.add_message_handler(handle_dexalot_message)
    
    # Register cleanup
    shutdown.register_cleanup(ws_manager.stop_all, "websocket cleanup")
    
    try:
        # Start connections
        await ws_manager.start_all()
        
        # Keep running until shutdown
        while not shutdown.is_shutdown_requested():
            await shutdown.wait_with_timeout(1.0)
            
            # Print stats periodically
            stats = ws_manager.get_all_stats()
            for name, conn_stats in stats.items():
                if conn_stats['messages_received'] > 0:
                    logger.info(f"WebSocket {name}: {conn_stats['messages_received']} messages received")
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        shutdown.request_shutdown()
    finally:
        await shutdown.shutdown()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(example_dexalot_websockets())