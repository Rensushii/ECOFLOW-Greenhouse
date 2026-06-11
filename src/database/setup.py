# -*- coding: utf-8 -*-
"""
Database setup and initialization
"""

import os
import sys

# Add src to path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from config import DB_PATH
    from .models import DatabaseManager
except ImportError:
    # Fallback for direct execution
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from config import DB_PATH
    from src.database.models import DatabaseManager

def initialize_database():
    """Initialize the database with all tables"""
    print("? Initializing database...")
    
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # Create database manager
    db_manager = DatabaseManager(DB_PATH)
    
    # Create all tables
    db_manager.create_tables()
    
    print(f"? Database initialized at {DB_PATH}")
    return db_manager
