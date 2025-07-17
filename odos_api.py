"""
Odos API integration for price feeds
"""

import aiohttp
import asyncio
import logging
from typing import Dict, Optional, Tuple
from decimal import Decimal

logger = logging.getLogger(__name__)


class OdosAPI:
    """Odos API client for getting swap quotes and prices"""
    
    BASE_URL = "https://api.odos.xyz"
    QUOTE_URL = f"{BASE_URL}/sor/quote/v2"
    ASSEMBLE_URL = f"{BASE_URL}/sor/assemble"
    TOKEN_PRICE_URL = f"{BASE_URL}/pricing/token"
    
    def __init__(self, chain_id: int = 43114):
        """
        Initialize Odos API client
        
        Args:
            chain_id: Chain ID (default: 43114 for Avalanche C-Chain)
        """
        self.chain_id = chain_id
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def start(self):
        """Start the API client session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
    
    async def stop(self):
        """Stop the API client session"""
        if self.session:
            await self.session.close()
            self.session = None
    
    async def get_swap_quote(
        self,
        input_token: str,
        output_token: str,
        amount: int,
        user_address: str,
        slippage_percent: float = 1.0
    ) -> Optional[Dict]:
        """
        Get swap quote from Odos
        
        Args:
            input_token: Input token address
            output_token: Output token address
            amount: Amount in token's smallest unit (wei)
            user_address: User's wallet address
            slippage_percent: Slippage tolerance in percent
            
        Returns:
            Quote data or None if failed
        """
        if not self.session:
            logger.error("Session not started. Call start() first.")
            return None
        
        quote_body = {
            "chainId": self.chain_id,
            "inputTokens": [{
                "tokenAddress": input_token,
                "amount": str(amount)
            }],
            "outputTokens": [{
                "tokenAddress": output_token,
                "proportion": 1
            }],
            "userAddr": user_address,
            "slippageLimitPercent": slippage_percent,
            "compact": True,
            "disableRFQs": False,
            "sourceBlacklist": ["Native"],
            "sourceWhitelist": [
                "Dexalot", "Uniswap V3", "Uniswap V4", "Pangolin V3", 
                "Pharaoh V2", "LFJ V2.1", "LFJ V2.2", "WOOFi V2", 
                "Wrapped AVAX", "Cables Finance"
            ],
            "referralCode": 0
        }
        
        try:
            async with self.session.post(
                self.QUOTE_URL,
                json=quote_body,
                headers={'Content-Type': 'application/json'}
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    text = await response.text()
                    logger.error(f"Quote request failed: {response.status} - {text}")
                    return None
        except Exception as e:
            logger.error(f"Error getting quote: {e}")
            return None
    
    async def get_price_from_quotes(
        self,
        base_token_address: str,
        quote_token_address: str,
        base_decimals: int,
        quote_decimals: int,
        amt_to_swap: float = 100.0,
        user_address: str = "0x0000000000000000000000000000000000000000"
    ) -> Optional[float]:
        """
        Get price by fetching quotes in both directions and averaging
        
        Args:
            base_token_address: Base token address (e.g., AVAX)
            quote_token_address: Quote token address (e.g., USDC)
            base_decimals: Base token decimals
            quote_decimals: Quote token decimals
            amount_usd: USD value to use for quotes
            user_address: User address for quote
            
        Returns:
            Average price or None if failed
        """
        try:
            # Calculate amount of base token for the USD value
            base_amount = amt_to_swap
            
            # Get buy quote (quote_token -> base_token)
            # This gives us how much quote token we need to buy base token
            buy_quote = await self.get_swap_quote(
                input_token=quote_token_address,
                output_token=base_token_address,
                amount=int(amt_to_swap * (10 ** quote_decimals)),  # $100 worth of USDC
                user_address=user_address,
                slippage_percent=1.0
            )
            
            if not buy_quote:
                logger.warning(f"Failed to get buy quote for {base_token_address}/{quote_token_address}")
                return None

            buy_out = float(buy_quote["outAmounts"][0]) / (10 ** base_decimals)
            base_amount_wei = int(buy_out * (10 ** base_decimals))
            
            # Get sell quote (base_token -> quote_token)
            # This gives us how much quote token we get for selling base token
            sell_quote = await self.get_swap_quote(
                input_token=base_token_address,
                output_token=quote_token_address,
                amount=base_amount_wei,
                user_address=user_address,
                slippage_percent=1.0
            )
            
            if not sell_quote:
                logger.warning(f"Failed to get sell quote for {base_token_address}/{quote_token_address}")
                return None
            
            # Calculate prices from quotes
            # Buy quote: input USDC to get base token
            buy_in = float(buy_quote["inAmounts"][0]) / (10 ** quote_decimals)
            
            if buy_out > 0:
                buy_price = buy_in / buy_out  # USDC per base token
            else:
                logger.warning("Buy quote returned zero output")
                return None
            
            # Sell quote: input base token to get USDC
            sell_in = float(sell_quote["inAmounts"][0]) / (10 ** base_decimals)
            sell_out = float(sell_quote["outAmounts"][0]) / (10 ** quote_decimals)
            
            if sell_in > 0:
                sell_price = sell_out / sell_in  # USDC per base token
            else:
                logger.warning("Sell quote returned zero input")
                return None
            
            # Average the two prices
            average_price = (buy_price + sell_price) / 2.0
            
            logger.debug(f"Odos prices for {base_token_address}: "
                        f"buy={buy_price:.4f}, sell={sell_price:.4f}, "
                        f"avg={average_price:.4f}, spread={(abs(buy_price - sell_price) / average_price * 100):.2f}%")
            
            return average_price
            
        except Exception as e:
            logger.error(f"Error getting price from quotes: {e}")
            return None