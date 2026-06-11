# -*- coding: utf-8 -*-
"""
Schedule Executor - Automatically executes AI irrigation schedules
"""

import sqlite3
import time
from datetime import datetime, timedelta
import json
import threading


class ScheduleExecutor:
    def __init__(self, db_path, serial_reader=None):
        self.db_path = db_path
        self.serial_reader = serial_reader
        self.running = False
        self.check_interval = 30  # Check every 30 seconds
        self.last_check = None
        print("? Schedule Executor initialized" if serial_reader 
              else "?? Schedule Executor initialized WITHOUT serial connection")
   
    def start(self):
        """Start the schedule executor"""
        self.running = True
        print("? Schedule Executor started")
   
    def stop(self):
        """Stop the schedule executor"""
        self.running = False
        print("? Schedule Executor stopped")
   
    def check_and_execute_schedules(self):
        """Check for schedules that need to be executed"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
           
            # Get current time
            current_time = datetime.now().isoformat()
           
            # Get schedules that are scheduled and past their scheduled time
            cursor.execute("""
                SELECT
                    id, decision_id, action, reason,
                    scheduled_time, execution_time, status
                FROM ai_schedules
                WHERE status = 'scheduled'
                AND datetime(scheduled_time) <= datetime(?)
                ORDER BY scheduled_time
                LIMIT 5
            """, (current_time,))
           
            schedules = [dict(row) for row in cursor.fetchall()]
           
            if len(schedules) > 0:
                print(f"? Found {len(schedules)} schedules to check")
                for schedule in schedules:
                    print(f"   Schedule {schedule['id']}: {schedule['scheduled_time']} -> {schedule['reason']}")
           
            executed_count = 0
            for schedule in schedules:
                print(f"? Executing schedule: {schedule['id']} - {schedule['reason']}")
                success = self.execute_schedule(schedule)
                if success:
                    executed_count += 1
           
            conn.close()
            self.last_check = datetime.now()
            return executed_count
           
        except Exception as e:
            print(f"? Error checking schedules: {e}")
            import traceback
            traceback.print_exc()
            return 0
   
    def execute_schedule(self, schedule):
        """Execute a single schedule"""
        try:
            schedule_id = schedule['id']
            decision_id = schedule['decision_id']
            action = schedule['action']
            reason = schedule['reason']
           
            print(f"? Executing schedule {schedule_id}: {action} minutes - {reason}")
           
            # Mark as executing
            self.update_schedule_status(schedule_id, 'executing')
           
            # If no decision_id exists, create one
            if not decision_id:
                decision_id = self.create_ai_decision_for_schedule(schedule_id, action, reason)
                if decision_id:
                    print(f"? Created decision record {decision_id} for schedule {schedule_id}")
           
            # Execute the irrigation - PUMP-ONLY VERSION
            success = self.execute_irrigation(action, reason, decision_id, schedule_id)
           
            # Update status based on result
            if success:
                self.update_schedule_status(schedule_id, 'completed')
                print(f"? Schedule {schedule_id} completed successfully")
            else:
                self.update_schedule_status(schedule_id, 'failed')
                print(f"? Schedule {schedule_id} failed")
           
            return success
           
        except Exception as e:
            print(f"? Error executing schedule {schedule_id}: {e}")
            self.update_schedule_status(schedule_id, 'failed')
            return False
    
    def create_ai_decision_for_schedule(self, schedule_id, action, reason):
        """Create an AI decision record for a schedule"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Create decision record
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
                1  # Mark as executed
            ))
            
            decision_id = cursor.lastrowid
            
            # Update schedule with decision_id
            cursor.execute("""
                UPDATE ai_schedules
                SET decision_id = ?
                WHERE id = ?
            """, (decision_id, schedule_id))
            
            conn.commit()
            conn.close()
            
            return decision_id
            
        except Exception as e:
            print(f"? Error creating AI decision for schedule {schedule_id}: {e}")
            return None
   
    def execute_irrigation(self, duration_minutes, reason, decision_id=None, schedule_id=None):
        """Execute irrigation by controlling pump ONLY (valve stays closed)"""
        try:
            # Convert minutes to seconds for display
            duration_seconds = int(duration_minutes * 60)
            print(f"? Starting SCHEDULED PUMP-ONLY irrigation: {duration_seconds} seconds - {reason}")
            
            # Safety check: don't run if no serial connection
            if not self.serial_reader:
                print("? No serial connection - cannot execute irrigation")
                return False
            
            # CRITICAL CHANGE: ONLY turn on pump
            print(f"? Turning pump ON (valve stays closed)")
            pump_success = self.serial_reader.send_command({"pump": "on"})
            
            if not pump_success:
                print("? Failed to turn pump ON")
                return False
            
            # Step 3: ACTUALLY RUN FOR THE DURATION (in seconds)
            print(f"?? PUMP-ONLY irrigation ACTUALLY running for {duration_seconds} seconds")
            
            # Cap at 10 minutes (600 seconds) for safety, but allow short test durations
            max_duration = min(duration_seconds, 600)
            
            # Actually wait for the duration in smaller chunks
            for i in range(max_duration):
                time.sleep(1)
                # Show progress every 5 seconds for short durations, every 30 for long
                progress_interval = 5 if max_duration <= 30 else 30
                if i % progress_interval == 0:
                    print(f"?? PUMP-ONLY irrigation running... {i+1}/{max_duration} seconds")
            
            # Step 4: Turn off pump ONLY
            print(f"? Turning pump OFF")
            self.serial_reader.send_command({"pump": "off"})
            
            # DO NOT turn off valve (it wasn't turned on)
            
            # Record the execution in irrigation history
            self.record_irrigation_history(duration_minutes, reason, decision_id, schedule_id, success=True)
            
            print(f"? PUMP-ONLY irrigation completed successfully - {duration_seconds} seconds")
            return True
            
        except Exception as e:
            print(f"? Error during PUMP-ONLY irrigation: {e}")
            # Try to turn pump off
            try:
                if self.serial_reader:
                    self.serial_reader.send_command({"pump": "off"})
            except:
                pass
            
            self.record_irrigation_history(duration_minutes, reason, decision_id, schedule_id, success=False)
            return False
    
    def record_irrigation_history(self, duration, reason, decision_id, schedule_id, success):
        """Record irrigation execution in irrigation history"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Log to manual_commands table (for compatibility)
            cursor.execute("""
                INSERT INTO manual_commands
                (timestamp, device, command, requested_state, actual_state, success, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'pump',
                'ai_scheduled_irrigation',
                'ON',
                'ON' if success else 'OFF',
                1 if success else 0,
                f"AI Schedule: {duration} minutes - {reason} (Schedule ID: {schedule_id}) - PUMP-ONLY"
            ))
            
            # If we have a decision_id, update the decision record
            if decision_id:
                cursor.execute("""
                    UPDATE ai_decisions
                    SET executed = ?
                    WHERE id = ?
                """, (1 if success else 0, decision_id))
            
            # Update schedule with execution time
            if schedule_id:
                execution_time = datetime.now().isoformat()
                cursor.execute("""
                    UPDATE ai_schedules
                    SET execution_time = ?
                    WHERE id = ?
                """, (execution_time, schedule_id))
            
            conn.commit()
            conn.close()
            
            print(f"? Recorded irrigation history: {duration} mins, success={success} (PUMP-ONLY)")
           
        except Exception as e:
            print(f"? Error recording irrigation history: {e}")
   
    def update_schedule_status(self, schedule_id, status):
        """Update schedule status in database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
           
            execution_time = datetime.now().isoformat() if status == 'completed' else None
           
            if status == 'completed':
                cursor.execute("""
                    UPDATE ai_schedules
                    SET status = ?, execution_time = ?
                    WHERE id = ?
                """, (status, execution_time, schedule_id))
            else:
                cursor.execute("""
                    UPDATE ai_schedules
                    SET status = ?
                    WHERE id = ?
                """, (status, schedule_id))
           
            conn.commit()
            conn.close()
           
            print(f"? Updated schedule {schedule_id} to {status}")
           
        except Exception as e:
            print(f"? Error updating schedule status: {e}")
            import traceback
            traceback.print_exc()
   
    def run_continuously(self):
        """Run the schedule executor continuously"""
        self.start()
        print("? Schedule Executor running continuously...")
       
        while self.running:
            try:
                # Check and execute schedules
                executed = self.check_and_execute_schedules()
               
                if executed > 0:
                    print(f"? Executed {executed} schedule(s)")
               
                # Wait before next check
                time.sleep(self.check_interval)
               
            except Exception as e:
                print(f"? Error in schedule executor loop: {e}")
                time.sleep(5)

    def emergency_stop(self):
        """Emergency stop all irrigation"""
        print("? EMERGENCY STOP - Turning off pump and valve")
        try:
            if self.serial_reader:
                self.serial_reader.send_command({"pump": "off"})
                self.serial_reader.send_command({"valve": "off"})
                print("? Emergency stop executed")
                return True
        except:
            pass
        return False

    def test_schedule(self):
        """Test that creates a pump-only schedule"""
        try:
            # Create a test schedule for 1 minute from now
            schedule_time = (datetime.now() + timedelta(minutes=1)).isoformat()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # First create a dummy decision
            cursor.execute("""
                INSERT INTO ai_decisions
                (timestamp, action, reason, system_state, model_used, executed)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                0.17,  # 10 seconds in minutes
                "TEST: Pump-only irrigation (10 seconds)",
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
                0.17,  # 10 seconds in minutes
                "TEST: Pump-only irrigation for debugging (10 seconds)",
                schedule_time,
                "scheduled"
            ))
            
            conn.commit()
            conn.close()
            
            return {
                "success": True,
                "message": "Test schedule created (PUMP-ONLY)",
                "schedule_time": schedule_time,
                "duration": 0.17,
                "decision_id": decision_id,
                "note": "PUMP-ONLY - valve stays closed"
            }
            
        except Exception as e:
            print(f"? Error creating test schedule: {e}")
            return {"success": False, "error": str(e)}