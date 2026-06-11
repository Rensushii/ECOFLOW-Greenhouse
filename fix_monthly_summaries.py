# /home/group4/GREENHOUSE/fix_monthly_summaries.py
#!/usr/bin/env python3
"""
Fix monthly summaries by recalculating from source tables
"""

import sqlite3
from datetime import datetime
from config import DB_PATH

def recalculate_monthly_summary(year, month):
    """Recalculate monthly summary from source tables"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        month_year = f"{year}-{month:02d}"
        start_date = f"{year}-{month:02d}-01"
        
        if month == 12:
            end_date = f"{year+1}-01-01"
        else:
            end_date = f"{year}-{month+1:02d}-01"
        
        print(f"\n? Recalculating {month_year}...")
        
        # Delete existing entry
        cursor.execute("DELETE FROM monthly_summary WHERE month_year = ?", (month_year,))
        
        # 1. Calculate sensor averages from greenhouse_data
        cursor.execute("""
            SELECT
                AVG(temperature) as avg_temp,
                AVG(humidity) as avg_humidity,
                AVG(soil1) as avg_soil1,
                AVG(soil2) as avg_soil2,
                AVG(soil3) as avg_soil3,
                COUNT(*) as data_points
            FROM greenhouse_data
            WHERE timestamp >= ? AND timestamp < ?
        """, (start_date, end_date))
        
        sensor_result = cursor.fetchone()
        
        # 2. Calculate ACTUAL water and energy from resource_consumption table
        cursor.execute("""
            SELECT
                COALESCE(SUM(water_consumed_liters), 0) as total_water,
                COALESCE(SUM(energy_consumed_kwh), 0) as total_energy,
                COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds,
                COALESCE(SUM(valve_runtime_seconds), 0) as valve_seconds,
                COUNT(CASE WHEN pump_state = 1 THEN 1 END) as irrigation_events
            FROM resource_consumption
            WHERE timestamp >= ? AND timestamp < ?
        """, (start_date, end_date))
        
        resource_result = cursor.fetchone()
        
        # 3. Calculate system and ESP32 energy (FIXED: Always add these!)
        total_water = resource_result[0] if resource_result[0] else 0
        total_energy = resource_result[1] if resource_result[1] else 0
        pump_seconds = resource_result[2] if resource_result[2] else 0
        valve_seconds = resource_result[3] if resource_result[3] else 0
        irrigation_events = resource_result[4] if resource_result[4] else 0
        
        # Calculate days in month
        if month == 12:
            next_month = datetime(year+1, 1, 1)
        else:
            next_month = datetime(year, month+1, 1)
        current_month = datetime(year, month, 1)
        days_in_month = (next_month - current_month).days
        
        # FIXED: Always add system and ESP32 energy
        system_energy = 0.12 * days_in_month  # 5W × 24h = 0.12 kWh/day
        esp32_energy = 0.0024 * days_in_month  # 0.1W × 24h = 0.0024 kWh/day
        total_combined_energy = total_energy + system_energy + esp32_energy
        
        # 4. Insert corrected monthly summary
        cursor.execute("""
            INSERT INTO monthly_summary (
                month_year, year, month,
                avg_temp, avg_humidity, avg_soil1, avg_soil2, avg_soil3,
                total_water_liters, total_energy_kwh,
                total_system_energy_kwh, total_esp32_energy_kwh,
                total_combined_energy_kwh,
                pump_runtime_hours, valve_runtime_hours,
                irrigation_events, data_points,
                generated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            month_year, year, month,
            sensor_result[0] if sensor_result[0] else 0,
            sensor_result[1] if sensor_result[1] else 0,
            sensor_result[2] if sensor_result[2] else 0,
            sensor_result[3] if sensor_result[3] else 0,
            sensor_result[4] if sensor_result[4] else 0,
            total_water,
            total_energy,
            system_energy,
            esp32_energy,
            total_combined_energy,
            pump_seconds / 3600,  # Convert to hours
            valve_seconds / 3600,  # Convert to hours
            irrigation_events,
            sensor_result[5] if sensor_result[5] else 0,
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        print(f"? Monthly summary recalculated for {month_year}")
        print(f"   Water: {total_water:.2f} L")
        print(f"   Energy: {total_energy:.6f} kWh")
        print(f"   System Energy: {system_energy:.4f} kWh ({days_in_month} days)")
        print(f"   ESP32 Energy: {esp32_energy:.6f} kWh")
        print(f"   Combined Energy: {total_combined_energy:.6f} kWh")
        print(f"   Pump Runtime: {pump_seconds/3600:.2f} hours")
        print(f"   Valve Runtime: {valve_seconds/3600:.2f} hours")
        
        return True
        
    except Exception as e:
        print(f"? Error recalculating monthly summary: {e}")
        import traceback
        traceback.print_exc()
        return False

def fix_all_monthly_summaries():
    """Fix all monthly summaries"""
    db_path = str(DB_PATH)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get all months with data
    cursor.execute("""
        SELECT DISTINCT 
            strftime('%Y', timestamp) as year,
            strftime('%m', timestamp) as month
        FROM greenhouse_data
        WHERE timestamp IS NOT NULL
        ORDER BY year DESC, month DESC
    """)
    
    months = cursor.fetchall()
    
    print(f"Found {len(months)} months with data")
    
    for year, month in months:
        if year and month:
            year = int(year)
            month = int(month)
            recalculate_monthly_summary(year, month)
    
    conn.close()
    print("\n? All monthly summaries fixed!")

if __name__ == "__main__":
    fix_all_monthly_summaries()