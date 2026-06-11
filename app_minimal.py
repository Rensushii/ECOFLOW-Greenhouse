#!/usr/bin/env python3
"""
Minimal working greenhouse app
"""

from flask import Flask, render_template, jsonify, request, session, redirect, url_for
import threading
import time
import sqlite3
import json
import hashlib
from datetime import datetime
from functools import wraps
import os

# Create Flask app
app = Flask(__name__, 
            template_folder='templates',
            static_folder='static')
app.secret_key = "group4_thesis_secret_key_2024_secure_random"

# Configuration (simplified)
DB_PATH = "data/database/greenhouse.db"
ADMIN_PASSWORD = "ecoflow"
API_KEY = "group4_thesis_secret54rg79j32k4dsn930ytt26"
ML_ENABLED = True

# Global data storage
sensor_data = {}
resource_stats = {}
running = True

# Initialize database
def init_database():
    """Initialize database tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
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
        pump INTEGER
    )
    ''')
    
    conn.commit()
    conn.close()
    print("? Database initialized")

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
    """Simplified kiosk interface"""
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
    """Admin dashboard"""
    return render_template('admin/dashboard.html')

@app.route('/graphs')
@login_required
def graphs():
    """Graphs page"""
    return render_template('admin/graphs.html')

@app.route('/graphs/enhanced')
@login_required
def graphs_enhanced():
    """Enhanced graphs"""
    return render_template('admin/graphs_enhanced.html')

# API endpoints
@app.route('/api/sensors')
def get_sensors():
    """Get sensor data"""
    return jsonify(sensor_data)

@app.route('/api/data')
@api_key_required
def get_data():
    """Get historical data"""
    hours = request.args.get('hours', 24, type=int)
    limit = request.args.get('limit', 100, type=int)
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT * FROM greenhouse_data 
        WHERE timestamp >= datetime('now', ?) 
        ORDER BY timestamp DESC 
        LIMIT ?
    """, (f'-{hours} hours', limit))
    
    data = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify(data)

@app.route('/api/control', methods=['POST'])
@api_key_required
def control():
    """Control pump/valve"""
    data = request.json
    
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # In a real system, this would send to ESP32
    print(f"? Control command: {data}")
    
    return jsonify({
        "success": True,
        "message": "Command received (simulated)",
        "command": data,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/api/ml/status')
def ml_status():
    """ML system status"""
    return jsonify({
        "ml_enabled": ML_ENABLED,
        "system_state": "initial_q_learning",
        "data_samples": 0,
        "daily_water_usage": 0
    })

@app.route('/api/resources')
def get_resources():
    """Get resource consumption"""
    return jsonify({
        'success': True,
        'resources': {
            'water_consumed_liters': 0.0,
            'energy_consumed_kwh': 0.0,
            'daily_water_liters': 0.0,
            'daily_energy_kwh': 0.0,
            'pump_runtime_display': '0h 0m',
            'valve_runtime_display': '0h 0m'
        }
    })

# Background thread simulation
def background_thread():
    """Simulate background data processing"""
    print("? Background thread started")
    
    # Simulate sensor data
    global sensor_data
    
    while running:
        # Simulate sensor readings
        sensor_data = {
            'temperature': 25.5,
            'humidity': 65.2,
            'soil1': 45.0,
            'soil2': 50.0,
            'soil3': 55.0,
            'lowLevel': 0,
            'highLevel': 0,
            'valve': 0,
            'pump': 0,
            'timestamp': datetime.now().isoformat()
        }
        
        # Save to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO greenhouse_data 
            (temperature, humidity, soil1, soil2, soil3, lowLevel, highLevel, valve, pump)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sensor_data['temperature'],
            sensor_data['humidity'],
            sensor_data['soil1'],
            sensor_data['soil2'],
            sensor_data['soil3'],
            sensor_data['lowLevel'],
            sensor_data['highLevel'],
            sensor_data['valve'],
            sensor_data['pump']
        ))
        conn.commit()
        conn.close()
        
        time.sleep(5)  # Update every 5 seconds

# Main function
def main():
    """Main application entry point"""
    print("="*60)
    print("GREENHOUSE MONITORING SYSTEM - MINIMAL VERSION")
    print("="*60)
    
    # Initialize database
    init_database()
    
    # Start background thread
    thread = threading.Thread(target=background_thread, daemon=True)
    thread.start()
    
    # Run Flask app
    print(f"? Web interface: http://localhost:5000")
    print(f"? Admin login: http://localhost:5000/login (password: {ADMIN_PASSWORD})")
    print("="*60)
    
    try:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\n? Shutting down...")
        global running
        running = False

if __name__ == '__main__':
    main()
