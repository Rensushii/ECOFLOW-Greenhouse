#!/usr/bin/env python3
"""
Fix daily_resources table schema
"""

import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import DB_PATH

def fix_daily_resources_table():
    """Fix the daily_resources table schema"""
    print("? Fixing daily_resources table schema...")
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # First, let's see what columns we have
    cursor.execute("PRAGMA table_info(daily_resources)")
    columns = cursor.fetchall()
    print("Current columns:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    # Check if we need to add missing columns
    column_names = [col[1] for col in columns]
    
    if 'system_energy_kwh' not in column_names:
        print("? Adding system_energy_kwh column...")
        cursor.execute("ALTER TABLE daily_resources ADD COLUMN system_energy_kwh REAL DEFAULT 0.12")
    
    if 'esp32_energy_kwh' not in column_names:
        print("? Adding esp32_energy_kwh column...")
        cursor.execute("ALTER TABLE daily_resources ADD COLUMN esp32_energy_kwh REAL DEFAULT 0.0024")
    
    if 'pump_runtime_hours' not in column_names:
        print("? Adding pump_runtime_hours column...")
        cursor.execute("ALTER TABLE daily_resources ADD COLUMN pump_runtime_hours REAL DEFAULT 0")
    
    if 'valve_runtime_hours' not in column_names:
        print("? Adding valve_runtime_hours column...")
        cursor.execute("ALTER TABLE daily_resources ADD COLUMN valve_runtime_hours REAL DEFAULT 0")
    
    if 'irrigation_events' not in column_names:
        print("? Adding irrigation_events column...")
        cursor.execute("ALTER TABLE daily_resources ADD COLUMN irrigation_events INTEGER DEFAULT 0")
    
    # Update existing rows with default values
    print("? Updating existing rows with default values...")
    cursor.execute("""
        UPDATE daily_resources 
        SET 
            system_energy_kwh = 0.12,
            esp32_energy_kwh = 0.0024,
            irrigation_events = 0
        WHERE 
            system_energy_kwh IS NULL OR 
            esp32_energy_kwh IS NULL OR
            irrigation_events IS NULL
    """)
    
    # For pump/valve runtime, calculate from resource_consumption table if it exists
    try:
        cursor.execute("SELECT COUNT(*) FROM resource_consumption")
        if cursor.fetchone()[0] > 0:
            print("? Calculating pump/valve runtime from resource_consumption...")
            cursor.execute("""
                UPDATE daily_resources dr
                SET pump_runtime_hours = (
                    SELECT COALESCE(SUM(pump_runtime_seconds), 0) / 3600.0
                    FROM resource_consumption rc
                    WHERE date(rc.timestamp) = dr.date
                ),
                valve_runtime_hours = (
                    SELECT COALESCE(SUM(valve_runtime_seconds), 0) / 3600.0
                    FROM resource_consumption rc
                    WHERE date(rc.timestamp) = dr.date
                )
                WHERE EXISTS (
                    SELECT 1 FROM resource_consumption rc 
                    WHERE date(rc.timestamp) = dr.date
                )
            """)
    except:
        print("? Could not calculate from resource_consumption table")
    
    conn.commit()
    
    # Show final schema
    cursor.execute("PRAGMA table_info(daily_resources)")
    columns = cursor.fetchall()
    print("\nFinal columns:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    conn.close()
    print("? daily_resources table fixed successfully!")
    return True

if __name__ == '__main__':
    fix_daily_resources_table()