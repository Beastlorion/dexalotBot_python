#!/usr/bin/env python3
"""
Configuration management for Dexalot Bot.
Centralizes environment variables and settings.
"""

import os
import logging
from typing import Dict, Any, Optional
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

class Config:
    """Centralized configuration management."""
    
    def __init__(self):
        self._config = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from environment files."""
        try:
            # Load environment variables
            shared_config = dotenv_values(".env.shared")
            secret_config = dotenv_values(".env.secret")
            
            # Merge configurations (secret takes precedence)
            self._config = {**shared_config, **secret_config}
            
            # Also load from actual environment variables (highest precedence)
            for key, value in os.environ.items():
                if key in self._config or key.endswith('_pk') or key.startswith('DEXALOT_'):
                    self._config[key] = value
                    
            logger.info("Configuration loaded successfully")
            
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self._config.get(key, default)
    
    def get_required(self, key: str) -> Any:
        """Get required configuration value, raise if missing."""
        value = self._config.get(key)
        if value is None:
            raise ValueError(f"Required configuration key '{key}' not found")
        return value
    
    def get_rpc_url(self, network: str, chain: str = "subnet") -> str:
        """Get RPC URL for specific network and chain."""
        if network == "fuji":
            if chain == "subnet":
                return self.get_required("fuji_rpc_url")
            elif chain == "avaxc":
                return self.get_required("fuji_avaxc_rpc_url")
        else:  # mainnet
            if chain == "subnet":
                return self.get_required("dexalot_rpc_url")
            elif chain == "avaxc":
                return self.get_required("avaxc_rpc_url")
            elif chain == "arbitrum":
                return self.get_required("arb_rpc_url")
            elif chain == "base":
                return self.get_required("base_rpc_url")
            elif chain == "bsc":
                return self.get_required("bsc_rpc_url")
        
        raise ValueError(f"Unknown network/chain combination: {network}/{chain}")
    
    def get_api_url(self, network: str) -> str:
        """Get API URL for specific network."""
        if network == "fuji":
            return self.get_required("fuji_apiUrl")
        else:
            return self.get_required("apiUrl")
    
    def get_private_key(self, market: str) -> str:
        """Get private key for specific market."""
        key = f"{market}_pk"
        private_key = self.get(key)
        
        if not private_key:
            raise ValueError(f"Private key for market '{market}' not found. Set {key} in environment.")
        
        if not private_key.startswith("0x"):
            raise ValueError(f"Private key for market '{market}' must start with 0x")
        
        return private_key
    
    @property
    def all_config(self) -> Dict[str, Any]:
        """Get all configuration (for debugging - be careful with secrets)."""
        return self._config.copy()

# Global configuration instance
config = Config() 