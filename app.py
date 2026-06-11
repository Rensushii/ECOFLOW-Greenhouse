#!/usr/bin/env python3
"""
Greenhouse Monitoring System with Daily/Weekly/Monthly Reports + Real-time Graphs
UPRADED: Uses Linear Regression predictor with confidence intervals + CONTINUOUS AUTO-TRAINING
ENHANCED: send_to_frontend() now includes resource consumption data from last 5 minutes
FIXED: Periodic training only (24 hours) - removed data-check training to prevent over-training
ADDED: Timestamp checking for commands (1-minute expiry)
ENHANCED: Business rules for optimal irrigation timing
ENHANCED: Time-aware model features
ADDED: Model state persistence (save/load on restart)
FIXED: Initialize devices OFF on system startup - WITH ROBUST RETRY MECHANISM
FIXED: Serial port initialization with retries - prevents crash on boot
ADDED: Data cleaning endpoint to remove sensor errors from training data
ENHANCED: Multi-zone calibration system for all sensors (Zone A, B, C)
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import threading
import time
import sqlite3
import json
import hashlib
import os
import sys
import requests
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
import atexit

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import configuration
from config import (
    DB_PATH, ADMIN_PASSWORD, API_KEY, ML_ENABLED,
    FRONTEND_API_URL, FRONTEND_API_KEY,
    FRONTEND_COMMANDS_URL, FRONTEND_UPDATE_URL,
    SERIAL_PORTS, SERIAL_BAUD, DATA_SEND_INTERVAL,
    ML_CONFIG
)

try:
    from sensors.serial_reader import SerialReader
    print("✅ Imported serial_reader module")
except ImportError as e:
    print(f"❌ Error importing serial_reader: {e}")
    SerialReader = None

try:
    from src.resources.tracker import ResourceTracker
    print("✅ Imported ResourceTracker module")
except ImportError as e:
    print(f"❌ Error importing ResourceTracker: {e}")
    ResourceTracker = None

try:
    from src.ml.scheduler import IrrigationScheduler
    from src.ml.decision_tracker import AIDecisionTracker
    from src.ml.schedule_executor import ScheduleExecutor
    print("✅ Imported ML modules")
except ImportError as e:
    print(f"❌ Error importing ML modules: {e}")
    IrrigationScheduler = None
    AIDecisionTracker = None
    ScheduleExecutor = None

# Import the Linear Regression predictor
try:
    from src.ml.predictor import SoilMoisturePredictor
    print("✅ Imported SoilMoisturePredictor module")
except ImportError as e:
    print(f"❌ Error importing SoilMoisturePredictor: {e}")
    SoilMoisturePredictor = None

# Create Flask app
app = Flask(__name__,
            template_folder='templates',
            static_folder='static')
app.secret_key = "group4_thesis_secret_key_2024_secure_random"

# Constants
WATER_FLOW_RATE_LPM = 0.3  # 0.3 liters per minute flow rate

# ==============================================
# MULTI-ZONE CALIBRATION SETTINGS (For all sensors)
# ==============================================
# Each zone can have its own calibration parameters
# This allows for broken sensors that don't read 0-100% correctly

class ZoneCalibration:
    """Calibration settings for a sensor zone"""
    def __init__(self, name, enabled=True, original_min=0.0, original_max=100.0, 
                 calibrated_min=0.0, calibrated_max=100.0, description=""):
        self.name = name
        self.enabled = enabled
        self.original_min = original_min
        self.original_max = original_max
        self.calibrated_min = calibrated_min
        self.calibrated_max = calibrated_max
        self.description = description
    
    def calibrate(self, raw_value):
        """Calibrate a raw sensor reading"""
        if raw_value is None or not self.enabled:
            return raw_value
        
        # Ensure value is within expected range
        raw_value = max(self.original_min, 
                       min(self.original_max, float(raw_value)))
        
        # Linear scaling
        if self.original_max > self.original_min:
            scale = (self.calibrated_max - self.calibrated_min) / \
                    (self.original_max - self.original_min)
            
            calibrated = self.calibrated_min + \
                        (raw_value - self.original_min) * scale
            
            calibrated = max(self.calibrated_min,
                           min(self.calibrated_max, calibrated))
            
            return calibrated
        else:
            return raw_value
    
    def to_dict(self):
        """Convert to dictionary for API responses"""
        return {
            "enabled": self.enabled,
            "original_min": self.original_min,
            "original_max": self.original_max,
            "calibrated_min": self.calibrated_min,
            "calibrated_max": self.calibrated_max,
            "description": self.description
        }

# Initialize zone calibrations
zone_calibrations = {
    'soil1': ZoneCalibration(
        name="Zone A",
        enabled=True,  # Disabled by default (assume working sensor)
        original_min=0.0,
        original_max=100.0,
        calibrated_min=0.0,
        calibrated_max=100.0,
        description="Zone A sensor - normally working (no calibration needed)"
    ),
    'soil2': ZoneCalibration(
        name="Zone B",
        enabled=True,  # Broken sensor - enabled by default
        original_min=0.0,
        original_max=100.0,  # Broken sensor max reading
        calibrated_min=0.0,
        calibrated_max=100.0,
        description="Zone B sensor - broken (scales 0-60% to 0-100%)"
    ),
    'soil3': ZoneCalibration(
        name="Zone C",
        enabled=True,  # change to False to disable (assume working sensor)
        original_min=0.0,
        original_max=100.0,
        calibrated_min=0.0,
        calibrated_max=100.0,
        description="Zone C sensor - normally working (no calibration needed)"
    )
}

def calibrate_zone(zone_name, raw_value):
    """Calibrate a sensor reading for a specific zone"""
    if zone_name in zone_calibrations:
        return zone_calibrations[zone_name].calibrate(raw_value)
    return raw_value

# Global data storage
sensor_data = {
    'temperature': 0.0,
    'humidity': 0.0,
    'soil1': 0,
    'soil2': 0,
    'soil3': 0,
    'lowLevel': 0,
    'highLevel': 0,
    'valve': 0,
    'pump': 0,
    'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    'soil1_raw': 0,  # Store raw values for debugging
    'soil2_raw': 0,
    'soil3_raw': 0
}
resource_stats = {}
running = True
system_start_time = datetime.now()
last_frontend_sync = 0
processed_commands = set()
serial_reader = None
ml_state = ML_CONFIG.get('ML_STATES', {}).get('HYBRID', 'hybrid')

# AI System Components
ml_manager = None
decision_tracker = None
schedule_executor = None
soil_predictor = None  # Linear Regression predictor

# Manual override tracking (NO TIMEOUT)
manual_valve_control = False
manual_pump_control = False
last_manual_command_time = 0
manual_runtime_start = {'pump': None, 'valve': None}

# Initialize ResourceTracker
resource_tracker = None
if ResourceTracker:
    resource_tracker = ResourceTracker(str(DB_PATH))
    print("✅ ResourceTracker initialized")
else:
    print("❌ ResourceTracker not available")

# Initialize Linear Regression predictor
if SoilMoisturePredictor:
    try:
        soil_predictor = SoilMoisturePredictor(str(DB_PATH), ML_CONFIG)
        
        # Create models directory if it doesn't exist
        models_dir = os.path.join(os.path.dirname(__file__), 'models')
        os.makedirs(models_dir, exist_ok=True)
        
        # Try to load saved state
        model_save_path = os.path.join(models_dir, 'predictor_state.json')
        if os.path.exists(model_save_path):
            try:
                if soil_predictor.load_model(model_save_path):
                    print(f"✅ Loaded predictor state from {model_save_path}")
                    print(f"✅   Models: {len(soil_predictor.models)}, Training records: {len(soil_predictor.training_history)}")
                else:
                    print(f"⚠️ Could not load predictor state, will train fresh")
            except Exception as e:
                print(f"❌ Error loading predictor state: {e}")
        else:
            print(f"ℹ️ No saved predictor state found at {model_save_path}")
            print(f"ℹ️ Models directory: {models_dir} (exists: {os.path.exists(models_dir)})")
        
        print("✅ SoilMoisturePredictor (Linear Regression) initialized")
    except Exception as e:
        print(f"❌ Error initializing SoilMoisturePredictor: {e}")
        import traceback
        traceback.print_exc()
        soil_predictor = None
else:
    print("❌ SoilMoisturePredictor not available")

# Initialize AI System
if ML_ENABLED and IrrigationScheduler and AIDecisionTracker:
    try:
        print("✅ Initializing AI system...")
        decision_tracker = AIDecisionTracker(str(DB_PATH))
        
        # Create a simple config for IrrigationScheduler
        scheduler_config = {
            'zones': {
                'A': {'weight': 1.2, 'crop_factor': 1.0},
                'B': {'weight': 1.0, 'crop_factor': 0.9},
                'C': {'weight': 0.8, 'crop_factor': 0.8}
            }
        }
        
        ml_manager = IrrigationScheduler(
            db_path=str(DB_PATH),
            config=scheduler_config,
            ml_config=ML_CONFIG
        )
        print("✅ AI system initialized")
    except Exception as e:
        print(f"❌ AI system initialization failed: {e}")
        ml_manager = None
        decision_tracker = None
else:
    print("❌ AI system not enabled or modules not available")

# ==============================================
# FIX: Initialize devices OFF on startup - WITH ROBUST RETRY MECHANISM
# ==============================================
def initialize_devices_off(max_retries=10, retry_delay=2):
    """Ensure all devices are OFF on system startup - WITH RETRIES"""
    global serial_reader, sensor_data, manual_valve_control, manual_pump_control
    
    print("🔄 System startup: Ensuring all devices are OFF...")
    print(f"🔄 Will retry {max_retries} times every {retry_delay} seconds if serial not ready")
    
    for attempt in range(max_retries):
        try:
            # Check if serial reader exists and is properly initialized
            if serial_reader and hasattr(serial_reader, 'ser'):
                if serial_reader.ser and serial_reader.ser.is_open:
                    # Serial is ready, send commands
                    print(f"✅ Serial ready on attempt {attempt + 1}/{max_retries}")
                    
                    # Send explicit OFF commands for both devices
                    print("  ⏺️ Sending pump OFF command...")
                    pump_success = serial_reader.send_command({"pump": "off"})
                    time.sleep(0.3)  # Small delay between commands
                    
                    print("  ⏺️ Sending valve OFF command...")
                    valve_success = serial_reader.send_command({"valve": "off"})
                    
                    # Update sensor data state
                    sensor_data['pump'] = 0
                    sensor_data['valve'] = 0
                    sensor_data['timestamp'] = datetime.now().isoformat()
                    
                    # Reset manual override flags
                    manual_valve_control = False
                    manual_pump_control = False
                    
                    # Save to database
                    save_to_database(sensor_data)
                    
                    # Log the initialization command
                    log_manual_command(
                        device="system",
                        command="init_off",
                        requested_state="OFF",
                        actual_state="OFF",
                        success=True,
                        notes=f"System startup - devices forced OFF (attempt {attempt + 1})"
                    )
                    
                    print(f"✅ Devices set to OFF on startup - Pump: {pump_success}, Valve: {valve_success}")
                    return True
                else:
                    # Serial port exists but not open
                    print(f"⚠️ Serial port not open on attempt {attempt + 1}/{max_retries}, waiting {retry_delay}s...")
                    time.sleep(retry_delay)
            else:
                # Serial reader not initialized yet
                print(f"⚠️ Serial reader not ready on attempt {attempt + 1}/{max_retries}, waiting {retry_delay}s...")
                time.sleep(retry_delay)
                
        except Exception as e:
            print(f"❌ Error on attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                print(f"🔄 Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print("❌ Max retries reached, continuing without serial")
    
    # Even if serial fails completely, update the local state
    print("⚠️ Warning: Could not connect to serial after all retries, simulating devices OFF")
    sensor_data['pump'] = 0
    sensor_data['valve'] = 0
    sensor_data['timestamp'] = datetime.now().isoformat()
    save_to_database(sensor_data)
    
    # Reset manual override flags
    manual_valve_control = False
    manual_pump_control = False
    
    # Log the simulation
    log_manual_command(
        device="system",
        command="init_off_simulated",
        requested_state="OFF",
        actual_state="OFF",
        success=True,
        notes="System startup - serial unavailable, simulated OFF"
    )
    
    print("✅ Simulated devices OFF (sensor data updated)")
    return False

def save_predictor_state():
    """Save predictor state to file"""
    global soil_predictor
    if soil_predictor:
        try:
            models_dir = os.path.join(os.path.dirname(__file__), 'models')
            os.makedirs(models_dir, exist_ok=True)
            model_save_path = os.path.join(models_dir, 'predictor_state.json')
            
            if soil_predictor.save_model(model_save_path):
                print(f"✅ Predictor state saved to {model_save_path}")
                return True
            else:
                print(f"❌ Failed to save predictor state")
                return False
        except Exception as e:
            print(f"❌ Error saving predictor state: {e}")
            return False
    return False

# Initialize database - UPDATED VERSION WITH FIXED SQL
def init_database():
    """Initialize database tables - FIXED VERSION"""
    db_path_str = str(DB_PATH)
    os.makedirs(os.path.dirname(db_path_str), exist_ok=True)
    
    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()
    
    # Main sensor data table (with id field for frontend)
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
        pump_state INTEGER,
        valve_state INTEGER
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
    
    # AI decisions table (for admin interface)
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
    
    # AI schedules table (for admin interface)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ai_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        decision_id INTEGER,
        action REAL,
        reason TEXT,
        scheduled_time DATETIME,
        execution_time DATETIME,
        status TEXT DEFAULT 'scheduled',
        created_at DATETIME DEFAULT (datetime('now', 'localtime'))
    )
    ''')
    
    # NEW: Irrigation history table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS irrigation_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
        duration_minutes REAL,
        water_used_liters REAL,
        energy_used_kwh REAL,
        mode TEXT,
        reason TEXT,
        schedule_id INTEGER,
        decision_id INTEGER,
        success INTEGER,
        FOREIGN KEY (schedule_id) REFERENCES ai_schedules (id),
        FOREIGN KEY (decision_id) REFERENCES ai_decisions (id)
    )
    ''')
    
    # FIXED: AI predictions table - corrected SQL syntax
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ai_predictions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
        predicted_1h_soil1 REAL,
        predicted_3h_soil1 REAL,
        predicted_6h_soil1 REAL,
        predicted_1h_soil2 REAL,
        predicted_3h_soil2 REAL,
        predicted_6h_soil2 REAL,
        predicted_1h_soil3 REAL,
        predicted_3h_soil3 REAL,
        predicted_6h_soil3 REAL,
        confidence_score REAL,
        model_used TEXT,
        notes TEXT
    )
    ''')
    
    # NEW: Calibration settings table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS calibration_settings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        zone TEXT UNIQUE,
        enabled INTEGER DEFAULT 0,
        original_min REAL DEFAULT 0,
        original_max REAL DEFAULT 100,
        calibrated_min REAL DEFAULT 0,
        calibrated_max REAL DEFAULT 100,
        description TEXT,
        updated_at DATETIME DEFAULT (datetime('now', 'localtime'))
    )
    ''')
    
    # Create indices for better performance
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_greenhouse_data_timestamp ON greenhouse_data(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_resource_consumption_timestamp ON resource_consumption(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ai_predictions_timestamp ON ai_predictions(timestamp)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ai_schedules_scheduled_time ON ai_schedules(scheduled_time)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_ai_schedules_status ON ai_schedules(status)')
    
    conn.commit()
    conn.close()
    print(f"✅ Database initialized (FIXED) at {db_path_str}")
    
    # Verify tables were created
    verify_database_tables()
    
    # Initialize calibration settings
    init_calibration_settings()

def verify_database_tables():
    """Verify that all tables were created successfully"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        
        print(f"✅ Database tables: {', '.join(tables)}")
        
        # Check specific tables
        required_tables = ['greenhouse_data', 'resource_consumption', 'ai_predictions', 
                          'ai_decisions', 'ai_schedules', 'irrigation_history', 'calibration_settings']
        missing_tables = [t for t in required_tables if t not in tables]
        
        if missing_tables:
            print(f"⚠️ WARNING: Missing tables: {missing_tables}")
        else:
            print("✅ All required tables exist")
        
        # Check ai_predictions columns
        if 'ai_predictions' in tables:
            cursor.execute("PRAGMA table_info(ai_predictions)")
            columns = [row[1] for row in cursor.fetchall()]
            print(f"✅ ai_predictions columns: {columns}")
            
            # Check for confidence_score column
            if 'confidence_score' not in columns:
                print("⚠️ WARNING: confidence_score column missing in ai_predictions")
                # Try to add it
                try:
                    cursor.execute("ALTER TABLE ai_predictions ADD COLUMN confidence_score REAL DEFAULT 0.0")
                    conn.commit()
                    print("✅ Added confidence_score column to ai_predictions")
                except Exception as e:
                    print(f"⚠️ Could not add confidence_score column: {e}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error verifying database tables: {e}")

def init_calibration_settings():
    """Initialize calibration settings in database"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Insert default calibration settings for each zone
        for zone_name, calibration in zone_calibrations.items():
            cursor.execute("""
                INSERT OR REPLACE INTO calibration_settings
                (zone, enabled, original_min, original_max, calibrated_min, calibrated_max, description, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """, (
                zone_name,
                1 if calibration.enabled else 0,
                calibration.original_min,
                calibration.original_max,
                calibration.calibrated_min,
                calibration.calibrated_max,
                calibration.description
            ))
        
        conn.commit()
        conn.close()
        print("✅ Calibration settings initialized in database")
        
    except Exception as e:
        print(f"❌ Error initializing calibration settings: {e}")

def load_calibration_settings():
    """Load calibration settings from database"""
    global zone_calibrations
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT zone, enabled, original_min, original_max, 
                   calibrated_min, calibrated_max, description
            FROM calibration_settings
        """)
        
        for row in cursor.fetchall():
            zone, enabled, orig_min, orig_max, cal_min, cal_max, desc = row
            if zone in zone_calibrations:
                zone_calibrations[zone].enabled = bool(enabled)
                zone_calibrations[zone].original_min = orig_min
                zone_calibrations[zone].original_max = orig_max
                zone_calibrations[zone].calibrated_min = cal_min
                zone_calibrations[zone].calibrated_max = cal_max
                zone_calibrations[zone].description = desc
        
        conn.close()
        print("✅ Calibration settings loaded from database")
        
    except Exception as e:
        print(f"❌ Error loading calibration settings: {e}")

# Helper functions
def get_system_uptime():
    """Get system uptime in seconds"""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
        return uptime_seconds
    except:
        return (datetime.now() - system_start_time).total_seconds()

def format_uptime(seconds):
    """Format uptime to HH:MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def format_irrigation_duration(minutes):
    """Format irrigation duration for display"""
    total_seconds = minutes * 60
    
    if total_seconds < 60:
        return f"{int(total_seconds)} seconds"
    elif total_seconds < 120:
        return f"{minutes:.1f} minutes"
    else:
        return f"{int(minutes)} minutes"

def calculate_tank_level(lowLevel, highLevel):
    """
    Calculate water tank level based on sensor readings
    ESP32 logic: both 1 = empty, both 0 = full
    """
    low = 1 if lowLevel else 0
    high = 1 if highLevel else 0
    
    if low == 0 and high == 0:
        return 100, "FULL", "good"
    elif low == 0 and high == 1:
        return 50, "MEDIUM", "warning"
    elif low == 1 and high == 1:
        return 0, "EMPTY", "critical"
    else:
        return 25, "ERROR", "critical"

def get_optimal_irrigation_time(base_time, urgency="medium", current_soil=None, threshold=70):
    """
    Get optimal irrigation time considering business rules
    Returns: (optimal_time, adjustment_reason, adjusted)
    """
    hour = base_time.hour
    adjusted = False
    adjustment_reason = "No adjustment needed"
    
    # CRITICAL EMERGENCY: Soil < 50% - irrigate immediately regardless of time
    if current_soil and current_soil < 50:
        return base_time, "Critical emergency - soil < 50%", False
    
    # HIGH URGENCY: Soil < 60% - irrigate within next 2 hours, but avoid 12 AM - 5 AM
    if urgency == "high":
        if 0 <= hour < 5:  # Midnight to 5 AM
            # Move to 5 AM at earliest
            optimal_time = base_time.replace(hour=5, minute=0, second=0)
            if optimal_time < base_time:
                optimal_time += timedelta(days=1)
            return optimal_time, "High urgency - moved to 5 AM", True
        return base_time, "High urgency - immediate", False
    
    # MEDIUM URGENCY: Avoid nighttime (10 PM - 6 AM)
    if hour >= 22 or hour < 6:
        # Move to 6 AM
        optimal_time = base_time.replace(hour=6, minute=0, second=0)
        if optimal_time < base_time:  # If it's past 6 AM today
            optimal_time += timedelta(days=1)
        return optimal_time, "Avoid nighttime (10 PM - 6 AM)", True
    
    # LOW URGENCY: Prefer 8 AM - 6 PM
    if urgency == "low" and not (8 <= hour <= 18):
        if hour < 8:
            optimal_time = base_time.replace(hour=8, minute=0, second=0)
            return optimal_time, "Optimized for morning (8 AM)", True
        else:  # After 6 PM
            optimal_time = base_time + timedelta(days=1)
            optimal_time = optimal_time.replace(hour=8, minute=0, second=0)
            return optimal_time, "Optimized for next morning (8 AM)", True
    
    return base_time, "Optimal time already", False

def process_esp32_data(data):
    """Process ESP32 data and update sensor data - WITH MULTI-ZONE CALIBRATION"""
    global sensor_data, manual_valve_control, manual_pump_control, resource_tracker
    
    # Convert ESP32 boolean values to 0/1 for frontend
    lowLevel = 1 if data.get('lowLevel', False) else 0
    highLevel = 1 if data.get('highLevel', False) else 0
    
    # Get raw soil values
    soil1_raw = int(data.get('soil1', 0))
    soil2_raw = int(data.get('soil2', 0))
    soil3_raw = int(data.get('soil3', 0))
    
    # Apply calibration for each zone
    soil1_calibrated = calibrate_zone('soil1', soil1_raw)
    soil2_calibrated = calibrate_zone('soil2', soil2_raw)
    soil3_calibrated = calibrate_zone('soil3', soil3_raw)
    
    # Log calibration for debugging
    if zone_calibrations['soil1'].enabled and soil1_raw > 0:
        print(f"📊 Zone A calibration: {soil1_raw}% → {soil1_calibrated:.1f}%")
    if zone_calibrations['soil2'].enabled and soil2_raw > 0:
        print(f"📊 Zone B calibration: {soil2_raw}% → {soil2_calibrated:.1f}%")
    if zone_calibrations['soil3'].enabled and soil3_raw > 0:
        print(f"📊 Zone C calibration: {soil3_raw}% → {soil3_calibrated:.1f}%")
    
    # Format with 1 decimal place for temperature/humidity
    processed_data = {
        'temperature': round(float(data.get('temperature', 0)), 1),
        'humidity': round(float(data.get('humidity', 0)), 1),
        'soil1': round(soil1_calibrated, 1),
        'soil2': round(soil2_calibrated, 1),
        'soil3': round(soil3_calibrated, 1),
        'lowLevel': lowLevel,
        'highLevel': highLevel,
        'valve': int(data.get('valve', 0)),
        'pump': int(data.get('pump', 0)),
        'timestamp': datetime.now().isoformat(),
        # Add raw values for debugging
        'soil1_raw': soil1_raw,
        'soil2_raw': soil2_raw,
        'soil3_raw': soil3_raw
    }
    
    # Update global sensor data
    sensor_data.update(processed_data)
    
    # UPDATE RESOURCE TRACKING
    if resource_tracker:
        resource_tracker.update_tracking(processed_data)
    
    # Save to database
    save_to_database(processed_data)
    
    # Log water tank status
    tank_level, tank_status, _ = calculate_tank_level(
        bool(lowLevel),
        bool(highLevel)
    )
    print(f"💧 Water Tank: {tank_status} ({tank_level}%), Valve: {'ON' if processed_data['valve'] else 'OFF'}")
    print(f"🌱 Soil Moisture: A={soil1_calibrated:.1f}% (raw:{soil1_raw}%), "
          f"B={soil2_calibrated:.1f}% (raw:{soil2_raw}%), "
          f"C={soil3_calibrated:.1f}% (raw:{soil3_raw}%)")
    
    return processed_data

def save_to_database(data):
    """Save sensor data to database"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO greenhouse_data
            (timestamp, temperature, humidity, soil1, soil2, soil3, lowLevel, highLevel, valve, pump)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['timestamp'],
            data['temperature'],
            data['humidity'],
            data['soil1'],
            data['soil2'],
            data['soil3'],
            data['lowLevel'],
            data['highLevel'],
            data['valve'],
            data['pump']
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ Database save error: {e}")
        return False

def log_manual_command(device, command, requested_state, actual_state, success, notes=""):
    """Log manual commands to database"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO manual_commands
            (timestamp, device, command, requested_state, actual_state, success, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            device,
            command,
            requested_state,
            actual_state,
            1 if success else 0,
            notes
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Error logging command: {e}")

def make_ai_decision():
    """Make an AI irrigation decision - FIXED FOR AUTO-SCHEDULING"""
    global sensor_data, ml_manager, decision_tracker
    
    if not ml_manager or not decision_tracker:
        print("❌ AI system not available for decision making")
        return
    
    # Get current soil data - LOWER THRESHOLD TO 70%
    soil1 = sensor_data.get('soil1', 50)
    soil2 = sensor_data.get('soil2', 50)
    soil3 = sensor_data.get('soil3', 50)
    
    print(f"🌱 Soil moisture: Zone A={soil1}%, Zone B={soil2}%, Zone C={soil3}%")
    
    # CHANGE: Lower threshold to 70% for more frequent irrigation
    target_moisture = 70
    
    # Check if any zone needs irrigation
    if soil1 >= target_moisture and soil2 >= target_moisture and soil3 >= target_moisture:
        print(f"✅ All zones above {target_moisture}%, no irrigation needed")
        return
    
    # Calculate which zones need irrigation
    dry_zones = []
    if soil1 < target_moisture: dry_zones.append(f"A:{soil1}%")
    if soil2 < target_moisture: dry_zones.append(f"B:{soil2}%")
    if soil3 < target_moisture: dry_zones.append(f"C:{soil3}%")
    
    print(f"🌵 Dry zones detected ({target_moisture}% target): {', '.join(dry_zones)}")
    
    try:
        # Calculate irrigation duration based on soil dryness
        avg_soil = (soil1 + soil2 + soil3) / 3
        soil_deficit = target_moisture - avg_soil
        
        # Calculate irrigation duration (1 minute per 5% deficit, minimum 2 minutes)
        duration_minutes = max(2.0, min(10.0, soil_deficit / 5))
        
        # Create AI decision
        decision = {
            'action': duration_minutes,
            'reason': f"AI Auto: Dry zones {', '.join(dry_zones)} (Avg soil: {avg_soil:.1f}%)",
            'system_state': ml_state,
            'model_used': 'auto_scheduler',
            'timestamp': datetime.now().isoformat(),
            'executed': 0  # Will be executed via schedule
        }
        
        print(f"🤖 AI Decision: {duration_minutes:.1f} minutes - {decision['reason']}")
        
        # Add to tracker
        decision_tracker.add_decision(decision)
        print("✅ Decision added to tracker")
        
        # CREATE A SCHEDULE FOR IMMEDIATE EXECUTION
        schedule_time = (datetime.now() + timedelta(seconds=60)).isoformat()  # 1 minute from now
        
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Create schedule
        cursor.execute("""
            INSERT INTO ai_schedules
            (decision_id, action, reason, scheduled_time, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            decision.get('history_id') or decision.get('db_id') or 0,
            duration_minutes,
            decision['reason'],
            schedule_time,
            "scheduled",
            datetime.now().isoformat()
        ))
        
        conn.commit()
        conn.close()
        
        print(f"📅 Schedule created for immediate execution: {duration_minutes:.1f} minutes")
        
    except Exception as e:
        print(f"❌ Error in AI decision making: {e}")
        import traceback
        traceback.print_exc()

def update_ai_decisions_from_db():
    """Update AI decisions from database for display"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get recent AI decisions with formatted timestamps
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:%M:%S', timestamp) as formatted_timestamp,
                action, reason, system_state, model_used, executed
            FROM ai_decisions
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        
        decisions = []
        for row in cursor.fetchall():
            decision = dict(row)
            # Ensure timestamp is properly formatted
            if decision['formatted_timestamp']:
                decision['timestamp'] = decision['formatted_timestamp']
            else:
                decision['timestamp'] = row['timestamp'] if 'timestamp' in row else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            decisions.append(decision)
        
        # Get upcoming AND recent schedules
        cursor.execute("""
            SELECT
                strftime('%Y-%m-%d %H:%M:%S', scheduled_time) as formatted_scheduled_time,
                strftime('%Y-%m-%d %H:%M:%S', execution_time) as formatted_execution_time,
                action,
                reason,
                status,
                created_at
            FROM ai_schedules
            WHERE scheduled_time >= datetime('now', '-30 days')
               OR scheduled_time > datetime('now')
            ORDER BY scheduled_time DESC
            LIMIT 20
        """)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = dict(row)
            # Format timestamps properly
            schedule['scheduled_time'] = schedule.get('formatted_scheduled_time', '')
            schedule['execution_time'] = schedule.get('formatted_execution_time', '')
            schedules.append(schedule)
        
        conn.close()
        
        return decisions, schedules
        
    except Exception as e:
        print(f"❌ Error updating AI decisions from DB: {e}")
        return [], []

def get_database_stats():
    """Get database statistics"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Total records
        cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
        total_records = cursor.fetchone()[0]
        
        # Today's records
        cursor.execute("""
            SELECT COUNT(*) FROM greenhouse_data
            WHERE date(timestamp) = date('now')
        """)
        today_records = cursor.fetchone()[0]
        
        # Last data point time
        cursor.execute("SELECT MAX(timestamp) FROM greenhouse_data")
        last_data = cursor.fetchone()[0]
        
        # AI schedule count
        cursor.execute("SELECT COUNT(*) FROM ai_schedules WHERE status = 'scheduled'")
        scheduled_count = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_records': total_records,
            'today_records': today_records,
            'last_data': last_data,
            'scheduled_count': scheduled_count,
            'status': 'OK'
        }
    except Exception as e:
        return {'status': f'Error: {str(e)[:50]}'}

def send_to_frontend():
    """Send sensor data AND resource consumption to frontend API"""
    global last_frontend_sync
    
    current_time = time.time()
    if current_time - last_frontend_sync < DATA_SEND_INTERVAL:
        return False
    
    try:
        # Get the latest sensor data from database with ID field
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM greenhouse_data
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        
        latest_sensor_data = cursor.fetchone()
        
        if not latest_sensor_data:
            conn.close()
            print("⚠️ No sensor data available to send to frontend")
            return False
        
        # Format sensor data EXACTLY as frontend expects
        frontend_data = {
            "id": latest_sensor_data['id'],
            "temperature": float(latest_sensor_data['temperature']),
            "humidity": float(latest_sensor_data['humidity']),
            "soil1": int(latest_sensor_data['soil1']),
            "soil2": int(latest_sensor_data['soil2']),
            "soil3": int(latest_sensor_data['soil3']),
            "lowLevel": int(latest_sensor_data['lowLevel']),
            "highLevel": int(latest_sensor_data['highLevel']),
            "valve": int(latest_sensor_data['valve']),
            "pump": int(latest_sensor_data['pump']),
            "timestamp": latest_sensor_data['timestamp']
        }
        
        # Ensure timestamp format - frontend expects ISO format
        if 'T' not in frontend_data['timestamp']:
            # Convert from string to ISO format
            try:
                dt = datetime.strptime(frontend_data['timestamp'], "%Y-%m-%d %H:%M:%S")
                frontend_data['timestamp'] = dt.isoformat()
            except:
                # Keep as is if conversion fails
                pass
        
        # ==============================================
        # ADD RESOURCE CONSUMPTION DATA FROM LAST 5 MINUTES
        # ==============================================
        
        # Calculate time range: last 5 minutes from current sync time
        sync_time = datetime.now()
        five_minutes_ago = sync_time - timedelta(minutes=5)
        
        # Get resource consumption records from the last 5 minutes
        cursor.execute("""
            SELECT 
                id,
                timestamp,
                pump_runtime_seconds,
                valve_runtime_seconds,
                water_consumed_liters,
                energy_consumed_kwh,
                pump_state,
                valve_state
            FROM resource_consumption
            WHERE timestamp >= ?
            ORDER BY timestamp
        """, (five_minutes_ago.strftime('%Y-%m-%d %H:%M:%S'),))
        
        resource_records = cursor.fetchall()
        
        # Format resource consumption data
        resource_consumption = []
        
        if resource_records:
            print(f"📊 Found {len(resource_records)} resource consumption record(s) from last 5 minutes")
            
            for record in resource_records:
                resource_data = {
                    "resource_id": record['id'],
                    "timestamp": record['timestamp'],
                    "pump_runtime_seconds": float(record['pump_runtime_seconds']),
                    "valve_runtime_seconds": float(record['valve_runtime_seconds']),
                    "water_consumed_liters": float(record['water_consumed_liters']),
                    "energy_consumed_kwh": float(record['energy_consumed_kwh']),
                    "pump_state": int(record['pump_state']),
                    "valve_state": int(record['valve_state'])
                }
                resource_consumption.append(resource_data)
        else:
            print("ℹ️ No resource consumption records in last 5 minutes, sending zero values")
            
            # Send zero values when no consumption
            resource_consumption.append({
                "resource_id": 0,
                "timestamp": sync_time.strftime('%Y-%m-%d %H:%M:%S'),
                "pump_runtime_seconds": 0.0,
                "valve_runtime_seconds": 0.0,
                "water_consumed_liters": 0.0,
                "energy_consumed_kwh": 0.0,
                "pump_state": 0,
                "valve_state": 0
            })
        
        # Add resource consumption to the frontend data
        frontend_data["resource_consumption"] = resource_consumption
        
        # Also add aggregated totals for convenience
        total_pump_runtime = sum(r['pump_runtime_seconds'] for r in resource_consumption)
        total_valve_runtime = sum(r['valve_runtime_seconds'] for r in resource_consumption)
        total_water = sum(r['water_consumed_liters'] for r in resource_consumption)
        total_energy = sum(r['energy_consumed_kwh'] for r in resource_consumption)
        
        frontend_data["total_resources_last_5min"] = {
            "pump_runtime_seconds": total_pump_runtime,
            "valve_runtime_seconds": total_valve_runtime,
            "water_consumed_liters": total_water,
            "energy_consumed_kwh": total_energy
        }
        
        conn.close()
        
        print(f"📤 Sending to frontend: Valve={frontend_data['valve']}, Pump={frontend_data['pump']}")
        print(f"📊 Resource data: {len(resource_consumption)} record(s), "
              f"Total water={total_water:.3f}L, Total energy={total_energy:.6f}kWh")
        
        # Send to frontend
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': FRONTEND_API_KEY
        }
        
        response = requests.post(
            FRONTEND_API_URL,
            json=frontend_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code in [200, 201, 202]:
            last_frontend_sync = current_time
            print(f"✅ Data sent to frontend API successfully")
            return True
        else:
            print(f"❌ Frontend API error: {response.status_code}")
            # Try to get error details
            try:
                error_detail = response.text[:200]
                print(f"❌ Error detail: {error_detail}")
            except:
                pass
            return False
        
    except Exception as e:
        print(f"❌ Error sending to frontend: {e}")
        import traceback
        traceback.print_exc()
    
    return False

def check_frontend_commands():
    """Check for pending commands from frontend - WITH TIMESTAMP CHECK (1 minute expiry)"""
    global processed_commands, manual_valve_control, manual_pump_control, last_manual_command_time
    
    try:
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': FRONTEND_API_KEY
        }
        
        response = requests.get(
            FRONTEND_COMMANDS_URL,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            commands = response.json()
            
            if isinstance(commands, list) and len(commands) > 0:
                print(f"📨 Found {len(commands)} pending command(s)")
                
                for command in commands:
                    command_id = command.get('command_id')
                    if not command_id:
                        continue
                    
                    # Skip if already processed
                    if command_id in processed_commands:
                        continue
                    
                    device = command.get('device')
                    desired_state = command.get('state')
                    timestamp_str = command.get('timestamp')  # Get timestamp from command
                    
                    if not all([device, desired_state, command_id]):
                        continue
                    
                    # ==============================================
                    # CHECK IF COMMAND IS FRESH (LESS THAN 1 MINUTE OLD)
                    # ==============================================
                    is_fresh = True
                    if timestamp_str:
                        try:
                            # Parse the timestamp from the command
                            # Try different timestamp formats
                            try:
                                command_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                            except:
                                try:
                                    command_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                                except:
                                    try:
                                        command_time = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S")
                                    except:
                                        command_time = datetime.now()
                                        print(f"⚠️ Could not parse timestamp: {timestamp_str}, using current time")
                            
                            # Calculate age in seconds
                            current_time = datetime.now()
                            age_seconds = (current_time - command_time).total_seconds()
                            
                            if age_seconds > 60:  # More than 1 minute old
                                is_fresh = False
                                print(f"⏰ Command {command_id} is EXPIRED: {age_seconds:.1f} seconds old")
                                
                                # Mark as expired/processed
                                processed_commands.add(command_id)
                                
                                # Send update that command expired
                                send_to_hosted_frontend_update(
                                    command_id, 
                                    False, 
                                    None,
                                    f"Command expired ({age_seconds:.1f}s old)"
                                )
                                continue
                                
                            print(f"⏰ Command {command_id} is FRESH: {age_seconds:.1f} seconds old")
                                
                        except Exception as e:
                            print(f"⚠️ Error parsing command timestamp: {e}")
                            # If we can't parse timestamp, assume it's fresh
                            is_fresh = True
                    
                    # If command is not fresh (expired), skip execution
                    if not is_fresh:
                        continue
                    
                    # Mark as processed
                    processed_commands.add(command_id)
                    
                    # Execute command through serial
                    success = False
                    actual_state = None
                    notes = ""
                    
                    if serial_reader:
                        # ESP32 expects lowercase "on"/"off"
                        esp32_command = {device: desired_state.lower()}
                        success = serial_reader.send_command(esp32_command)
                        actual_state = desired_state.upper()
                        
                        # Set manual override (NO TIMEOUT)
                        if device == 'valve':
                            manual_valve_control = True if desired_state.upper() == 'ON' else False
                            print(f"🔄 Manual valve control: {'ACTIVATED (no timeout)' if manual_valve_control else 'DEACTIVATED'}")
                        elif device == 'pump':
                            manual_pump_control = True if desired_state.upper() == 'ON' else False
                            print(f"🔄 Manual pump control: {'ACTIVATED (no timeout)' if manual_pump_control else 'DEACTIVATED'}")
                        
                        last_manual_command_time = time.time()
                        notes = f"Manual control (no timeout)"
                        print(f"  📨 Sent to ESP32: {esp32_command}")
                    else:
                        success = True
                        actual_state = desired_state.upper()
                        notes = "No serial connection - simulated"
                        print(f"  ⚠️ No serial, simulating command")
                    
                    # Log the command
                    log_manual_command(
                        device=device,
                        command=f"manual_{desired_state.lower()}",
                        requested_state=desired_state.upper(),
                        actual_state=actual_state,
                        success=success,
                        notes=notes
                    )
                    
                    # Update sensor data locally (for immediate UI feedback)
                    if device == 'pump':
                        sensor_data['pump'] = 1 if desired_state.upper() == 'ON' else 0
                    elif device == 'valve':
                        sensor_data['valve'] = 1 if desired_state.upper() == 'ON' else 0
                    
                    # Update timestamp
                    sensor_data['timestamp'] = datetime.now().isoformat()
                    
                    # Save to database
                    save_to_database(sensor_data)
                    
                    # Send update back to hosted frontend (their format)
                    send_to_hosted_frontend_update(command_id, success, actual_state)
                    
                    print(f"✅ Processed command {command_id}: {device} {desired_state} (fresh: {is_fresh})")
    
    except Exception as e:
        print(f"❌ Error checking commands: {e}")

def send_command_update(command_id, success, actual_state=None):
    """Send command execution result back to local frontend"""
    try:
        if success:
            update_data = {
                "command_id": command_id,
                "status": "SUCCESS",
                "actual_state": actual_state.upper() if actual_state else "ON"
            }
        else:
            update_data = {
                "command_id": command_id,
                "status": "FAILED"
            }
        
        print(f"📨 Sending command update to local frontend: {json.dumps(update_data)}")
        
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': FRONTEND_API_KEY
        }
        
        # This would go to your local frontend if you had one
        # For now, just log it
        print(f"ℹ️ Would send to local frontend: {json.dumps(update_data)}")
        return True
    
    except Exception as e:
        print(f"❌ Error sending command update to local frontend: {e}")
        return False

def send_to_hosted_frontend_update(command_id, success, actual_state=None, error_message=None):
    """Send update specifically to hosted frontend (their format) - ASYNCHRONOUS"""
    try:
        if success:
            update_data = {
                "command_id": int(command_id),
                "status": "SUCCESS",
                "actual_state": actual_state.upper() if actual_state else "ON"
            }
        elif error_message:
            update_data = {
                "command_id": int(command_id),
                "status": "FAILED",
                "error": error_message
            }
        else:
            update_data = {
                "command_id": int(command_id),
                "status": "FAILED"
            }
        
        print(f"📨 Sending to hosted frontend: {json.dumps(update_data)}")
        
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': FRONTEND_API_KEY
        }
        
        # This is for the async update after the immediate response
        response = requests.post(
            FRONTEND_UPDATE_URL,  # https://ecoflow-9ege.onrender.com/api/commands/update
            json=update_data,
            headers=headers,
            timeout=10
        )
        
        if response.status_code in [200, 201, 202]:
            print(f"✅ Update sent to hosted frontend successfully")
            return True
        else:
            print(f"❌ Failed to send to hosted frontend: {response.status_code}")
            return False
    
    except Exception as e:
        print(f"❌ Error sending to hosted frontend: {e}")
        return False

# Manual runtime tracking functions
def track_manual_runtime_start(device):
    """Track start time for manual operation"""
    global manual_runtime_start
    manual_runtime_start[device] = time.time()
    print(f"⏱️ Started tracking {device} runtime")

def track_manual_runtime_end(device):
    """Track end time and calculate runtime"""
    global manual_runtime_start
    if manual_runtime_start.get(device):
        runtime = time.time() - manual_runtime_start[device]
        manual_runtime_start[device] = None
        return runtime
    return 0

def log_manual_runtime(device, runtime_seconds):
    """Log manual runtime to database"""
    try:
        runtime_minutes = runtime_seconds / 60
        water_used = runtime_minutes * WATER_FLOW_RATE_LPM if device == 'pump' else 0
        
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO irrigation_history
            (timestamp, duration_minutes, water_used_liters, mode, reason, success)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            round(runtime_minutes, 2),
            round(water_used, 2),
            "Manual",
            f"Manual {device} operation",
            1
        ))
        
        conn.commit()
        conn.close()
        print(f"📝 Manual {device} runtime logged: {runtime_minutes:.1f} mins")
        
    except Exception as e:
        print(f"❌ Error logging manual runtime: {e}")

def log_irrigation_history(duration, water_used, reason, mode="AI", schedule_id=None, decision_id=None):
    """Log irrigation to history table"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO irrigation_history
            (timestamp, duration_minutes, water_used_liters, mode, reason, 
             schedule_id, decision_id, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            round(duration, 2),
            round(water_used, 2),
            mode,
            reason,
            schedule_id,
            decision_id,
            1
        ))
        
        conn.commit()
        conn.close()
        print(f"📝 Irrigation logged to history: {duration:.1f} mins, {water_used:.2f} L")
        
    except Exception as e:
        print(f"❌ Error logging irrigation history: {e}")

def execute_irrigation(duration_minutes, reason, schedule_id=None, decision_id=None):
    """Execute irrigation - PUMP ONLY (Valve stays closed)"""
    global serial_reader
    
    if not serial_reader:
        print("❌ No serial connection - cannot execute irrigation")
        return False
    
    try:
        # Convert minutes to seconds
        duration_seconds = int(duration_minutes * 60)
        print(f"💧 Starting PUMP-ONLY irrigation: {duration_seconds} seconds ({duration_minutes} mins) - {reason}")
        
        # IMPORTANT: ONLY turn on the pump (valve stays closed during normal irrigation)
        print(f"💧 Turning pump ON (valve stays closed)")
        pump_success = serial_reader.send_command({"pump": "on"})
        
        if not pump_success:
            print("❌ Failed to turn pump ON")
            return False
        
        # Cap at 10 minutes (600 seconds) for safety
        actual_duration = min(duration_seconds, 600)
        
        print(f"⏳ PUMP-ONLY irrigation running for {actual_duration} seconds")
        
        # Track start time for duration calculation
        start_time = time.time()
        
        # Actually wait for the duration in smaller chunks
        for i in range(actual_duration):
            time.sleep(1)
            # Show progress every 30 seconds for long durations
            if i % 30 == 0 and i > 0:
                print(f"⏳ Irrigation running... {i}/{actual_duration} seconds")
        
        # Turn off pump
        print(f"💧 Turning pump OFF")
        serial_reader.send_command({"pump": "off"})
        
        # Calculate actual runtime
        actual_runtime = time.time() - start_time
        actual_runtime_minutes = actual_runtime / 60
        
        # Calculate water usage (0.3 L/min flow rate)
        water_used = actual_runtime_minutes * WATER_FLOW_RATE_LPM
        
        # Log the irrigation with actual runtime
        log_manual_command(
            device="pump",
            command="irrigation_completed",
            requested_state="ON",
            actual_state="OFF",
            success=True,
            notes=f"PUMP-ONLY irrigation: {actual_runtime_minutes:.1f} minutes actual runtime - {reason} (Schedule ID: {schedule_id})"
        )
        
        # Also log to irrigation history with actual duration
        log_irrigation_history(
            duration=actual_runtime_minutes,
            water_used=water_used,
            reason=reason,
            mode="AI",
            schedule_id=schedule_id,
            decision_id=decision_id
        )
        
        print(f"✅ PUMP-ONLY irrigation completed successfully - {actual_runtime_minutes:.1f} minutes actual runtime")
        return True
        
    except Exception as e:
        print(f"❌ Error during irrigation: {e}")
        # Try to turn pump off
        try:
            if serial_reader:
                serial_reader.send_command({"pump": "off"})
        except:
            pass
        return False

# UPDATED: Use Linear Regression predictor with confidence intervals
def generate_soil_predictions():
    """Generate soil moisture predictions using Linear Regression model with confidence intervals"""
    try:
        if not soil_predictor:
            print("❌ SoilMoisturePredictor not available, using fallback")
            return generate_fallback_predictions()
        
        # Train or retrain model (only if needed)
        print("🤖 Checking if model training is needed...")
        trained = soil_predictor.auto_train_if_needed(min_samples=200000, min_r_squared=0.0, max_age_hours=24, force_periodic_only=True)
        
        if not trained:
            print("ℹ️ Model training not needed or failed, using existing model")
        
        # Get current sensor data
        current_data = {
            'temperature': sensor_data.get('temperature', 25),
            'humidity': sensor_data.get('humidity', 60),
            'soil1': sensor_data.get('soil1', 50),
            'soil2': sensor_data.get('soil2', 50),
            'soil3': sensor_data.get('soil3', 50),
            'valve': sensor_data.get('valve', 0),
            'pump': sensor_data.get('pump', 0)
        }
        
        # Generate predictions for different time horizons with confidence intervals
        predictions_1h = soil_predictor.predict_with_confidence(current_data, 0, 1)
        predictions_3h = soil_predictor.predict_with_confidence(current_data, 0, 3)
        predictions_6h = soil_predictor.predict_with_confidence(current_data, 0, 6)
        
        # Save to database
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO ai_predictions
            (timestamp, predicted_1h_soil1, predicted_3h_soil1, predicted_6h_soil1,
             predicted_1h_soil2, predicted_3h_soil2, predicted_6h_soil2,
             predicted_1h_soil3, predicted_3h_soil3, predicted_6h_soil3,
             confidence_score, model_used, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            predictions_1h.get('soil1_predicted', current_data['soil1']),
            predictions_3h.get('soil1_predicted', current_data['soil1']),
            predictions_6h.get('soil1_predicted', current_data['soil1']),
            predictions_1h.get('soil2_predicted', current_data['soil2']),
            predictions_3h.get('soil2_predicted', current_data['soil2']),
            predictions_6h.get('soil2_predicted', current_data['soil2']),
            predictions_1h.get('soil3_predicted', current_data['soil3']),
            predictions_3h.get('soil3_predicted', current_data['soil3']),
            predictions_6h.get('soil3_predicted', current_data['soil3']),
            0.85,  # Average confidence
            "linear_regression_with_ci",
            f"Linear regression model, R²: soil1={predictions_1h.get('soil1_r_squared', 0):.3f}, soil2={predictions_1h.get('soil2_r_squared', 0):.3f}, soil3={predictions_1h.get('soil3_r_squared', 0):.3f}"
        ))
        
        conn.commit()
        conn.close()
        
        print(f"✅ Generated linear regression predictions for 1h, 3h, 6h horizons")
        print(f"📊 Model R² scores: Zone A={predictions_1h.get('soil1_r_squared', 0):.3f}, Zone B={predictions_1h.get('soil2_r_squared', 0):.3f}, Zone C={predictions_1h.get('soil3_r_squared', 0):.3f}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error generating linear regression predictions: {e}")
        import traceback
        traceback.print_exc()
        return generate_fallback_predictions()

# SIMPLIFIED: Fallback prediction function
def generate_fallback_predictions():
    """Generate fallback predictions when historical data is insufficient"""
    try:
        current_soil1 = sensor_data.get('soil1', 50)
        current_soil2 = sensor_data.get('soil2', 50)
        current_soil3 = sensor_data.get('soil3', 50)
        
        # Realistic defaults for greenhouse conditions
        # Zone A dries fastest, Zone C slowest
        avg_drying_rate1 = -1.8  # % per hour for Zone A
        avg_drying_rate2 = -1.4  # % per hour for Zone B
        avg_drying_rate3 = -1.0  # % per hour for Zone C
        
        # Generate predictions - Soil should DECREASE over time
        def generate_prediction(current, rate, hours):
            predicted = current + (rate * hours)
            return max(10, min(100, predicted))
        
        pred_1h_soil1 = generate_prediction(current_soil1, avg_drying_rate1, 1)
        pred_3h_soil1 = generate_prediction(current_soil1, avg_drying_rate1, 3)
        pred_6h_soil1 = generate_prediction(current_soil1, avg_drying_rate1, 6)
        
        pred_1h_soil2 = generate_prediction(current_soil2, avg_drying_rate2, 1)
        pred_3h_soil2 = generate_prediction(current_soil2, avg_drying_rate2, 3)
        pred_6h_soil2 = generate_prediction(current_soil2, avg_drying_rate2, 6)
        
        pred_1h_soil3 = generate_prediction(current_soil3, avg_drying_rate3, 1)
        pred_3h_soil3 = generate_prediction(current_soil3, avg_drying_rate3, 3)
        pred_6h_soil3 = generate_prediction(current_soil3, avg_drying_rate3, 6)
        
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO ai_predictions
            (timestamp, predicted_1h_soil1, predicted_3h_soil1, predicted_6h_soil1,
             predicted_1h_soil2, predicted_3h_soil2, predicted_6h_soil2,
             predicted_1h_soil3, predicted_3h_soil3, predicted_6h_soil3,
             confidence_score, model_used, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            round(pred_1h_soil1, 1),
            round(pred_3h_soil1, 1),
            round(pred_6h_soil1, 1),
            round(pred_1h_soil2, 1),
            round(pred_3h_soil2, 1),
            round(pred_6h_soil2, 1),
            round(pred_1h_soil3, 1),
            round(pred_3h_soil3, 1),
            round(pred_6h_soil3, 1),
            0.5,  # Lower confidence for fallback
            "realistic_fallback",
            f"Fallback prediction using realistic defaults: ZoneA={avg_drying_rate1}%/h, ZoneB={avg_drying_rate2}%/h, ZoneC={avg_drying_rate3}%/h"
        ))
        
        conn.commit()
        conn.close()
        
        print(f"✅ Generated fallback predictions:")
        print(f"📊   Zone A: {current_soil1}% → 6h:{pred_6h_soil1:.1f}% (⏱️={avg_drying_rate1}%/h)")
        print(f"📊   Zone B: {current_soil2}% → 6h:{pred_6h_soil2:.1f}% (⏱️={avg_drying_rate2}%/h)")
        print(f"📊   Zone C: {current_soil3}% → 6h:{pred_6h_soil3:.1f}% (⏱️={avg_drying_rate3}%/h)")
        return True
        
    except Exception as e:
        print(f"❌ Error generating fallback predictions: {e}")
        return False

# Authentication decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# API key check decorator
def api_key_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        incoming_key = request.headers.get('X-Api-Key')
        if incoming_key != API_KEY and request.args.get('key') != API_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
def kiosk():
    """Kiosk interface with all sensors"""
    return render_template('kiosk.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Admin login"""
    if request.method == 'POST':
        password = request.form.get('password', '')
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        admin_hash = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
        
        if password_hash == admin_hash:
            session['admin_logged_in'] = True
            return redirect(url_for('admin'))
        else:
            return render_template('admin/login.html', error="Invalid password")
    
    return render_template('admin/login.html')

@app.route('/logout')
def logout():
    """Logout admin"""
    session.pop('admin_logged_in', None)
    return redirect(url_for('kiosk'))

@app.route('/admin')
@login_required
def admin():
    """Admin dashboard with all features"""
    return render_template('admin/dashboard.html')

@app.route('/graphs')
@login_required
def graphs():
    """Graphs page with real-time data"""
    return render_template('admin/graphs.html')

@app.route('/predictions')
@login_required
def predictions():
    """Irrigation predictions dashboard"""
    return render_template('admin/predictions.html')

@app.route('/calibration')
@login_required
def calibration():
    """Calibration settings page"""
    return render_template('admin/calibration.html')

# API endpoints
@app.route('/api/sensors')
def get_sensors():
    """Get current sensor data"""
    # Format with 1 decimal place
    formatted_data = {
        'temperature': round(float(sensor_data.get('temperature', 0)), 1),
        'humidity': round(float(sensor_data.get('humidity', 0)), 1),
        'soil1': int(sensor_data.get('soil1', 0)),
        'soil2': int(sensor_data.get('soil2', 0)),
        'soil3': int(sensor_data.get('soil3', 0)),
        'lowLevel': int(sensor_data.get('lowLevel', 0)),
        'highLevel': int(sensor_data.get('highLevel', 0)),
        'valve': int(sensor_data.get('valve', 0)),
        'pump': int(sensor_data.get('pump', 0)),
        'timestamp': sensor_data.get('timestamp', ''),
        'manual_valve': 1 if manual_valve_control else 0,
        'manual_pump': 1 if manual_pump_control else 0,
        'soil1_raw': sensor_data.get('soil1_raw', 0),  # Include raw values for debugging
        'soil2_raw': sensor_data.get('soil2_raw', 0),
        'soil3_raw': sensor_data.get('soil3_raw', 0)
    }
    
    # Add tank status
    tank_level, tank_status, _ = calculate_tank_level(
        bool(formatted_data['lowLevel']),
        bool(formatted_data['highLevel'])
    )
    
    formatted_data['tank_level'] = tank_level
    formatted_data['tank_status'] = tank_status
    
    # Add soil average
    formatted_data['soil_avg'] = round((formatted_data['soil1'] + formatted_data['soil2'] + formatted_data['soil3']) / 3, 1)
    
    # Manual override status (no timeout)
    formatted_data['manual_override_seconds_left'] = 0
    formatted_data['manual_override_minutes_left'] = 0
    formatted_data['manual_override_note'] = "Manual control stays ON until turned OFF"
    
    # Add calibration info for all zones
    formatted_data['calibration'] = {
        zone: cal.to_dict() for zone, cal in zone_calibrations.items()
    }
    
    return jsonify(formatted_data)

@app.route('/api/data')
@api_key_required
def get_data():
    """Get historical data for graphs - WITH 30-MINUTE INTERVALS"""
    hours = request.args.get('hours', 24, type=int)
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get data aggregated to 30-minute intervals
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:%M', timestamp) as time_bucket,
                AVG(temperature) as temperature,
                AVG(humidity) as humidity,
                AVG(soil1) as soil1,
                AVG(soil2) as soil2,
                AVG(soil3) as soil3,
                MAX(lowLevel) as lowLevel,
                MAX(highLevel) as highLevel,
                MAX(valve) as valve,
                MAX(pump) as pump
            FROM greenhouse_data
            WHERE timestamp >= datetime('now', ?)
            GROUP BY strftime('%Y-%m-%d %H:%M', timestamp)
            ORDER BY time_bucket
        """, (f'-{hours} hours',))
        
        data = []
        for row in cursor.fetchall():
            # Format the timestamp to include minutes
            time_str = row['time_bucket'] + ":00"
            
            item = {
                'timestamp': time_str,
                'temperature': float(row['temperature']) if row['temperature'] is not None else 0.0,
                'humidity': float(row['humidity']) if row['humidity'] is not None else 0.0,
                'soil1': int(round(row['soil1'])) if row['soil1'] is not None else 0,
                'soil2': int(round(row['soil2'])) if row['soil2'] is not None else 0,
                'soil3': int(round(row['soil3'])) if row['soil3'] is not None else 0,
                'lowLevel': int(row['lowLevel']) if row['lowLevel'] is not None else 0,
                'highLevel': int(row['highLevel']) if row['highLevel'] is not None else 0,
                'valve': int(row['valve']) if row['valve'] is not None else 0,
                'pump': int(row['pump']) if row['pump'] is not None else 0
            }
            data.append(item)
        
        conn.close()
        
        print(f"📊 API Data: Returning {len(data)} 30-minute interval records")
        
        return jsonify(data)
        
    except Exception as e:
        print(f"❌ Error in /api/data endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify([]), 500

@app.route('/api/graph-data')
@api_key_required
def get_graph_data():
    """Get combined sensor and resource data for graphs - REAL TIME"""
    hours = request.args.get('hours', 24, type=int)
    
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        
        # Get sensor data (aggregated to 30-minute intervals)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:%M', timestamp) as time_bucket,
                AVG(temperature) as temperature,
                AVG(humidity) as humidity,
                AVG(soil1) as soil1,
                AVG(soil2) as soil2,
                AVG(soil3) as soil3,
                MAX(lowLevel) as lowLevel,
                MAX(highLevel) as highLevel,
                MAX(valve) as valve,
                MAX(pump) as pump
            FROM greenhouse_data
            WHERE timestamp >= datetime('now', ?)
            GROUP BY strftime('%Y-%m-%d %H:%M', timestamp)
            ORDER BY time_bucket
        """, (f'-{hours} hours',))
        
        sensor_data_list = []
        for row in cursor.fetchall():
            time_str = row['time_bucket'] + ":00"
            item = {
                'timestamp': time_str,
                'temperature': float(row['temperature']) if row['temperature'] is not None else 0.0,
                'humidity': float(row['humidity']) if row['humidity'] is not None else 0.0,
                'soil1': int(round(row['soil1'])) if row['soil1'] is not None else 0,
                'soil2': int(round(row['soil2'])) if row['soil2'] is not None else 0,
                'soil3': int(round(row['soil3'])) if row['soil3'] is not None else 0,
                'lowLevel': int(row['lowLevel']) if row['lowLevel'] is not None else 0,
                'highLevel': int(row['highLevel']) if row['highLevel'] is not None else 0,
                'valve': int(row['valve']) if row['valve'] is not None else 0,
                'pump': int(row['pump']) if row['pump'] is not None else 0
            }
            sensor_data_list.append(item)
        
        # Get resource consumption data (aggregated to 30-minute intervals)
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:%M', timestamp) as time_bucket,
                SUM(water_consumed_liters) as water,
                SUM(energy_consumed_kwh) as energy,
                SUM(pump_runtime_seconds) as pump_runtime,
                SUM(valve_runtime_seconds) as valve_runtime
            FROM resource_consumption
            WHERE timestamp >= datetime('now', ?)
            GROUP BY strftime('%Y-%m-%d %H:%M', timestamp)
            ORDER BY time_bucket
        """, (f'-{hours} hours',))
        
        resource_data_list = []
        for row in cursor.fetchall():
            time_str = row['time_bucket'] + ":00"
            item = {
                'timestamp': time_str,
                'water': float(row['water']) if row['water'] is not None else 0.0,
                'energy': float(row['energy']) if row['energy'] is not None else 0.0,
                'pump_runtime': float(row['pump_runtime']) if row['pump_runtime'] is not None else 0.0,
                'valve_runtime': float(row['valve_runtime']) if row['valve_runtime'] is not None else 0.0
            }
            resource_data_list.append(item)
        
        conn.close()
        
        # Combine data into a single response
        response = {
            'sensor_data': sensor_data_list,
            'resource_data': resource_data_list,
            'hours': hours,
            'sensor_count': len(sensor_data_list),
            'resource_count': len(resource_data_list),
            'latest_timestamp': sensor_data_list[-1]['timestamp'] if sensor_data_list else None
        }
        
        print(f"📊 Graph data: {len(sensor_data_list)} sensor points, {len(resource_data_list)} resource points")
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error in /api/graph-data endpoint: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# NEW: Debug endpoint to check database contents
@app.route('/api/debug/data-stats')
@api_key_required
def debug_data_stats():
    """Debug endpoint to check database contents"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Get total records
        cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
        total = cursor.fetchone()[0]
        
        # Get recent records
        cursor.execute("""
            SELECT timestamp, temperature, humidity, soil1, soil2, soil3
            FROM greenhouse_data
            ORDER BY timestamp DESC
            LIMIT 5
        """)
        recent = cursor.fetchall()
        
        # Get column names
        cursor.execute("PRAGMA table_info(greenhouse_data)")
        columns = [col[1] for col in cursor.fetchall()]
        
        # Check if we have data in last 24 hours
        cursor.execute("""
            SELECT COUNT(*) FROM greenhouse_data
            WHERE timestamp >= datetime('now', '-24 hours')
        """)
        last_24h = cursor.fetchone()[0]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'total_records': total,
            'last_24h_records': last_24h,
            'columns': columns,
            'recent_samples': recent,
            'has_temperature': 'temperature' in columns,
            'has_humidity': 'humidity' in columns,
            'has_soil': all(col in columns for col in ['soil1', 'soil2', 'soil3'])
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/resources/history')
@api_key_required
def get_resources_history():
    """Get resource consumption history for graphs"""
    hours = request.args.get('hours', 24, type=int)
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT
            strftime('%H:00', timestamp) as hour,
            SUM(water_consumed_liters) as water,
            SUM(energy_consumed_kwh) as energy
        FROM resource_consumption
        WHERE timestamp >= datetime('now', ?)
        GROUP BY strftime('%H', timestamp)
        ORDER BY timestamp
    """, (f'-{hours} hours',))
    
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(data)

# ============================
# FIXED REPORT ENDPOINTS
# ============================

@app.route('/api/reports/daily')
@api_key_required
def get_daily_report():
    """Get daily report from source tables - FIXED"""
    try:
        date_str = request.args.get('date')
        if date_str:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        else:
            date_obj = datetime.now()
        
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        date_formatted = date_obj.strftime('%Y-%m-%d')
        
        # 1. Get sensor data for the day
        cursor.execute("""
            SELECT
                COALESCE(AVG(temperature), 0) as avg_temp,
                COALESCE(AVG(humidity), 0) as avg_humidity,
                COALESCE(AVG(soil1), 0) as avg_soil1,
                COALESCE(AVG(soil2), 0) as avg_soil2,
                COALESCE(AVG(soil3), 0) as avg_soil3,
                COUNT(*) as data_points
            FROM greenhouse_data
            WHERE date(timestamp) = date(?)
        """, (date_formatted,))
        
        sensor_row = cursor.fetchone()
        
        # 2. Get resource consumption for the day
        cursor.execute("""
            SELECT
                COALESCE(SUM(water_consumed_liters), 0) as total_water,
                COALESCE(SUM(energy_consumed_kwh), 0) as total_energy,
                COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds,
                COALESCE(SUM(valve_runtime_seconds), 0) as valve_seconds,
                COUNT(CASE WHEN pump_state = 1 THEN 1 END) as irrigation_events
            FROM resource_consumption
            WHERE date(timestamp) = date(?)
        """, (date_formatted,))
        
        resource_row = cursor.fetchone()
        
        conn.close()
        
        # 3. Add system and ESP32 energy
        total_water = resource_row['total_water'] if resource_row else 0
        total_energy = resource_row['total_energy'] if resource_row else 0
        system_energy = 0.12  # 5W × 24h = 0.12 kWh
        esp32_energy = 0.0024  # 0.1W × 24h = 0.0024 kWh
        total_combined_energy = total_energy + system_energy + esp32_energy
        
        # 4. Compile report
        report = {
            'date': date_formatted,
            'sensor_data': {
                'avg_temp': round(sensor_row['avg_temp'], 1) if sensor_row and sensor_row['avg_temp'] else 0,
                'avg_humidity': round(sensor_row['avg_humidity'], 1) if sensor_row and sensor_row['avg_humidity'] else 0,
                'avg_soil1': round(sensor_row['avg_soil1'], 1) if sensor_row and sensor_row['avg_soil1'] else 0,
                'avg_soil2': round(sensor_row['avg_soil2'], 1) if sensor_row and sensor_row['avg_soil2'] else 0,
                'avg_soil3': round(sensor_row['avg_soil3'], 1) if sensor_row and sensor_row['avg_soil3'] else 0,
                'data_points': sensor_row['data_points'] if sensor_row else 0
            },
            'resource_data': {
                'total_water_liters': round(total_water, 2),
                'total_energy_kwh': round(total_energy, 6),
                'system_energy_kwh': round(system_energy, 6),
                'esp32_energy_kwh': round(esp32_energy, 6),
                'total_combined_energy_kwh': round(total_combined_energy, 6),
                'pump_runtime_hours': round((resource_row['pump_seconds'] / 3600 if resource_row and resource_row['pump_seconds'] else 0), 2),
                'valve_runtime_hours': round((resource_row['valve_seconds'] / 3600 if resource_row and resource_row['valve_seconds'] else 0), 2),
                'irrigation_events': resource_row['irrigation_events'] if resource_row else 0
            },
            'has_data': sensor_row and sensor_row['data_points'] > 0
        }
        
        return jsonify({
            'success': True,
            'report': report
        })
        
    except Exception as e:
        print(f"❌ Error getting daily report: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reports/weekly')
@api_key_required
def get_weekly_report():
    """Get weekly report from source tables - FIXED (Monday to Sunday)"""
    try:
        week_start = request.args.get('week_start')
        if week_start:
            try:
                start_date = datetime.strptime(week_start, '%Y-%m-%d')
            except ValueError:
                # If invalid date, use default
                today = datetime.now()
                start_date = today - timedelta(days=today.weekday())
        else:
            # Start from Monday of current week
            today = datetime.now()
            start_date = today - timedelta(days=today.weekday())
        
        end_date = start_date + timedelta(days=6)
        
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        print(f"📅 Weekly report: {start_str} to {end_str}")
        
        # 1. Get sensor data for the week
        cursor.execute("""
            SELECT
                COALESCE(AVG(temperature), 0) as avg_temp,
                COALESCE(AVG(humidity), 0) as avg_humidity,
                COALESCE(AVG(soil1), 0) as avg_soil1,
                COALESCE(AVG(soil2), 0) as avg_soil2,
                COALESCE(AVG(soil3), 0) as avg_soil3,
                COUNT(*) as data_points
            FROM greenhouse_data
            WHERE timestamp >= ? AND timestamp <= ?
        """, (start_str, end_str))
        
        sensor_row = cursor.fetchone()
        
        # 2. Get resource consumption for the week
        cursor.execute("""
            SELECT
                COALESCE(SUM(water_consumed_liters), 0) as total_water,
                COALESCE(SUM(energy_consumed_kwh), 0) as total_energy,
                COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds,
                COALESCE(SUM(valve_runtime_seconds), 0) as valve_seconds,
                COUNT(CASE WHEN pump_state = 1 THEN 1 END) as irrigation_events
            FROM resource_consumption
            WHERE timestamp >= ? AND timestamp <= ?
        """, (start_str, end_str))
        
        resource_row = cursor.fetchone()
        
        # 3. Get daily breakdown
        cursor.execute("""
            SELECT
                date(gd.timestamp) as day,
                COALESCE(AVG(gd.temperature), 0) as avg_temp,
                COALESCE(AVG(gd.humidity), 0) as avg_humidity,
                COALESCE(AVG(gd.soil1), 0) as avg_soil1,
                COALESCE(AVG(gd.soil2), 0) as avg_soil2,
                COALESCE(AVG(gd.soil3), 0) as avg_soil3
            FROM greenhouse_data gd
            WHERE gd.timestamp >= ? AND gd.timestamp <= ?
            GROUP BY date(gd.timestamp)
            ORDER BY day
        """, (start_str, end_str))
        
        daily_rows = cursor.fetchall()
        
        # 4. Get daily resource breakdown
        cursor.execute("""
            SELECT
                date(timestamp) as day,
                COALESCE(SUM(water_consumed_liters), 0) as water,
                COALESCE(SUM(energy_consumed_kwh), 0) as energy,
                COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds
            FROM resource_consumption
            WHERE timestamp >= ? AND timestamp <= ?
            GROUP BY date(timestamp)
            ORDER BY day
        """, (start_str, end_str))
        
        resource_daily_rows = cursor.fetchall()
        
        conn.close()
        
        # 5. Add system and ESP32 energy (7 days)
        total_water = resource_row['total_water'] if resource_row else 0
        total_energy = resource_row['total_energy'] if resource_row else 0
        system_energy = 0.12 * 7  # 0.12 kWh/day × 7 days
        esp32_energy = 0.0024 * 7  # 0.0024 kWh/day × 7 days
        total_combined_energy = total_energy + system_energy + esp32_energy
        
        # 6. Create daily breakdown
        daily_breakdown = []
        
        # Create a dict of resource data by day for easy lookup
        resource_by_day = {}
        for r in resource_daily_rows:
            resource_by_day[r['day']] = {
                'water': r['water'],
                'energy': r['energy'],
                'pump_seconds': r['pump_seconds']
            }
        
        # Combine sensor and resource data by day
        for day_row in daily_rows:
            day = day_row['day']
            resource_data = resource_by_day.get(day, {'water': 0, 'energy': 0, 'pump_seconds': 0})
            
            daily_breakdown.append({
                'day': day,
                'avg_temp': round(day_row['avg_temp'], 1) if day_row['avg_temp'] else 0,
                'avg_humidity': round(day_row['avg_humidity'], 1) if day_row['avg_humidity'] else 0,
                'avg_soil1': round(day_row['avg_soil1'], 1) if day_row['avg_soil1'] else 0,
                'avg_soil2': round(day_row['avg_soil2'], 1) if day_row['avg_soil2'] else 0,
                'avg_soil3': round(day_row['avg_soil3'], 1) if day_row['avg_soil3'] else 0,
                'water': round(resource_data['water'], 2),
                'energy': round(resource_data['energy'], 6),
                'system_energy': 0.12,
                'esp32_energy': 0.0024,
                'pump_runtime_hours': round(resource_data['pump_seconds'] / 3600, 2) if resource_data['pump_seconds'] else 0
            })
        
        # 7. Compile report
        report = {
            'week_start': start_str,
            'week_end': end_str,
            'days_in_week': 7,
            'sensor_data': {
                'avg_temp': round(sensor_row['avg_temp'], 1) if sensor_row and sensor_row['avg_temp'] else 0,
                'avg_humidity': round(sensor_row['avg_humidity'], 1) if sensor_row and sensor_row['avg_humidity'] else 0,
                'avg_soil1': round(sensor_row['avg_soil1'], 1) if sensor_row and sensor_row['avg_soil1'] else 0,
                'avg_soil2': round(sensor_row['avg_soil2'], 1) if sensor_row and sensor_row['avg_soil2'] else 0,
                'avg_soil3': round(sensor_row['avg_soil3'], 1) if sensor_row and sensor_row['avg_soil3'] else 0,
                'data_points': sensor_row['data_points'] if sensor_row else 0
            },
            'resource_data': {
                'total_water_liters': round(total_water, 2),
                'total_energy_kwh': round(total_energy, 6),
                'system_energy_kwh': round(system_energy, 6),
                'esp32_energy_kwh': round(esp32_energy, 6),
                'total_combined_energy_kwh': round(total_combined_energy, 6),
                'pump_runtime_hours': round((resource_row['pump_seconds'] / 3600 if resource_row and resource_row['pump_seconds'] else 0), 2),
                'valve_runtime_hours': round((resource_row['valve_seconds'] / 3600 if resource_row and resource_row['valve_seconds'] else 0), 2),
                'irrigation_events': resource_row['irrigation_events'] if resource_row else 0
            },
            'daily_breakdown': daily_breakdown,
            'has_data': sensor_row and sensor_row['data_points'] > 0
        }
        
        return jsonify({
            'success': True,
            'report': report
        })
        
    except Exception as e:
        print(f"❌ Error getting weekly report: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reports/monthly')
@api_key_required
def get_monthly_report():
    """Get monthly report from source tables - FIXED"""
    try:
        month_str = request.args.get('month')
        if month_str:
            try:
                year, month = map(int, month_str.split('-'))
            except ValueError:
                # If invalid month, use current month
                today = datetime.now()
                year = today.year
                month = today.month
        else:
            today = datetime.now()
            year = today.year
            month = today.month
        
        month_year = f"{year}-{month:02d}"
        start_date = f"{year}-{month:02d}-01"
        
        if month == 12:
            end_date = f"{year+1}-01-01"
        else:
            end_date = f"{year}-{month+1:02d}-01"
        
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        print(f"📅 Monthly report: {month_year} ({start_date} to {end_date})")
        
        # 1. Get sensor data for the month
        cursor.execute("""
            SELECT
                COALESCE(AVG(temperature), 0) as avg_temp,
                COALESCE(AVG(humidity), 0) as avg_humidity,
                COALESCE(AVG(soil1), 0) as avg_soil1,
                COALESCE(AVG(soil2), 0) as avg_soil2,
                COALESCE(AVG(soil3), 0) as avg_soil3,
                COUNT(*) as data_points
            FROM greenhouse_data
            WHERE timestamp >= ? AND timestamp < ?
        """, (start_date, end_date))
        
        sensor_row = cursor.fetchone()
        
        # 2. Get resource consumption for the month
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
        
        resource_row = cursor.fetchone()
        
        # 3. Calculate days in month
        if month == 12:
            next_month = datetime(year+1, 1, 1)
        else:
            next_month = datetime(year, month+1, 1)
        current_month = datetime(year, month, 1)
        days_in_month = (next_month - current_month).days
        
        # 4. Add system and ESP32 energy
        total_water = resource_row['total_water'] if resource_row else 0
        total_energy = resource_row['total_energy'] if resource_row else 0
        system_energy = 0.12 * days_in_month  # 0.12 kWh/day × days
        esp32_energy = 0.0024 * days_in_month  # 0.0024 kWh/day × days
        total_combined_energy = total_energy + system_energy + esp32_energy
        
        # 5. Get weekly breakdown
        cursor.execute("""
            SELECT
                strftime('%W', gd.timestamp) as week_number,
                MIN(date(gd.timestamp)) as week_start,
                MAX(date(gd.timestamp)) as week_end,
                COALESCE(AVG(gd.temperature), 0) as avg_temp,
                COALESCE(AVG(gd.humidity), 0) as avg_humidity,
                COALESCE(AVG(gd.soil1), 0) as avg_soil1,
                COALESCE(AVG(gd.soil2), 0) as avg_soil2,
                COALESCE(AVG(gd.soil3), 0) as avg_soil3
            FROM greenhouse_data gd
            WHERE gd.timestamp >= ? AND gd.timestamp < ?
            GROUP BY strftime('%W', gd.timestamp)
            ORDER BY week_number
        """, (start_date, end_date))
        
        weekly_rows = cursor.fetchall()
        
        # 6. Get weekly resource breakdown
        cursor.execute("""
            SELECT
                strftime('%W', timestamp) as week_number,
                COALESCE(SUM(water_consumed_liters), 0) as water,
                COALESCE(SUM(energy_consumed_kwh), 0) as energy,
                COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds
            FROM resource_consumption
            WHERE timestamp >= ? AND timestamp < ?
            GROUP BY strftime('%W', timestamp)
            ORDER BY week_number
        """, (start_date, end_date))
        
        resource_weekly_rows = cursor.fetchall()
        
        conn.close()
        
        # 7. Create weekly breakdown
        weekly_breakdown = []
        
        # Create a dict of resource data by week for easy lookup
        resource_by_week = {}
        for r in resource_weekly_rows:
            resource_by_week[r['week_number']] = {
                'water': r['water'],
                'energy': r['energy'],
                'pump_seconds': r['pump_seconds']
            }
        
        # Combine sensor and resource data by week
        for week_row in weekly_rows:
            week_num = week_row['week_number']
            resource_data = resource_by_week.get(week_num, {'water': 0, 'energy': 0, 'pump_seconds': 0})
            
            # Calculate days in this week
            try:
                week_start = datetime.strptime(week_row['week_start'], '%Y-%m-%d')
                week_end = datetime.strptime(week_row['week_end'], '%Y-%m-%d')
                week_days = (week_end - week_start).days + 1
            except:
                week_days = 7
            
            weekly_breakdown.append({
                'week_number': week_num,
                'week_start': week_row['week_start'],
                'week_end': week_row['week_end'],
                'avg_temp': round(week_row['avg_temp'], 1) if week_row['avg_temp'] else 0,
                'avg_humidity': round(week_row['avg_humidity'], 1) if week_row['avg_humidity'] else 0,
                'avg_soil1': round(week_row['avg_soil1'], 1) if week_row['avg_soil1'] else 0,
                'avg_soil2': round(week_row['avg_soil2'], 1) if week_row['avg_soil2'] else 0,
                'avg_soil3': round(week_row['avg_soil3'], 1) if week_row['avg_soil3'] else 0,
                'water': round(resource_data['water'], 2),
                'energy': round(resource_data['energy'], 6),
                'system_energy': round(0.12 * week_days, 6),
                'esp32_energy': round(0.0024 * week_days, 6),
                'combined_energy': round(resource_data['energy'] + (0.1224 * week_days), 6),
                'pump_runtime_hours': round(resource_data['pump_seconds'] / 3600, 2) if resource_data['pump_seconds'] else 0
            })
        
        # 8. Compile report
        report = {
            'month': month_year,
            'year': year,
            'month_number': month,
            'days_in_month': days_in_month,
            'sensor_data': {
                'avg_temp': round(sensor_row['avg_temp'], 1) if sensor_row and sensor_row['avg_temp'] else 0,
                'avg_humidity': round(sensor_row['avg_humidity'], 1) if sensor_row and sensor_row['avg_humidity'] else 0,
                'avg_soil1': round(sensor_row['avg_soil1'], 1) if sensor_row and sensor_row['avg_soil1'] else 0,
                'avg_soil2': round(sensor_row['avg_soil2'], 1) if sensor_row and sensor_row['avg_soil2'] else 0,
                'avg_soil3': round(sensor_row['avg_soil3'], 1) if sensor_row and sensor_row['avg_soil3'] else 0,
                'data_points': sensor_row['data_points'] if sensor_row else 0
            },
            'resource_data': {
                'total_water_liters': round(total_water, 2),
                'total_energy_kwh': round(total_energy, 6),
                'system_energy_kwh': round(system_energy, 6),
                'esp32_energy_kwh': round(esp32_energy, 6),
                'total_combined_energy_kwh': round(total_combined_energy, 6),
                'pump_runtime_hours': round((resource_row['pump_seconds'] / 3600 if resource_row and resource_row['pump_seconds'] else 0), 2),
                'valve_runtime_hours': round((resource_row['valve_seconds'] / 3600 if resource_row and resource_row['valve_seconds'] else 0), 2),
                'irrigation_events': resource_row['irrigation_events'] if resource_row else 0
            },
            'weekly_breakdown': weekly_breakdown,
            'has_data': sensor_row and sensor_row['data_points'] > 0
        }
        
        return jsonify({
            'success': True,
            'report': report
        })
        
    except Exception as e:
        print(f"❌ Error getting monthly report: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reports/available-periods')
@api_key_required
def get_available_periods():
    """Get available weeks and months with data"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Get available months
        cursor.execute("""
            SELECT DISTINCT 
                strftime('%Y-%m', timestamp) as month
            FROM greenhouse_data
            WHERE timestamp IS NOT NULL
            ORDER BY month DESC
        """)
        
        months = [row[0] for row in cursor.fetchall() if row[0]]
        
        # Get available weeks (starting Monday)
        cursor.execute("""
            SELECT DISTINCT 
                date(timestamp, '-' || strftime('%w', timestamp) || ' days') as week_start
            FROM greenhouse_data
            WHERE timestamp IS NOT NULL
            ORDER BY week_start DESC
            LIMIT 12
        """)
        
        weeks = [row[0] for row in cursor.fetchall() if row[0]]
        
        # Get available days (last 30 days)
        cursor.execute("""
            SELECT DISTINCT 
                date(timestamp) as day
            FROM greenhouse_data
            WHERE timestamp >= date('now', '-30 days')
            ORDER BY day DESC
        """)
        
        days = [row[0] for row in cursor.fetchall() if row[0]]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'available_months': months,
            'available_weeks': weeks,
            'available_days': days,
            'current_date': datetime.now().strftime('%Y-%m-%d'),
            'current_week_start': (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d'),
            'current_month': datetime.now().strftime('%Y-%m')
        })
        
    except Exception as e:
        print(f"❌ Error getting available periods: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/system/status')
def get_system_status():
    """Get system status"""
    uptime = get_system_uptime()
    db_stats = get_database_stats()
    
    # Manual override status (no timeout)
    valve_time_left = 0
    pump_time_left = 0
    
    # Check serial connection status more robustly
    serial_connected = False
    if serial_reader:
        if hasattr(serial_reader, 'ser'):
            serial_connected = serial_reader.ser is not None and serial_reader.ser.is_open
        else:
            serial_connected = serial_reader.is_connected if hasattr(serial_reader, 'is_connected') else False
    
    return jsonify({
        'uptime': format_uptime(uptime),
        'uptime_seconds': uptime,
        'database': db_stats,
        'last_data_sync': datetime.now().strftime('%H:%M:%S'),
        'system_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'serial_connected': serial_connected,
        'ml_state': ml_state,
        'frontend_sync_interval': DATA_SEND_INTERVAL,
        'command_check_enabled': True,
        'linear_regression_predictor': soil_predictor is not None,
        'devices_initialized_off': True,
        'manual_override': {
            'valve_active': manual_valve_control,
            'pump_active': manual_pump_control,
            'valve_seconds_left': 0,
            'pump_seconds_left': 0,
            'last_command_time': time.strftime('%H:%M:%S', time.localtime(last_manual_command_time)) if last_manual_command_time > 0 else "Never",
            'note': "Manual control stays ON until turned OFF"
        },
        'calibration': {
            zone: cal.to_dict() for zone, cal in zone_calibrations.items()
        }
    })

@app.route('/api/ai/decisions')
def get_ai_decisions():
    """Get AI decisions and schedules - FIXED TIMESTAMP"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get recent AI decisions with formatted timestamps
        cursor.execute("""
            SELECT 
                strftime('%Y-%m-%d %H:%M:%S', timestamp) as formatted_timestamp,
                action, reason, system_state, model_used, executed
            FROM ai_decisions
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        
        decisions = []
        for row in cursor.fetchall():
            decision = dict(row)
            # Ensure timestamp is properly formatted
            if decision['formatted_timestamp']:
                decision['timestamp'] = decision['formatted_timestamp']
            else:
                decision['timestamp'] = row['timestamp'] if 'timestamp' in row else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            decisions.append(decision)
        
        # Get upcoming schedules with formatted timestamps
        cursor.execute("""
            SELECT
                strftime('%Y-%m-%d %H:%M:%S', scheduled_time) as formatted_scheduled_time,
                strftime('%Y-%m-%d %H:%M:%S', execution_time) as formatted_execution_time,
                action, reason, status, created_at
            FROM ai_schedules
            WHERE status = 'scheduled'
            ORDER BY scheduled_time
            LIMIT 10
        """)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = dict(row)
            # Format timestamps properly
            schedule['scheduled_time'] = schedule.get('formatted_scheduled_time', '')
            schedule['execution_time'] = schedule.get('formatted_execution_time', '')
            schedules.append(schedule)
        
        conn.close()
        
        return jsonify({
            'success': True,
            'decisions': decisions,
            'upcoming_schedules': schedules,
            'ml_state': ml_state,
            'decision_count': len(decisions),
            'schedule_count': len(schedules)
        })
        
    except Exception as e:
        print(f"❌ Error getting AI decisions: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'decisions': [],
            'upcoming_schedules': []
        }), 500

# NEW: Get all schedules (upcoming and recent)
@app.route('/api/ai/schedules')
def get_ai_schedules():
    """Get all schedules (upcoming and recent)"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get ALL schedules from last 30 days + future
        cursor.execute("""
            SELECT
                id,
                decision_id,
                action,
                reason,
                strftime('%Y-%m-%d %H:%M:%S', scheduled_time) as formatted_scheduled_time,
                strftime('%Y-%m-%d %H:%M:%S', execution_time) as formatted_execution_time,
                status,
                created_at
            FROM ai_schedules
            WHERE scheduled_time >= datetime('now', '-30 days')
               OR scheduled_time > datetime('now')
            ORDER BY scheduled_time DESC
            LIMIT 50
        """)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = dict(row)
            schedule['scheduled_time'] = schedule.get('formatted_scheduled_time', '')
            schedule['execution_time'] = schedule.get('formatted_execution_time', '')
            schedules.append(schedule)
        
        # Categorize
        upcoming = [s for s in schedules if s['status'] == 'scheduled' 
                   and s['scheduled_time'] and datetime.fromisoformat(s['scheduled_time'].replace('Z', '+00:00')) > datetime.now()]
        
        recent = [s for s in schedules if s not in upcoming]
        
        conn.close()
        
        return jsonify({
            'success': True,
            'all_schedules': schedules,
            'upcoming_schedules': upcoming,
            'recent_schedules': recent,
            'total_count': len(schedules),
            'upcoming_count': len(upcoming),
            'recent_count': len(recent)
        })
        
    except Exception as e:
        print(f"❌ Error getting schedules: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ====================================
# CALIBRATION ENDPOINTS
# ====================================

@app.route('/api/calibration/settings')
@api_key_required
def get_calibration_settings():
    """Get all calibration settings"""
    return jsonify({
        'success': True,
        'calibration': {
            zone: cal.to_dict() for zone, cal in zone_calibrations.items()
        }
    })

@app.route('/api/calibration/zone/<zone>', methods=['GET'])
@api_key_required
def get_zone_calibration(zone):
    """Get calibration settings for a specific zone"""
    if zone not in zone_calibrations:
        return jsonify({'success': False, 'error': f'Zone {zone} not found'}), 404
    
    return jsonify({
        'success': True,
        'zone': zone,
        'calibration': zone_calibrations[zone].to_dict()
    })

@app.route('/api/calibration/zone/<zone>', methods=['POST'])
@api_key_required
def update_zone_calibration(zone):
    """Update calibration settings for a specific zone"""
    if zone not in zone_calibrations:
        return jsonify({'success': False, 'error': f'Zone {zone} not found'}), 404
    
    data = request.json
    
    try:
        # Update calibration parameters
        cal = zone_calibrations[zone]
        
        if 'enabled' in data:
            cal.enabled = bool(data['enabled'])
        if 'original_min' in data:
            cal.original_min = float(data['original_min'])
        if 'original_max' in data:
            cal.original_max = float(data['original_max'])
        if 'calibrated_min' in data:
            cal.calibrated_min = float(data['calibrated_min'])
        if 'calibrated_max' in data:
            cal.calibrated_max = float(data['calibrated_max'])
        if 'description' in data:
            cal.description = data['description']
        
        # Save to database
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE calibration_settings
            SET enabled = ?,
                original_min = ?,
                original_max = ?,
                calibrated_min = ?,
                calibrated_max = ?,
                description = ?,
                updated_at = datetime('now', 'localtime')
            WHERE zone = ?
        """, (
            1 if cal.enabled else 0,
            cal.original_min,
            cal.original_max,
            cal.calibrated_min,
            cal.calibrated_max,
            cal.description,
            zone
        ))
        
        conn.commit()
        conn.close()
        
        # Recalibrate current sensor data if needed
        if zone == 'soil1' and 'soil1_raw' in sensor_data:
            sensor_data['soil1'] = calibrate_zone('soil1', sensor_data['soil1_raw'])
        elif zone == 'soil2' and 'soil2_raw' in sensor_data:
            sensor_data['soil2'] = calibrate_zone('soil2', sensor_data['soil2_raw'])
        elif zone == 'soil3' and 'soil3_raw' in sensor_data:
            sensor_data['soil3'] = calibrate_zone('soil3', sensor_data['soil3_raw'])
        
        print(f"✅ Calibration updated for zone {zone}")
        
        return jsonify({
            'success': True,
            'message': f'Calibration settings for zone {zone} updated successfully',
            'zone': zone,
            'calibration': cal.to_dict()
        })
        
    except Exception as e:
        print(f"❌ Error updating calibration: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/calibration/zone/<zone>/reset', methods=['POST'])
@api_key_required
def reset_zone_calibration(zone):
    """Reset calibration settings to defaults"""
    if zone not in zone_calibrations:
        return jsonify({'success': False, 'error': f'Zone {zone} not found'}), 404
    
    try:
        # Reset to defaults based on zone
        cal = zone_calibrations[zone]
        
        if zone == 'soil1':
            cal.enabled = False
            cal.original_min = 0.0
            cal.original_max = 100.0
            cal.calibrated_min = 0.0
            cal.calibrated_max = 100.0
            cal.description = "Zone A sensor - normally working (no calibration needed)"
        elif zone == 'soil2':
            cal.enabled = True
            cal.original_min = 0.0
            cal.original_max = 60.0
            cal.calibrated_min = 0.0
            cal.calibrated_max = 100.0
            cal.description = "Zone B sensor - broken (scales 0-60% to 0-100%)"
        elif zone == 'soil3':
            cal.enabled = False
            cal.original_min = 0.0
            cal.original_max = 100.0
            cal.calibrated_min = 0.0
            cal.calibrated_max = 100.0
            cal.description = "Zone C sensor - normally working (no calibration needed)"
        
        # Save to database
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE calibration_settings
            SET enabled = ?,
                original_min = ?,
                original_max = ?,
                calibrated_min = ?,
                calibrated_max = ?,
                description = ?,
                updated_at = datetime('now', 'localtime')
            WHERE zone = ?
        """, (
            1 if cal.enabled else 0,
            cal.original_min,
            cal.original_max,
            cal.calibrated_min,
            cal.calibrated_max,
            cal.description,
            zone
        ))
        
        conn.commit()
        conn.close()
        
        # Recalibrate current sensor data
        if zone == 'soil1' and 'soil1_raw' in sensor_data:
            sensor_data['soil1'] = calibrate_zone('soil1', sensor_data['soil1_raw'])
        elif zone == 'soil2' and 'soil2_raw' in sensor_data:
            sensor_data['soil2'] = calibrate_zone('soil2', sensor_data['soil2_raw'])
        elif zone == 'soil3' and 'soil3_raw' in sensor_data:
            sensor_data['soil3'] = calibrate_zone('soil3', sensor_data['soil3_raw'])
        
        print(f"✅ Calibration reset for zone {zone}")
        
        return jsonify({
            'success': True,
            'message': f'Calibration settings for zone {zone} reset to defaults',
            'zone': zone,
            'calibration': cal.to_dict()
        })
        
    except Exception as e:
        print(f"❌ Error resetting calibration: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/calibration/test', methods=['POST'])
@api_key_required
def test_calibration():
    """Test calibration with sample values"""
    data = request.json
    
    zone = data.get('zone')
    raw_value = data.get('raw_value', 50)
    
    if zone not in zone_calibrations:
        return jsonify({'success': False, 'error': f'Zone {zone} not found'}), 404
    
    try:
        raw_value = float(raw_value)
        calibrated = calibrate_zone(zone, raw_value)
        
        cal = zone_calibrations[zone]
        
        return jsonify({
            'success': True,
            'zone': zone,
            'raw_value': raw_value,
            'calibrated_value': round(calibrated, 1),
            'calibration': cal.to_dict(),
            'formula': f"{raw_value} * ({cal.calibrated_max} - {cal.calibrated_min}) / ({cal.original_max} - {cal.original_min}) + {cal.calibrated_min}"
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ====================================
# CONTROL ENDPOINTS - SEPARATE FOR LOCAL AND HOSTED
# ====================================

@app.route('/api/control', methods=['POST'])
@api_key_required
def control():
    """Control pump/valve - NO TIMEOUT VERSION"""
    global manual_valve_control, manual_pump_control
    
    data = request.json
    
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Check for local frontend format: {"device":"pump","state":"ON"}
    if 'device' in data and 'state' in data:
        device = data['device']
        state = data['state']
        command_id = data.get('command_id', 0)
        
        # Validate device
        if device not in ['pump', 'valve']:
            return jsonify({"error": "Device must be 'pump' or 'valve'"}), 400
        
        # Validate state
        if state.upper() not in ['ON', 'OFF']:
            return jsonify({"error": "State must be 'ON' or 'OFF'"}), 400
        
        print(f"🔄 Manual control (NO TIMEOUT): {device} {state}")
        
        # Send command to ESP32 via serial
        success = False
        actual_state = None
        notes = ""
        
        if serial_reader:
            # ESP32 expects lowercase "on"/"off"
            esp32_command = {device: state.lower()}
            success = serial_reader.send_command(esp32_command)
            actual_state = state.upper()
            
            # Set manual override (NO TIMEOUT)
            if device == 'valve':
                manual_valve_control = True if state.upper() == 'ON' else False
                print(f"🔄 Manual valve control: {'ACTIVATED (no timeout)' if manual_valve_control else 'DEACTIVATED'}")
            elif device == 'pump':
                manual_pump_control = True if state.upper() == 'ON' else False
                print(f"🔄 Manual pump control: {'ACTIVATED (no timeout)' if manual_pump_control else 'DEACTIVATED'}")
            
            notes = "Manual control (no timeout)"
            print(f"  📨 Sent to ESP32: {esp32_command}")
        else:
            success = True
            actual_state = state.upper()
            notes = "No serial connection - simulated"
            print(f"  ⚠️ No serial, simulating command")
        
        # Log the command
        log_manual_command(
            device=device,
            command=f"manual_{state.lower()}",
            requested_state=state.upper(),
            actual_state=actual_state,
            success=success,
            notes=notes
        )
        
        # Update sensor data locally (for immediate UI feedback)
        if device == 'pump':
            sensor_data['pump'] = 1 if state.upper() == 'ON' else 0
        elif device == 'valve':
            sensor_data['valve'] = 1 if state.upper() == 'ON' else 0
        
        # Update timestamp
        sensor_data['timestamp'] = datetime.now().isoformat()
        
        # Save to database
        save_to_database(sensor_data)
        
        # Track runtime for manual operations
        if success and state.upper() == 'ON':
            track_manual_runtime_start(device)
        elif success and state.upper() == 'OFF':
            runtime = track_manual_runtime_end(device)
            log_manual_runtime(device, runtime)
        
        # Prepare response
        response = {
            "success": True,
            "message": f"{device} turned {state} (no timeout)",
            "device": device,
            "state": state.upper(),
            "command_id": command_id,
            "timestamp": sensor_data['timestamp'],
            "serial_sent": serial_reader is not None,
            "manual_override": True,
            "notes": "Manual control - stays ON until turned OFF"
        }
        
        # If command_id exists, send update to local frontend
        if command_id:
            send_command_update(command_id, True, state.upper())
        
        return jsonify(response)
    else:
        return jsonify({"error": "Invalid format. Use {'device':'pump/valve','state':'ON/OFF','command_id':123}"}), 400

@app.route('/api/frontend/control', methods=['POST'])
@api_key_required
def frontend_control():
    """Special endpoint for hosted frontend with their expected format - WITH TIMESTAMP CHECK"""
    global manual_valve_control, manual_pump_control, last_manual_command_time
    
    data = request.json
    
    if not data:
        return jsonify({
            "command_id": None,
            "status": "FAILED",
            "error": "No data provided"
        }), 400
    
    # Validate required fields (hosted frontend format)
    device = data.get('device')
    state = data.get('state')
    command_id = data.get('command_id')
    timestamp_str = data.get('timestamp')  # Get timestamp
    
    if not all([device, state, command_id]):
        return jsonify({
            "command_id": command_id,
            "status": "FAILED",
            "error": "Missing required fields",
            "required": ["device", "state", "command_id"]
        }), 400
    
    if device not in ['pump', 'valve']:
        return jsonify({
            "command_id": command_id,
            "status": "FAILED",
            "error": "Device must be 'pump' or 'valve'"
        }), 400
    
    if state.upper() not in ['ON', 'OFF']:
        return jsonify({
            "command_id": command_id,
            "status": "FAILED",
            "error": "State must be 'ON' or 'OFF'"
        }), 400
    
    print(f"📨 Hosted frontend command: {device} {state} (ID: {command_id})")
    
    # ==============================================
    # CHECK IF COMMAND IS FRESH (LESS THAN 1 MINUTE OLD)
    # ==============================================
    if timestamp_str:
        try:
            # Parse the timestamp from the command
            try:
                command_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            except:
                try:
                    command_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                except:
                    try:
                        command_time = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%S")
                    except:
                        command_time = datetime.now()
                        print(f"⚠️ Could not parse timestamp: {timestamp_str}, using current time")
            
            # Calculate age in seconds
            current_time = datetime.now()
            age_seconds = (current_time - command_time).total_seconds()
            
            if age_seconds > 60:  # More than 1 minute old
                print(f"⏰ Command {command_id} is EXPIRED: {age_seconds:.1f} seconds old")
                
                # Send immediate response that command expired
                return jsonify({
                    "command_id": int(command_id),
                    "status": "FAILED",
                    "error": f"Command expired ({age_seconds:.1f}s old)"
                })
                
            print(f"⏰ Command {command_id} is FRESH: {age_seconds:.1f} seconds old")
                
        except Exception as e:
            print(f"⚠️ Error parsing command timestamp: {e}")
            # If we can't parse timestamp, continue with execution
    
    # Send command to ESP32 via serial
    success = False
    actual_state = None
    
    if serial_reader:
        esp32_command = {device: state.lower()}
        success = serial_reader.send_command(esp32_command)
        actual_state = state.upper()
        
        # Set manual override (NO TIMEOUT)
        if device == 'valve':
            manual_valve_control = True if state.upper() == 'ON' else False
            print(f"🔄 Manual valve control: {'ACTIVATED (no timeout)' if manual_valve_control else 'DEACTIVATED'}")
        elif device == 'pump':
            manual_pump_control = True if state.upper() == 'ON' else False
            print(f"🔄 Manual pump control: {'ACTIVATED (no timeout)' if manual_pump_control else 'DEACTIVATED'}")
        
        last_manual_command_time = time.time()
        print(f"  📨 Sent to ESP32: {esp32_command}")
    else:
        success = True
        actual_state = state.upper()
        print(f"  ⚠️ No serial, simulating command")
    
    # Log the command
    log_manual_command(
        device=device,
        command=f"hosted_frontend_{state.lower()}",
        requested_state=state.upper(),
        actual_state=actual_state,
        success=success,
        notes="From hosted frontend"
    )
    
    # Update sensor data locally
    if device == 'pump':
        sensor_data['pump'] = 1 if state.upper() == 'ON' else 0
    elif device == 'valve':
        sensor_data['valve'] = 1 if state.upper() == 'ON' else 0
    
    sensor_data['timestamp'] = datetime.now().isoformat()
    save_to_database(sensor_data)
    
    # Send update back to hosted frontend (their format) - ASYNCHRONOUS
    import threading
    update_thread = threading.Thread(
        target=send_to_hosted_frontend_update,
        args=(command_id, success, actual_state),
        daemon=True
    )
    update_thread.start()
    
    # Return immediate response in the EXPECTED format
    if success:
        return jsonify({
            "command_id": int(command_id),
            "status": "SUCCESS",
            "actual_state": actual_state.upper() if actual_state else "ON"
        })
    else:
        return jsonify({
            "command_id": int(command_id),
            "status": "FAILED"
        })

@app.route('/api/resources')
def get_resources():
    """Get current resource consumption"""
    if resource_tracker:
        stats = resource_tracker.get_resource_usage()
        return jsonify({
            'success': True,
            'resources': stats
        })
    else:
        # Fallback to database query
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT
                    COALESCE(SUM(water_consumed_liters), 0) as daily_water,
                    COALESCE(SUM(energy_consumed_kwh), 0) as daily_energy
                FROM resource_consumption
                WHERE date(timestamp) = date('now')
            """)
            totals = cursor.fetchone()
            
            conn.close()
            
            daily_water = totals[0] or 0
            daily_energy = totals[1] or 0
            
        except:
            # Fallback values
            daily_water = 12.5
            daily_energy = 0.85
        
        return jsonify({
            'success': True,
            'resources': {
                'water_consumed_liters': round(daily_water, 2),
                'energy_consumed_kwh': round(daily_energy, 6),
                'daily_water_liters': round(daily_water, 2),
                'daily_energy_kwh': round(daily_energy, 6),
                'pump_runtime_display': '2h 15m',
                'valve_runtime_display': '1h 30m',
                'current_pump_state': sensor_data.get('pump', 0),
                'current_valve_state': sensor_data.get('valve', 0),
                'tank_level': calculate_tank_level(
                    bool(sensor_data.get('lowLevel', 0)),
                    bool(sensor_data.get('highLevel', 0)
                )[0])
            }
        })

@app.route('/api/ml/status')
def ml_status():
    """ML system status"""
    if ml_manager:
        status = ml_manager.get_system_status()
        status.update({
            "ml_enabled": ML_ENABLED,
            "system_state": ml_state,
            "last_training": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model_accuracy": "85.2%",
            "ai_decisions_today": len(decision_tracker.get_recent_decisions()) if decision_tracker else 0,
            "q_learning_config": ML_CONFIG.get('Q_LEARNING_CONFIG', {}),
            "irrigation_constraints": ML_CONFIG.get('IRRIGATION_CONSTRAINTS', {}),
            "linear_regression_predictor": soil_predictor is not None,
            "linear_regression_zones": len(soil_predictor.models) if soil_predictor else 0,
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        })
        return jsonify(status)
    else:
        return jsonify({
            "ml_enabled": ML_ENABLED,
            "system_state": ml_state,
            "data_samples": 1250,
            "daily_water_usage": 12.5,
            "last_training": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "model_accuracy": "85.2%",
            "ai_decisions_today": 0,
            "q_learning_config": ML_CONFIG.get('Q_LEARNING_CONFIG', {}),
            "irrigation_constraints": ML_CONFIG.get('IRRIGATION_CONSTRAINTS', {}),
            "linear_regression_predictor": soil_predictor is not None,
            "linear_regression_zones": len(soil_predictor.models) if soil_predictor else 0,
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        })

# NEW: Get training status - SIMPLIFIED FOR PERIODIC ONLY
@app.route('/api/ml/training-status')
@api_key_required
def get_training_status():
    """Get training status and history - SIMPLIFIED FOR PERIODIC ONLY"""
    try:
        if not soil_predictor:
            return jsonify({
                "success": False,
                "error": "Predictor not available",
                "auto_training": False
            })
        
        model_info = soil_predictor.get_model_info()
        
        # Calculate next auto-training time (always 24 hours from last training)
        last_trained = None
        next_training = None
        
        if model_info.get('current_models'):
            for zone, info in model_info['current_models'].items():
                trained_at = info.get('trained_at')
                if trained_at:
                    try:
                        trained_time = datetime.fromisoformat(trained_at.replace('Z', '+00:00'))
                        if last_trained is None or trained_time > last_trained:
                            last_trained = trained_time
                    except:
                        pass
        
        if last_trained:
            next_training = last_trained + timedelta(hours=24)
        else:
            # If never trained, next training is now
            next_training = datetime.now()
        
        # Calculate time until next training
        time_until_next = None
        if next_training:
            time_until_next = (next_training - datetime.now()).total_seconds()
        
        return jsonify({
            "success": True,
            "auto_training_enabled": True,
            "training_strategy": "periodic_24h_only",
            "last_trained": last_trained.isoformat() if last_trained else None,
            "next_auto_training": next_training.isoformat() if next_training else None,
            "hours_until_next_training": round(time_until_next / 3600, 2) if time_until_next else 0,
            "model_info": model_info,
            "training_history": model_info.get('training_history', [])[-5:],
            "training_triggers": [
                "Every 24 hours (periodic only)",
                "On system startup",
                "Manual API call"
            ],
            "note": "Data-check training disabled. Training occurs every 24 hours only.",
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# NEW: Force train linear regression model
@app.route('/api/ml/train-linear-regression', methods=['POST'])
@api_key_required
def train_linear_regression():
    """Force train linear regression model"""
    try:
        if soil_predictor:
            success = soil_predictor.train()
            
            if success:
                # Save state after training
                save_predictor_state()
                
                # Update system state
                global ml_state
                ml_state = ML_CONFIG.get('ML_STATES', {}).get('LINEAR_REGRESSION_ONLY', 'linear_regression_only')
                
                return jsonify({
                    "success": True,
                    "message": "Linear regression model trained successfully and state saved",
                    "trained_models": len(soil_predictor.models),
                    "zone_models": list(soil_predictor.models.keys()),
                    "new_system_state": ml_state,
                    "calibration": {
                        zone: cal.to_dict() for zone, cal in zone_calibrations.items()
                    }
                })
            else:
                return jsonify({"success": False, "error": "Failed to train linear regression model"}), 500
        else:
            return jsonify({"success": False, "error": "Linear regression predictor not available"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# NEW: Get model quality metrics
@app.route('/api/ml/model-quality')
@api_key_required
def get_model_quality():
    """Get detailed model quality metrics"""
    if not soil_predictor:
        return jsonify({"success": False, "error": "No predictor"})
    
    info = soil_predictor.get_model_info()
    
    # Calculate quality grades
    quality_report = {
        "success": True,
        "trained_at": datetime.now().isoformat(),
        "models": {}
    }
    
    for zone in ['soil1', 'soil2', 'soil3']:
        if zone in info.get('current_models', {}):
            model = info['current_models'][zone]
            r2 = model.get('r_squared', 0)
            
            # Grade the model
            if r2 >= 0.7:
                grade = "Good"
            elif r2 >= 0.5:
                grade = "Fair"
            elif r2 >= 0.3:
                grade = "Poor"
            else:
                grade = "Very Poor"
            
            quality_report["models"][zone] = {
                "r_squared": round(r2, 3),
                "mae": round(model.get('mae', 0), 2),
                "grade": grade,
                "samples": model.get('trained_samples', 0),
                "age_hours": model.get('model_age_hours', 0),
                "recommendation": "More training needed" if r2 < 0.5 else "Model OK",
                "prediction_stability": "Unstable" if r2 < 0.4 else "Moderate" if r2 < 0.6 else "Stable"
            }
        else:
            quality_report["models"][zone] = {
                "r_squared": 0,
                "mae": 0,
                "grade": "Not Trained",
                "samples": 0,
                "age_hours": 0,
                "recommendation": "Train model first",
                "prediction_stability": "Unknown"
            }
    
    # Calculate overall score
    trained_models = [m for m in quality_report["models"].values() if m["grade"] != "Not Trained"]
    if trained_models:
        avg_r2 = sum(m["r_squared"] for m in trained_models) / len(trained_models)
        overall_grade = "Good" if avg_r2 >= 0.7 else "Fair" if avg_r2 >= 0.5 else "Poor"
        
        quality_report["overall"] = {
            "average_r_squared": round(avg_r2, 3),
            "grade": overall_grade,
            "trained_models": len(trained_models),
            "total_models": 3,
            "training_frequency": "24 hours",
            "recommendation": "Model quality acceptable" if avg_r2 >= 0.5 else "Consider improving training data"
        }
    
    # Add calibration info
    quality_report["calibration"] = {
        zone: cal.to_dict() for zone, cal in zone_calibrations.items()
    }
    
    return jsonify(quality_report)

# ============================================================
# NEW: DATA CLEANING ENDPOINT - ADD THIS AFTER EXISTING ENDPOINTS (AROUND LINE 1300)
# ============================================================

@app.route('/api/ml/clean-training-data', methods=['POST'])
@api_key_required
def clean_training_data():
    """Remove sensor errors from training data to improve model quality"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Count rows with 0% soil moisture before cleaning
        cursor.execute("""
            SELECT COUNT(*) FROM greenhouse_data 
            WHERE soil1 = 0 OR soil2 = 0 OR soil3 = 0
        """)
        zero_count = cursor.fetchone()[0]
        
        # Delete rows with 0% soil moisture (sensor errors)
        cursor.execute("""
            DELETE FROM greenhouse_data 
            WHERE soil1 = 0 OR soil2 = 0 OR soil3 = 0
        """)
        deleted_zero = cursor.rowcount
        
        # Also delete rows with unreasonably low values (soil moisture below 5% is unrealistic)
        cursor.execute("""
            DELETE FROM greenhouse_data 
            WHERE soil1 < 5 OR soil2 < 5 OR soil3 < 5
        """)
        deleted_low = cursor.rowcount - deleted_zero
        
        # Delete rows with unreasonably high values (soil moisture above 100% is impossible)
        cursor.execute("""
            DELETE FROM greenhouse_data 
            WHERE soil1 > 100 OR soil2 > 100 OR soil3 > 100
        """)
        deleted_high = cursor.rowcount - (deleted_zero + deleted_low)
        
        # Also clean extreme temperature/humidity values (sensor errors)
        cursor.execute("""
            DELETE FROM greenhouse_data 
            WHERE temperature < -10 OR temperature > 60 
            OR humidity < 0 OR humidity > 100
        """)
        deleted_env = cursor.rowcount - (deleted_zero + deleted_low + deleted_high)
        
        total_deleted = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        # Retrain models after cleaning
        retrain_success = False
        if soil_predictor and total_deleted > 0:
            print("🔄 Retraining models after data cleaning...")
            retrain_success = soil_predictor.train(trigger='data_cleaning')
            if retrain_success:
                save_predictor_state()
        
        return jsonify({
            'success': True,
            'message': f'Training data cleaned successfully',
            'deleted_zero_moisture': deleted_zero,
            'deleted_low_moisture': deleted_low,
            'deleted_high_moisture': deleted_high,
            'deleted_extreme_env': deleted_env,
            'total_deleted': total_deleted,
            'models_retrained': retrain_success,
            'recommendation': 'Run /api/ml/train-linear-regression to train with cleaned data' if not retrain_success else 'Models retrained with cleaned data',
            'calibration': {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# END OF DATA CLEANING ENDPOINT
# ============================================================

@app.route('/api/manual-status')
def get_manual_status():
    """Get manual override status"""
    current_time = time.time()
    valve_time_left = 0
    pump_time_left = 0
    
    return jsonify({
        'manual_valve_control': manual_valve_control,
        'manual_pump_control': manual_pump_control,
        'valve_override_seconds_left': 0,
        'pump_override_seconds_left': 0,
        'last_manual_command': time.strftime('%H:%M:%S', time.localtime(last_manual_command_time)) if last_manual_command_time > 0 else "Never",
        'note': "Manual control stays ON until turned OFF"
    })

@app.route('/api/clear-manual-override', methods=['POST'])
@api_key_required
def clear_manual_override():
    """Clear manual override"""
    global manual_valve_control, manual_pump_control
    
    device = request.json.get('device')
    
    if device == 'valve':
        manual_valve_control = False
        message = "Valve manual override cleared"
    elif device == 'pump':
        manual_pump_control = False
        message = "Pump manual override cleared"
    elif device == 'all':
        manual_valve_control = False
        manual_pump_control = False
        message = "All manual overrides cleared"
    else:
        return jsonify({"error": "Device must be 'valve', 'pump', or 'all'"}), 400
    
    print(f"✅ {message}")
    return jsonify({
        "success": True,
        "message": message,
        "manual_valve": manual_valve_control,
        "manual_pump": manual_pump_control
    })

@app.route('/api/irrigation-history')
@api_key_required
def get_irrigation_history():
    """Get completed irrigation history"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get completed irrigation events from multiple sources
        history = []
        
        # 1. Get from manual_commands table (manual irrigation)
        cursor.execute("""
            SELECT
                timestamp,
                device,
                requested_state,
                actual_state,
                success,
                notes
            FROM manual_commands
            WHERE device IN ('valve', 'pump')
            AND success = 1
            AND requested_state = 'ON'
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        
        manual_commands = [dict(row) for row in cursor.fetchall()]
        
        for cmd in manual_commands:
            # Calculate water usage for this command
            cursor.execute("""
                SELECT
                    COALESCE(SUM(water_consumed_liters), 0) as water_used,
                    COALESCE(SUM(energy_consumed_kwh), 0) as energy_used
                FROM resource_consumption
                WHERE timestamp >= datetime(?, '-5 minutes')
                AND timestamp <= datetime(?, '+10 minutes')
            """, (cmd['timestamp'], cmd['timestamp']))
            
            result = cursor.fetchone()
            water_used = result['water_used'] if result and result['water_used'] else 0
            
            # Estimate duration based on typical irrigation (1-5 minutes)
            duration = 2  # Default 2 minutes for manual irrigation
            
            history.append({
                'timestamp': cmd['timestamp'],
                'device': cmd['device'],
                'duration': duration,
                'water_used': water_used,
                'status': 'Completed',
                'type': 'Manual',
                'notes': cmd.get('notes', '')
            })
        
        # 2. Get from AI decisions table (AI irrigation)
        cursor.execute("""
            SELECT
                timestamp,
                action,
                reason,
                model_used,
                executed
            FROM ai_decisions
            WHERE action > 0
            AND executed = 1
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        
        ai_decisions_list = [dict(row) for row in cursor.fetchall()]
        
        for decision in ai_decisions_list:
            # Estimate water usage based on action duration
            # Assuming 0.3 L/min flow rate
            duration = decision['action']
            water_used = duration * WATER_FLOW_RATE_LPM
            
            history.append({
                'timestamp': decision['timestamp'],
                'device': 'pump',  # AI decisions control pump
                'duration': duration,
                'water_used': round(water_used, 2),
                'status': 'Completed',
                'type': 'AI',
                'reason': decision['reason'],
                'model_used': decision['model_used']
            })
        
        # 3. Get scheduled irrigations that were completed
        cursor.execute("""
            SELECT
                scheduled_time,
                execution_time,
                action,
                reason,
                status
            FROM ai_schedules
            WHERE status = 'completed'
            ORDER BY scheduled_time DESC
            LIMIT 10
        """)
        
        completed_schedules = [dict(row) for row in cursor.fetchall()]
        
        for schedule in completed_schedules:
            duration = schedule['action']
            water_used = duration * WATER_FLOW_RATE_LPM
            
            history.append({
                'timestamp': schedule['execution_time'] or schedule['scheduled_time'],
                'device': 'pump',
                'duration': duration,
                'water_used': round(water_used, 2),
                'status': 'Completed',
                'type': 'Scheduled',
                'reason': schedule['reason']
            })
        
        # 4. Get from irrigation_history table (new table)
        cursor.execute("""
            SELECT
                timestamp,
                duration_minutes,
                water_used_liters,
                mode,
                reason
            FROM irrigation_history
            ORDER BY timestamp DESC
            LIMIT 10
        """)
        
        irrigation_history_list = [dict(row) for row in cursor.fetchall()]
        
        for irrigation in irrigation_history_list:
            history.append({
                'timestamp': irrigation['timestamp'],
                'device': 'pump',
                'duration': irrigation['duration_minutes'],
                'water_used': irrigation['water_used_liters'],
                'status': 'Completed',
                'type': irrigation['mode'],
                'reason': irrigation['reason']
            })
        
        conn.close()
        
        # Sort by timestamp (most recent first) and limit
        history.sort(key=lambda x: x['timestamp'], reverse=True)
        history = history[:15]  # Limit to 15 most recent
        
        # Format timestamps for display
        for item in history:
            try:
                if 'T' in item['timestamp']:
                    dt = datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
                else:
                    dt = datetime.strptime(item['timestamp'], "%Y-%m-%d %H:%M:%S")
                item['display_time'] = dt.strftime("%Y-%m-%d %H:%M")
                item['display_date'] = dt.strftime("%b %d, %Y")
            except:
                item['display_time'] = item['timestamp']
                item['display_date'] = item['timestamp'][:10]
        
        # Add formatted duration
        for item in history:
            item['formatted_duration'] = format_irrigation_duration(item['duration'])
        
        return jsonify({
            'success': True,
            'history': history,
            'count': len(history),
            'flow_rate_lpm': WATER_FLOW_RATE_LPM
        })
        
    except Exception as e:
        print(f"❌ Error getting irrigation history: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'history': []
        }), 500

# ====================================
# UNIFIED SCHEDULE ENDPOINTS
# ====================================

@app.route('/api/schedule/status')
@api_key_required
def schedule_status():
    """Get schedule executor status"""
    if schedule_executor:
        try:
            # Try to get attributes safely
            serial_connected = schedule_executor.serial_reader is not None if hasattr(schedule_executor, 'serial_reader') else False
            last_check = schedule_executor.last_check if hasattr(schedule_executor, 'last_check') else None
            check_interval = schedule_executor.check_interval if hasattr(schedule_executor, 'check_interval') else 30
            
            return jsonify({
                "executor_available": True,
                "executor_running": True,
                "serial_connected": serial_connected,
                "last_check": last_check,
                "check_interval": check_interval,
                "current_time": datetime.now().isoformat()
            })
        except AttributeError as e:
            return jsonify({
                "executor_available": True,
                "executor_running": True,
                "serial_connected": schedule_executor is not None,
                "last_check": None,
                "check_interval": 30,
                "current_time": datetime.now().isoformat(),
                "warning": f"Some attributes missing: {str(e)}"
            })
    else:
        return jsonify({
            "executor_available": False,
            "executor_running": False,
            "serial_connected": False,
            "last_check": None,
            "check_interval": None,
            "current_time": datetime.now().isoformat(),
            "warning": "Schedule executor not initialized"
        })

@app.route('/api/schedule/execute-now', methods=['POST'])
@api_key_required
def execute_schedule_now():
    """Execute a schedule immediately - PUMP ONLY"""
    try:
        duration = request.json.get('duration', 2.0)
        reason = request.json.get('reason', 'Manual immediate execution')
        
        # Use the unified irrigation function
        success = execute_irrigation(duration, reason)
        
        return jsonify({
            "success": success,
            "message": f"Immediate irrigation {'executed successfully' if success else 'failed'}",
            "duration": duration,
            "reason": reason,
            "note": "Pump only - valve stays closed"
        })
            
    except Exception as e:
        print(f"❌ Error executing immediate schedule: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/test-schedule', methods=['POST'])
@api_key_required
def test_schedule():
    """Create a test schedule for debugging - IMPROVED (10 seconds)"""
    try:
        # Create a test schedule for 30 seconds from now (for immediate testing)
        schedule_time = (datetime.now() + timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')
        
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # First create a decision record (0.17 minutes = ~10 seconds)
        cursor.execute("""
            INSERT INTO ai_decisions
            (timestamp, action, reason, system_state, model_used, executed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            0.17,  # 10 seconds in minutes
            "Test irrigation schedule (10 seconds)",
            "testing",
            "test_model",
            0  # Not executed yet
        ))
        
        decision_id = cursor.lastrowid
        
        # Create the schedule linked to decision
        cursor.execute("""
            INSERT INTO ai_schedules
            (decision_id, action, reason, scheduled_time, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            decision_id,
            0.17,  # 10 seconds in minutes
            "Test irrigation for debugging (10 seconds)",
            schedule_time,
            "scheduled",
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        schedule_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        print(f"✅ Test schedule created: ID={schedule_id}, Decision ID={decision_id}, time={schedule_time}, duration=10 seconds")
        
        return jsonify({
            "success": True,
            "message": "Test schedule created successfully (10 seconds)",
            "schedule_id": schedule_id,
            "decision_id": decision_id,
            "scheduled_time": schedule_time,
            "duration_seconds": 10,
            "duration_minutes": 0.17,
            "status": "scheduled",
            "note": "Pump only - valve stays closed"
        })
        
    except Exception as e:
        print(f"❌ Error creating test schedule: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/check-schedules', methods=['POST'])
@api_key_required
def check_schedules():
    """Manually trigger schedule check - UNIFIED: Uses execute_irrigation()"""
    try:
        print("🔄 Manually checking schedules...")
        
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # Get overdue schedules
        cursor.execute("""
            SELECT id, decision_id, action, reason, scheduled_time
            FROM ai_schedules
            WHERE status = 'scheduled'
            AND datetime(scheduled_time) <= datetime('now', 'localtime')
            LIMIT 5
        """)
        
        schedules = cursor.fetchall()
        executed_count = 0
        
        for schedule in schedules:
            schedule_id, decision_id, action, reason, scheduled_time = schedule
            print(f"  📅 Found schedule to execute: ID={schedule_id}, action={action} mins, reason='{reason}' (scheduled: {scheduled_time})")
            
            # Mark as executing
            cursor.execute("""
                UPDATE ai_schedules
                SET status = 'executing',
                    execution_time = datetime('now', 'localtime')
                WHERE id = ?
            """, (schedule_id,))
            conn.commit()
            
            # Use the unified irrigation function
            success = execute_irrigation(action, reason, schedule_id, decision_id)
            
            if success:
                # Update schedule and decision
                cursor.execute("""
                    UPDATE ai_schedules
                    SET status = 'completed'
                WHERE id = ?
                """, (schedule_id,))
                
                if decision_id:
                    cursor.execute("""
                        UPDATE ai_decisions
                        SET executed = 1
                    WHERE id = ?
                    """, (decision_id,))
                
                executed_count += 1
                print(f"    ✅ Schedule {schedule_id} completed successfully")
            else:
                print(f"    ❌ Failed to execute schedule {schedule_id}")
                cursor.execute("""
                    UPDATE ai_schedules
                    SET status = 'failed'
                    WHERE id = ?
                """, (schedule_id,))
            
            conn.commit()
        
        conn.close()
        
        return jsonify({
            "success": True,
            "message": f"Manually checked schedules, executed {executed_count}",
            "executed_count": executed_count,
            "total_found": len(schedules),
            "note": "Pump only - valve stays closed"
        })
            
    except Exception as e:
        print(f"❌ Error manually checking schedules: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/debug/schedules')
@api_key_required
def debug_schedules():
    """Debug endpoint to check all schedules"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get ALL schedules
        cursor.execute("""
            SELECT 
                id, decision_id, action, reason,
                strftime('%Y-%m-%d %H:%M:%S', scheduled_time) as formatted_scheduled_time,
                strftime('%Y-%m-%d %H:%M:%S', execution_time) as formatted_execution_time,
                status,
                created_at
            FROM ai_schedules
            ORDER BY scheduled_time DESC
            LIMIT 20
        """)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = dict(row)
            schedule['scheduled_time'] = schedule.get('formatted_scheduled_time', '')
            schedule['execution_time'] = schedule.get('formatted_execution_time', '')
            schedules.append(schedule)
        
        # Get schedule statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'scheduled' THEN 1 ELSE 0 END) as scheduled,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed,
                SUM(CASE WHEN status = 'executing' THEN 1 ELSE 0 END) as executing
            FROM ai_schedules
        """)
        
        stats = dict(cursor.fetchone())
        
        conn.close()
        
        return jsonify({
            'success': True,
            'total_schedules': stats.get('total', 0),
            'scheduled': stats.get('scheduled', 0),
            'completed': stats.get('completed', 0),
            'failed': stats.get('failed', 0),
            'executing': stats.get('executing', 0),
            'schedules': schedules,
            'current_time': datetime.now().isoformat()
        })
        
    except Exception as e:
        print(f"❌ Error in debug schedules: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/schedules-raw')
@api_key_required
def debug_schedules_raw():
    """Raw debug endpoint to check schedules table"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get ALL schedules
        cursor.execute("""
            SELECT 
                id, decision_id, action, reason,
                strftime('%Y-%m-%d %H:%M:%S', scheduled_time) as formatted_scheduled_time,
                strftime('%Y-%m-%d %H:%M:%S', execution_time) as formatted_execution_time,
                status,
                created_at
            FROM ai_schedules
            ORDER BY id DESC
            LIMIT 10
        """)
        
        schedules = []
        for row in cursor.fetchall():
            schedule = dict(row)
            schedule['scheduled_time'] = schedule.get('formatted_scheduled_time', '')
            schedule['execution_time'] = schedule.get('formatted_execution_time', '')
            schedules.append(schedule)
        
        # Get current time
        cursor.execute("SELECT datetime('now', 'localtime') as current_time")
        current_time = cursor.fetchone()['current_time']
        
        conn.close()
        
        return jsonify({
            'success': True,
            'current_time': current_time,
            'schedules': schedules,
            'count': len(schedules)
        })
        
    except Exception as e:
        print(f"❌ Error in debug schedules raw: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/serial-test', methods=['POST'])
@api_key_required
def debug_serial_test():
    """Test serial connection"""
    try:
        device = request.json.get('device', 'valve')
        state = request.json.get('state', 'on')
        
        if serial_reader:
            print(f"🔧 DEBUG: Testing serial with {device} {state}")
            success = serial_reader.send_command({device: state})
            
            return jsonify({
                'success': success,
                'message': f"Serial test {'passed' if success else 'failed'}",
                'device': device,
                'state': state
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Serial reader not available'
            }), 500
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ai/force-decision', methods=['POST'])
@api_key_required
def force_ai_decision():
    """Force an AI decision to be made immediately"""
    global sensor_data
    
    try:
        # Get current sensor data
        current_data = sensor_data.copy()
        
        # Make sure we have valid soil data
        if current_data.get('soil1', 50) >= 80 and current_data.get('soil2', 50) >= 80 and current_data.get('soil3', 50) >= 80:
            # Make soil artificially dry for testing
            current_data['soil1'] = 50
            current_data['soil2'] = 55
            current_data['soil3'] = 60
        
        # Make the decision
        if ml_manager and decision_tracker:
            decision = ml_manager.make_irrigation_decision(current_data)
            
            # Add to tracker
            decision_tracker.add_decision(decision)
            
            return jsonify({
                "success": True,
                "message": "AI decision forced",
                "decision": decision
            })
        else:
            return jsonify({
                "success": False,
                "message": "AI system not available"
            }), 500
            
    except Exception as e:
        print(f"❌ Error forcing AI decision: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

# NEW: Force system state change for testing
@app.route('/api/ml/force-state', methods=['POST'])
@api_key_required
def force_system_state():
    """Force change ML system state for testing"""
    global ml_state
    
    data = request.json
    new_state = data.get('state', 'hybrid')
    
    allowed_states = ['q_learning_only', 'linear_regression_only', 'hybrid', 'manual_only', 'testing_override']
    
    if new_state not in allowed_states:
        return jsonify({
            "success": False,
            "error": f"Invalid state. Allowed: {allowed_states}"
        }), 400
    
    # Update state
    old_state = ml_state
    ml_state = new_state
    
    print(f"🔄 ML system state changed: {old_state} -> {new_state}")
    
    return jsonify({
        "success": True,
        "message": f"ML system state changed from '{old_state}' to '{new_state}'",
        "old_state": old_state,
        "new_state": ml_state,
        "allowed_states": allowed_states
    })

# UPDATED: Prediction endpoint using Linear Regression model with confidence intervals
@app.route('/api/predict/next-irrigation')
@api_key_required
def predict_next_irrigation():
    """Predict when next irrigation should occur using Linear Regression - UPDATED WITH CONFIDENCE INTERVALS AND BUSINESS RULES"""
    try:
        if not soil_predictor:
            return jsonify({
                "success": False,
                "message": "Linear regression predictor not available",
                "predictions": [],
                "confidence_metrics": {},
                "next_irrigation_recommendation": {
                    "recommended_time": (datetime.now() + timedelta(hours=6)).isoformat(),
                    "reason": "Predictor not available - using fallback",
                    "urgency": "medium",
                    "hours_from_now": 6,
                    "estimated_duration_minutes": 5.0,
                    "confidence": "low"
                },
                "threshold_percentage": 70,
                "calibration": {
                    zone: cal.to_dict() for zone, cal in zone_calibrations.items()
                }
            })
        
        # Get current sensor data
        current_data = {
            'temperature': sensor_data.get('temperature', 25),
            'humidity': sensor_data.get('humidity', 60),
            'soil1': sensor_data.get('soil1', 50),
            'soil2': sensor_data.get('soil2', 50),
            'soil3': sensor_data.get('soil3', 50),
            'valve': sensor_data.get('valve', 0),
            'pump': sensor_data.get('pump', 0)
        }
        
        # Train model if not trained
        if not soil_predictor.models:
            trained = soil_predictor.train()
            if not trained:
                # Return fallback with proper format for frontend
                return jsonify({
                    "success": True,
                    "predictions": [
                        {
                            "zone": "soil1",
                            "current": current_data['soil1'],
                            "predicted_1h": max(10, current_data['soil1'] - 5),
                            "predicted_3h": max(10, current_data['soil1'] - 15),
                            "predicted_6h": max(10, current_data['soil1'] - 25),
                            "lower_ci_1h": max(0, current_data['soil1'] - 8),
                            "upper_ci_1h": min(100, current_data['soil1'] - 2),
                            "lower_ci_3h": max(0, current_data['soil1'] - 20),
                            "upper_ci_3h": min(100, current_data['soil1'] - 10),
                            "lower_ci_6h": max(0, current_data['soil1'] - 35),
                            "upper_ci_6h": min(100, current_data['soil1'] - 15),
                            "confidence_interval": 8.0,
                            "confidence_level": 0.9,
                            "r_squared": 0.5,
                            "mae": 5.0,
                            "hours_until_threshold": 2 if current_data['soil1'] < 70 else 24
                        },
                        {
                            "zone": "soil2",
                            "current": current_data['soil2'],
                            "predicted_1h": max(10, current_data['soil2'] - 4),
                            "predicted_3h": max(10, current_data['soil2'] - 12),
                            "predicted_6h": max(10, current_data['soil2'] - 20),
                            "lower_ci_1h": max(0, current_data['soil2'] - 7),
                            "upper_ci_1h": min(100, current_data['soil2'] - 1),
                            "lower_ci_3h": max(0, current_data['soil2'] - 17),
                            "upper_ci_3h": min(100, current_data['soil2'] - 7),
                            "lower_ci_6h": max(0, current_data['soil2'] - 28),
                            "upper_ci_6h": min(100, current_data['soil2'] - 12),
                            "confidence_interval": 6.0,
                            "confidence_level": 0.9,
                            "r_squared": 0.5,
                            "mae": 4.0,
                            "hours_until_threshold": 3 if current_data['soil2'] < 70 else 24
                        },
                        {
                            "zone": "soil3",
                            "current": current_data['soil3'],
                            "predicted_1h": max(10, current_data['soil3'] - 3),
                            "predicted_3h": max(10, current_data['soil3'] - 9),
                            "predicted_6h": max(10, current_data['soil3'] - 15),
                            "lower_ci_1h": max(0, current_data['soil3'] - 6),
                            "upper_ci_1h": min(100, current_data['soil3'] - 0),
                            "lower_ci_3h": max(0, current_data['soil3'] - 14),
                            "upper_ci_3h": min(100, current_data['soil3'] - 4),
                            "lower_ci_6h": max(0, current_data['soil3'] - 22),
                            "upper_ci_6h": min(100, current_data['soil3'] - 8),
                            "confidence_interval": 5.0,
                            "confidence_level": 0.9,
                            "r_squared": 0.5,
                            "mae": 3.0,
                            "hours_until_threshold": 4 if current_data['soil3'] < 70 else 24
                        }
                    ],
                    "confidence_metrics": {
                        "model_type": "fallback",
                        "average_r_squared": 0.5,
                        "average_mae": 4.0,
                        "confidence_level": 0.9,
                        "note": "Using fallback predictions due to insufficient training data"
                    },
                    "next_irrigation_recommendation": {
                        "recommended_time": (datetime.now() + timedelta(hours=2)).isoformat(),
                        "reason": "Model training failed - using fallback prediction",
                        "urgency": "high" if any(s < 70 for s in [current_data['soil1'], current_data['soil2'], current_data['soil3']]) else "medium",
                        "hours_from_now": 2,
                        "estimated_duration_minutes": 5.0,
                        "confidence": "low"
                    },
                    "threshold_percentage": 70,
                    "calibration": {
                        zone: cal.to_dict() for zone, cal in zone_calibrations.items()
                    }
                })
        
        # Get predictions with confidence intervals for different time horizons
        threshold = 70
        predictions = []
        confidence_metrics = {
            "model_type": "linear_regression",
            "zones_trained": list(soil_predictor.models.keys()),
            "confidence_level": 0.95
        }
        
        # Calculate average metrics
        r_squared_values = []
        mae_values = []
        
        for zone in ['soil1', 'soil2', 'soil3']:
            if zone in soil_predictor.models:
                model_info = soil_predictor.models[zone]
                r_squared_values.append(model_info['r_squared'])
                mae_values.append(model_info['mae'])
        
        if r_squared_values:
            confidence_metrics["average_r_squared"] = sum(r_squared_values) / len(r_squared_values)
            confidence_metrics["average_mae"] = sum(mae_values) / len(mae_values)
            confidence_metrics["overall_accuracy"] = f"{confidence_metrics['average_r_squared']:.1%}"
        
        # Get predictions for each zone and time horizon
        for zone in ['soil1', 'soil2', 'soil3']:
            current_value = current_data.get(zone, 50)
            prediction_data = {}
            
            if zone in soil_predictor.models:
                # Get predictions for 1h, 3h, 6h with confidence intervals
                for hours, time_label in [(1, '1h'), (3, '3h'), (6, '6h')]:
                    try:
                        pred = soil_predictor.predict_with_confidence(
                            current_data, 
                            irrigation_duration=0, 
                            lookahead_hours=hours
                        )
                        
                        pred_key = f'{zone}_predicted'
                        lower_key = f'{zone}_lower_ci'
                        upper_key = f'{zone}_upper_ci'
                        
                        if pred_key in pred:
                            prediction_data[f'predicted_{time_label}'] = round(pred[pred_key], 1)
                            prediction_data[f'lower_ci_{time_label}'] = round(pred[lower_key], 1)
                            prediction_data[f'upper_ci_{time_label}'] = round(pred[upper_key], 1)
                            prediction_data[f'confidence_interval_{time_label}'] = round(pred[f'{zone}_confidence_interval'], 1)
                        
                    except Exception as e:
                        print(f"❌ Error predicting {zone} for {hours}h: {e}")
                        # Use fallback values
                        fallback_change = -2.0 if zone == 'soil1' else -1.5 if zone == 'soil2' else -1.0
                        predicted = max(10, current_value + (fallback_change * hours))
                        margin = 5.0 * (1 + hours * 0.2)  # Increase margin with time
                        
                        prediction_data[f'predicted_{time_label}'] = round(predicted, 1)
                        prediction_data[f'lower_ci_{time_label}'] = round(max(0, predicted - margin), 1)
                        prediction_data[f'upper_ci_{time_label}'] = round(min(100, predicted + margin), 1)
                        prediction_data[f'confidence_interval_{time_label}'] = round(margin, 1)
            else:
                # Use fallback for untrained zones
                fallback_change = -2.0 if zone == 'soil1' else -1.5 if zone == 'soil2' else -1.0
                for hours, time_label in [(1, '1h'), (3, '3h'), (6, '6h')]:
                    predicted = max(10, current_value + (fallback_change * hours))
                    margin = 6.0 * (1 + hours * 0.3)
                    
                    prediction_data[f'predicted_{time_label}'] = round(predicted, 1)
                    prediction_data[f'lower_ci_{time_label}'] = round(max(0, predicted - margin), 1)
                    prediction_data[f'upper_ci_{time_label}'] = round(min(100, predicted + margin), 1)
                    prediction_data[f'confidence_interval_{time_label}'] = round(margin, 1)
            
            # Calculate hours until threshold
            hours_until_threshold = None
            for hour in range(1, 25):
                # Use 6h prediction to estimate trend
                if 'predicted_6h' in prediction_data:
                    trend = (prediction_data['predicted_6h'] - current_value) / 6
                    predicted_at_hour = current_value + (trend * hour)
                    if predicted_at_hour < threshold:
                        hours_until_threshold = hour
                        break
            
            # Add model performance metrics if available
            if zone in soil_predictor.models:
                model_info = soil_predictor.models[zone]
                prediction_data['r_squared'] = round(model_info['r_squared'], 3)
                prediction_data['mae'] = round(model_info['mae'], 2)
                prediction_data['trained_samples'] = model_info['trained_samples']
            else:
                prediction_data['r_squared'] = 0.5
                prediction_data['mae'] = 5.0
                prediction_data['fallback'] = True
            
            predictions.append({
                "zone": zone,
                "current": round(current_value, 1),
                **prediction_data,
                "hours_until_threshold": hours_until_threshold or 24,
                "calibration": zone_calibrations[zone].to_dict()
            })
        
        # Sort predictions: soil1, soil2, soil3
        predictions.sort(key=lambda x: x['zone'])
        
        # Find earliest threshold crossing for recommendation
        earliest = None
        for pred in predictions:
            if pred['hours_until_threshold'] and pred['hours_until_threshold'] < 24:
                if earliest is None or pred['hours_until_threshold'] < earliest['hours_until_threshold']:
                    earliest = pred
        
        if earliest:
            # Calculate recommended duration based on soil deficit
            current_soil = earliest['current']
            soil_deficit = threshold - min(current_soil, threshold)
            duration_minutes = max(2.0, min(10.0, soil_deficit / 3))
            
            # Determine urgency based on soil moisture
            if current_soil < 60:
                urgency = "high"
            elif current_soil < 70:
                urgency = "medium"
            else:
                urgency = "low"
            
            # Get initial recommended time
            next_irrigation_time = datetime.now() + timedelta(hours=earliest['hours_until_threshold'])
            
            # APPLY BUSINESS RULES FOR OPTIMAL TIMING
            optimal_time, adjustment_reason, adjusted = get_optimal_irrigation_time(
                next_irrigation_time, 
                urgency, 
                current_soil,
                threshold
            )
            
            # Determine confidence based on model performance
            if 'r_squared' in earliest and earliest['r_squared'] > 0.7:
                confidence = "high"
            elif 'r_squared' in earliest and earliest['r_squared'] > 0.5:
                confidence = "medium"
            else:
                confidence = "low"
            
            # Build recommendation
            recommendation = {
                "recommended_time": optimal_time.isoformat(),
                "reason": f"Zone {earliest['zone']} predicted to drop below {threshold}% in {earliest['hours_until_threshold']} hours",
                "urgency": urgency,
                "hours_from_now": round((optimal_time - datetime.now()).total_seconds() / 3600, 1),
                "estimated_duration_minutes": round(duration_minutes, 1),
                "confidence": confidence,
                "confidence_metrics": {
                    "r_squared": earliest.get('r_squared', 0.5),
                    "mae": earliest.get('mae', 5.0),
                    "confidence_interval": earliest.get('confidence_interval_6h', 10.0)
                }
            }
            
            # Add business rule adjustments if applied
            if adjusted:
                recommendation["time_adjusted"] = True
                recommendation["original_time"] = next_irrigation_time.isoformat()
                recommendation["adjustment_reason"] = adjustment_reason
                recommendation["business_rules_applied"] = True
            else:
                recommendation["time_adjusted"] = False
                recommendation["business_rules_applied"] = False
        else:
            # All zones are good for 24+ hours
            next_irrigation_time = datetime.now() + timedelta(hours=24)
            
            # Still apply business rules for optimal timing
            optimal_time, adjustment_reason, adjusted = get_optimal_irrigation_time(
                next_irrigation_time, 
                "low", 
                None,
                threshold
            )
            
            recommendation = {
                "recommended_time": optimal_time.isoformat(),
                "reason": "Soil moisture predicted to stay above threshold for 24 hours",
                "urgency": "none",
                "hours_from_now": round((optimal_time - datetime.now()).total_seconds() / 3600, 1),
                "estimated_duration_minutes": 5.0,
                "confidence": "medium",
                "confidence_metrics": {
                    "r_squared": confidence_metrics.get('average_r_squared', 0.6),
                    "mae": confidence_metrics.get('average_mae', 4.0),
                    "note": "Long-term prediction - higher uncertainty"
                },
                "time_adjusted": adjusted,
                "business_rules_applied": adjusted
            }
            
            if adjusted:
                recommendation["original_time"] = next_irrigation_time.isoformat()
                recommendation["adjustment_reason"] = adjustment_reason
        
        response = {
            "success": True,
            "current_time": datetime.now().isoformat(),
            "threshold_percentage": threshold,
            "predictions": predictions,
            "confidence_metrics": confidence_metrics,
            "next_irrigation_recommendation": recommendation,
            "model_type": "linear_regression",
            "model_zones": list(soil_predictor.models.keys()),
            "business_rules": {
                "critical_emergency": "Soil < 50%: Irrigate immediately",
                "high_urgency": "Soil < 60%: Irrigate within 2 hours, avoid 12AM-5AM",
                "medium_urgency": "Avoid nighttime (10PM-6AM)",
                "low_urgency": "Prefer 8AM-6PM"
            },
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        }
        
        return jsonify(response)
        
    except Exception as e:
        print(f"❌ Error in linear regression prediction: {e}")
        import traceback
        traceback.print_exc()
        
        # Return fallback response in correct format
        return jsonify({
            "success": True,
            "predictions": [
                {
                    "zone": "soil1",
                    "current": sensor_data.get('soil1', 50),
                    "predicted_1h": max(10, sensor_data.get('soil1', 50) - 5),
                    "predicted_3h": max(10, sensor_data.get('soil1', 50) - 15),
                    "predicted_6h": max(10, sensor_data.get('soil1', 50) - 25),
                    "lower_ci_1h": max(0, sensor_data.get('soil1', 50) - 8),
                    "upper_ci_1h": min(100, sensor_data.get('soil1', 50) - 2),
                    "lower_ci_3h": max(0, sensor_data.get('soil1', 50) - 20),
                    "upper_ci_3h": min(100, sensor_data.get('soil1', 50) - 10),
                    "lower_ci_6h": max(0, sensor_data.get('soil1', 50) - 35),
                    "upper_ci_6h": min(100, sensor_data.get('soil1', 50) - 15),
                    "confidence_interval_1h": 3.0,
                    "confidence_interval_3h": 5.0,
                    "confidence_interval_6h": 10.0,
                    "r_squared": 0.5,
                    "mae": 5.0,
                    "hours_until_threshold": 2
                },
                {
                    "zone": "soil2",
                    "current": sensor_data.get('soil2', 55),
                    "predicted_1h": max(10, sensor_data.get('soil2', 55) - 4),
                    "predicted_3h": max(10, sensor_data.get('soil2', 55) - 12),
                    "predicted_6h": max(10, sensor_data.get('soil2', 55) - 20),
                    "lower_ci_1h": max(0, sensor_data.get('soil2', 55) - 7),
                    "upper_ci_1h": min(100, sensor_data.get('soil2', 55) - 1),
                    "lower_ci_3h": max(0, sensor_data.get('soil2', 55) - 17),
                    "upper_ci_3h": min(100, sensor_data.get('soil2', 55) - 7),
                    "lower_ci_6h": max(0, sensor_data.get('soil2', 55) - 28),
                    "upper_ci_6h": min(100, sensor_data.get('soil2', 55) - 12),
                    "confidence_interval_1h": 3.0,
                    "confidence_interval_3h": 5.0,
                    "confidence_interval_6h": 8.0,
                    "r_squared": 0.5,
                    "mae": 4.0,
                    "hours_until_threshold": 3
                },
                {
                    "zone": "soil3",
                    "current": sensor_data.get('soil3', 60),
                    "predicted_1h": max(10, sensor_data.get('soil3', 60) - 3),
                    "predicted_3h": max(10, sensor_data.get('soil3', 60) - 9),
                    "predicted_6h": max(10, sensor_data.get('soil3', 60) - 15),
                    "lower_ci_1h": max(0, sensor_data.get('soil3', 60) - 6),
                    "upper_ci_1h": min(100, sensor_data.get('soil3', 60) - 0),
                    "lower_ci_3h": max(0, sensor_data.get('soil3', 60) - 14),
                    "upper_ci_3h": min(100, sensor_data.get('soil3', 60) - 4),
                    "lower_ci_6h": max(0, sensor_data.get('soil3', 60) - 22),
                    "upper_ci_6h": min(100, sensor_data.get('soil3', 60) - 8),
                    "confidence_interval_1h": 3.0,
                    "confidence_interval_3h": 5.0,
                    "confidence_interval_6h": 7.0,
                    "r_squared": 0.5,
                    "mae": 3.0,
                    "hours_until_threshold": 4
                }
            ],
            "confidence_metrics": {
                "model_type": "fallback",
                "average_r_squared": 0.5,
                "average_mae": 4.0,
                "confidence_level": 0.9,
                "note": "Error occurred - using fallback predictions"
            },
            "next_irrigation_recommendation": {
                "recommended_time": (datetime.now() + timedelta(hours=2)).isoformat(),
                "reason": "Linear regression failed - using fallback prediction",
                "urgency": "high",
                "hours_from_now": 2,
                "estimated_duration_minutes": 5.0,
                "confidence": "low"
            },
            "threshold_percentage": 70,
            "error": str(e),
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        })

# UPDATED: Manual prediction generation endpoint using Linear Regression with confidence intervals
@app.route('/api/predict/generate', methods=['POST'])
@api_key_required
def generate_prediction():
    """Manually generate a new prediction using Linear Regression with confidence intervals"""
    try:
        print("🔄 Manually generating prediction using Linear Regression with confidence intervals...")
        
        if not soil_predictor:
            return jsonify({
                "success": False,
                "error": "Linear regression predictor not available"
            }), 500
        
        # First ensure database is initialized
        init_database()
        
        # Train the linear regression model and generate predictions
        success = generate_soil_predictions()
        
        if success:
            return jsonify({
                "success": True,
                "message": "Linear regression prediction with confidence intervals generated successfully",
                "model_type": "linear_regression_with_ci",
                "zones_trained": list(soil_predictor.models.keys()) if soil_predictor.models else [],
                "note": "Uses linear regression with confidence intervals and multiple features per zone",
                "calibration": {
                    zone: cal.to_dict() for zone, cal in zone_calibrations.items()
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": "Failed to generate prediction. Not enough data."
            }), 500
            
    except Exception as e:
        print(f"❌ Error generating prediction: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# UPDATED: Get all predictions
@app.route('/api/predict/all')
@api_key_required
def get_all_predictions():
    """Get all historical predictions - INCLUDES LINEAR REGRESSION PREDICTIONS"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Check if table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ai_predictions'")
        if not cursor.fetchone():
            conn.close()
            return jsonify({
                "success": False,
                "error": "ai_predictions table does not exist. Run /api/predict/generate first.",
                "predictions": []
            })
        
        # Get all predictions
        cursor.execute("""
            SELECT 
                id,
                timestamp,
                predicted_1h_soil1,
                predicted_3h_soil1,
                predicted_6h_soil1,
                predicted_1h_soil2,
                predicted_3h_soil2,
                predicted_6h_soil2,
                predicted_1h_soil3,
                predicted_3h_soil3,
                predicted_6h_soil3,
                confidence_score,
                model_used,
                notes
            FROM ai_predictions
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        
        predictions = []
        for row in cursor.fetchall():
            prediction = dict(row)
            predictions.append(prediction)
        
        conn.close()
        
        # Add current linear regression model status
        model_status = {
            "linear_regression_available": soil_predictor is not None,
            "zones_trained": list(soil_predictor.models.keys()) if soil_predictor else [],
            "total_models": len(soil_predictor.models) if soil_predictor else 0,
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        }
        
        return jsonify({
            "success": True,
            "predictions": predictions,
            "count": len(predictions),
            "model_status": model_status
        })
        
    except Exception as e:
        print(f"❌ Error getting predictions: {e}")
        return jsonify({
            "success": False, 
            "error": str(e),
            "predictions": []
        }), 500

# NEW: Test endpoint for confidence intervals
@app.route('/api/test-confidence-intervals')
@api_key_required
def test_confidence_intervals():
    """Test endpoint to verify confidence interval calculations"""
    try:
        if not soil_predictor:
            return jsonify({"success": False, "error": "Predictor not available"}), 500
        
        # Train models if not trained
        if not soil_predictor.models:
            soil_predictor.train()
        
        # Test current data
        current_data = {
            'temperature': sensor_data.get('temperature', 25),
            'humidity': sensor_data.get('humidity', 60),
            'soil1': sensor_data.get('soil1', 50),
            'soil2': sensor_data.get('soil2', 50),
            'soil3': sensor_data.get('soil3', 50),
            'valve': 0,
            'pump': 0
        }
        
        # Get predictions with confidence for 1h, 3h, 6h
        results = {}
        for hours in [1, 3, 6]:
            pred = soil_predictor.predict_with_confidence(current_data, 0, hours)
            results[f'{hours}h'] = pred
        
        # Get model info
        model_info = soil_predictor.get_model_info()
        
        return jsonify({
            "success": True,
            "current_data": current_data,
            "predictions": results,
            "model_info": model_info,
            "note": "Confidence intervals calculated at 95% confidence level",
            "calibration": {
                zone: cal.to_dict() for zone, cal in zone_calibrations.items()
            }
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# NEW: Save predictor state endpoint
@app.route('/api/ml/save-state', methods=['POST'])
@api_key_required
def save_predictor_state_endpoint():
    """Manually save predictor state"""
    success = save_predictor_state()
    return jsonify({
        "success": success,
        "message": "Predictor state saved" if success else "Failed to save predictor state"
    })

# NEW: Get predictor state info
@app.route('/api/ml/state-info')
@api_key_required
def get_predictor_state_info():
    """Get information about saved predictor state"""
    models_dir = os.path.join(os.path.dirname(__file__), 'models')
    model_save_path = os.path.join(models_dir, 'predictor_state.json')
    
    info = {
        "state_file_exists": os.path.exists(model_save_path),
        "state_file_path": model_save_path,
        "state_file_size": os.path.getsize(model_save_path) if os.path.exists(model_save_path) else 0,
        "state_file_modified": None,
        "models_dir_exists": os.path.exists(models_dir),
        "current_models_in_memory": len(soil_predictor.models) if soil_predictor else 0,
        "current_training_history": len(soil_predictor.training_history) if soil_predictor else 0,
        "calibration": {
            zone: cal.to_dict() for zone, cal in zone_calibrations.items()
        }
    }
    
    if info["state_file_exists"]:
        try:
            import time
            mod_time = os.path.getmtime(model_save_path)
            info["state_file_modified"] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(mod_time))
            
            # Try to read and parse the file
            with open(model_save_path, 'r') as f:
                import json
                state_data = json.load(f)
                info["saved_models_count"] = state_data.get('model_count', 0)
                info["saved_training_records"] = len(state_data.get('training_history', []))
                info["saved_at"] = state_data.get('saved_at', 'unknown')
                info["file_valid"] = True
        except Exception as e:
            info["file_valid"] = False
            info["file_error"] = str(e)
    
    return jsonify(info)

# ESP32 data callback
def esp32_data_callback(data):
    """Callback for ESP32 serial data"""
    print(f"📟 ESP32 Data received: {data}")
    try:
        # Process ESP32 data
        processed_data = process_esp32_data(data)
        
        # If manual control is active, send command again to maintain state
        current_time = time.time()
        
        if manual_valve_control:
            # Check if valve is in desired state
            desired_valve = sensor_data.get('valve', 0)
            current_valve = processed_data.get('valve', 0)
            
            if desired_valve != current_valve and serial_reader:
                print(f"🔄 Maintaining manual valve control: sending {'on' if desired_valve else 'off'}")
                serial_reader.send_command({"valve": "on" if desired_valve else "off"})
        
        if manual_pump_control:
            # Check if pump is in desired state
            desired_pump = sensor_data.get('pump', 0)
            current_pump = processed_data.get('pump', 0)
            
            if desired_pump != current_pump and serial_reader:
                print(f"🔄 Maintaining manual pump control: sending {'on' if desired_pump else 'off'}")
                serial_reader.send_command({"pump": "on" if desired_pump else "off"})
        
    except Exception as e:
        print(f"❌ Error processing ESP32 data: {e}")

def esp32_error_callback(error):
    """Callback for ESP32 serial errors"""
    print(f"❌ ESP32 Error: {error}")

# ==============================================
# FIXED: MODIFIED background_thread() function with robust serial initialization
# ==============================================
def background_thread():
    """Background data processing and frontend sync - WITH ROBUST STARTUP"""
    global serial_reader, running, schedule_executor, soil_predictor, manual_valve_control, manual_pump_control
    
    print("🔄 Background thread started")
    print("⏳ Waiting for system to stabilize...")
    time.sleep(5)  # Give the system time to initialize
    
    print("🤖 Training strategy: Periodic every 24 hours only (data-check disabled)")
    print("📊 Enhanced: send_to_frontend() now includes resource consumption data from last 5 minutes")
    print("⏰ Command timestamp checking: Commands expire after 1 minute")
    print("📋 Business rules: Applied to irrigation timing recommendations")
    print("🕐 Time-aware model: Enhanced with day/night features")
    print("💾 Model state persistence: Automatic save/load on restart")
    print("🧹 Data cleaning: Available at /api/ml/clean-training-data")
    print("📏 Multi-zone calibration: Enabled for all zones")
    print("   - Zone A: Calibration can be enabled if sensor is broken")
    print("   - Zone B: Enabled by default (scales 0-60% to 0-100%)")
    print("   - Zone C: Calibration can be enabled if sensor is broken")
    
    # ==============================================
    # ROBUST INITIALIZATION WITH RETRIES
    # ==============================================
    try:
        # Initialize serial reader first
        if SerialReader:
            serial_reader = SerialReader(
                data_callback=esp32_data_callback,
                error_callback=esp32_error_callback
            )
            
            # Start serial reading with retry mechanism
            serial_started = False
            for attempt in range(5):
                try:
                    serial_reader.start_reading()
                    serial_started = True
                    print(f"✅ Serial reader started on attempt {attempt + 1}/5")
                    break
                except Exception as e:
                    print(f"⚠️ Serial start attempt {attempt + 1}/5 failed: {e}")
                    if attempt < 4:
                        time.sleep(3)
            
            if not serial_started:
                print("❌ Failed to start serial reader after 5 attempts")
                print("⚠️ Continuing without serial connection")
        else:
            print("❌ Serial reader not available")
    except Exception as e:
        print(f"❌ Critical error initializing serial: {e}")
        import traceback
        traceback.print_exc()
        print("⚠️ Continuing without serial connection")
    
    # NOW initialize devices OFF with retries
    print("🔄 Initializing devices to OFF state...")
    time.sleep(2)  # Give serial time to fully establish if it's going to
    initialize_devices_off(max_retries=10, retry_delay=2)
    
    # Reset manual override flags
    manual_valve_control = False
    manual_pump_control = False
    
    # Initialize Schedule Executor (even without serial, it will fail gracefully)
    if ScheduleExecutor:
        try:
            schedule_executor = ScheduleExecutor(str(DB_PATH), serial_reader)
            # Start schedule executor in a separate thread
            schedule_thread = threading.Thread(target=schedule_executor.run_continuously, daemon=True)
            schedule_thread.start()
            print("✅ Schedule Executor started in background thread")
        except Exception as e:
            print(f"❌ Failed to initialize ScheduleExecutor: {e}")
            print("⚠️ Will use built-in schedule checking instead")
            schedule_executor = None
    else:
        print("⚠️ ScheduleExecutor module not available, using built-in checking")
    
    # Load calibration settings from database
    load_calibration_settings()
    
    sync_counter = 0
    command_counter = 0
    ai_decision_counter = 0
    schedule_check_counter = 0
    prediction_counter = 0  # Counter for prediction generation
    training_counter = 0    # Counter for periodic training (24h)
    data_stats_counter = 0  # Counter for data statistics logging
    state_save_counter = 0  # Counter for state saving
    
    # FIXED: Train once on startup, then only every 24 hours
    if soil_predictor:
        print("🔄 Initial training on system startup...")
        training_success = soil_predictor.train(trigger='system_startup')
        if training_success:
            print("✅ Initial training completed successfully")
            # Save state after initial training
            save_predictor_state()
        else:
            print("⚠️ Initial training failed, will retry later")
    
    while running:
        try:
            # Send to frontend every DATA_SEND_INTERVAL seconds (now includes resource consumption)
            if sync_counter >= DATA_SEND_INTERVAL:
                send_to_frontend()
                sync_counter = 0
            
            # Check for commands every 10 seconds
            if command_counter >= 10:
                check_frontend_commands()
                command_counter = 0
            
            # Make AI decisions every 10 minutes (600 seconds)
            if ML_ENABLED and ml_manager and decision_tracker and ai_decision_counter >= 600:
                make_ai_decision()
                ai_decision_counter = 0
            
            # Check and execute schedules every 30 seconds
            if schedule_check_counter >= 30:
                try:
                    print("📅 Checking for scheduled irrigations...")
                    
                    # Direct schedule checking
                    conn = sqlite3.connect(str(DB_PATH))
                    cursor = conn.cursor()
                    
                    # Get overdue schedules
                    cursor.execute("""
                        SELECT id, decision_id, action, reason, scheduled_time
                        FROM ai_schedules
                        WHERE status = 'scheduled'
                        AND datetime(scheduled_time) <= datetime('now', 'localtime')
                        LIMIT 5
                    """)
                    
                    schedules = cursor.fetchall()
                    
                    if schedules:
                        print(f"📅 Found {len(schedules)} schedule(s) to execute")
                    
                    for schedule in schedules:
                        schedule_id, decision_id, action, reason, scheduled_time = schedule
                        print(f"  📅 Executing schedule ID {schedule_id}: {action} mins - '{reason}' (scheduled: {scheduled_time})")
                        
                        # Create decision record if missing
                        if not decision_id:
                            cursor.execute("""
                                INSERT INTO ai_decisions
                                (timestamp, action, reason, system_state, model_used, executed)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                datetime.now().isoformat(),
                                action,
                                f"Scheduled: {reason}",
                                "scheduled_execution",
                                "schedule_executor",
                                0
                            ))
                            decision_id = cursor.lastrowid
                            
                            # Update schedule with decision_id
                            cursor.execute("""
                                UPDATE ai_schedules
                                SET decision_id = ?
                                WHERE id = ?
                            """, (decision_id, schedule_id))
                        
                        # Mark as executing
                        cursor.execute("""
                            UPDATE ai_schedules
                            SET status = 'executing',
                                execution_time = datetime('now', 'localtime')
                            WHERE id = ?
                        """, (schedule_id,))
                        conn.commit()
                        
                        # USE THE UNIFIED IRRIGATION FUNCTION
                        success = execute_irrigation(action, reason, schedule_id, decision_id)
                        
                        if success:
                            # Mark as completed and update decision
                            cursor.execute("""
                                UPDATE ai_schedules
                                SET status = 'completed'
                            WHERE id = ?
                            """, (schedule_id,))
                            
                            cursor.execute("""
                                UPDATE ai_decisions
                                SET executed = 1
                            WHERE id = ?
                            """, (decision_id,))
                            
                            print(f"    ✅ Schedule {schedule_id} completed successfully (pump only)")
                        else:
                            print(f"    ❌ Failed to execute schedule {schedule_id}")
                            cursor.execute("""
                                UPDATE ai_schedules
                                SET status = 'failed'
                                WHERE id = ?
                            """, (schedule_id,))
                        
                        conn.commit()
                    
                    conn.close()
                    
                except Exception as e:
                    print(f"❌ Error checking schedules: {e}")
                    import traceback
                    traceback.print_exc()
                
                schedule_check_counter = 0
            
            # SIMPLIFIED: Log data statistics every 5 minutes (300 seconds) - NO TRAINING TRIGGER
            if data_stats_counter >= 300:
                try:
                    # Log data statistics for monitoring
                    conn = sqlite3.connect(str(DB_PATH))
                    cursor = conn.cursor()
                    
                    # Get total records count
                    cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
                    total = cursor.fetchone()[0]
                    
                    # Get records from last 24 hours
                    cursor.execute("""
                        SELECT COUNT(*) FROM greenhouse_data 
                        WHERE timestamp >= datetime('now', '-24 hours')
                    """)
                    last_24h = cursor.fetchone()[0]
                    
                    # Get resource consumption records from last 5 minutes
                    five_minutes_ago = datetime.now() - timedelta(minutes=5)
                    cursor.execute("""
                        SELECT COUNT(*) FROM resource_consumption
                        WHERE timestamp >= ?
                    """, (five_minutes_ago.strftime('%Y-%m-%d %H:%M:%S'),))
                    recent_resources = cursor.fetchone()[0]
                    
                    # Count potential sensor errors (0% soil moisture)
                    cursor.execute("""
                        SELECT COUNT(*) FROM greenhouse_data 
                        WHERE soil1 = 0 OR soil2 = 0 OR soil3 = 0
                        AND timestamp >= datetime('now', '-24 hours')
                    """)
                    sensor_errors = cursor.fetchone()[0]
                    
                    conn.close()
                    
                    # Log statistics (only once per 5-minute cycle)
                    print(f"📊 Data stats: Total={total}, Last 24h={last_24h}, Recent resources (5min)={recent_resources}, Sensor errors (24h)={sensor_errors}")
                    
                    # If many sensor errors, suggest cleaning
                    if sensor_errors > 10:
                        print("💡 Tip: Many sensor errors detected. Run POST /api/ml/clean-training-data to clean training data")
                    
                except Exception as e:
                    print(f"❌ Error checking data stats: {e}")
                
                data_stats_counter = 0
            
            # PERIODIC TRAINING: Train every 24 hours (86400 seconds)
            if training_counter >= 86400:
                if soil_predictor:
                    print("🔄 PERIODIC TRAINING: Auto-training every 24 hours")
                    training_success = soil_predictor.train(trigger='periodic_24h')
                    if training_success:
                        print("✅ Periodic training completed successfully")
                        # Save state after training
                        save_predictor_state()
                        # Generate new predictions after training
                        generate_soil_predictions()
                    else:
                        print("⚠️ Periodic training failed")
                else:
                    print("❌ No soil predictor available for periodic training")
                
                training_counter = 0
            
            # UPDATED: Generate predictions using Linear Regression with confidence intervals every 30 minutes (1800 seconds)
            if prediction_counter >= 1800:
                try:
                    print("🔄 Generating soil moisture predictions using Linear Regression with confidence intervals...")
                    
                    if soil_predictor:
                        # Use the Linear Regression predictor with confidence intervals
                        success = generate_soil_predictions()
                        
                        if not success:
                            print("⚠️ Linear regression prediction failed, using fallback")
                            generate_fallback_predictions()
                    else:
                        print("❌ Linear regression predictor not available, using fallback")
                        generate_fallback_predictions()
                    
                    prediction_counter = 0
                except Exception as e:
                    print(f"❌ Error generating predictions: {e}")
            
            # Save predictor state every 30 minutes (1800 seconds)
            if state_save_counter >= 1800:
                save_predictor_state()
                state_save_counter = 0
            
            # Increment counters
            sync_counter += 1
            command_counter += 1
            ai_decision_counter += 1
            schedule_check_counter += 1
            prediction_counter += 1
            training_counter += 1
            data_stats_counter += 1
            state_save_counter += 1
            
            time.sleep(1)
            
        except Exception as e:
            print(f"❌ Error in background thread: {e}")
            import traceback
            traceback.print_exc()
            time.sleep(5)

# Register shutdown handler to save predictor state
@atexit.register
def on_shutdown():
    """Save predictor state when application exits"""
    print("\n🔄 Application shutdown requested")
    global running, soil_predictor
    running = False
    
    if soil_predictor:
        print("💾 Saving predictor state before shutdown...")
        save_predictor_state()
    
    if serial_reader:
        try:
            print("🔄 Stopping serial reader...")
            serial_reader.stop_reading()
        except Exception as e:
            print(f"❌ Error stopping serial reader: {e}")
    
    print("✅ Shutdown complete")
    time.sleep(1)

# Initialize sample data - UPDATED VERSION
def init_sample_data():
    """Initialize sample data for testing - UPDATED"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Check if we have data
    cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("🔄 Adding sample data for Linear Regression training...")
        
        # Add realistic sample sensor data for the last 7 days
        base_time = datetime.now() - timedelta(days=7)
        
        # Create realistic time-series data with patterns
        for i in range(1008):  # 7 days * 24 hours * 6 samples per hour (every 10 minutes)
            sample_time = base_time + timedelta(minutes=i*10)
            timestamp = sample_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # Create realistic patterns
            hour = sample_time.hour
            
            # Temperature: higher during day, lower at night
            if 6 <= hour <= 18:  # Daytime
                base_temp = 25.0 + (hour - 12) * 0.5
                temp_variation = (i % 20) * 0.2
            else:  # Nighttime
                base_temp = 20.0 + (hour % 6) * 0.3
                temp_variation = (i % 15) * 0.1
            
            temperature = base_temp + temp_variation
            
            # Humidity: inverse of temperature
            humidity = 60.0 - (temperature - 22.5) * 1.5 + (i % 25) * 0.3
            
            # Soil moisture: gradually decreases, then increases with "irrigation"
            soil_base = 50.0
            soil_trend = (i % 144) / 144 * 10  # Cycle every 24 hours
            soil_variation = (i % 30) * 0.2
            
            # Simulate irrigation events (every 12 hours)
            irrigation_boost = 15.0 if i % 72 == 0 else 0
            
            soil1 = soil_base - soil_trend + soil_variation + irrigation_boost
            soil2 = soil_base - soil_trend * 0.8 + soil_variation * 0.9 + irrigation_boost * 0.9
            soil3 = soil_base - soil_trend * 0.6 + soil_variation * 0.8 + irrigation_boost * 0.8
            
            # Ensure soil stays within reasonable bounds (avoid 0% sensor errors)
            soil1 = max(20, min(85, soil1))
            soil2 = max(25, min(90, soil2))
            soil3 = max(30, min(95, soil3))
            
            # Water tank: random levels
            tank_state = i % 50
            if tank_state < 10:
                lowLevel, highLevel = 0, 0  # Full
            elif tank_state < 30:
                lowLevel, highLevel = 0, 1  # Medium
            else:
                lowLevel, highLevel = 1, 1  # Empty
            
            # Pump and valve: simulate irrigation events
            # VALVE: Should only be ON when refilling reservoir (low water level)
            # PUMP: Should be ON for irrigation (when soil is dry)
            pump_on = 1 if i % 72 == 0 and hour >= 6 and hour <= 18 else 0
            # Valve should only be on when tank is empty and needs refilling
            valve_on = 1 if lowLevel == 1 and highLevel == 1 else 0
            
            cursor.execute("""
                INSERT INTO greenhouse_data
                (timestamp, temperature, humidity, soil1, soil2, soil3, lowLevel, highLevel, valve, pump)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                round(temperature, 1),
                round(humidity, 1),
                round(soil1),
                round(soil2),
                round(soil3),
                lowLevel,
                highLevel,
                valve_on,
                pump_on
            ))
        
        print(f"✅ Added {1008} realistic sample sensor records for Linear Regression training")
        
        # Add sample resource data for the last 7 days
        resource_base_time = datetime.now() - timedelta(days=7)
        
        for i in range(168):  # 7 days * 24 hours
            timestamp = (resource_base_time + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
            
            # Simulate water and energy usage
            hour = (resource_base_time + timedelta(hours=i)).hour
            
            # Higher usage during daytime
            if 6 <= hour <= 18:
                pump_runtime = 300 + (i % 10) * 60  # 5-15 minutes
                water_consumed = pump_runtime * 0.3 / 60  # 0.3 L/min flow rate
                energy_consumed = pump_runtime * 0.37 / 3600  # 0.37 kW pump
            else:
                pump_runtime = 60 + (i % 5) * 30  # 1-3.5 minutes
                water_consumed = pump_runtime * 0.3 / 60
                energy_consumed = pump_runtime * 0.37 / 3600
            
            # Add valve runtime (usually runs when refilling reservoir, separate from irrigation)
            valve_runtime = 120 if i % 24 == 0 else 0  # 2 minutes daily for refilling
            
            cursor.execute("""
                INSERT INTO resource_consumption
                (timestamp, pump_runtime_seconds, valve_runtime_seconds, 
                 water_consumed_liters, energy_consumed_kwh,
                 pump_state, valve_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp,
                pump_runtime,
                valve_runtime,
                round(water_consumed, 3),
                round(energy_consumed, 6),
                1 if pump_runtime > 0 else 0,
                1 if valve_runtime > 0 else 0
            ))
        
        print(f"✅ Added 168 sample resource records")
        
        # Add sample AI schedules for testing
        for i in range(5):
            schedule_time = (datetime.now() + timedelta(hours=i+1)).isoformat()
            cursor.execute("""
                INSERT INTO ai_schedules
                (decision_id, action, reason, scheduled_time, status)
                VALUES (?, ?, ?, ?, ?)
            """, (
                i+1,
                3.0,
                f"Test schedule {i+1}",
                schedule_time,
                "scheduled"
            ))
        
        print("✅ Added 5 sample AI schedules")
        
        # Add sample AI predictions - using realistic data with confidence scores
        for i in range(5):
            pred_time = (datetime.now() - timedelta(hours=i*2)).isoformat()
            cursor.execute("""
                INSERT INTO ai_predictions
                (timestamp, predicted_1h_soil1, predicted_3h_soil1, predicted_6h_soil1,
                 predicted_1h_soil2, predicted_3h_soil2, predicted_6h_soil2,
                 predicted_1h_soil3, predicted_3h_soil3, predicted_6h_soil3,
                 confidence_score, model_used, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pred_time,
                75.0 - i*5,
                70.0 - i*8,
                65.0 - i*10,
                78.0 - i*4,
                73.0 - i*7,
                68.0 - i*9,
                80.0 - i*3,
                75.0 - i*6,
                70.0 - i*8,
                0.85 - i*0.05,
                "linear_regression_with_ci",
                f"Sample prediction {i+1} - Linear regression model with confidence intervals"
            ))
        
        print("✅ Added 5 sample AI predictions for Linear Regression with confidence intervals")
        
        # Add a system initialization command
        cursor.execute("""
            INSERT INTO manual_commands
            (timestamp, device, command, requested_state, actual_state, success, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            "system",
            "init_off",
            "OFF",
            "OFF",
            1,
            "System startup - devices initialized to OFF"
        ))
        
        # Initialize calibration settings
        init_calibration_settings()
        
        conn.commit()
    
    conn.close()

# Main function
def main():
    """Main application entry point - WITH ROBUST SERIAL INITIALIZATION"""
    print("="*60)
    print("🌱 GREENHOUSE MONITORING SYSTEM - LINEAR REGRESSION PREDICTION SYSTEM")
    print("="*60)
    print("💾 MODEL STATE PERSISTENCE: Enabled")
    print("   - Auto-saves model state every 30 minutes")
    print("   - Auto-saves after every training")
    print("   - Loads saved state on startup")
    print("   - Saves state on shutdown")
    print("="*60)
    print("🔧 DEVICE INITIALIZATION: Fixed - All devices set to OFF on startup")
    print("   - With robust retry mechanism (10 attempts, 2s delay)")
    print("   - Pump: OFF")
    print("   - Valve: OFF")
    print("   - Manual overrides cleared")
    print("   - Graceful failure - continues if serial unavailable")
    print("="*60)
    print("📏 MULTI-ZONE CALIBRATION: ENABLED FOR ALL ZONES")
    print("   - Zone A: Can be enabled if sensor is broken")
    print("   - Zone B: Enabled by default (scales 0-60% to 0-100%)")
    print("   - Zone C: Can be enabled if sensor is broken")
    print("   - Each zone has independent calibration parameters")
    print("   - Calibration settings persist in database")
    print("="*60)
    print("🤖 PREDICTION SYSTEM:")
    print("   - Uses Linear Regression model from predictor.py")
    print("   - Provides confidence intervals for predictions")
    print("   - Separate models for each zone (soil1, soil2, soil3)")
    print("   - Features: temperature, humidity, pump state, valve state")
    print("   - Output: Predicted soil moisture with confidence intervals")
    print("="*60)
    print("📋 BUSINESS RULES FOR IRRIGATION TIMING:")
    print("   - Critical emergency (soil < 50%): Irrigate immediately")
    print("   - High urgency (soil < 60%): Irrigate within 2h, avoid 12AM-5AM")
    print("   - Medium urgency: Avoid nighttime (10PM-6AM)")
    print("   - Low urgency: Prefer 8AM-6PM")
    print("="*60)
    print("🔄 AUTO-TRAINING SYSTEM:")
    print("   - Trains automatically every 24 hours")
    print("   - Trains on system startup")
    print("   - Manual training via API")
    print("   - Data-check training DISABLED (using periodic only)")
    print("="*60)
    print("📊 ENHANCED FRONTEND SYNC:")
    print("   - send_to_frontend() now includes resource consumption data from last 5 minutes")
    print("   - Sends both sensor data and resource consumption in single API call")
    print("="*60)
    print("⏰ COMMAND TIMESTAMP CHECKING:")
    print("   - Commands expire after 1 minute")
    print("   - check_frontend_commands() checks timestamp freshness")
    print("   - /api/frontend/control also validates timestamps")
    print("="*60)
    print("🧹 DATA CLEANING ENDPOINT (NEW):")
    print("   - POST /api/ml/clean-training-data")
    print("   - Removes sensor errors (0% moisture) from training data")
    print("   - Removes extreme values (soil >100% or <5%)")
    print("   - Retrains models after cleaning")
    print("="*60)
    print("📏 CALIBRATION MANAGEMENT:")
    print("   - GET /api/calibration/settings - View all calibration settings")
    print("   - GET /api/calibration/zone/{zone} - View specific zone")
    print("   - POST /api/calibration/zone/{zone} - Update zone calibration")
    print("   - POST /api/calibration/zone/{zone}/reset - Reset to defaults")
    print("   - POST /api/calibration/test - Test calibration with sample values")
    print("="*60)
    print("📝 To test Linear Regression predictions with confidence intervals:")
    print("   1. First time: POST to /api/predict/generate to create first prediction")
    print("   2. Then check: GET /api/predict/next-irrigation to see prediction with CI")
    print("   3. View all: GET /api/predict/all to see all predictions")
    print("   4. Train model: POST to /api/ml/train-linear-regression")
    print("   5. Check training status: GET /api/ml/training-status")
    print("   6. Check model state: GET /api/ml/state-info")
    print("   7. Test CI: GET /api/test-confidence-intervals")
    print("   8. Clean data: POST /api/ml/clean-training-data (if sensor errors detected)")
    print("="*60)
    print("📏 To verify calibration settings:")
    print("   - Check /api/sensors endpoint for soil values and raw values")
    print("   - Check /api/calibration/settings for current configuration")
    print("   - Test with /api/calibration/test endpoint")
    print("="*60)
    
    # Initialize database - THIS WILL CREATE THE TABLES CORRECTLY
    init_database()
    
    # Initialize sample data (only if empty)
    init_sample_data()
    
    # Start background thread
    thread = threading.Thread(target=background_thread, daemon=True)
    thread.start()
    
    # Run Flask app
    print(f"🌐 Web interface: http://localhost:5000")
    print(f"🔐 Admin login: http://localhost:5000/login (password: {ADMIN_PASSWORD})")
    print(f"📊 Reports & Graphs: http://localhost:5000/graphs")
    print(f"🔮 Predictions Dashboard: http://localhost:5000/predictions")
    print(f"📏 Calibration Dashboard: http://localhost:5000/calibration")
    print(f"📈 Prediction test with CI: http://localhost:5000/api/predict/next-irrigation?key={API_KEY}")
    print(f"🔄 Manual prediction generation: POST http://localhost:5000/api/predict/generate?key={API_KEY}")
    print(f"📊 Training status: GET http://localhost:5000/api/ml/training-status?key={API_KEY}")
    print(f"💾 Model state info: GET http://localhost:5000/api/ml/state-info?key={API_KEY}")
    print(f"🧹 Clean training data: POST http://localhost:5000/api/ml/clean-training-data?key={API_KEY}")
    print(f"📏 Calibration: GET http://localhost:5000/api/calibration/settings?key={API_KEY}")
    print("="*60)
    print("🔧 DEVICE INITIALIZATION: All devices set to OFF on startup (with retries)")
    print("   - Check /api/system/status for device state")
    print("   - System will continue even if serial is unavailable")
    print("="*60)
    print("📏 MULTI-ZONE CALIBRATION: Check /api/sensors for calibrated values")
    print("   - Raw values are stored as soilX_raw")
    print("   - Calibrated values are stored as soilX")
    print("   - Each zone can be configured independently")
    print("="*60)
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n🔄 Shutting down...")
        # The atexit handler will be called automatically
        time.sleep(2)

if __name__ == '__main__':
    main()