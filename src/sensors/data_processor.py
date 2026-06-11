# -*- coding: utf-8 -*-
"""
Sensor data processing and validation
"""

import json
import numpy as np
from datetime import datetime
import sys
import os

# Add src to path to allow imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Now we can import from utils
try:
    from utils.constants import SENSOR_RANGES, OPTIMAL_RANGES, STATUS_LABELS
except ImportError:
    # Fallback if utils.constants doesn't exist
    SENSOR_RANGES = {
        'temperature': (0, 50),
        'humidity': (0, 100),
        'soil1': (0, 100),
        'soil2': (0, 100),
        'soil3': (0, 100),
        'lowLevel': (0, 1),
        'highLevel': (0, 1),
        'valve': (0, 1),
        'pump': (0, 1)
    }
    
    OPTIMAL_RANGES = {
        'temperature': (20, 28),
        'humidity': (40, 85),
        'soil': (40, 60),
        'soil_optimal': (80, 85)
    }
    
    STATUS_LABELS = {
        'good': 'OPTIMAL',
        'warning': 'MONITOR',
        'critical': 'ALERT',
        'info': 'INFO'
    }

class DataProcessor:
    def __init__(self):
        self.last_processed_id = 0
        
    def validate_sensor_data(self, data):
        """Validate and clean sensor data"""
        cleaned = {}
        anomalies = []
        
        for key, value in data.items():
            if key in SENSOR_RANGES:
                min_val, max_val = SENSOR_RANGES[key]
                if min_val <= value <= max_val:
                    cleaned[key] = value
                else:
                    anomalies.append(f"{key}={value} (range: {min_val}-{max_val})")
                    cleaned[key] = None  # Mark as invalid
            else:
                cleaned[key] = value
        
        if anomalies:
            print(f"?? Data anomalies detected: {', '.join(anomalies)}")
        
        return cleaned
    
    def create_advanced_features(self, data):
        """Create advanced features from sensor data"""
        features = data.copy()
        now = datetime.now()
        
        # Time-based features
        features['hour'] = now.hour
        features['day_of_week'] = now.weekday()
        features['hour_sin'] = np.sin(2 * np.pi * now.hour / 24)
        features['hour_cos'] = np.cos(2 * np.pi * now.hour / 24)
        features['is_daytime'] = 1 if 6 <= now.hour <= 18 else 0
        
        # Soil features
        if all(k in data for k in ['soil1', 'soil2', 'soil3']):
            features['soil_avg'] = np.mean([
                data.get('soil1', 50),
                data.get('soil2', 50),
                data.get('soil3', 50)
            ])
            features['soil_gradient_1_2'] = data.get('soil1', 50) - data.get('soil2', 50)
            features['soil_gradient_2_3'] = data.get('soil2', 50) - data.get('soil3', 50)
        else:
            features['soil_avg'] = 50
            features['soil_gradient_1_2'] = 0
            features['soil_gradient_2_3'] = 0
        
        # Environmental features
        if 'temperature' in data and 'humidity' in data:
            features['temp_humidity'] = data.get('temperature', 25) * data.get('humidity', 60) / 100
            features['evaporation_est'] = 0.5 * (data.get('temperature', 25) / 80) * (1 - data.get('humidity', 60) / 100)
        else:
            features['temp_humidity'] = 15
            features['evaporation_est'] = 0.2
        
        return features
    
    def calculate_status(self, sensor_type, value):
        """Calculate status based on sensor value"""
        if sensor_type not in OPTIMAL_RANGES:
            return 'info', 'NO DATA'
        
        min_val, max_val = OPTIMAL_RANGES[sensor_type]
        
        if value is None:
            return 'info', 'NO DATA'
        
        if min_val <= value <= max_val:
            return 'good', 'OPTIMAL'
        elif value >= min_val - 5 and value < min_val or value > max_val and value <= max_val + 5:
            return 'warning', 'MONITOR'
        else:
            return 'critical', 'ALERT'
