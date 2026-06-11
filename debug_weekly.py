# Create a debug script to check the weekly calculation
# /home/group4/GREENHOUSE/debug_weekly.py
#!/usr/bin/env python3
"""
Debug weekly report calculation
"""

import sqlite3
from datetime import datetime, timedelta
from config import DB_PATH

def debug_weekly_report():
    """Debug weekly report calculation"""
    db_path = str(DB_PATH)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("? Debugging weekly report...")
    
    # Check what data we have
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM greenhouse_data")
    date_range = cursor.fetchone()
    print(f"Data range: {date_range[0]} to {date_range[1]}")
    
    # Check resource_consumption table
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM resource_consumption")
    resource_range = cursor.fetchone()
    print(f"Resource range: {resource_range[0]} to {resource_range[1]}")
    
    # Try the weekly calculation directly
    start_date = "2026-01-01"
    end_date = "2026-01-08"
    
    print(f"\n? Testing weekly calculation from {start_date} to {end_date}")
    
    # 1. Sensor data
    cursor.execute("""
        SELECT
            AVG(temperature) as avg_temp,
            AVG(humidity) as avg_humidity,
            COUNT(*) as data_points
        FROM greenhouse_data
        WHERE timestamp >= ? AND timestamp < ?
    """, (start_date, end_date))
    
    sensor_result = cursor.fetchone()
    print(f"? Sensor query successful:")
    print(f"   Avg temp: {sensor_result[0]}")
    print(f"   Avg humidity: {sensor_result[1]}")
    print(f"   Data points: {sensor_result[2]}")
    
    # 2. Resource data
    cursor.execute("""
        SELECT
            COALESCE(SUM(water_consumed_liters), 0) as total_water,
            COALESCE(SUM(energy_consumed_kwh), 0) as total_energy
        FROM resource_consumption
        WHERE timestamp >= ? AND timestamp < ?
    """, (start_date, end_date))
    
    resource_result = cursor.fetchone()
    print(f"\n? Resource query successful:")
    print(f"   Total water: {resource_result[0]}")
    print(f"   Total energy: {resource_result[1]}")
    
    # 3. Daily breakdown
    cursor.execute("""
        SELECT
            date(gd.timestamp) as day,
            AVG(gd.temperature) as avg_temp,
            AVG(gd.humidity) as avg_humidity,
            COALESCE(SUM(rc.water_consumed_liters), 0) as water,
            COALESCE(SUM(rc.energy_consumed_kwh), 0) as energy
        FROM greenhouse_data gd
        LEFT JOIN resource_consumption rc ON date(gd.timestamp) = date(rc.timestamp)
        WHERE gd.timestamp >= ? AND gd.timestamp < ?
        GROUP BY date(gd.timestamp)
        ORDER BY day
    """, (start_date, end_date))
    
    daily_rows = cursor.fetchall()
    print(f"\n? Daily breakdown successful: {len(daily_rows)} days")
    
    conn.close()

if __name__ == "__main__":
    debug_weekly_report()