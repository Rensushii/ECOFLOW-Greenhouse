# -*- coding: utf-8 -*-
"""
Configuration settings for the Greenhouse Monitoring System
"""

import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
SRC_DIR = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
LOGS_DIR = BASE_DIR / "logs"

# Database configuration
DB_PATH = DATA_DIR / "database" / "greenhouse.db"
DB_BACKUP_DIR = DATA_DIR / "backups"

# Serial communication
SERIAL_BAUD = 115200
SERIAL_PORTS = [
    '/dev/ttyUSB0',
    '/dev/ttyUSB1',
    '/dev/ttyACM0',
    '/dev/ttyAMA0'
]

# API Configuration
API_KEY = "group4_thesis_secret54rg79j32k4dsn930ytt26"
FRONTEND_API_URL = "https://ecoflow-9ege.onrender.com/api/data/ingest"
FRONTEND_API_KEY = "group4_thesis_secret54rg79j32k4dsn930ytt26"
FRONTEND_COMMANDS_URL = "https://ecoflow-9ege.onrender.com/api/commands/pending"
FRONTEND_UPDATE_URL = "https://ecoflow-9ege.onrender.com/api/commands/update"

# Data sending interval (seconds)
DATA_SEND_INTERVAL = 300  # 5 minutes

# Admin authentication
ADMIN_PASSWORD = "ecoflow"

# Flask configuration
FLASK_SECRET_KEY = "group4_thesis_secret_key_2024_secure_random"

# Resource tracking
WATER_FLOW_RATE_LPM = 0.3      # Liters per minute
PUMP_POWER_KW = 0.37          # 385 Watts = 0.37 kW
VALVE_POWER_KW = 0.002        # 2 Watts = 0.002 kW
RASPBERRY_PI_POWER_KW = 0.005  # 5W average
ESP32_POWER_KW = 0.0001       # 0.1W for ESP32

# ML Configuration
ML_ENABLED = True
ML_CONFIG = {
    'Q_LEARNING_CONFIG': {
        'learning_rate': 0.1,
        'discount_factor': 0.95,
        'exploration_rate': 1.0,
        'exploration_decay': 0.9995,
        'min_exploration_rate': 0.01,
        'episodes': 10000,
        'soil_bins': 5,
        'temp_bins': 5,
        'humidity_bins': 4,
        'actions': [0, 5, 10, 15, 20, 25, 30],
        'rewards': {
            'optimal_soil': 10,
            'too_dry': -5,
            'too_wet': -3,
            'waste_water': -2,
            'pump_energy': -1
        },
        'zones': {
            'A': {'weight': 1.2, 'crop_factor': 1.0},
            'B': {'weight': 1.0, 'crop_factor': 0.9},
            'C': {'weight': 0.8, 'crop_factor': 0.8}
        }
    },
    'LINEAR_REGRESSION_CONFIG': {
        'min_training_samples': 100,
        'features': [
            'temperature', 'humidity', 'soil1', 'soil2', 'soil3',
            'valve', 'pump', 'soil_avg', 'temp_humidity',
            'evaporation_est'
        ],
        'target': 'soil_moisture_change',
        'retrain_interval_hours': 24
    },
    'IRRIGATION_CONSTRAINTS': {
        'max_daily_water': 100,
        'min_interval': 60,
        'max_duration': 30,
        'night_irrigation': False,
        'valve_switch_delay': 2
    },
    'ML_STATES': {
        'INITIAL': 'initial_q_learning',
        'COLLECTING_DATA': 'collecting_data',
        'HYBRID': 'hybrid_q_lr',
        'FULL_ML': 'full_machine_learning'
    }
}

# Auto-training configuration
AUTO_TRAINING_CONFIG = {
    'ENABLED': True,
    'PERIODIC_INTERVAL_HOURS': 24,  # Train every 24 hours
    'MIN_NEW_SAMPLES': 50,  # Train when 50+ new samples
    'CHECK_INTERVAL_MINUTES': 5,  # Check every 5 minutes
    'MIN_TRAINING_SAMPLES': 20,  # Minimum samples needed
    'MAX_TRAINING_DAYS': 7,  # Use last 7 days of data
}

# Create directories if they don't exist
for directory in [DATA_DIR, DATA_DIR/"database", DATA_DIR/"backups", 
                  DATA_DIR/"ml_models", TEMPLATES_DIR/"admin", 
                  TEMPLATES_DIR/"partials", STATIC_DIR/"css", 
                  STATIC_DIR/"js", LOGS_DIR]:
    directory.mkdir(parents=True, exist_ok=True)