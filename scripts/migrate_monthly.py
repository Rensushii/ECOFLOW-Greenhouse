#!/usr/bin/env python3
"""
Migration script to create monthly_summary table and migrate data
FIXED VERSION - handles missing columns
"""

import sqlite3
from datetime import datetime, timedelta
import sys
import os

# Add path to config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import DB_PATH

def check_table_schema():
    """Check the actual schema of the daily_resources table"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Check daily_resources table schema
    cursor.execute("PRAGMA table_info(daily_resources)")
    columns = cursor.fetchall()
    
    print("? Current daily_resources table columns:")
    column_names = []
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
        column_names.append(col[1])
    
    conn.close()
    return column_names

def migrate_database():
    """Migrate database to add monthly_summary table - FIXED VERSION"""
    print("="*60)
    print("? Starting database migration (FIXED VERSION)")
    print("="*60)
    
    # First check the actual schema
    column_names = check_table_schema()
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # 1. Drop the old daily_summary table if it exists
    try:
        cursor.execute("DROP TABLE IF EXISTS daily_summary")
        print("? Dropped old daily_summary table")
    except Exception as e:
        print(f"? Error dropping daily_summary: {e}")
    
    # 2. Create the new monthly_summary table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS monthly_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        month_year VARCHAR(7) UNIQUE,
        year INTEGER,
        month INTEGER,
        avg_temp REAL,
        avg_humidity REAL,
        avg_soil1 REAL,
        avg_soil2 REAL,
        avg_soil3 REAL,
        total_water_liters REAL,
        total_energy_kwh REAL,
        total_system_energy_kwh REAL,
        total_esp32_energy_kwh REAL,
        total_combined_energy_kwh REAL,
        pump_runtime_hours REAL,
        valve_runtime_hours REAL,
        irrigation_events INTEGER DEFAULT 0,
        data_points INTEGER,
        generated_at DATETIME DEFAULT (datetime('now', 'localtime'))
    )
    ''')
    print("? Created monthly_summary table")
    
    # 3. Generate initial monthly data from existing greenhouse_data
    print("? Generating initial monthly data...")
    
    # Get distinct months from greenhouse_data
    cursor.execute("""
        SELECT DISTINCT 
            strftime('%Y-%m', timestamp) as month_year,
            strftime('%Y', timestamp) as year,
            strftime('%m', timestamp) as month
        FROM greenhouse_data
        WHERE timestamp IS NOT NULL
        ORDER BY year DESC, month DESC
        LIMIT 12
    """)
    
    months = cursor.fetchall()
    print(f"? Found {len(months)} months of data to process")
    
    for month_year, year, month in months:
        # Check if monthly summary already exists
        cursor.execute("SELECT id FROM monthly_summary WHERE month_year = ?", (month_year,))
        existing = cursor.fetchone()
        
        if existing:
            print(f"? Monthly summary for {month_year} already exists, skipping...")
            continue
        
        start_date = f"{year}-{month}-01"
        if int(month) == 12:
            end_date = f"{int(year)+1}-01-01"
        else:
            end_date = f"{year}-{int(month)+1:02d}-01"
        
        print(f"? Processing {month_year} ({start_date} to {end_date})")
        
        # Calculate averages from greenhouse_data
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
            AND temperature IS NOT NULL
        """, (start_date, end_date))
        
        sensor_result = cursor.fetchone()
        
        # Calculate totals from daily_resources with dynamic column handling
        daily_query = """
            SELECT 
                SUM(total_water_liters) as total_water,
                SUM(total_energy_kwh) as total_energy
        """
        
        # Add system_energy_kwh if column exists
        if 'system_energy_kwh' in column_names:
            daily_query += ", SUM(system_energy_kwh) as total_system_energy"
        else:
            daily_query += ", 0 as total_system_energy"
            
        # Add esp32_energy_kwh if column exists
        if 'esp32_energy_kwh' in column_names:
            daily_query += ", SUM(esp32_energy_kwh) as total_esp32_energy"
        else:
            daily_query += ", 0 as total_esp32_energy"
            
        # Add pump_runtime_hours if column exists
        if 'pump_runtime_hours' in column_names:
            daily_query += ", SUM(pump_runtime_hours) as pump_hours"
        else:
            daily_query += ", 0 as pump_hours"
            
        # Add valve_runtime_hours if column exists
        if 'valve_runtime_hours' in column_names:
            daily_query += ", SUM(valve_runtime_hours) as valve_hours"
        else:
            daily_query += ", 0 as valve_hours"
        
        daily_query += """
            FROM daily_resources
            WHERE date >= ? AND date < ?
        """
        
        cursor.execute(daily_query, (start_date, end_date))
        resource_result = cursor.fetchone()
        
        # Calculate total combined energy (with fallbacks)
        total_water = resource_result[0] if resource_result and resource_result[0] is not None else 0
        total_energy = resource_result[1] if resource_result and resource_result[1] is not None else 0
        total_system_energy = resource_result[2] if resource_result and resource_result[2] is not None else 0.12 * 30  # Estimate
        total_esp32_energy = resource_result[3] if resource_result and resource_result[3] is not None else 0.0024 * 30  # Estimate
        total_combined_energy = total_energy + total_system_energy + total_esp32_energy
        
        # Calculate irrigation events from AI decisions (if table exists)
        irrigation_events = 0
        try:
            cursor.execute("""
                SELECT COUNT(*) 
                FROM ai_decisions 
                WHERE timestamp >= ? AND timestamp < ?
                AND action > 0
            """, (start_date, end_date))
            irrigation_result = cursor.fetchone()
            irrigation_events = irrigation_result[0] if irrigation_result else 0
        except:
            irrigation_events = 0
        
        # Insert monthly summary with safe defaults
        cursor.execute("""
            INSERT INTO monthly_summary (
                month_year, year, month,
                avg_temp, avg_humidity, avg_soil1, avg_soil2, avg_soil3,
                total_water_liters, total_energy_kwh,
                total_system_energy_kwh, total_esp32_energy_kwh,
                total_combined_energy_kwh,
                pump_runtime_hours, valve_runtime_hours,
                irrigation_events, data_points
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            month_year, int(year), int(month),
            sensor_result[0] if sensor_result and sensor_result[0] is not None else 25.0,
            sensor_result[1] if sensor_result and sensor_result[1] is not None else 60.0,
            sensor_result[2] if sensor_result and sensor_result[2] is not None else 50.0,
            sensor_result[3] if sensor_result and sensor_result[3] is not None else 50.0,
            sensor_result[4] if sensor_result and sensor_result[4] is not None else 50.0,
            total_water,
            total_energy,
            total_system_energy,
            total_esp32_energy,
            total_combined_energy,
            resource_result[4] if resource_result and resource_result[4] is not None else 0,
            resource_result[5] if resource_result and resource_result[5] is not None else 0,
            irrigation_events,
            sensor_result[5] if sensor_result and sensor_result[5] is not None else 0
        ))
        
        print(f"? Generated monthly summary for {month_year}:")
        print(f"  - Avg Temp: {sensor_result[0] if sensor_result and sensor_result[0] else 25.0:.1f}°C")
        print(f"  - Avg Humidity: {sensor_result[1] if sensor_result and sensor_result[1] else 60.0:.1f}%")
        print(f"  - Total Water: {total_water:.1f}L")
        print(f"  - Total Energy: {total_combined_energy:.3f}kWh")
        print(f"  - Data Points: {sensor_result[5] if sensor_result and sensor_result[5] else 0}")
    
    conn.commit()
    conn.close()
    
    print("="*60)
    print("? Database migration completed successfully!")
    print("="*60)
    return True

def create_sample_monthly_data():
    """Create sample monthly data for testing"""
    print("? Creating sample monthly data...")
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Clear existing monthly data
    cursor.execute("DELETE FROM monthly_summary")
    
    # Create sample data for the last 6 months
    from datetime import datetime
    current_date = datetime.now()
    
    sample_data = []
    for i in range(6):
        month_date = current_date.replace(day=1) - timedelta(days=30*i)
        month_year = month_date.strftime("%Y-%m")
        year = month_date.year
        month = month_date.month
        
        # Generate realistic sample data
        avg_temp = 25.0 + (i * 0.5)  # Slight variation
        avg_humidity = 60.0 + (i * 2)  # Slight variation
        avg_soil = 50.0 + (i * 3)  # Slight variation
        
        # Resource usage increases slightly each month
        base_water = 350.0 + (i * 20)
        base_energy = 28.5 + (i * 1.5)
        system_energy = 0.12 * 30  # Fixed daily * 30 days
        esp32_energy = 0.0024 * 30  # Fixed daily * 30 days
        total_energy = base_energy + system_energy + esp32_energy
        
        sample_data.append((
            month_year, year, month,
            avg_temp, avg_humidity,
            avg_soil, avg_soil + 5, avg_soil - 5,  # Different soil zones
            base_water, base_energy,
            system_energy, esp32_energy, total_energy,
            i * 10 + 50, i * 5 + 25,  # Pump and valve runtime
            i * 3 + 10,  # Irrigation events
            720 * (6 - i)  # Data points (12hrs/day * 60 days)
        ))
    
    # Insert sample data
    cursor.executemany("""
        INSERT INTO monthly_summary (
            month_year, year, month,
            avg_temp, avg_humidity, avg_soil1, avg_soil2, avg_soil3,
            total_water_liters, total_energy_kwh,
            total_system_energy_kwh, total_esp32_energy_kwh,
            total_combined_energy_kwh,
            pump_runtime_hours, valve_runtime_hours,
            irrigation_events, data_points
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sample_data)
    
    conn.commit()
    conn.close()
    
    print(f"? Created {len(sample_data)} months of sample data")
    return True

if __name__ == '__main__':
    try:
        migrate_database()
        
        # Check if we have any monthly data
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM monthly_summary")
        count = cursor.fetchone()[0]
        conn.close()
        
        if count == 0:
            print("? No monthly data found, creating sample data...")
            create_sample_monthly_data()
        
        print("? Migration complete!")
        
    except Exception as e:
        print(f"?? Migration failed: {e}")
        import traceback
        traceback.print_exc()