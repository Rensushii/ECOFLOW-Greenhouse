# -*- coding: utf-8 -*-
"""
Frontend API synchronization
"""

import requests
import time
import json
from datetime import datetime
from config import (
    FRONTEND_API_URL, FRONTEND_API_KEY,
    FRONTEND_COMMANDS_URL, FRONTEND_UPDATE_URL,
    DATA_SEND_INTERVAL
)

class FrontendSync:
    def __init__(self, db_manager=None):
        self.db_manager = db_manager
        self.last_data_send_time = 0
        self.processed_commands = set()
        self.last_command_check = 0
    
    def send_sensor_data(self, sensor_data, resource_stats):
        """Send sensor data to frontend API (every 5 minutes)"""
        current_time = time.time()
        
        # Check if 5 minutes have passed since last send
        if current_time - self.last_data_send_time < DATA_SEND_INTERVAL:
            return False
        
        try:
            # Prepare the data
            frontend_data = {
                "temperature": sensor_data.get("temperature"),
                "humidity": sensor_data.get("humidity"),
                "soil1": sensor_data.get("soil1"),
                "soil2": sensor_data.get("soil2"),
                "soil3": sensor_data.get("soil3"),
                "lowLevel": sensor_data.get("lowLevel"),
                "highLevel": sensor_data.get("highLevel"),
                "valve": sensor_data.get("valve"),
                "pump": sensor_data.get("pump"),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "resources": {
                    "water_consumed_liters": round(resource_stats.get('water_consumed_liters', 0), 2),
                    "power_consumed_kwh": round(resource_stats.get('energy_consumed_kwh', 0), 4),
                    "daily_water_liters": round(resource_stats.get('daily_water_liters', 0), 2),
                    "daily_energy_kwh": round(resource_stats.get('daily_energy_kwh', 0), 4)
                }
            }
            
            headers = {
                'Content-Type': 'application/json',
                'X-Api-Key': FRONTEND_API_KEY
            }
            
            response = requests.post(FRONTEND_API_URL, json=frontend_data, headers=headers, timeout=10)
            
            if response.status_code in [200, 201, 202]:
                self.last_data_send_time = current_time
                current_time_str = datetime.now().strftime("%H:%M:%S")
                print(f"[{current_time_str}] ? Data sent to frontend API (every 5 minutes)")
                print(f"   Resources sent: {frontend_data['resources']}")
                return True
            else:
                print(f"?? Frontend API responded with {response.status_code}: {response.text}")
                return False
        
        except Exception as e:
            print(f"?? Frontend API send failed: {e}")
            return False
    
    def check_pending_commands(self, command_callback):
        """Check for pending commands from frontend"""
        current_time = time.time()
        
        # Check every 5 seconds
        if current_time - self.last_command_check < 5:
            return
        
        self.last_command_check = current_time
        
        try:
            headers = {
                'Content-Type': 'application/json',
                'X-Api-Key': FRONTEND_API_KEY
            }
            
            response = requests.get(FRONTEND_COMMANDS_URL, headers=headers, timeout=10)
            
            if response.status_code == 200:
                commands = response.json()
                
                if isinstance(commands, list) and len(commands) > 0:
                    print(f"? Found {len(commands)} pending command(s)")
                    
                    for command in commands:
                        command_id = command.get('command_id')
                        if not command_id:
                            continue
                        
                        # Skip if already processed
                        if command_id in self.processed_commands:
                            continue
                        
                        device = command.get('device')
                        desired_state = command.get('state')
                        
                        if not all([device, desired_state, command_id]):
                            continue
                        
                        # Mark as processed
                        self.processed_commands.add(command_id)
                        
                        # Execute command
                        success, actual_state = command_callback(device, desired_state)
                        
                        # Send update back to frontend
                        self._send_command_update(command_id, success, actual_state)
                
                elif isinstance(commands, list):
                    print("? No pending commands found (empty list)")
        
        except Exception as e:
            print(f"?? Error checking commands: {e}")
    
    def _send_command_update(self, command_id, success, actual_state=None):
        """Send command execution result back to frontend"""
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
            
            headers = {
                'Content-Type': 'application/json',
                'X-Api-Key': FRONTEND_API_KEY
            }
            
            response = requests.post(FRONTEND_UPDATE_URL, json=update_data, headers=headers, timeout=10)
            
            if response.status_code == 200:
                print(f"? Command update sent for ID {command_id}: {'SUCCESS' if success else 'FAILED'}")
            else:
                print(f"?? Failed to send command update: {response.status_code} - {response.text}")
        
        except Exception as e:
            print(f"?? Error sending command update: {e}")
    
    def clear_command_cache(self):
        """Clear processed commands cache"""
        old_count = len(self.processed_commands)
        self.processed_commands.clear()
        print(f"? Cleared {old_count} processed commands from cache")
        return old_count