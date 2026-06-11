#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Run script for Greenhouse Monitoring System
"""

import sys
import os
import signal

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from app import greenhouse_app

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\n? Received signal {signum}, shutting down...")
    greenhouse_app.stop()
    sys.exit(0)

if __name__ == '__main__':
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Initialize the application
        greenhouse_app.initialize()
        
        # Start background threads
        greenhouse_app.start()
        
        # Run Flask app
        greenhouse_app.run_flask(host='0.0.0.0', port=5000, debug=False)
        
    except Exception as e:
        print(f"?? Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)