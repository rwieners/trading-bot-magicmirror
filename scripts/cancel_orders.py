#!/usr/bin/env python3
"""
Cancel all pending ETH/EUR orders
"""
import os
import sys
import ccxt
from dotenv import load_dotenv
load_dotenv()

# Get API keys from environment
api_key = os.getenv('KRAKEN_API_KEY', '')
api_secret = os.getenv('KRAKEN_API_SECRET', '')

if not api_key or not api_secret:
    print("ERROR: Kraken credentials not set. Please set KRAKEN_API_KEY and KRAKEN_API_SECRET")
    sys.exit(1)

try:
    kraken = ccxt.kraken({'apiKey': api_key, 'secret': api_secret, 'enableRateLimit': True})
    
    # Get open orders
    orders = kraken.fetch_open_orders('ETH/EUR')
    print(f"Found {len(orders)} open ETH/EUR orders\n")
    
    for order in orders:
        order_id = order['id']
        side = order['side']
        amount = order['amount']
        price = order['price']
        
        print(f"Cancelling Order {order_id}: {side.upper()} {amount:.6f} @ €{price:.2f}")
        
        try:
            result = kraken.cancel_order(order_id, 'ETH/EUR')
            print(f"  ✓ Successfully cancelled")
        except Exception as e:
            print(f"  ✗ Error: {e}")
    
    print("\nRemaining open orders:")
    remaining = kraken.fetch_open_orders('ETH/EUR')
    print(f"Total: {len(remaining)}")
    
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
