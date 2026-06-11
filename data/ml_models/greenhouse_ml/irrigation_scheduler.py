
import threading
import time
import json
from datetime import datetime, timedelta
import sqlite3

class IrrigationScheduler:
    def __init__(self, db_path, config, ml_config):
        self.db_path = db_path
        self.config = config
        self.ml_config = ml_config
        from .q_learning_simulator import QLearningSimulator
        from .linear_regression_model import SoilMoisturePredictor
        self.q_learning = QLearningSimulator(ml_config['Q_LEARNING_CONFIG'])
        self.lr_predictor = SoilMoisturePredictor(db_path, ml_config['LINEAR_REGRESSION_CONFIG'])
        self.irrigation_log = []
        self.daily_water_usage = 0
        self.last_irrigation_time = None
        self.system_state = ml_config['ML_STATES']['INITIAL']
        self.data_collection_start = None
        self._initialize_models()
    
    def _initialize_models(self):
        import os
        q_model_path = '/home/group4/GREENHOUSE/q_learning_model.pkl'
        if os.path.exists(q_model_path):
            if self.q_learning.load_model(q_model_path):
                print("Loaded existing Q-Learning model")
            else:
                print("Training new Q-Learning model...")
                self.q_learning.train()
                self.q_learning.save_model(q_model_path)
        else:
            print("Training Q-Learning model from scratch...")
            self.q_learning.train()
            self.q_learning.save_model(q_model_path)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM greenhouse_data WHERE temperature IS NOT NULL")
        count = cursor.fetchone()[0]
        conn.close()
        
        min_samples = self.ml_config['LINEAR_REGRESSION_CONFIG']['min_training_samples']
        if count >= min_samples:
            print(f"Enough data ({count} samples) for linear regression")
            self.system_state = self.ml_config['ML_STATES']['HYBRID']
            lr_model_dir = '/home/group4/GREENHOUSE/lr_models'
            if not self.lr_predictor.load_models(lr_model_dir):
                if self.lr_predictor.train():
                    self.lr_predictor.save_models(lr_model_dir)
                    self.system_state = self.ml_config['ML_STATES']['HYBRID']
        else:
            print(f"Insufficient data for linear regression ({count}/{min_samples})")
            self.data_collection_start = datetime.now()
            self.system_state = self.ml_config['ML_STATES']['COLLECTING_DATA']
    
    def make_irrigation_decision(self, sensor_data):
        if not self._check_constraints(sensor_data):
            return {
                'action': 0,
                'reason': 'Constraints not met',
                'system_state': self.system_state,
                'timestamp': datetime.now().isoformat()
            }
        
        if self.system_state in [self.ml_config['ML_STATES']['INITIAL'], self.ml_config['ML_STATES']['COLLECTING_DATA']]:
            action_duration = self.q_learning.get_action(sensor_data)
            reason = f"Q-Learning only (State: {self.system_state})"
            decision = {
                'action': action_duration,
                'reason': reason,
                'system_state': self.system_state,
                'model_used': 'q_learning',
                'timestamp': datetime.now().isoformat()
            }
        else:
            q_action = self.q_learning.get_action(sensor_data)
            lr_recommendation = self.lr_predictor.recommend_irrigation(sensor_data, q_action)
            decision = {
                'action': lr_recommendation['final_action'],
                'reason': lr_recommendation['reason'],
                'system_state': self.system_state,
                'model_used': 'hybrid_q_lr',
                'q_learning_action': lr_recommendation['q_learning_action'],
                'predicted_benefits': lr_recommendation['predicted_benefits'],
                'total_benefit': lr_recommendation['total_benefit'],
                'timestamp': lr_recommendation['timestamp']
            }
        
        self.irrigation_log.append(decision)
        if len(self.irrigation_log) > 1000:
            self.irrigation_log = self.irrigation_log[-1000:]
        
        return decision
    
    def _check_constraints(self, sensor_data):
        now = datetime.now()
        
        # Check night irrigation constraint
        if not self.ml_config['IRRIGATION_CONSTRAINTS']['night_irrigation']:
            if 22 <= now.hour or now.hour < 5:
                return False
        
        # Check minimum interval
        if self.last_irrigation_time:
            time_since_last = (now - self.last_irrigation_time).total_seconds() / 60
            if time_since_last < self.ml_config['IRRIGATION_CONSTRAINTS']['min_interval']:
                return False
        
        # Check daily water limit
        if self.daily_water_usage >= self.ml_config['IRRIGATION_CONSTRAINTS']['max_daily_water']:
            return False
        
        # Check water tank level
        if sensor_data.get('lowLevel', 0) == 1 and sensor_data.get('highLevel', 0) == 1:
            print("Water tank is empty")
            return False
        
        return True
    
    def get_system_status(self):
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
            'irrigation_decisions_today': len([l for l in self.irrigation_log if datetime.fromisoformat(l['timestamp']).date() == datetime.now().date()])
        }
    
    def update_system_state(self):
        if self.system_state == self.ml_config['ML_STATES']['COLLECTING_DATA']:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM greenhouse_data WHERE temperature IS NOT NULL)
            count = cursor.fetchone()[0]
            conn.close()
            
            min_samples = self.ml_config['LINEAR_REGRESSION_CONFIG']['min_training_samples']
            if count >= min_samples:
                print(f"Collected enough data ({count} samples). Training linear regression...")
                if self.lr_predictor.train():
                    self.lr_predictor.save_models('/home/group4/GREENHOUSE/lr_models')
                    self.system_state = self.ml_config['ML_STATES']['HYBRID']
                    print("Switched to HYBRID mode (Q-Learning + Linear Regression)")
    
    def train_all_models(self):
        """Force retrain all models"""
        print("Forcing retraining of all models...")
        
        # Retrain Q-Learning
        self.q_learning.train()
        self.q_learning.save_model('/home/group4/GREENHOUSE/q_learning_model.pkl')
        
        # Retrain Linear Regression
        if self.lr_predictor.train():
            self.lr_predictor.save_models('/home/group4/GREENHOUSE/lr_models')
        
        print("All models retrained successfully")
        return True
