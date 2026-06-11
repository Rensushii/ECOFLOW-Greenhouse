# -*- coding: utf-8 -*-
"""
Irrigation scheduler combining Q-Learning and Linear Regression
"""


from .q_learning import QLearningSimulator
from .predictor import SoilMoisturePredictor
from datetime import datetime, timedelta
import sqlite3
import json
import random
import time


class IrrigationScheduler:
    def __init__(self, db_path, config, ml_config, decision_tracker=None):
        self.db_path = db_path
        self.config = config
        self.ml_config = ml_config
        self.decision_tracker = decision_tracker
       
        # Initialize ML components
        print("? Initializing Q-Learning simulator...")
        self.q_learning = QLearningSimulator(ml_config['Q_LEARNING_CONFIG'])
       
        print("? Initializing Soil Moisture Predictor...")
        self.lr_predictor = SoilMoisturePredictor(db_path, ml_config['LINEAR_REGRESSION_CONFIG'])
       
        self.irrigation_log = []
        self.daily_water_usage = 0
        self.last_irrigation_time = None
        self.system_state = ml_config['ML_STATES']['HYBRID']
        self.data_collection_start = None
       
        # Train initial models
        self._initialize_models()
       
        print(f"? IrrigationScheduler initialized in {self.system_state} mode")
   
    def _initialize_models(self):
        """Initialize and train ML models"""
        print("? Training Q-Learning model...")
        self.q_learning.train(episodes=1000)
       
        # Check if we have enough data for linear regression
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM greenhouse_data WHERE temperature IS NOT NULL")
        count = cursor.fetchone()[0]
        conn.close()
       
        min_samples = self.ml_config['LINEAR_REGRESSION_CONFIG']['min_training_samples']
        if count >= min_samples:
            print(f"? Enough data ({count} samples) for linear regression")
            if self.lr_predictor.train():
                self.system_state = self.ml_config['ML_STATES']['HYBRID']
                print("? Switched to HYBRID mode (Q-Learning + Linear Regression)")
            else:
                self.system_state = self.ml_config['ML_STATES']['COLLECTING_DATA']
                print(f"? Collecting more data for linear regression")
        else:
            print(f"? Insufficient data for linear regression ({count}/{min_samples})")
            self.data_collection_start = datetime.now()
            self.system_state = self.ml_config['ML_STATES']['COLLECTING_DATA']
       
        print(f"? System state: {self.system_state}")
   
    def make_irrigation_decision(self, sensor_data):
        """Make irrigation decision based on sensor data AND create schedule"""
        # Check soil moisture
        soil1 = sensor_data.get('soil1', 50)
        soil2 = sensor_data.get('soil2', 50)
        soil3 = sensor_data.get('soil3', 50)
        soil_is_dry = any(soil < 80 for soil in [soil1, soil2, soil3])
       
        # Check minimum interval
        if self.last_irrigation_time:
            time_since_last = (datetime.now() - self.last_irrigation_time).total_seconds() / 60
            min_interval = self.ml_config['IRRIGATION_CONSTRAINTS']['min_interval']
            if time_since_last < min_interval:
                decision = {
                    'action': 0,
                    'reason': f'Minimum interval not met ({time_since_last:.1f} < {min_interval} mins)',
                    'system_state': self.system_state,
                    'model_used': 'constraint_check',
                    'timestamp': datetime.now().isoformat(),
                    'executed': 0
                }
                return decision
       
        # Check constraints
        if not self._check_constraints(sensor_data):
            decision = {
                'action': 0,
                'reason': 'Constraints not met',
                'system_state': self.system_state,
                'model_used': 'constraint_check',
                'timestamp': datetime.now().isoformat(),
                'executed': 0
            }
            return decision
       
        # If soil is not dry, don't irrigate
        if not soil_is_dry:
            decision = {
                'action': 0,
                'reason': 'Soil moisture is sufficient (>80%)',
                'system_state': self.system_state,
                'model_used': 'soil_check',
                'timestamp': datetime.now().isoformat(),
                'executed': 0
            }
            return decision
       
        # Make decision based on system state
        if self.system_state in [self.ml_config['ML_STATES']['INITIAL'], self.ml_config['ML_STATES']['COLLECTING_DATA']]:
            action_duration = self.q_learning.get_action(sensor_data)
            reason = f"Q-Learning only (State: {self.system_state})"
            decision = {
                'action': action_duration,
                'reason': reason,
                'system_state': self.system_state,
                'model_used': 'q_learning',
                'timestamp': datetime.now().isoformat(),
                'executed': 0
            }
        else:
            # Hybrid mode: Combine Q-Learning and Linear Regression
            q_action = self.q_learning.get_action(sensor_data)
           
            if self.lr_predictor.models:
                pred_no_irrigation = self.lr_predictor.predict_soil_changes(sensor_data, 0)
                pred_with_irrigation = self.lr_predictor.predict_soil_changes(sensor_data, q_action)
               
                # Calculate benefits
                benefits = {}
                for zone in ['soil1', 'soil2', 'soil3']:
                    current = sensor_data.get(zone, 50)
                    expected_no_irr = current + pred_no_irrigation.get(zone, 0)
                    expected_with_irr = current + pred_with_irrigation.get(zone, 0)
                   
                    def distance_to_optimal(moisture):
                        if 80 <= moisture <= 85:
                            return 0
                        return min(abs(moisture - 80), abs(moisture - 85))
                   
                    benefit = distance_to_optimal(expected_no_irr) - distance_to_optimal(expected_with_irr)
                    benefits[zone] = benefit
               
                weights = {'soil1': 1.2, 'soil2': 1.0, 'soil3': 0.8}
                total_benefit = sum(benefits[zone] * weights[zone] for zone in benefits)
               
                if total_benefit > 2:
                    final_action = q_action
                    reason = "Both models agree on irrigation need"
                    model_used = 'hybrid_q_lr'
                elif total_benefit > 0:
                    final_action = max(0, q_action - 5)
                    reason = "Linear regression suggests less irrigation"
                    model_used = 'hybrid_q_lr'
                else:
                    final_action = 0
                    reason = "Linear regression predicts no benefit"
                    model_used = 'hybrid_q_lr'
               
                decision = {
                    'action': final_action,
                    'reason': reason,
                    'system_state': self.system_state,
                    'model_used': model_used,
                    'q_learning_action': q_action,
                    'predicted_benefits': benefits,
                    'total_benefit': total_benefit,
                    'timestamp': datetime.now().isoformat(),
                    'executed': 0
                }
            else:
                # Fallback to Q-Learning only
                action_duration = self.q_learning.get_action(sensor_data)
                decision = {
                    'action': action_duration,
                    'reason': f"Q-Learning only (No LR model)",
                    'system_state': self.system_state,
                    'model_used': 'q_learning',
                    'timestamp': datetime.now().isoformat(),
                    'executed': 0
                }
       
        # Add some randomness to decisions for testing
        if decision['action'] > 0:
            # Randomly decide if this decision should be executed
            if random.random() < 0.7:  # 70% chance to execute
                decision['executed'] = 1
                # Update last irrigation time if irrigation occurred
                self.last_irrigation_time = datetime.now()
                self.daily_water_usage += decision['action']
                
                # CREATE SCHEDULE if executed
                self._create_schedule_for_decision(decision, sensor_data)
       
        # Update irrigation log
        self.irrigation_log.append(decision)
        if len(self.irrigation_log) > 1000:
            self.irrigation_log = self.irrigation_log[-1000:]
       
        return decision
    
    def _create_schedule_for_decision(self, decision, sensor_data):
        """Create a schedule for an irrigation decision"""
        try:
            if decision['action'] <= 0 or decision.get('executed', 0) == 0:
                return None
            
            # Schedule for immediate execution (30 seconds from now)
            schedule_time = datetime.now() + timedelta(seconds=30)
            
            schedule = {
                'action': decision['action'],
                'reason': decision['reason'],
                'scheduled_time': schedule_time.isoformat(),
                'status': 'scheduled',
                'sensor_data': json.dumps(sensor_data)
            }
            
            # Save to database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # First save the decision
            cursor.execute("""
                INSERT INTO ai_decisions 
                (timestamp, action, reason, system_state, model_used, 
                 q_learning_action, predicted_benefits, total_benefit, sensor_data, executed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision['timestamp'],
                decision['action'],
                decision['reason'],
                decision.get('system_state', ''),
                decision.get('model_used', ''),
                decision.get('q_learning_action', 0),
                json.dumps(decision.get('predicted_benefits', {})),
                decision.get('total_benefit', 0),
                json.dumps(sensor_data),
                decision.get('executed', 0)
            ))
            
            decision_id = cursor.lastrowid
            
            # Then create the schedule
            cursor.execute("""
                INSERT INTO ai_schedules 
                (decision_id, action, reason, scheduled_time, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                decision_id,
                schedule['action'],
                schedule['reason'],
                schedule['scheduled_time'],
                schedule['status'],
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
            print(f"? Schedule created for {schedule_time.strftime('%H:%M:%S')} - {decision['action']} minutes")
            return schedule
            
        except Exception as e:
            print(f"?? Error creating schedule: {e}")
            return None
   
    def _check_constraints(self, sensor_data):
        """Check irrigation constraints"""
        now = datetime.now()
       
        # Check night irrigation constraint
        if not self.ml_config['IRRIGATION_CONSTRAINTS']['night_irrigation']:
            if 22 <= now.hour or now.hour < 5:
                return False
       
        # Check daily water limit
        if self.daily_water_usage >= self.ml_config['IRRIGATION_CONSTRAINTS']['max_daily_water']:
            return False
       
        # Check water tank level
        low_level = sensor_data.get('lowLevel', 0)
        high_level = sensor_data.get('highLevel', 0)
       
        # If tank is empty (both sensors show 1)
        if low_level == 1 and high_level == 1:
            print("? Water tank is empty - skipping irrigation")
            return False
       
        return True
   
    def get_system_status(self):
        """Get ML system status"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
       
        # Get total data count
        cursor.execute("SELECT COUNT(*) FROM greenhouse_data WHERE temperature IS NOT NULL")
        data_count = cursor.fetchone()[0]
       
        # Get daily water usage
        cursor.execute("""
            SELECT SUM(valve * pump * 0.1) as daily_water
            FROM greenhouse_data
            WHERE date(timestamp) = date('now')
        """)
        water_result = cursor.fetchone()
        daily_water = water_result[0] if water_result[0] else 0
       
        conn.close()
       
        return {
            'system_state': self.system_state,
            'data_samples': data_count,
            'daily_water_usage': daily_water,
            'last_irrigation': self.last_irrigation_time.isoformat() if self.last_irrigation_time else None,
            'q_learning_exploration': self.q_learning.exploration_rate,
            'linear_regression_trained': len(self.lr_predictor.models) > 0,
            'irrigation_decisions_today': len([l for l in self.irrigation_log if l['timestamp'] and
                                               datetime.fromisoformat(l['timestamp']).date() == datetime.now().date()])
        }
   
    def train_all_models(self):
        """Force retrain all models"""
        print("? Forcing retraining of all models...")
       
        # Retrain Q-Learning
        self.q_learning.train(episodes=5000)
       
        # Retrain Linear Regression
        self.lr_predictor.train()
       
        print("? All models retrained successfully")
        return True


# Updated app.py with ScheduleExecutor integration:

# python
#!/usr/bin/env python3
"""
Greenhouse Monitoring System with Daily/Weekly/Monthly Reports + Real-time Graphs + Schedule Executor
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

# Add src to path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

# Import configuration
from config import (
    DB_PATH, ADMIN_PASSWORD, API_KEY, ML_ENABLED,
    FRONTEND_API_URL, FRONTEND_API_KEY,
    FRONTEND_COMMANDS_URL, FRONTEND_UPDATE_URL,
    SERIAL_PORTS, SERIAL_BAUD, DATA_SEND_INTERVAL,
    ML_CONFIG, WATER_FLOW_RATE_LPM
)

try:
    from sensors.serial_reader import SerialReader
    print("? Imported serial_reader module")
except ImportError as e:
    print(f"? Error importing serial_reader: {e}")
    SerialReader = None

try:
    from src.resources.tracker import ResourceTracker
    print("? Imported ResourceTracker module")
except ImportError as e:
    print(f"? Error importing ResourceTracker: {e}")
    ResourceTracker = None

try:
    from src.ml.scheduler import IrrigationScheduler
    from src.ml.decision_tracker import AIDecisionTracker
    from src.ml.schedule_executor import ScheduleExecutor
    print("? Imported ML modules")
except ImportError as e:
    print(f"? Error importing ML modules: {e}")
    IrrigationScheduler = None
    AIDecisionTracker = None
    ScheduleExecutor = None

# Create Flask app
app = Flask(__name__,
            template_folder='templates',
            static_folder='static')
app.secret_key = "group4_thesis_secret_key_2024_secure_random"

# Constants
MANUAL_OVERRIDE_DURATION = 300  # 5 minutes manual override

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
    'timestamp': datetime.now().strftime("%Y-%m-d %H:%M:%S")
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

# Manual override tracking
manual_valve_control = False
manual_pump_control = False
last_manual_command_time = 0

# Initialize ResourceTracker
resource_tracker = None
if ResourceTracker:
    resource_tracker = ResourceTracker(str(DB_PATH))
    print("? ResourceTracker initialized")
else:
    print("? ResourceTracker not available")

# Initialize AI System
if ML_ENABLED and IrrigationScheduler and AIDecisionTracker:
    try:
        print("? Initializing AI system...")
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
            ml_config=ML_CONFIG,
            decision_tracker=decision_tracker
        )
        print("? AI system initialized")
    except Exception as e:
        print(f"? AI system initialization failed: {e}")
        ml_manager = None
        decision_tracker = None
else:
    print("? AI system not enabled or modules not available")

# Initialize database
def init_database():
    """Initialize database tables"""
    db_path_str = str(DB_PATH)
    os.makedirs(os.path.dirname(db_path_str), exist_ok=True)
    
    conn = sqlite3.connect(db_path_str)
    cursor = conn.cursor()
    
    # ONLY CREATE ESSENTIAL TABLES
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
    
    conn.commit()
    conn.close()
    print(f"? Database initialized at {db_path_str}")

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

def process_esp32_data(data):
    """Process ESP32 data and update sensor_data"""
    global sensor_data, manual_valve_control, manual_pump_control, last_manual_command_time, resource_tracker
    
    # Convert ESP32 boolean values to 0/1 for frontend
    lowLevel = 1 if data.get('lowLevel', False) else 0
    highLevel = 1 if data.get('highLevel', False) else 0
    
    # Check if manual override has expired
    current_time = time.time()
    if manual_valve_control and (current_time - last_manual_command_time > MANUAL_OVERRIDE_DURATION):
        print("? Manual valve control expired, returning to automatic")
        manual_valve_control = False
    
    if manual_pump_control and (current_time - last_manual_command_time > MANUAL_OVERRIDE_DURATION):
        print("? Manual pump control expired, returning to automatic")
        manual_pump_control = False
    
    # Format with 1 decimal place for temperature/humidity
    processed_data = {
        'temperature': round(float(data.get('temperature', 0)), 1),
        'humidity': round(float(data.get('humidity', 0)), 1),
        'soil1': int(data.get('soil1', 0)),
        'soil2': int(data.get('soil2', 0)),
        'soil3': int(data.get('soil3', 0)),
        'lowLevel': lowLevel,
        'highLevel': highLevel,
        'valve': int(data.get('valve', 0)),
        'pump': int(data.get('pump', 0)),
        'timestamp': datetime.now().isoformat()
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
    print(f"? Water Tank: {tank_status} ({tank_level}%), Valve: {'ON' if processed_data['valve'] else 'OFF'}")
    
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
        print(f"? Database save error: {e}")
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
        print(f"? Error logging command: {e}")

def make_ai_decision():
    """Make an AI irrigation decision"""
    global sensor_data, ml_manager, decision_tracker
    
    if not ml_manager or not decision_tracker:
        print("? AI system not available for decision making")
        return
    
    # Check if any soil zone needs irrigation
    soil1 = sensor_data.get('soil1', 50)
    soil2 = sensor_data.get('soil2', 50)
    soil3 = sensor_data.get('soil3', 50)
    
    # Only irrigate if soil is dry (below 80%)
    if all(soil >= 80 for soil in [soil1, soil2, soil3]):
        print("? Soil moisture is sufficient, no irrigation needed")
        return
    
    print("? Making AI irrigation decision...")
    
    try:
        decision = ml_manager.make_irrigation_decision(sensor_data)
        
        # Only schedule if action > 0
        if decision.get('action', 0) > 0:
            print(f"? AI Decision: {decision.get('action', 0)} minutes - {decision.get('reason', 'No reason')}")
            
            # The scheduler will automatically create a schedule
            # if executed flag is set in make_irrigation_decision()
            
            print(f"? Decision made and scheduled if executed")
        else:
            print(f"? AI Decision: No irrigation needed - {decision.get('reason', 'No reason')}")
        
    except Exception as e:
        print(f"? Error making AI decision: {e}")

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
    """Send sensor data to frontend API"""
    global last_frontend_sync
    
    current_time = time.time()
    if current_time - last_frontend_sync < DATA_SEND_INTERVAL:
        return False
    
    try:
        # Get the latest data from database with ID field
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM greenhouse_data
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        
        latest_data = cursor.fetchone()
        conn.close()
        
        if latest_data:
            # Format data EXACTLY as frontend expects
            frontend_data = {
                "id": latest_data['id'],
                "temperature": float(latest_data['temperature']),
                "humidity": float(latest_data['humidity']),
                "soil1": int(latest_data['soil1']),
                "soil2": int(latest_data['soil2']),
                "soil3": int(latest_data['soil3']),
                "lowLevel": int(latest_data['lowLevel']),
                "highLevel": int(latest_data['highLevel']),
                "valve": int(latest_data['valve']),
                "pump": int(latest_data['pump']),
                "timestamp": latest_data['timestamp']
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
            
            print(f"? Sending to frontend: Valve={frontend_data['valve']}, Pump={frontend_data['pump']}")
            
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
                print(f"? Data sent to frontend API")
                return True
            else:
                print(f"? Frontend API error: {response.status_code}")
                return False
        
    except Exception as e:
        print(f"? Error sending to frontend: {e}")
    
    return False

def check_frontend_commands():
    """Check for pending commands from frontend"""
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
                print(f"? Found {len(commands)} pending command(s)")
                
                for command in commands:
                    command_id = command.get('command_id')
                    if not command_id:
                        continue
                    
                    # Skip if already processed
                    if command_id in processed_commands:
                        continue
                    
                    device = command.get('device')
                    desired_state = command.get('state')
                    
                    if not all([device, desired_state, command_id]):
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
                        
                        # Set manual override
                        if device == 'valve':
                            manual_valve_control = True
                            print(f"? Manual valve control activated for {MANUAL_OVERRIDE_DURATION} seconds")
                        elif device == 'pump':
                            manual_pump_control = True
                            print(f"? Manual pump control activated for {MANUAL_OVERRIDE_DURATION} seconds")
                        
                        last_manual_command_time = time.time()
                        notes = f"Manual override set for {MANUAL_OVERRIDE_DURATION}s"
                        print(f"  ? Sent to ESP32: {esp32_command}")
                    else:
                        success = True
                        actual_state = desired_state.upper()
                        notes = "No serial connection - simulated"
                        print(f"  ?? No serial, simulating command")
                    
                    # FIXED: Use desired_state instead of undefined 'state'
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
                    
                    print(f"? Processed command {command_id}: {device} {desired_state}")
    
    except Exception as e:
        print(f"? Error checking commands: {e}")

def send_to_hosted_frontend_update(command_id, success, actual_state=None):
    """Send update specifically to hosted frontend (their format)"""
    try:
        if success:
            update_data = {
                "command_id": int(command_id),
                "status": "SUCCESS",
                "actual_state": actual_state.upper() if actual_state else "ON"
            }
        else:
            update_data = {
                "command_id": int(command_id),
                "status": "FAILED"
            }
        
        print(f"? Sending to hosted frontend: {json.dumps(update_data)}")
        
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
            print(f"? Update sent to hosted frontend successfully")
            return True
        else:
            print(f"? Failed to send to hosted frontend: {response.status_code}")
            return False
    
    except Exception as e:
        print(f"? Error sending to hosted frontend: {e}")
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
        'manual_pump': 1 if manual_pump_control else 0
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
    
    # Calculate manual override time remaining
    current_time = time.time()
    if manual_valve_control or manual_pump_control:
        time_left = max(0, MANUAL_OVERRIDE_DURATION - (current_time - last_manual_command_time))
        formatted_data['manual_override_seconds_left'] = int(time_left)
        formatted_data['manual_override_minutes_left'] = round(time_left / 60, 1)
    else:
        formatted_data['manual_override_seconds_left'] = 0
        formatted_data['manual_override_minutes_left'] = 0
    
    return jsonify(formatted_data)

# Add new API endpoints for schedule control
@app.route('/api/test-schedule', methods=['POST'])
@api_key_required
def test_schedule():
    """Create a test schedule for debugging"""
    try:
        # Create a test schedule for 1 minute from now
        schedule_time = (datetime.now() + timedelta(minutes=1)).isoformat()
        
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        
        # First create a dummy decision
        cursor.execute("""
            INSERT INTO ai_decisions
            (timestamp, action, reason, system_state, model_used, executed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            5.0,
            "Test irrigation schedule",
            "testing",
            "test_model",
            0
        ))
        
        decision_id = cursor.lastrowid
        
        # Create the schedule
        cursor.execute("""
            INSERT INTO ai_schedules
            (decision_id, action, reason, scheduled_time, status)
            VALUES (?, ?, ?, ?, ?)
        """, (
            decision_id,
            5.0,
            "Test irrigation for debugging",
            schedule_time,
            "scheduled"
        ))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            "success": True,
            "message": "Test schedule created",
            "schedule_time": schedule_time,
            "duration": 5.0,
            "decision_id": decision_id
        })
        
    except Exception as e:
        print(f"? Error creating test schedule: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/check-schedules', methods=['POST'])
@api_key_required
def check_schedules():
    """Manually trigger schedule check"""
    if schedule_executor:
        executed = schedule_executor.check_and_execute_schedules()
        return jsonify({
            "success": True,
            "message": f"Checked schedules, executed {executed}"
        })
    else:
        return jsonify({
            "success": False,
            "message": "Schedule executor not available"
        }), 500

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
        print(f"? Error forcing AI decision: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/schedule/status')
@api_key_required
def schedule_status():
    """Get schedule executor status"""
    if schedule_executor:
        return jsonify({
            "success": True,
            "running": schedule_executor.running,
            "check_interval": schedule_executor.check_interval,
            "serial_connected": schedule_executor.serial_reader is not None,
            "last_check": schedule_executor.last_check
        })
    else:
        return jsonify({"success": False, "error": "Schedule executor not initialized"})

@app.route('/api/schedule/execute-now', methods=['POST'])
@api_key_required
def execute_schedule_now():
    """Execute a schedule immediately"""
    try:
        if schedule_executor and serial_reader:
            # Execute immediately
            success = schedule_executor.execute_irrigation(2.0, "Manual immediate execution")
            
            return jsonify({
                "success": success,
                "message": "Schedule executed successfully" if success else "Schedule failed"
            })
        else:
            return jsonify({"success": False, "error": "Schedule executor or serial reader not available"}), 500
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ESP32 data callback
def esp32_data_callback(data):
    """Callback for ESP32 serial data"""
    print(f"? ESP32 Data received: {data}")
    try:
        # Process ESP32 data
        processed_data = process_esp32_data(data)
        
        # If manual control is active, send command again to maintain state
        current_time = time.time()
        
        if manual_valve_control and (current_time - last_manual_command_time < MANUAL_OVERRIDE_DURATION):
            # Check if valve is in desired state
            desired_valve = sensor_data.get('valve', 0)
            current_valve = processed_data.get('valve', 0)
            
            if desired_valve != current_valve and serial_reader:
                print(f"? Maintaining manual valve control: sending {'on' if desired_valve else 'off'}")
                serial_reader.send_command({"valve": "on" if desired_valve else "off"})
        
        if manual_pump_control and (current_time - last_manual_command_time < MANUAL_OVERRIDE_DURATION):
            # Check if pump is in desired state
            desired_pump = sensor_data.get('pump', 0)
            current_pump = processed_data.get('pump', 0)
            
            if desired_pump != current_pump and serial_reader:
                print(f"? Maintaining manual pump control: sending {'on' if desired_pump else 'off'}")
                serial_reader.send_command({"pump": "on" if desired_pump else "off"})
        
    except Exception as e:
        print(f"? Error processing ESP32 data: {e}")

def esp32_error_callback(error):
    """Callback for ESP32 serial errors"""
    print(f"? ESP32 Error: {error}")

# Background thread for data processing
def background_thread():
    """Background data processing and frontend sync"""
    global serial_reader, running, schedule_executor
    
    print("? Background thread started")
    
    # Initialize serial reader
    if SerialReader:
        serial_reader = SerialReader(
            data_callback=esp32_data_callback,
            error_callback=esp32_error_callback
        )
        
        # Start serial reading
        serial_reader.start_reading()
        print("? Serial reader started")
    else:
        print("?? Serial reader not available")
    
    # Initialize Schedule Executor
    if ScheduleExecutor and serial_reader:
        schedule_executor = ScheduleExecutor(str(DB_PATH), serial_reader)
        # Start schedule executor in a separate thread
        schedule_thread = threading.Thread(target=schedule_executor.run_continuously, daemon=True)
        schedule_thread.start()
        print("? Schedule Executor started in background thread")
    elif ScheduleExecutor:
        print("?? Schedule Executor available but no serial reader")
    
    sync_counter = 0
    command_counter = 0
    ai_decision_counter = 0
    schedule_check_counter = 0
    
    while running:
        try:
            # Send to frontend every DATA_SEND_INTERVAL seconds
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
            if schedule_executor and schedule_check_counter >= 30:
                executed = schedule_executor.check_and_execute_schedules()
                if executed > 0:
                    print(f"? Executed {executed} scheduled irrigation(s)")
                schedule_check_counter = 0
            
            # Update schedule executor last_check if available
            if schedule_executor:
                schedule_executor.last_check = datetime.now()
            
            # Increment counters
            sync_counter += 1
            command_counter += 1
            ai_decision_counter += 1
            schedule_check_counter += 1
            
            time.sleep(1)
            
        except Exception as e:
            print(f"? Error in background thread: {e}")
            time.sleep(5)

# Initialize sample data
def init_sample_data():
    """Initialize sample data for testing"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Check if we have data
    cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("? Adding sample data...")
        
        # Add realistic sample sensor data for the last 7 days
        base_time = datetime.now() - timedelta(days=7)
        
        for i in range(1008):  # 7 days * 24 hours * 6 samples per hour
            sample_time = base_time + timedelta(minutes=i*10)
            timestamp = sample_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # Create realistic patterns
            hour = sample_time.hour
            
            # Temperature
            if 6 <= hour <= 18:
                base_temp = 25.0 + (hour - 12) * 0.5
            else:
                base_temp = 20.0 + (hour % 6) * 0.3
            
            temperature = base_temp + (i % 20) * 0.2
            
            # Humidity
            humidity = 60.0 - (temperature - 22.5) * 1.5 + (i % 25) * 0.3
            
            # Soil moisture
            soil_base = 50.0
            soil_trend = (i % 144) / 144 * 10
            soil_variation = (i % 30) * 0.2
            
            irrigation_boost = 15.0 if i % 72 == 0 else 0
            
            soil1 = soil_base - soil_trend + soil_variation + irrigation_boost
            soil2 = soil_base - soil_trend * 0.8 + soil_variation * 0.9 + irrigation_boost * 0.9
            soil3 = soil_base - soil_trend * 0.6 + soil_variation * 0.8 + irrigation_boost * 0.8
            
            # Ensure bounds
            soil1 = max(30, min(80, soil1))
            soil2 = max(35, min(85, soil2))
            soil3 = max(40, min(90, soil3))
            
            # Water tank
            tank_state = i % 50
            if tank_state < 10:
                lowLevel, highLevel = 0, 0
            elif tank_state < 30:
                lowLevel, highLevel = 0, 1
            else:
                lowLevel, highLevel = 1, 1
            
            # Pump and valve
            pump_on = 1 if i % 72 == 0 and hour >= 6 and hour <= 18 else 0
            valve_on = 1 if pump_on == 1 else 0
            
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
        
        print(f"? Added {1008} realistic sample sensor records")
        conn.commit()
    
    conn.close()

# Main function
def main():
    """Main application entry point"""
    print("="*60)
    print("? GREENHOUSE MONITORING SYSTEM - WITH SCHEDULE EXECUTOR")
    print("="*60)
    print("? COMPLETE FEATURES:")
    print("   ? AI-Powered Irrigation Scheduling")
    print("   ? Schedule Executor (runs every 30 seconds)")
    print("   ? Manual override with 5-minute timeout")
    print("   ? Real-time graphs and reporting")
    print("   ? Frontend API synchronization")
    print("="*60)
    print("?  SCHEDULE CONTROL ENDPOINTS:")
    print("   ? Test: /api/test-schedule (POST)")
    print("   ? Check: /api/check-schedules (POST)")
    print("   ? Force AI: /api/ai/force-decision (POST)")
    print("   ? Execute Now: /api/schedule/execute-now (POST)")
    print("   ? Status: /api/schedule/status")
    print("="*60)
    print("?  IMPORTANT SAFETY NOTES:")
    print("   - Valve has automatic safety logic in ESP32")
    print("   - Manual override lasts 5 minutes")
    print("   - Schedules execute every 30 seconds")
    print("="*60)
    
    # Initialize database
    init_database()
    
    # Initialize sample data (only if empty)
    init_sample_data()
    
    # Start background thread
    thread = threading.Thread(target=background_thread, daemon=True)
    thread.start()
    
    # Run Flask app
    print(f"? Web interface: http://localhost:5000")
    print(f"? Admin login: http://localhost:5000/login (password: {ADMIN_PASSWORD})")
    print(f"? Reports & Graphs: http://localhost:5000/graphs")
    print(f"? Frontend sync: Every {DATA_SEND_INTERVAL} seconds")
    print(f"? Command check: Every 10 seconds")
    print(f"? AI decisions: Every 10 minutes")
    print(f"? Schedule check: Every 30 seconds")
    print(f"? Manual override: {MANUAL_OVERRIDE_DURATION} seconds duration")
    print(f"? AI system: {'ENABLED' if ml_manager else 'DISABLED'}")
    print(f"? Schedule executor: {'RUNNING' if schedule_executor else 'DISABLED'}")
    print("="*60)
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n? Shutting down...")
        global running
        running = False
        if serial_reader:
            serial_reader.stop_reading()
        time.sleep(1)
        print("? Shutdown complete")

if __name__ == '__main__':
    main()