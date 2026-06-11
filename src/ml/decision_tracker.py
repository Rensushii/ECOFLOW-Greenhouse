# -*- coding: utf-8 -*-
"""
AI Decision Tracker with database persistence
"""

import sqlite3
import json
from datetime import datetime, timedelta
import os

class AIDecisionTracker:
    def __init__(self, db_path, max_history=100):
        self.db_path = db_path
        self.max_history = max_history
        self.decision_history = []
        self.upcoming_schedules = []
        self.soil_predictions = []
        self._create_decision_tables()
        self._load_from_database()
    
    def _create_decision_tables(self):
        """Create tables for storing AI decisions in database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create AI decisions table (already created in models.py)
        # This ensures it exists
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
        
        # Create AI schedules table
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
        
        # Create AI predictions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            current_soil1 REAL,
            current_soil2 REAL,
            current_soil3 REAL,
            predicted_change1 REAL,
            predicted_change2 REAL,
            predicted_change3 REAL,
            predicted_1h_soil1 REAL,
            predicted_1h_soil2 REAL,
            predicted_1h_soil3 REAL,
            predicted_3h_soil1 REAL,
            predicted_3h_soil2 REAL,
            predicted_3h_soil3 REAL,
            predicted_6h_soil1 REAL,
            predicted_6h_soil2 REAL,
            predicted_6h_soil3 REAL
        )
        ''')
        
        # Create AI statistics table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            period TEXT,
            total_decisions INTEGER,
            irrigation_decisions INTEGER,
            total_irrigation_minutes REAL,
            average_irrigation_duration REAL,
            model_q_learning INTEGER,
            model_hybrid_q_lr INTEGER,
            model_constraint_check INTEGER,
            model_soil_check INTEGER,
            model_unknown INTEGER
        )
        ''')
        
        conn.commit()
        conn.close()
        print("? AI decision tables created/verified")
    
    def _load_from_database(self):
        """Load AI decision history from database on startup"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Load recent decisions
            cursor.execute("""
                SELECT timestamp, action, reason, system_state, model_used,
                       q_learning_action, predicted_benefits, total_benefit, sensor_data
                FROM ai_decisions
                ORDER BY timestamp DESC
                LIMIT ?
            """, (self.max_history,))
            
            rows = cursor.fetchall()
            for row in rows:
                decision = {
                    'history_id': len(self.decision_history) + 1,
                    'timestamp': row[0],
                    'action': row[1],
                    'reason': row[2],
                    'system_state': row[3],
                    'model_used': row[4],
                    'q_learning_action': row[5],
                    'predicted_benefits': json.loads(row[6]) if row[6] else {},
                    'total_benefit': row[7],
                    'sensor_data': json.loads(row[8]) if row[8] else {}
                }
                self.decision_history.append(decision)
            
            # Load upcoming schedules
            cursor.execute("""
                SELECT decision_id, action, reason, scheduled_time, execution_time, status
                FROM ai_schedules
                WHERE status = 'scheduled'
                AND scheduled_time > datetime('now', 'localtime')
                ORDER BY scheduled_time
            """)
            
            rows = cursor.fetchall()
            for i, row in enumerate(rows):
                schedule = {
                    'schedule_id': i + 1,
                    'decision_id': row[0],
                    'action': row[1],
                    'reason': row[2],
                    'scheduled_time': row[3],
                    'execution_time': row[4],
                    'status': row[5],
                    'created_at': datetime.now().isoformat()
                }
                self.upcoming_schedules.append(schedule)
            
            # Load recent predictions
            cursor.execute("""
                SELECT timestamp, current_soil1, current_soil2, current_soil3,
                       predicted_change1, predicted_change2, predicted_change3,
                       predicted_1h_soil1, predicted_1h_soil2, predicted_1h_soil3,
                       predicted_3h_soil1, predicted_3h_soil2, predicted_3h_soil3,
                       predicted_6h_soil1, predicted_6h_soil2, predicted_6h_soil3
                FROM ai_predictions
                ORDER BY timestamp DESC
                LIMIT 20
            """)
            
            rows = cursor.fetchall()
            for row in rows:
                prediction = {
                    'timestamp': row[0],
                    'current_soil': {
                        'soil1': row[1],
                        'soil2': row[2],
                        'soil3': row[3]
                    },
                    'predicted_changes': {
                        'soil1': row[4],
                        'soil2': row[5],
                        'soil3': row[6]
                    },
                    'predicted_1h': {
                        'soil1': row[7],
                        'soil2': row[8],
                        'soil3': row[9]
                    },
                    'predicted_3h': {
                        'soil1': row[10],
                        'soil2': row[11],
                        'soil3': row[12]
                    },
                    'predicted_6h': {
                        'soil1': row[13],
                        'soil2': row[14],
                        'soil3': row[15]
                    }
                }
                self.soil_predictions.append(prediction)
            
            conn.close()
            print(f"? Loaded {len(self.decision_history)} AI decisions from database")
            print(f"? Loaded {len(self.upcoming_schedules)} scheduled events from database")
            print(f"? Loaded {len(self.soil_predictions)} predictions from database")
        
        except Exception as e:
            print(f"?? Error loading AI decisions from database: {e}")
    
    def add_decision(self, decision):
        """Add a decision to history and save to database"""
        # Ensure decision has all required fields
        decision_copy = decision.copy()
        
        # Store sensor data if available
        sensor_data_json = json.dumps({})  # Placeholder
        
        # Ensure all required fields are present
        required_fields = ['action', 'reason', 'system_state', 'model_used', 'timestamp']
        for field in required_fields:
            if field not in decision_copy:
                if field == 'action':
                    decision_copy[field] = 0
                elif field == 'reason':
                    decision_copy[field] = 'Unknown reason'
                elif field == 'system_state':
                    decision_copy[field] = 'unknown'
                elif field == 'model_used':
                    decision_copy[field] = 'unknown'
                elif field == 'timestamp':
                    decision_copy[field] = datetime.now().isoformat()
        
        # Save to database
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Insert decision
            cursor.execute("""
                INSERT INTO ai_decisions
                (timestamp, action, reason, system_state, model_used,
                 q_learning_action, predicted_benefits, total_benefit, sensor_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                decision_copy.get('timestamp'),
                decision_copy.get('action'),
                decision_copy.get('reason'),
                decision_copy.get('system_state'),
                decision_copy.get('model_used'),
                decision_copy.get('q_learning_action'),
                json.dumps(decision_copy.get('predicted_benefits', {})),
                decision_copy.get('total_benefit', 0),
                sensor_data_json
            ))
            
            decision_id = cursor.lastrowid
            conn.commit()
            
            # Update decision copy with ID
            decision_copy['history_id'] = decision_id
            decision_copy['db_id'] = decision_id
            
            # Add to memory
            self.decision_history.insert(0, decision_copy)
            
            # Keep only recent history
            if len(self.decision_history) > self.max_history:
                self.decision_history = self.decision_history[:self.max_history]
            
            # Update statistics
            self._update_statistics()
            
            conn.close()
            print(f"? AI Decision saved to database: Action={decision_copy.get('action')} mins")
        
        except Exception as e:
            print(f"?? Error saving AI decision to database: {e}")
            # Fallback to memory only
            decision_copy['history_id'] = len(self.decision_history) + 1
            self.decision_history.insert(0, decision_copy)
            
            if len(self.decision_history) > self.max_history:
                self.decision_history = self.decision_history[:self.max_history]
        
        # If decision has an action > 0, schedule it
        if decision_copy.get('action', 0) > 0:
            self.schedule_irrigation(decision_copy)
    
    def schedule_irrigation(self, decision):
        """Schedule an irrigation event"""
        # Schedule for 1 minute from now for immediate execution
        schedule_time = datetime.now() + timedelta(minutes=1)
        execution_time = schedule_time + timedelta(minutes=decision.get('action', 0))
        
        schedule = {
            'schedule_id': len(self.upcoming_schedules) + 1,
            'decision_id': decision.get('history_id'),
            'action': decision.get('action'),
            'reason': decision.get('reason'),
            'scheduled_time': schedule_time.isoformat(),
            'execution_time': execution_time.isoformat(),
            'status': 'scheduled',
            'created_at': datetime.now().isoformat()
        }
        
        # Save to database
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO ai_schedules
                (decision_id, action, reason, scheduled_time, execution_time, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                decision.get('history_id'),
                schedule['action'],
                schedule['reason'],
                schedule['scheduled_time'],
                schedule['execution_time'],
                schedule['status']
            ))
            
            schedule_id = cursor.lastrowid
            schedule['db_id'] = schedule_id
            conn.commit()
            conn.close()
            
            print(f"? Schedule saved to database with ID: {schedule_id}")
            
        except Exception as e:
            print(f"?? Error saving schedule to database: {e}")
            schedule['db_id'] = None
        
        self.upcoming_schedules.append(schedule)
        print(f"? Irrigation scheduled: {decision.get('action')} mins at {schedule_time.strftime('%H:%M')}")
        
        # Clean up old schedules
        self.upcoming_schedules = [s for s in self.upcoming_schedules
                                  if datetime.fromisoformat(s['scheduled_time']) > datetime.now() - timedelta(hours=24)]
        
        return schedule
    
    def _update_statistics(self):
        """Update AI statistics in database"""
        try:
            # Calculate daily statistics
            today = datetime.now().date()
            start_of_day = datetime.combine(today, datetime.min.time())
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT
                    COUNT(*) as total_decisions,
                    SUM(CASE WHEN action > 0 THEN 1 ELSE 0 END) as irrigation_decisions,
                    SUM(action) as total_irrigation_minutes,
                    AVG(CASE WHEN action > 0 THEN action END) as average_irrigation_duration,
                    SUM(CASE WHEN model_used = 'q_learning' THEN 1 ELSE 0 END) as model_q_learning,
                    SUM(CASE WHEN model_used = 'hybrid_q_lr' THEN 1 ELSE 0 END) as model_hybrid_q_lr,
                    SUM(CASE WHEN model_used = 'constraint_check' THEN 1 ELSE 0 END) as model_constraint_check,
                    SUM(CASE WHEN model_used = 'soil_check' THEN 1 ELSE 0 END) as model_soil_check,
                    SUM(CASE WHEN model_used NOT IN ('q_learning', 'hybrid_q_lr', 'constraint_check', 'soil_check') THEN 1 ELSE 0 END) as model_unknown
                FROM ai_decisions
                WHERE timestamp >= ?
            """, (start_of_day.isoformat(),))
            
            stats_row = cursor.fetchone()
            
            if stats_row:
                cursor.execute("""
                    INSERT OR REPLACE INTO ai_statistics
                    (timestamp, period, total_decisions, irrigation_decisions,
                     total_irrigation_minutes, average_irrigation_duration,
                     model_q_learning, model_hybrid_q_lr, model_constraint_check,
                     model_soil_check, model_unknown)
                    VALUES (?, 'daily', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    stats_row[0] or 0,
                    stats_row[1] or 0,
                    stats_row[2] or 0,
                    stats_row[3] or 0,
                    stats_row[4] or 0,
                    stats_row[5] or 0,
                    stats_row[6] or 0,
                    stats_row[7] or 0,
                    stats_row[8] or 0
                ))
                
                conn.commit()
            
            conn.close()
        
        except Exception as e:
            print(f"?? Error updating AI statistics: {e}")
    
    def get_recent_decisions(self, limit=10):
        """Get recent decisions from memory"""
        return self.decision_history[:limit] if self.decision_history else []
    
    def get_upcoming_schedules(self):
        """Get upcoming schedules from memory"""
        now = datetime.now()
        upcoming = []
        for schedule in self.upcoming_schedules:
            if 'scheduled_time' in schedule:
                try:
                    scheduled_time = datetime.fromisoformat(schedule['scheduled_time'])
                    if scheduled_time > now and schedule.get('status') == 'scheduled':
                        upcoming.append(schedule)
                except:
                    continue
        
        # Sort by scheduled time
        upcoming.sort(key=lambda x: datetime.fromisoformat(x['scheduled_time']) if 'scheduled_time' in x else datetime.now())
        return upcoming
    
    def get_recent_predictions(self, limit=5):
        """Get recent predictions from memory"""
        return self.soil_predictions[:limit] if self.soil_predictions else []
    
    def get_statistics(self):
        """Get AI decision statistics"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get overall statistics
            cursor.execute("""
                SELECT
                    COUNT(*) as total_decisions,
                    SUM(CASE WHEN action > 0 THEN 1 ELSE 0 END) as irrigation_decisions,
                    SUM(action) as total_irrigation_minutes,
                    AVG(CASE WHEN action > 0 THEN action END) as average_irrigation_duration
                FROM ai_decisions
            """)
            
            stats_row = cursor.fetchone()
            
            # Get model breakdown
            cursor.execute("""
                SELECT
                    model_used,
                    COUNT(*) as count
                FROM ai_decisions
                GROUP BY model_used
                ORDER BY count DESC
            """)
            
            model_rows = cursor.fetchall()
            decision_breakdown = {row[0]: row[1] for row in model_rows}
            
            conn.close()
            
            if stats_row:
                return {
                    'total_decisions': stats_row[0] or 0,
                    'irrigation_decisions': stats_row[1] or 0,
                    'total_irrigation_minutes': stats_row[2] or 0,
                    'average_irrigation_duration': stats_row[3] or 0,
                    'decision_breakdown': decision_breakdown
                }
        
        except Exception as e:
            print(f"?? Error getting AI statistics from database: {e}")
        
        # Fallback to memory calculation
        if not self.decision_history:
            return {
                'total_decisions': 0,
                'irrigation_decisions': 0,
                'total_irrigation_minutes': 0,
                'average_irrigation_duration': 0,
                'decision_breakdown': {}
            }
        
        total_decisions = len(self.decision_history)
        
        # Filter irrigation decisions and calculate total minutes
        irrigation_decisions = []
        total_irrigation_minutes = 0
        
        for d in self.decision_history:
            action = d.get('action')
            if action is not None and action > 0:
                irrigation_decisions.append(d)
                total_irrigation_minutes += float(action)
        
        # Calculate average irrigation duration
        if irrigation_decisions:
            average_irrigation_duration = total_irrigation_minutes / len(irrigation_decisions)
        else:
            average_irrigation_duration = 0
        
        # Count decisions by model used
        decision_breakdown = {}
        for decision in self.decision_history:
            model = decision.get('model_used', 'unknown')
            if model not in decision_breakdown:
                decision_breakdown[model] = 0
            decision_breakdown[model] += 1
        
        return {
            'total_decisions': total_decisions,
            'irrigation_decisions': len(irrigation_decisions),
            'total_irrigation_minutes': total_irrigation_minutes,
            'average_irrigation_duration': average_irrigation_duration,
            'decision_breakdown': decision_breakdown
        }
    
    def get_decision_timeline(self, hours=24):
        """Get decisions for the last X hours from database"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT timestamp, action, reason, system_state, model_used,
                       q_learning_action, predicted_benefits, total_benefit
                FROM ai_decisions
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
            """, (cutoff_time.isoformat(),))
            
            rows = cursor.fetchall()
            conn.close()
            
            recent_decisions = []
            for row in rows:
                decision = {
                    'timestamp': row[0],
                    'action': row[1],
                    'reason': row[2],
                    'system_state': row[3],
                    'model_used': row[4],
                    'q_learning_action': row[5],
                    'predicted_benefits': json.loads(row[6]) if row[6] else {},
                    'total_benefit': row[7]
                }
                recent_decisions.append(decision)
            
            return recent_decisions
        
        except Exception as e:
            print(f"?? Error getting decision timeline from database: {e}")
            
            # Fallback to memory
            cutoff_time = datetime.now() - timedelta(hours=hours)
            recent_decisions = []
            
            for decision in self.decision_history:
                if 'timestamp' in decision:
                    try:
                        decision_time = datetime.fromisoformat(decision['timestamp'])
                        if decision_time > cutoff_time:
                            recent_decisions.append(decision)
                    except:
                        continue
            
            return recent_decisions