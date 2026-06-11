# -*- coding: utf-8 -*-
"""
Constants used throughout the application
"""

# Sensor value ranges for validation
SENSOR_RANGES = {
    'temperature': (0, 50),      # °C
    'humidity': (0, 100),        # %
    'soil1': (0, 100),           # %
    'soil2': (0, 100),           # %
    'soil3': (0, 100),           # %
    'lowLevel': (0, 1),          # binary
    'highLevel': (0, 1),         # binary
    'valve': (0, 1),             # binary
    'pump': (0, 1)              # binary
}

# Optimal ranges for sensors
OPTIMAL_RANGES = {
    'temperature': (20, 28),     # °C
    'humidity': (40, 85),        # %
    'soil': (40, 60),           # %
    'soil_optimal': (80, 85)    # Soil optimal range
}

# Status colors and labels
STATUS_COLORS = {
    'good': '#10b981',
    'warning': '#f59e0b',
    'critical': '#ef4444',
    'info': '#3b82f6'
}

STATUS_LABELS = {
    'good': 'OPTIMAL',
    'warning': 'MONITOR',
    'critical': 'ALERT',
    'info': 'INFO'
}

# Water tank status mapping
# lowLevel=0, highLevel=0 -> FULL (both sensors submerged)
# lowLevel=0, highLevel=1 -> MEDIUM (low sensor submerged, high sensor dry)
# lowLevel=1, highLevel=1 -> LOW (both sensors dry)
TANK_STATUS_MAP = {
    (0, 0): {'level': 100, 'status': 'FULL', 'color': 'good'},
    (0, 1): {'level': 50, 'status': 'MEDIUM', 'color': 'good'},
    (1, 1): {'level': 5, 'status': 'LOW', 'color': 'warning'},
    (1, 0): {'level': 25, 'status': 'ERROR', 'color': 'critical'}  # Shouldn't happen normally
}
