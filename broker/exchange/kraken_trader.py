"""
Kraken Exchange Integration
Handles order placement, position management, and account interactions.
"""

import logging
import time
from typing import Dict, Optional, Tuple
import ccxt

logger = logging.getLogger(__name__)


class KrakenTrader:
    """
    Interface to Kraken exchange via CCXT.
    Handles order placement, account queries, and position management.
    """
    
    def __init__(self, api_key: str = '', api_secret: str = '',
                 sandbox: bool = False):
        """
        Initialize Kraken trader.
        
        Args:
            api_key: Kraken API key
            api_secret: Kraken API secret
            sandbox: Use sandbox/test mode if available
        """
        try:
            self.exchange = ccxt.kraken({
                'apiKey': api_key,
                'secret': api_secret,
                'enableRateLimit': True,
                'options': {
                    'sandboxMode': sandbox
                }
            })
            
            # Load markets
            self.exchange.load_markets()
            logger.info(f"Kraken trader initialized ({'sandbox' if sandbox else 'live'})")
        except Exception as e:
            logger.error(f"Failed to initialize Kraken: {e}")
            raise
    
    def get_balance(self, refresh: bool = True) -> Dict[str, Dict]:
        """
        Get account balance.
        
        Args:
            refresh: Force refresh from exchange
        
        Returns:
            Dict with 'free', 'used', 'total' for each asset
        """
        try:
            balance = self.exchange.fetch_balance()
            return balance
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return {}
    
    def get_eur_balance(self) -> Tuple[float, float]:
        """
        Get EUR balance.
        
        Returns:
            (free, total) EUR
        """
        try:
            balance = self.get_balance()
            eur = balance.get('EUR', {'free': 0, 'total': 0})
            return eur.get('free', 0), eur.get('total', 0)
        except Exception as e:
            logger.error(f"Failed to get EUR balance: {e}")
            return 0, 0
    
    def get_average_entry_prices(self, symbols: list) -> Dict[str, float]:
        """
        Calculate weighted average entry prices from trade history.
        Only considers BUY trades that contribute to current holdings.
        
        Args:
            symbols: List of symbols to check
        
        Returns:
            Dict: {symbol: average_entry_price}
        """
        entry_prices = {}
        try:
            # Fetch recent trade history
            all_trades = self.exchange.fetch_my_trades(symbol=None, limit=100)
            
            for symbol in symbols:
                # Filter trades for this symbol
                symbol_trades = [t for t in all_trades if t['symbol'] == symbol]
                
                # Track net position and cost basis
                total_amount = 0.0
                total_cost = 0.0
                
                for trade in symbol_trades:
                    if trade['side'] == 'buy':
                        total_amount += trade['amount']
                        total_cost += trade['amount'] * trade['price']
                    elif trade['side'] == 'sell':
                        # Reduce position (FIFO simplification)
                        if total_amount > 0:
                            avg_price = total_cost / total_amount if total_amount > 0 else 0
                            sell_amount = min(trade['amount'], total_amount)
                            total_amount -= sell_amount
                            total_cost -= sell_amount * avg_price
                
                if total_amount > 0:
                    entry_prices[symbol] = total_cost / total_amount
                    logger.debug(f"Calculated entry price for {symbol}: €{entry_prices[symbol]:.2f}")
            
            return entry_prices
        except Exception as e:
            logger.error(f"Failed to get entry prices from trade history: {e}")
            return {}
    
    def get_crypto_holdings(self, allowed_symbols: list) -> Dict[str, Dict]:
        """
        Get crypto holdings for allowed trading pairs with actual entry prices.
        
        Args:
            allowed_symbols: List of symbols to check (e.g., ['BTC/EUR', 'SOL/EUR'])
        
        Returns:
            Dict of holdings: {symbol: {'amount': float, 'current_price': float, 'value_eur': float, 'entry_price': float}}
        """
        holdings = {}
        try:
            balance = self.get_balance()
            
            # Get symbols with holdings first
            symbols_with_holdings = []
            for symbol in allowed_symbols:
                base = symbol.split('/')[0]
                asset_balance = balance.get(base, {})
                amount = asset_balance.get('total', 0) or 0
                if amount > 0:
                    symbols_with_holdings.append(symbol)
            
            # Get actual entry prices from trade history
            entry_prices = self.get_average_entry_prices(symbols_with_holdings)
            
            for symbol in allowed_symbols:
                # Extract base currency (e.g., 'BTC' from 'BTC/EUR')
                base = symbol.split('/')[0]
                
                asset_balance = balance.get(base, {})
                amount = asset_balance.get('total', 0) or 0
                
                # Skip if no holdings or negligible amount
                if amount <= 0:
                    continue
                
                # Get current price
                try:
                    ticker = self.fetch_ticker(symbol)
                    if ticker:
                        current_price = ticker.get('last', 0)
                        value_eur = amount * current_price
                        
                        # Only include if value > 1 EUR (skip dust)
                        if value_eur >= 1.0:
                            # Use actual entry price if available, otherwise current price
                            actual_entry = entry_prices.get(symbol, current_price)
                            holdings[symbol] = {
                                'amount': amount,
                                'current_price': current_price,
                                'value_eur': value_eur,
                                'entry_price': actual_entry
                            }
                            logger.info(f"Found holding: {symbol} = {amount:.6f}, entry €{actual_entry:.2f}, current €{current_price:.2f}")
                except Exception as e:
                    logger.warning(f"Could not get price for {symbol}: {e}")
            
            return holdings
        except Exception as e:
            logger.error(f"Failed to get crypto holdings: {e}")
            return {}

    def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        """
        Get current ticker data.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/EUR')
        
        Returns:
            Ticker dict or None
        """
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch ticker for {symbol}: {e}")
            return None
    
    def fetch_order_book(self, symbol: str, limit: int = 10) -> Optional[Dict]:
        """
        Get order book.
        
        Args:
            symbol: Trading pair
            limit: Depth of orderbook
        
        Returns:
            Order book dict or None
        """
        try:
            return self.exchange.fetch_order_book(symbol, limit)
        except Exception as e:
            logger.error(f"Failed to fetch orderbook for {symbol}: {e}")
            return None
    
    def create_limit_order(self, symbol: str, side: str, amount: float,
                          price: float, params: Dict = None) -> Optional[Dict]:
        """
        Create a limit order.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/EUR')
            side: 'buy' or 'sell'
            amount: Amount in base currency
            price: Limit price
            params: Additional parameters
        
        Returns:
            Order dict or None
        """
        try:
            if params is None:
                params = {}
            
            order = self.exchange.create_limit_order(
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                params=params
            )
            
            value = amount * price
            logger.info(f"Created {side} limit order for {amount:.6f} {symbol.split('/')[0]} @ {price:.2f}€ (value={value:.2f}€)")
            return order
        except Exception as e:
            logger.error(f"Failed to create limit order: {e}")
            return None
    
    def create_market_order(self, symbol: str, side: str, amount: float,
                           params: Dict = None) -> Optional[Dict]:
        """
        Create a market order.
        
        Args:
            symbol: Trading pair
            side: 'buy' or 'sell'
            amount: Amount in base currency
            params: Additional parameters
        
        Returns:
            Order dict or None
        """
        try:
            if params is None:
                params = {}
            
            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                params=params
            )
            
            logger.info(f"Created {side} market order for {amount} {symbol}")
            return order
        except Exception as e:
            logger.error(f"Failed to create {side} market order for {amount:.8f} {symbol}: {e}")
            return None
    
    def get_order(self, order_id: str, symbol: str = None) -> Optional[Dict]:
        """
        Get order details.
        
        Args:
            order_id: Order ID
            symbol: Trading pair (required for some exchanges)
        
        Returns:
            Order dict or None
        """
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except Exception as e:
            logger.error(f"Failed to fetch order {order_id}: {e}")
            return None
    
    def get_open_orders(self, symbol: str = None) -> list:
        """
        Get all open orders.
        
        Args:
            symbol: Filter by symbol (optional)
        
        Returns:
            List of order dicts
        """
        try:
            return self.exchange.fetch_open_orders(symbol)
        except Exception as e:
            logger.error(f"Failed to fetch open orders: {e}")
            return []
    
    def cancel_order(self, order_id: str, symbol: str = None) -> bool:
        """
        Cancel an order.
        
        Args:
            order_id: Order ID
            symbol: Trading pair
        
        Returns:
            True if successful
        """
        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False
    
    def calculate_order_amount(self, symbol: str, eur_amount: float,
                              price: float) -> float:
        """
        Calculate amount in base currency from EUR amount.
        
        Args:
            symbol: Trading pair
            eur_amount: Amount in EUR
            price: Current price
        
        Returns:
            Amount in base currency
        """
        if price == 0:
            return 0
        
        amount = eur_amount / price
        return amount
    
    def validate_order_symbol(self, symbol: str) -> Tuple[bool, str]:
        """
        Validate if symbol can be traded on Kraken.
        
        Returns:
            (is_valid, reason)
        """
        if symbol not in self.exchange.symbols:
            return False, f"{symbol} not available on Kraken"
        
        return True, "OK"
    
    def estimate_fees(self, symbol: str, side: str, amount: float,
                     price: float) -> float:
        """
        Estimate trading fees for an order (used BEFORE order is placed).
        Uses taker fee for market/sell orders, maker fee for limit/buy orders.
        
        Args:
            symbol: Trading pair
            side: 'buy' or 'sell'
            amount: Amount in base currency
            price: Price
        
        Returns:
            Estimated fee in EUR
        """
        from config.settings import TAKER_FEE
        # Always use taker fee for conservative estimation:
        # Buy limit orders at ask*1.001 fill immediately as taker on Kraken
        fee_rate = TAKER_FEE
        order_value = amount * price
        fee = order_value * fee_rate
        return fee

    @staticmethod
    def get_actual_fee(filled_order: dict) -> float:
        """
        Extract actual fee from a filled order response.
        Kraken returns fee as {'cost': float, 'currency': str}.
        
        Args:
            filled_order: Order dict from ccxt fetch_order
            
        Returns:
            Actual fee in EUR (0.0 if unavailable)
        """
        fee_info = filled_order.get('fee')
        if fee_info and isinstance(fee_info, dict):
            return fee_info.get('cost', 0.0) or 0.0
        return 0.0
    
    def get_last_price(self, symbol: str) -> Optional[float]:
        """Get last traded price"""
        ticker = self.fetch_ticker(symbol)
        if ticker:
            return ticker.get('last')
        return None
    
    def get_bid_ask(self, symbol: str) -> Optional[Tuple[float, float]]:
        """
        Get best bid and ask.
        
        Returns:
            (bid, ask) or None
        """
        ticker = self.fetch_ticker(symbol)
        if ticker:
            return (ticker.get('bid'), ticker.get('ask'))
        return None
    
    def get_exchange_info(self) -> Dict:
        """Get exchange information"""
        return {
            'name': self.exchange.name,
            'countries': self.exchange.countries,
            'has': self.exchange.has,
            'timeframes': self.exchange.timeframes if hasattr(self.exchange, 'timeframes') else {},
        }
    
    def wait_for_order_fill(self, order_id: str, symbol: str,
                           timeout: int = 300, check_interval: int = 5) -> Optional[Dict]:
        """
        Wait for an order to be filled.
        
        Args:
            order_id: Order ID
            symbol: Trading pair
            timeout: Max seconds to wait
            check_interval: Check status every N seconds
        
        Returns:
            Filled order dict or None if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            order = self.get_order(order_id, symbol)
            
            if order is None:
                logger.error(f"Order {order_id} not found")
                return None
            
            if order['status'] == 'closed':
                logger.info(f"Order {order_id} filled")
                return order
            
            if order['status'] == 'canceled':
                logger.warning(f"Order {order_id} cancelled")
                return None
            
            logger.debug(f"Order {order_id} status: {order['status']}, waiting...")
            time.sleep(check_interval)
        
        logger.warning(f"Order {order_id} not filled within {timeout}s timeout")
        return None
