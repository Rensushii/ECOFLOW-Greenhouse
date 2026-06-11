
import numpy as np
import pandas as pd
import pickle
import os
from datetime import datetime
import sqlite3
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler, PolynomialFeatures
from sklearn.model_selection import train_test_split

class SoilMoisturePredictor:
    def __init__(self, db_path, config):
        self.db_path = db_path
        self.config = config
        self.models = {}
        self.scalers = {}
        self.poly_features = {}
        self.training_history = []
    
    def prepare_features(self, df):
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['hour'] = df['timestamp'].dt.hour
            df['day_of_week'] = df['timestamp'].dt.dayofweek
            df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
            df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
            df['is_daytime'] = (df['hour'] >= 6) & (df['hour'] <= 18)
        
        # Basic features
        df['temp_humidity'] = df['temperature'] * df['humidity'] / 100
        df['soil_avg'] = (df['soil1'] + df['soil2'] + df['soil3']) / 3
        
        # Advanced features
        df['soil_gradient_1_2'] = df['soil1'] - df['soil2']
        df['soil_gradient_2_3'] = df['soil2'] - df['soil3']
        df['evaporation_est'] = 0.5 * (df['temperature'] / 30) * (1 - df['humidity'] / 100)
        
        # Calculate hours since last irrigation
        if 'valve' in df.columns:
            df['irrigation_event'] = (df['valve'] == 1) & (df['pump'] == 1)
            df['hours_since_last_irrigation'] = df.groupby(
                (~df['irrigation_event']).cumsum()
            ).cumcount()
        
        return df
    
    def load_training_data(self, days=7):
        conn = sqlite3.connect(self.db_path)
        query = f"""
        SELECT timestamp, temperature, humidity, soil1, soil2, soil3, 
               valve, pump, soil_avg, hour, day_of_week, hour_sin, hour_cos,
               is_daytime, temp_humidity, soil_gradient_1_2, soil_gradient_2_3,
               evaporation_est, hours_since_last_irrigation
        FROM greenhouse_data 
        WHERE timestamp >= datetime('now', '-{days} days') 
        AND temperature IS NOT NULL
        AND humidity IS NOT NULL
        ORDER BY timestamp
        """
        try:
            df = pd.read_sql_query(query, conn)
        except:
            # Fallback to basic data if advanced features not available
            query = f"""
            SELECT timestamp, temperature, humidity, soil1, soil2, soil3, valve, pump
            FROM greenhouse_data 
            WHERE timestamp >= datetime('now', '-{days} days') 
            ORDER BY timestamp
            """
            df = pd.read_sql_query(query, conn)
            df = self.prepare_features(df)
        
        conn.close()
        print(f"Loaded {len(df)} samples for training")
        return df
    
    def calculate_soil_changes(self, df):
        """Calculate soil moisture changes for training targets"""
        df = df.copy()
        for zone in ['soil1', 'soil2', 'soil3']:
            df[f'{zone}_change'] = df[zone].diff()
        return df
    
    def train(self):
        df = self.load_training_data(days=7)
        if df is None or len(df) < 50:
            print("Not enough data for linear regression training")
            return False
        
        # Calculate targets
        df = self.calculate_soil_changes(df)
        df = df.dropna()
        
        if len(df) < 20:
            print(f"Not enough valid samples after cleaning: {len(df)}")
            return False
        
        # Train model for each soil zone
        feature_columns = [f for f in self.config['features'] if f in df.columns]
        
        for zone in ['soil1', 'soil2', 'soil3']:
            target_col = f'{zone}_change'
            if target_col not in df.columns:
                continue
            
            # Prepare data
            X = df[feature_columns].fillna(0)
            y = df[target_col]
            
            # Split data
            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
            
            # Scale features
            scaler = StandardScaler()
            X_train_scaled = scaler.fit_transform(X_train)
            X_test_scaled = scaler.transform(X_test)
            
            # Train model
            model = LinearRegression()
            model.fit(X_train_scaled, y_train)
            
            # Evaluate
            train_score = model.score(X_train_scaled, y_train)
            test_score = model.score(X_test_scaled, y_test)
            
            # Store models
            self.models[zone] = model
            self.scalers[zone] = scaler
            
            print(f"Trained {zone} model - Train R²: {train_score:.3f}, Test R²: {test_score:.3f}")
        
        self.training_history.append({
            'timestamp': datetime.now().isoformat(),
            'samples': len(df),
            'model_count': len(self.models)
        })
        
        print(f"Linear regression training completed - {len(self.models)} models trained")
        return True
    
    def predict_soil_changes(self, current_data, irrigation_duration=0):
        predictions = {}
        
        if not self.models:
            # Return simple predictions if no models trained
            for zone in ['soil1', 'soil2', 'soil3']:
                current = current_data.get(zone, 50)
                evaporation = 0.5 * (current_data.get('temperature', 25) / 30) * (1 - current_data.get('humidity', 60) / 100)
                irrigation_effect = irrigation_duration * 0.1
                predictions[zone] = irrigation_effect - evaporation
            return predictions
        
        # Prepare features for prediction
        features = {}
        for feature in self.config['features']:
            if feature in current_data:
                features[feature] = current_data[feature]
            elif feature == 'hour':
                features[feature] = datetime.now().hour
            elif feature == 'hour_sin':
                features[feature] = np.sin(2 * np.pi * datetime.now().hour / 24)
            elif feature == 'hour_cos':
                features[feature] = np.cos(2 * np.pi * datetime.now().hour / 24)
            elif feature == 'is_daytime':
                features[feature] = 1 if 6 <= datetime.now().hour <= 18 else 0
            elif feature == 'temp_humidity':
                features[feature] = current_data.get('temperature', 25) * current_data.get('humidity', 60) / 100
            elif feature == 'soil_avg':
                features[feature] = np.mean([current_data.get('soil1', 50), current_data.get('soil2', 50), current_data.get('soil3', 50)])
            elif feature == 'soil_gradient_1_2':
                features[feature] = current_data.get('soil1', 50) - current_data.get('soil2', 50)
            elif feature == 'soil_gradient_2_3':
                features[feature] = current_data.get('soil2', 50) - current_data.get('soil3', 50)
            elif feature == 'evaporation_est':
                features[feature] = 0.5 * (current_data.get('temperature', 25) / 30) * (1 - current_data.get('humidity', 60) / 100)
            else:
                features[feature] = 0
        
        # Add irrigation effect
        features['valve_state'] = 1 if irrigation_duration > 0 else 0
        features['pump_state'] = 1 if irrigation_duration > 0 else 0
        
        # Make predictions for each zone
        for zone in ['soil1', 'soil2', 'soil3']:
            if zone in self.models:
                X = np.array([features[f] for f in self.config['features'] if f in features]).reshape(1, -1)
                X_scaled = self.scalers[zone].transform(X)
                predictions[zone] = self.models[zone].predict(X_scaled)[0]
            else:
                predictions[zone] = 0
        
        return predictions
    
    def recommend_irrigation(self, current_data, q_learning_action):
        pred_no_irrigation = self.predict_soil_changes(current_data, 0)
        pred_with_irrigation = self.predict_soil_changes(current_data, q_learning_action)
        
        benefits = {}
        for zone in ['soil1', 'soil2', 'soil3']:
            current = current_data.get(zone, 50)
            expected_no_irr = current + pred_no_irrigation.get(zone, 0)
            expected_with_irr = current + pred_with_irrigation.get(zone, 0)
            
            def distance_to_optimal(moisture):
                if 40 <= moisture <= 60:
                    return 0
                return min(abs(moisture - 40), abs(moisture - 60))
            
            benefit = distance_to_optimal(expected_no_irr) - distance_to_optimal(expected_with_irr)
            benefits[zone] = benefit
        
        weights = {'soil1': 1.2, 'soil2': 1.0, 'soil3': 0.8}
        total_benefit = sum(benefits[zone] * weights[zone] for zone in benefits)
        
        if total_benefit > 2:
            final_action = q_learning_action
            reason = "Both models agree on irrigation need"
        elif total_benefit > 0:
            final_action = max(0, q_learning_action - 5)
            reason = "Linear regression suggests less irrigation"
        else:
            final_action = 0
            reason = "Linear regression predicts no benefit"
        
        return {
            'q_learning_action': q_learning_action,
            'final_action': final_action,
            'predicted_benefits': benefits,
            'total_benefit': total_benefit,
            'reason': reason,
            'timestamp': datetime.now().isoformat()
        }
    
    def save_models(self, directory):
        os.makedirs(directory, exist_ok=True)
        model_data = {
            'models': self.models,
            'scalers': self.scalers,
            'config': self.config,
            'training_history': self.training_history,
            'timestamp': datetime.now().isoformat()
        }
        
        with open(os.path.join(directory, 'lr_models.pkl'), 'wb') as f:
            pickle.dump(model_data, f)
        
        print(f"Models saved to {directory}")
    
    def load_models(self, directory):
        model_path = os.path.join(directory, 'lr_models.pkl')
        if os.path.exists(model_path):
            with open(model_path, 'rb') as f:
                model_data = pickle.load(f)
            self.models = model_data.get('models', {})
            self.scalers = model_data.get('scalers', {})
            self.training_history = model_data.get('training_history', [])
            print(f"Loaded {len(self.models)} models from {directory}")
            return True
        return False
