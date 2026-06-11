#!/usr/bin/env python3
"""
Fix daily_resources table - set correct system/ESP32 energy values
"""

import sqlite3
import sys
from datetime import datetime, timedelta

# Fixed daily energy values (24/7 devices)
DAILY_SYSTEM_ENERGY_KWH = 0.12      # Raspberry Pi: 5W × 24h = 0.12 kWh
DAILY_ESP32_ENERGY_KWH = 0.0024     # ESP32: 0.1W × 24h = 0.0024 kWh

def fix_daily_resources():
    db_path = "data/database/greenhouse.db"
    
    print(f"Fixing daily_resources table: {db_path}")
    print(f"Using fixed values:")
    print(f"  - System (RPi) energy per day: {DAILY_SYSTEM_ENERGY_KWH} kWh")
    print(f"  - ESP32 energy per day: {DAILY_ESP32_ENERGY_KWH} kWh")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # First, backup the current table
        print("\n1. Creating backup of current data...")
        cursor.execute("CREATE TABLE IF NOT EXISTS daily_resources_backup AS SELECT * FROM daily_resources")
        
        # Get list of dates in the table
        cursor.execute("SELECT date FROM daily_resources ORDER BY date")
        dates = [row[0] for row in cursor.fetchall()]
        
        if dates:
            print(f"2. Found {len(dates)} dates in the table:")
            for date in dates:
                print(f"   - {date}")
            
            # Update each date with correct system/ESP32 energy
            print("\n3. Updating with correct energy values...")
            for date in dates:
                cursor.execute("""
                    UPDATE daily_resources 
                    SET system_energy_kwh = ?, esp32_energy_kwh = ?
                    WHERE date = ?
                """, (DAILY_SYSTEM_ENERGY_KWH, DAILY_ESP32_ENERGY_KWH, date))
                print(f"   Updated {date}")
        else:
            print("No data found in daily_resources table.")
            
            # Create sample data for last 7 days (for testing)
            print("\nCreating sample data for last 7 days...")
            for i in range(7, 0, -1):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                # Insert with some sample pump/valve data
                cursor.execute("""
                    INSERT OR REPLACE INTO daily_resources
                    (date, total_water_liters, total_energy_kwh, 
                     system_energy_kwh, esp32_energy_kwh,
                     pump_runtime_hours, valve_runtime_hours)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    date,
                    i * 2.5,  # Sample water usage
                    i * 0.1,  # Sample pump/valve energy
                    DAILY_SYSTEM_ENERGY_KWH,
                    DAILY_ESP32_ENERGY_KWH,
                    i * 0.5,  # Sample pump runtime
                    i * 0.3   # Sample valve runtime
                ))
                print(f"   Created {date}")
        
        # Verify the changes
        print("\n4. Verifying changes...")
        cursor.execute("""
            SELECT date, system_energy_kwh, esp32_energy_kwh 
            FROM daily_resources 
            ORDER BY date DESC 
            LIMIT 5
        """)
        results = cursor.fetchall()
        
        print("\nUpdated data (last 5 days):")
        print("-" * 60)
        print(f"{'Date':<12} {'System kWh':<12} {'ESP32 kWh':<12}")
        print("-" * 60)
        for row in results:
            print(f"{row[0]:<12} {row[1]:<12.6f} {row[2]:<12.6f}")
        
        # Ask if user wants to drop backup
        drop_backup = input("\nDrop backup table? (y/n): ").lower().strip()
        if drop_backup == 'y':
            cursor.execute("DROP TABLE daily_resources_backup")
            print("Backup table dropped.")
        else:
            print("Backup table kept as 'daily_resources_backup'")
        
        conn.commit()
        conn.close()
        
        print("\n? daily_resources table fixed successfully!")
        
    except Exception as e:
        print(f"? Error fixing database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def rebuild_daily_resources():
    """Alternative: Completely rebuild the table"""
    db_path = "data/database/greenhouse.db"
    
    print("\n" + "="*60)
    print("OPTION 2: Completely rebuild daily_resources table")
    print("="*60)
    
    confirm = input("\n??  This will DELETE ALL existing data and rebuild from resource_consumption.\nContinue? (y/n): ").lower().strip()
    
    if confirm != 'y':
        print("Cancelled.")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("\n1. Dropping old table...")
        cursor.execute("DROP TABLE IF EXISTS daily_resources")
        
        print("2. Creating new table...")
        cursor.execute("""
            CREATE TABLE daily_resources (
                date DATE PRIMARY KEY,
                total_water_liters REAL,
                total_energy_kwh REAL,
                system_energy_kwh REAL,
                esp32_energy_kwh REAL,
                pump_runtime_hours REAL,
                valve_runtime_hours REAL
            )
        """)
        
        print("3. Calculating daily totals from resource_consumption...")
        
        # Get unique dates from resource_consumption
        cursor.execute("""
            SELECT DISTINCT date(timestamp) as date
            FROM resource_consumption 
            ORDER BY date
        """)
        dates = [row[0] for row in cursor.fetchall()]
        
        if not dates:
            print("No data found in resource_consumption table.")
        else:
            for date in dates:
                # Get pump/valve totals for this date
                cursor.execute("""
                    SELECT 
                        COALESCE(SUM(water_consumed_liters), 0) as total_water,
                        COALESCE(SUM(energy_consumed_kwh), 0) as total_energy,
                        COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds,
                        COALESCE(SUM(valve_runtime_seconds), 0) as valve_seconds
                    FROM resource_consumption 
                    WHERE date(timestamp) = ?
                """, (date,))
                
                result = cursor.fetchone()
                total_water = result[0] if result[0] else 0
                total_energy = result[1] if result[1] else 0
                pump_seconds = result[2] if result[2] else 0
                valve_seconds = result[3] if result[3] else 0
                
                # Insert with FIXED system/ESP32 values
                cursor.execute("""
                    INSERT INTO daily_resources
                    (date, total_water_liters, total_energy_kwh, 
                     system_energy_kwh, esp32_energy_kwh,
                     pump_runtime_hours, valve_runtime_hours)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    date,
                    total_water,
                    total_energy,
                    DAILY_SYSTEM_ENERGY_KWH,
                    DAILY_ESP32_ENERGY_KWH,
                    pump_seconds / 3600,
                    valve_seconds / 3600
                ))
                
                print(f"   Processed {date}: Water={total_water:.2f}L, Energy={total_energy:.4f}kWh")
        
        # Also create today's entry if it doesn't exist
        today = datetime.now().strftime('%Y-%m-%d')
        cursor.execute("SELECT COUNT(*) FROM daily_resources WHERE date = ?", (today,))
        if cursor.fetchone()[0] == 0:
            cursor.execute("""
                INSERT INTO daily_resources
                (date, total_water_liters, total_energy_kwh, 
                 system_energy_kwh, esp32_energy_kwh,
                 pump_runtime_hours, valve_runtime_hours)
                VALUES (?, 0, 0, ?, ?, 0, 0)
            """, (today, DAILY_SYSTEM_ENERGY_KWH, DAILY_ESP32_ENERGY_KWH))
            print(f"   Created today's entry: {today}")
        
        conn.commit()
        conn.close()
        
        print("\n? daily_resources table rebuilt successfully!")
        
    except Exception as e:
        print(f"? Error rebuilding table: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def main():
    print("="*60)
    print("FIX DAILY_RESOURCES TABLE")
    print("="*60)
    print("\nOptions:")
    print("1. Update existing data with correct system/ESP32 values")
    print("2. Completely rebuild table from resource_consumption data")
    print("3. Exit")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    if choice == '1':
        fix_daily_resources()
    elif choice == '2':
        rebuild_daily_resources()
    elif choice == '3':
        print("Exiting.")
        sys.exit(0)
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    # Stop the Flask app first!
    print("??  IMPORTANT: Stop the Flask app before running this script!")
    print("Run: pkill -f 'python.*app.py'")
    print()
    
    confirm = input("Have you stopped the Flask app? (y/n): ").lower().strip()
    if confirm != 'y':
        print("Please stop the Flask app first!")
        sys.exit(1)
    
    main()