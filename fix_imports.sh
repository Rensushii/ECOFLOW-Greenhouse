#!/bin/bash
echo "Fixing import statements..."

# Fix database models.py
echo "Fixing database/models.py..."
cat > src/database/models.py << 'IMPORTFIX1'
# -*- coding: utf-8 -*-
"""
Database models and table definitions
"""

import sqlite3
from datetime import datetime
from pathlib import Path
import json
import numpy as np

# Helper functions for math operations
def sin(x):
    return np.sin(x)

def cos(x):
    return np.cos(x)

def pi():
    return np.pi

class DatabaseManager:
    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.connection = None
        
    def connect(self):
        """Establish database connection"""
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        return self.connection
    
    def close(self):
        """Close database connection"""
        if self.connection:
            self.connection.close()
    
    def execute(self, query, params=()):
        """Execute a query"""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        result = cursor.fetchall()
        self.close()
        return result
    
    def create_tables(self):
        """Create all necessary tables"""
        conn = self.connect()
        cursor = conn.cursor()
        
        # Main sensor data table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS greenhouse_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            temperature REAL,
            humidity REAL,
            soil1 REAL,
            soil2 REAL,
            soil3 REAL,
            lowLevel INTEGER,
            highLevel INTEGER,
            valve INTEGER,
            pump INTEGER,
            soil_avg REAL,
            hour INTEGER,
            day_of_week INTEGER,
            hour_sin REAL,
            hour_cos REAL,
            is_daytime BOOLEAN,
            temp_humidity REAL,
            soil_gradient_1_2 REAL,
            soil_gradient_2_3 REAL,
            evaporation_est REAL,
            hours_since_last_irrigation INTEGER
        )
        ''')
        
        # Resource consumption table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS resource_consumption (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            pump_runtime_seconds REAL,
            valve_runtime_seconds REAL,
            water_consumed_liters REAL,
            energy_consumed_kwh REAL,
            system_energy_kwh REAL,
            esp32_energy_kwh REAL,
            pump_state INTEGER,
            valve_state INTEGER
        )
        ''')
        
        # Daily resource summary
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_resources (
            date DATE PRIMARY KEY,
            total_water_liters REAL,
            total_energy_kwh REAL,
            system_energy_kwh REAL,
            esp32_energy_kwh REAL,
            pump_runtime_hours REAL,
            valve_runtime_hours REAL,
            irrigation_events INTEGER
        )
        ''')
        
        # AI decisions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            action REAL,
            reason TEXT,
            system_state TEXT,
            model_used TEXT,
            q_learning_action REAL,
            predicted_benefits TEXT,
            total_benefit REAL,
            sensor_data TEXT,
            executed INTEGER DEFAULT 0
        )
        ''')
        
        # AI schedules table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER,
            action REAL,
            reason TEXT,
            scheduled_time DATETIME,
            execution_time DATETIME,
            status TEXT DEFAULT 'scheduled',
            created_at DATETIME DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (decision_id) REFERENCES ai_decisions (id)
        )
        ''')
        
        # AI predictions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            current_soil1 REAL,
            current_soil2 REAL,
            current_soil3 REAL,
            predicted_change1 REAL,
            predicted_change2 REAL,
            predicted_change3 REAL,
            predicted_1h_soil1 REAL,
            predicted_1h_soil2 REAL,
            predicted_1h_soil3 REAL,
            predicted_3h_soil1 REAL,
            predicted_3h_soil2 REAL,
            predicted_3h_soil3 REAL,
            predicted_6h_soil1 REAL,
            predicted_6h_soil2 REAL,
            predicted_6h_soil3 REAL
        )
        ''')
        
        # AI statistics table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            period TEXT,
            total_decisions INTEGER,
            irrigation_decisions INTEGER,
            total_irrigation_minutes REAL,
            average_irrigation_duration REAL,
            model_q_learning INTEGER,
            model_hybrid_q_lr INTEGER,
            model_constraint_check INTEGER,
            model_soil_check INTEGER,
            model_unknown INTEGER
        )
        ''')
        
        # Hourly aggregation table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS greenhouse_hourly (
            hour_start TIMESTAMP PRIMARY KEY,
            avg_temp REAL,
            avg_humidity REAL,
            avg_soil1 REAL,
            avg_soil2 REAL,
            avg_soil3 REAL,
            valve_on_minutes INTEGER,
            pump_on_minutes INTEGER,
            irrigation_events INTEGER,
            data_points INTEGER
        )
        ''')
        
        # Daily aggregation table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS greenhouse_daily (
            date DATE PRIMARY KEY,
            max_temp REAL,
            min_temp REAL,
            avg_temp REAL,
            avg_humidity REAL,
            avg_soil_moisture REAL,
            total_water_used REAL,
            irrigation_count INTEGER,
            water_efficiency REAL,
            data_points INTEGER
        )
        ''')
        
        # Data quality log table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS data_quality_log (
            timestamp TIMESTAMP PRIMARY KEY,
            total_readings INTEGER,
            temp_quality_pct REAL,
            humidity_quality_pct REAL,
            avg_temp REAL,
            avg_humidity REAL,
            days_with_data INTEGER
        )
        ''')
        
        conn.commit()
        self.close()
        print("? Database tables created successfully")
    
    def insert_sensor_data(self, data):
        """Insert sensor data into database"""
        query = '''
        INSERT INTO greenhouse_data 
        (temperature, humidity, soil1, soil2, soil3, lowLevel, highLevel, 
         valve, pump, soil_avg, hour, day_of_week, hour_sin, hour_cos,
         is_daytime, temp_humidity, soil_gradient_1_2, soil_gradient_2_3,
         evaporation_est, hours_since_last_irrigation)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        '''
        
        # Calculate enhanced features
        from datetime import datetime
        now = datetime.now()
        
        soil_avg = (data.get('soil1', 0) + data.get('soil2', 0) + data.get('soil3', 0)) / 3
        hour = now.hour
        day_of_week = now.weekday()
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        is_daytime = 1 if 6 <= hour <= 18 else 0
        temp_humidity = data.get('temperature', 0) * data.get('humidity', 0) / 100
        soil_gradient_1_2 = data.get('soil1', 0) - data.get('soil2', 0)
        soil_gradient_2_3 = data.get('soil2', 0) - data.get('soil3', 0)
        evaporation_est = 0.5 * (data.get('temperature', 0) / 80) * (1 - data.get('humidity', 0) / 100)
        hours_since_last_irrigation = 999  # Default placeholder
        
        params = (
            data.get('temperature'),
            data.get('humidity'),
            data.get('soil1'),
            data.get('soil2'),
            data.get('soil3'),
            data.get('lowLevel'),
            data.get('highLevel'),
            data.get('valve'),
            data.get('pump'),
            soil_avg,
            hour,
            day_of_week,
            hour_sin,
            hour_cos,
            is_daytime,
            temp_humidity,
            soil_gradient_1_2,
            soil_gradient_2_3,
            evaporation_est,
            hours_since_last_irrigation
        )
        
        self.execute(query, params)
        return True
    
    def get_latest_data(self, limit=1):
        """Get latest sensor data"""
        query = "SELECT * FROM greenhouse_data ORDER BY timestamp DESC LIMIT ?"
        result = self.execute(query, (limit,))
        return [dict(row) for row in result]
    
    def get_data_range(self, hours=24, limit=1000):
        """Get data from the last X hours"""
        query = """
        SELECT * FROM greenhouse_data 
        WHERE timestamp >= datetime('now', ?) 
        ORDER BY timestamp DESC 
        LIMIT ?
        """
        result = self.execute(query, (f'-{hours} hours', limit))
        return [dict(row) for row in result]
    
    def get_database_info(self):
        """Get database statistics"""
        try:
            conn = self.connect()
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
            total_records = cursor.fetchone()[0]
            
            cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM greenhouse_data")
            time_range = cursor.fetchone()
            
            cursor.execute("SELECT COUNT(DISTINCT date(timestamp)) FROM greenhouse_data")
            days_with_data = cursor.fetchone()[0]
            
            conn.close()
            
            return {
                'total_records': total_records,
                'first_record': time_range[0] if time_range[0] else None,
                'last_record': time_range[1] if time_range[1] else None,
                'days_with_data': days_with_data
            }
        except Exception as e:
            return {'error': str(e)}
IMPORTFIX1

# Fix database/setup.py
echo "Fixing database/setup.py..."
cat > src/database/setup.py << 'IMPORTFIX2'
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
IMPORTFIX2

echo "Import fixes applied!"
