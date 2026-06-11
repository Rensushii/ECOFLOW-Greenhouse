# -*- coding: utf-8 -*-
"""
Database models and table definitions - SIMPLIFIED VERSION
"""

import sqlite3
from datetime import datetime
from pathlib import Path
import json
import numpy as np

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
        """Create only essential tables"""
        conn = self.connect()
        cursor = conn.cursor()
       
        # Main sensor data table - SIMPLIFIED
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS greenhouse_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            temperature REAL,
            humidity REAL,
            soil1 INTEGER,
            soil2 INTEGER,
            soil3 INTEGER,
            lowLevel INTEGER,
            highLevel INTEGER,
            valve INTEGER,
            pump INTEGER
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
       
        # Daily resource summary (for fast reporting)
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
       
        # Manual commands table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS manual_commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            device TEXT,
            command TEXT,
            requested_state TEXT,
            actual_state TEXT,
            success INTEGER,
            notes TEXT
        )
        ''')
       
        conn.commit()
        self.close()
        print("? Database tables created (simplified version)")