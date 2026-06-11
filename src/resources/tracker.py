# -*- coding: utf-8 -*-
"""
Resource consumption tracking - FIXED DAILY COUNTERS with correct energy calculation
"""

from datetime import datetime, date
import sqlite3
from config import (
    WATER_FLOW_RATE_LPM, PUMP_POWER_KW, VALVE_POWER_KW,
    RASPBERRY_PI_POWER_KW, ESP32_POWER_KW
)

class ResourceTracker:
    def __init__(self, db_path):
        self.db_path = db_path
        
        # Resource tracking variables
        self.pump_runtime_seconds = 0
        self.valve_runtime_seconds = 0
        self.water_consumed_liters = 0.0
        self.energy_consumed_kwh = 0.0  # Pump/valve only
        
        # Daily tracking - ALWAYS load from database on startup
        self.daily_water_consumed_liters = 0.0
        self.daily_energy_consumed_kwh = 0.0  # Pump/valve only
        
        # FIXED DAILY VALUES (24/7 devices)
        self.DAILY_SYSTEM_ENERGY_KWH = 0.12  # Raspberry Pi: 5W × 24h = 0.12 kWh
        self.DAILY_ESP32_ENERGY_KWH = 0.0024  # ESP32: 0.1W × 24h = 0.0024 kWh
        
        # State tracking
        self.pump_last_state_change = None
        self.valve_last_state_change = None
        self.pump_current_state = False
        self.valve_current_state = False
        self.system_start_time = datetime.now()
        
        # Load existing data
        self._load_from_database()
        print(f"? ResourceTracker initialized. Today's totals: Water={self.daily_water_consumed_liters:.2f}L, Energy={self.daily_energy_consumed_kwh:.6f}kWh")

    def _load_from_database(self):
        """Load resource data from database on startup"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get cumulative totals from ALL TIME
            cursor.execute("""
                SELECT
                    COALESCE(SUM(pump_runtime_seconds), 0) as total_pump,
                    COALESCE(SUM(valve_runtime_seconds), 0) as total_valve,
                    COALESCE(SUM(water_consumed_liters), 0) as total_water,
                    COALESCE(SUM(energy_consumed_kwh), 0) as total_energy
                FROM resource_consumption
            """)
            result = cursor.fetchone()
            
            if result:
                self.pump_runtime_seconds = result[0] if result[0] is not None else 0
                self.valve_runtime_seconds = result[1] if result[1] is not None else 0
                self.water_consumed_liters = result[2] if result[2] is not None else 0.0
                self.energy_consumed_kwh = result[3] if result[3] is not None else 0.0
            
            # Get TODAY's totals from resource_consumption table
            cursor.execute("""
                SELECT
                    COALESCE(SUM(pump_runtime_seconds), 0) as today_pump,
                    COALESCE(SUM(valve_runtime_seconds), 0) as today_valve,
                    COALESCE(SUM(water_consumed_liters), 0) as today_water,
                    COALESCE(SUM(energy_consumed_kwh), 0) as today_energy
                FROM resource_consumption
                WHERE date(timestamp) = date('now', 'localtime')
            """)
            today_result = cursor.fetchone()
            
            if today_result:
                self.daily_water_consumed_liters = today_result[2] if today_result[2] is not None else 0.0
                self.daily_energy_consumed_kwh = today_result[3] if today_result[3] is not None else 0.0
            
            # Also check daily_resources table
            cursor.execute("""
                SELECT total_water_liters, total_energy_kwh
                FROM daily_resources WHERE date = date('now', 'localtime')
            """)
            daily_result = cursor.fetchone()
            
            if daily_result and daily_result[0] is not None:
                # Use the higher value (should be same, but just in case)
                self.daily_water_consumed_liters = max(self.daily_water_consumed_liters, daily_result[0])
                self.daily_energy_consumed_kwh = max(self.daily_energy_consumed_kwh, daily_result[1])
            
            conn.close()
            print(f"? Loaded resource data from database")
            
        except Exception as e:
            print(f"? Error loading resource data from database: {e}")
            import traceback
            traceback.print_exc()

    def update_tracking(self, sensor_data):
        """Update resource consumption tracking"""
        # Get current states
        pump_state = bool(sensor_data.get('pump', 0))
        valve_state = bool(sensor_data.get('valve', 0))
        current_time = datetime.now()
        
        # Initialize if needed
        if self.pump_last_state_change is None:
            self.pump_last_state_change = current_time
        if self.valve_last_state_change is None:
            self.valve_last_state_change = current_time
        
        # Track state changes
        pump_state_changed = pump_state != self.pump_current_state
        valve_state_changed = valve_state != self.valve_current_state
        
        # Process pump state change
        if pump_state_changed:
            if self.pump_current_state:  # Pump was ON, now OFF
                session_runtime = (current_time - self.pump_last_state_change).total_seconds()
                self.pump_runtime_seconds += session_runtime
                
                # Calculate water and energy for THIS SESSION ONLY
                session_water = (WATER_FLOW_RATE_LPM * session_runtime) / 60
                session_energy = (PUMP_POWER_KW * session_runtime) / 3600
                
                self.water_consumed_liters += session_water
                self.energy_consumed_kwh += session_energy
                self.daily_water_consumed_liters += session_water
                self.daily_energy_consumed_kwh += session_energy
                
                print(f"? Pump OFF: Session={session_runtime:.1f}s, +{session_water:.3f}L, +{session_energy:.6f}kWh")
                print(f"? Daily total now: {self.daily_water_consumed_liters:.2f}L water, {self.daily_energy_consumed_kwh:.6f}kWh energy")
                
                # Save to database - FIXED: Include valve energy
                self._save_session_to_db(
                    session_runtime, 0,  # pump runtime, 0 valve runtime for pump session
                    session_water, session_energy,  # water and energy
                    1, 0  # pump was ON, now OFF
                )
            
            self.pump_current_state = pump_state
            self.pump_last_state_change = current_time
        
        # Process valve state change
        if valve_state_changed:
            if self.valve_current_state:  # Valve was ON, now OFF
                session_runtime = (current_time - self.valve_last_state_change).total_seconds()
                self.valve_runtime_seconds += session_runtime
                
                # Calculate energy for THIS SESSION ONLY - VALVE ENERGY ONLY
                session_energy = (VALVE_POWER_KW * session_runtime) / 3600
                self.energy_consumed_kwh += session_energy
                self.daily_energy_consumed_kwh += session_energy
                
                print(f"? Valve OFF: Session={session_runtime:.1f}s, +{session_energy:.6f}kWh (valve only)")
                print(f"? Daily energy total now: {self.daily_energy_consumed_kwh:.6f}kWh")
                
                # Save to database - VALVE ONLY session
                self._save_session_to_db(
                    0, session_runtime,  # 0 pump, valve runtime
                    0, session_energy,  # 0 water, VALVE energy only
                    0, 1  # valve was ON, now OFF
                )
            
            self.valve_current_state = valve_state
            self.valve_last_state_change = current_time
        
        return self._get_current_stats()

    def _save_session_to_db(self, pump_runtime, valve_runtime, water, energy, pump_state, valve_state):
        """Save session data to database - FIXED ENERGY CALCULATION"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Save SESSION data
            cursor.execute("""
                INSERT INTO resource_consumption
                (timestamp, pump_runtime_seconds, valve_runtime_seconds,
                 water_consumed_liters, energy_consumed_kwh,
                 pump_state, valve_state)
                VALUES (datetime('now', 'localtime'), ?, ?, ?, ?, ?, ?)
            """, (
                pump_runtime, valve_runtime,
                water, energy,
                pump_state, valve_state
            ))
            
            # Update daily resources table
            self._update_daily_resources()
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"? Database error saving session: {e}")
            import traceback
            traceback.print_exc()

    def _update_daily_resources(self):
        """Update daily resources table with correct energy calculation"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Calculate DAILY totals from resource_consumption table
            cursor.execute("""
                SELECT
                    COALESCE(SUM(pump_runtime_seconds), 0) as pump_seconds,
                    COALESCE(SUM(valve_runtime_seconds), 0) as valve_seconds,
                    COALESCE(SUM(water_consumed_liters), 0) as total_water,
                    COALESCE(SUM(energy_consumed_kwh), 0) as total_energy
                FROM resource_consumption
                WHERE date(timestamp) = date('now', 'localtime')
            """)
            
            result = cursor.fetchone()
            daily_pump_seconds = result[0] if result[0] else 0
            daily_valve_seconds = result[1] if result[1] else 0
            daily_water = result[2] if result[2] else 0
            daily_energy = result[3] if result[3] else 0
            
            # Count irrigation events (pump ON events)
            cursor.execute("""
                SELECT COUNT(*) FROM resource_consumption
                WHERE date(timestamp) = date('now', 'localtime')
                AND pump_state = 1
            """)
            irrigation_events = cursor.fetchone()[0]
            
            # Update or insert daily record
            cursor.execute("""
                INSERT OR REPLACE INTO daily_resources
                (date, total_water_liters, total_energy_kwh,
                 system_energy_kwh, esp32_energy_kwh,
                 pump_runtime_hours, valve_runtime_hours,
                 irrigation_events)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().strftime('%Y-%m-%d'),
                daily_water,
                daily_energy,
                self.DAILY_SYSTEM_ENERGY_KWH,
                self.DAILY_ESP32_ENERGY_KWH,
                daily_pump_seconds / 3600,  # Convert to HOURS
                daily_valve_seconds / 3600,  # Convert to HOURS
                irrigation_events
            ))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"? Error updating daily resources: {e}")
            import traceback
            traceback.print_exc()

    def _get_current_stats(self):
        """Get current statistics"""
        # Calculate display runtimes
        current_time = datetime.now()
        total_pump_display = self.pump_runtime_seconds
        total_valve_display = self.valve_runtime_seconds
        
        # Add current session if active
        if self.pump_current_state and self.pump_last_state_change:
            total_pump_display += (current_time - self.pump_last_state_change).total_seconds()
        
        if self.valve_current_state and self.valve_last_state_change:
            total_valve_display += (current_time - self.valve_last_state_change).total_seconds()
        
        # Format for display
        pump_hours = int(total_pump_display // 3600)
        pump_minutes = int((total_pump_display % 3600) // 60)
        valve_hours = int(total_valve_display // 3600)
        valve_minutes = int((total_valve_display % 3600) // 60)
        
        # Calculate system uptime
        system_uptime_seconds = (datetime.now() - self.system_start_time).total_seconds()
        
        return {
            'pump_runtime_seconds': total_pump_display,
            'valve_runtime_seconds': total_valve_display,
            'water_consumed_liters': round(self.water_consumed_liters, 2),
            'energy_consumed_kwh': round(self.energy_consumed_kwh, 6),
            
            # Daily totals
            'daily_water_liters': round(self.daily_water_consumed_liters, 2),
            'daily_energy_kwh': round(self.daily_energy_consumed_kwh, 6),
            'daily_system_energy_kwh': round(self.DAILY_SYSTEM_ENERGY_KWH, 6),
            'daily_esp32_energy_kwh': round(self.DAILY_ESP32_ENERGY_KWH, 6),
            
            'current_pump_state': self.pump_current_state,
            'current_valve_state': self.valve_current_state,
            'pump_runtime_display': f'{pump_hours}h {pump_minutes}m',
            'valve_runtime_display': f'{valve_hours}h {valve_minutes}m',
            'water_flow_rate_lpm': WATER_FLOW_RATE_LPM,
            'pump_power_kw': PUMP_POWER_KW,
            'valve_power_kw': VALVE_POWER_KW,
            'raspberry_pi_power_kw': RASPBERRY_PI_POWER_KW,
            'esp32_power_kw': ESP32_POWER_KW,
            'system_uptime_seconds': system_uptime_seconds
        }

    def get_resource_usage(self):
        """Get current resource usage statistics"""
        return self._get_current_stats()

    def reset_counters(self):
        """Reset all resource counters (WARNING: clears historical data)"""
        self.pump_runtime_seconds = 0
        self.valve_runtime_seconds = 0
        self.water_consumed_liters = 0.0
        self.energy_consumed_kwh = 0.0
        self.daily_water_consumed_liters = 0.0
        self.daily_energy_consumed_kwh = 0.0
        
        print("? Resource counters reset to zero")
        return self.get_resource_usage()