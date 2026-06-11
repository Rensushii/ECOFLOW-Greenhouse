# -*- coding: utf-8 -*-
"""
Linear Regression predictor for soil moisture with improved data handling and confidence intervals
FIXED VERSION: Corrected feature handling and prediction logic
IMPROVED VERSION: Enhanced feature engineering for better model quality
UPDATED: Removed lag features and fixed confidence intervals
UPDATED: 24-hour periodic training policy with simplified features
UPDATED: REALISTIC confidence intervals with proper error metrics
UPDATED: Added time-aware features for irrigation scheduling
UPDATED: Automatically saves model state after training
UPDATED: Fixed save/load to properly preserve improvement tracking metadata
UPDATED: Cleaned training data (removed 0% sensor errors)
UPDATED: Removed soil_avg from features to prevent data leakage
UPDATED: Improved prediction logic with physics-based blending
UPDATED: OPTIMIZED zone-specific feature sets for better per-zone performance
"""

import pandas as pd
import numpy as np
import sqlite3
from datetime import datetime, timedelta
import traceback
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler
import json
import warnings
import os

warnings.filterwarnings('ignore')

class SoilMoisturePredictor:
    def __init__(self, db_path, config):
        self.db_path = db_path
        self.config = config
        self.models = {}
        self.scalers = {}
        self.training_history = []
        self.feature_columns = ['temperature', 'humidity', 'valve', 'pump', 'hour', 'day_of_week']
    
    def prepare_features(self, df):
        """Prepare features for training with IMPROVED feature engineering"""
        if df is None or len(df) == 0:
            return df
        
        try:
            # Ensure timestamp is datetime
            if 'timestamp' in df.columns:
                df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
                
                # Remove rows with invalid timestamps
                df = df.dropna(subset=['timestamp'])
                
                # Sort by timestamp for rolling features
                df = df.sort_values('timestamp')
                
                # Extract basic time features
                df['hour'] = df['timestamp'].dt.hour.astype(np.float64)
                df['day_of_week'] = df['timestamp'].dt.dayofweek.astype(np.float64)
                df['day_of_year'] = df['timestamp'].dt.dayofyear.astype(np.float64)
                df['month'] = df['timestamp'].dt.month.astype(np.float64)
                df['is_weekend'] = (df['timestamp'].dt.dayofweek >= 5).astype(np.float64)
                
                # Cyclical time features for better temporal representation
                df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24).astype(np.float64)
                df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24).astype(np.float64)
                df['day_sin'] = np.sin(2 * np.pi * df['day_of_week'] / 7).astype(np.float64)
                df['day_cos'] = np.cos(2 * np.pi * df['day_of_week'] / 7).astype(np.float64)
                df['month_sin'] = np.sin(2 * np.pi * df['month'] / 12).astype(np.float64)
                df['month_cos'] = np.cos(2 * np.pi * df['month'] / 12).astype(np.float64)
                
                # Time of day categories
                df['is_night'] = ((df['hour'] >= 0) & (df['hour'] < 6)).astype(np.float64)
                df['is_morning'] = ((df['hour'] >= 6) & (df['hour'] < 12)).astype(np.float64)
                df['is_afternoon'] = ((df['hour'] >= 12) & (df['hour'] < 18)).astype(np.float64)
                df['is_evening'] = ((df['hour'] >= 18) & (df['hour'] <= 23)).astype(np.float64)
                
                # Enhanced time features for irrigation scheduling
                df['is_night_22_6'] = ((df['hour'] >= 22) | (df['hour'] < 6)).astype(np.float64)
                df['is_morning_6_10'] = ((df['hour'] >= 6) & (df['hour'] < 10)).astype(np.float64)
                df['is_midday_10_14'] = ((df['hour'] >= 10) & (df['hour'] < 14)).astype(np.float64)
                df['is_afternoon_14_18'] = ((df['hour'] >= 14) & (df['hour'] < 18)).astype(np.float64)
                df['is_evening_18_22'] = ((df['hour'] >= 18) & (df['hour'] < 22)).astype(np.float64)
                
                # Irrigation suitability score (1 = best, 0 = worst)
                def irrigation_suitability(hour):
                    if 6 <= hour < 10:   # Early morning - BEST
                        return 1.0
                    elif 16 <= hour < 18: # Late afternoon - GOOD
                        return 0.8
                    elif 10 <= hour < 16: # Midday - OK
                        return 0.6
                    elif 18 <= hour < 22: # Evening - POOR
                        return 0.3
                    else:                 # Night - WORST
                        return 0.1
                
                df['irrigation_suitability'] = df['hour'].apply(irrigation_suitability)
                
                # Growth phase estimation (simulated)
                df['growth_phase'] = (df['day_of_year'] % 90 / 90).astype(np.float64)  # 90-day growth cycle
            
            # Safely convert all numeric columns
            numeric_columns = ['temperature', 'humidity', 'soil1', 'soil2', 'soil3', 'valve', 'pump']
            
            for col in numeric_columns:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
            
            # Calculate derived environmental features
            if all(col in df.columns for col in ['temperature', 'humidity']):
                df['temp_humidity'] = (df['temperature'] * df['humidity'] / 100).astype(np.float64)
                df['evaporation_est'] = (0.5 * (df['temperature'] / 80) * (1 - df['humidity'] / 100)).astype(np.float64)
                df['dew_point'] = (df['temperature'] - ((100 - df['humidity']) / 5)).astype(np.float64)
                df['comfort_index'] = (0.5 * (df['temperature'] + df['humidity'])).astype(np.float64)
                
                # Temperature categories
                df['temp_low'] = (df['temperature'] < 20).astype(np.float64)
                df['temp_optimal'] = ((df['temperature'] >= 20) & (df['temperature'] <= 30)).astype(np.float64)
                df['temp_high'] = (df['temperature'] > 30).astype(np.float64)
                
                # Humidity categories
                df['humidity_low'] = (df['humidity'] < 40).astype(np.float64)
                df['humidity_optimal'] = ((df['humidity'] >= 40) & (df['humidity'] <= 70)).astype(np.float64)
                df['humidity_high'] = (df['humidity'] > 70).astype(np.float64)
            
            if all(col in df.columns for col in ['soil1', 'soil2', 'soil3']):
                # Soil statistics - NOTE: soil_avg is created but NOT used as a feature
                # It's kept for informational purposes only
                df['soil_avg'] = ((df['soil1'] + df['soil2'] + df['soil3']) / 3).astype(np.float64)
                df['soil_min'] = df[['soil1', 'soil2', 'soil3']].min(axis=1).astype(np.float64)
                df['soil_max'] = df[['soil1', 'soil2', 'soil3']].max(axis=1).astype(np.float64)
                df['soil_range'] = (df['soil_max'] - df['soil_min']).astype(np.float64)
                df['soil_std'] = df[['soil1', 'soil2', 'soil3']].std(axis=1).astype(np.float64)
                
                # CRITICAL: Soil gradients (differences between zones)
                df['soil_gradient_1_2'] = (df['soil1'] - df['soil2']).astype(np.float64)
                df['soil_gradient_2_3'] = (df['soil2'] - df['soil3']).astype(np.float64)
                df['soil_gradient_1_3'] = (df['soil1'] - df['soil3']).astype(np.float64)
                
                # Soil moisture categories
                df['soil_dry'] = (df['soil_avg'] < 30).astype(np.float64)
                df['soil_optimal'] = ((df['soil_avg'] >= 30) & (df['soil_avg'] <= 60)).astype(np.float64)
                df['soil_wet'] = (df['soil_avg'] > 60).astype(np.float64)
            
            # REMOVED: Lag features (previous values) - removed to avoid data leakage
            
            # Environmental rolling features (still valid - they don't leak future soil data)
            if 'temperature' in df.columns:
                for window in [6, 12]:
                    if len(df) >= window:
                        df[f'temp_rolling_mean_{window}'] = df['temperature'].rolling(window=window, min_periods=1).mean().astype(np.float64)
                        df[f'temp_trend_{window}'] = (df['temperature'] - df[f'temp_rolling_mean_{window}']).astype(np.float64)
            
            if 'humidity' in df.columns:
                for window in [6, 12]:
                    if len(df) >= window:
                        df[f'humidity_rolling_mean_{window}'] = df['humidity'].rolling(window=window, min_periods=1).mean().astype(np.float64)
            
            # Interaction features - WITHOUT soil_avg to prevent leakage
            if all(col in df.columns for col in ['temperature', 'humidity']):
                df['temp_soil_ratio'] = (df['temperature'] / 25).astype(np.float64)  # Normalized temp only
                df['humidity_soil_ratio'] = (df['humidity'] / 60).astype(np.float64)  # Normalized humidity only
            
            # System state features
            if 'valve' in df.columns:
                df['valve_state'] = (df['valve'] > 0).astype(np.float64)
                df['valve_duration'] = df['valve_state'].rolling(window=12, min_periods=1).sum().astype(np.float64)  # Last hour
        
            if 'pump' in df.columns:
                df['pump_state'] = (df['pump'] > 0).astype(np.float64)
                df['pump_duration'] = df['pump_state'].rolling(window=12, min_periods=1).sum().astype(np.float64)
            
            # Recent irrigation impact
            if all(col in df.columns for col in ['valve_duration', 'pump_duration']):
                df['recent_irrigation'] = ((df['valve_duration'] > 0) | (df['pump_duration'] > 0)).astype(np.float64)
                df['irrigation_intensity'] = (df['valve_duration'] + df['pump_duration']).astype(np.float64)
            
            # Time since last irrigation (approximate)
            if 'recent_irrigation' in df.columns:
                df['time_since_irrigation'] = (~df['recent_irrigation'].astype(bool)).groupby(df['recent_irrigation'].astype(bool).cumsum()).cumcount().astype(np.float64)
                df['time_since_irrigation'] = df['time_since_irrigation'] / 12  # Convert to hours if 5-minute intervals
            
            # Drop any rows with NaN values (after creating features)
            initial_count = len(df)
            
            # Fill NaN values
            df = df.fillna(method='ffill').fillna(method='bfill')
            
            # Drop any remaining NaN rows
            df = df.dropna()
            
            if len(df) < initial_count:
                print(f"? Dropped {initial_count - len(df)} rows with NaN values after feature engineering")
            
            print(f"? Feature engineering complete: {len(df.columns)} features created")
            
            return df
            
        except Exception as e:
            print(f"? Error in prepare_features: {e}")
            traceback.print_exc()
            return pd.DataFrame()
    
    def load_training_data(self, days=7):
        """Load training data from database with robust error handling and data cleaning"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            
            # Calculate timestamp threshold
            threshold = datetime.now() - timedelta(days=days)
            threshold_str = threshold.strftime('%Y-%m-%d %H:%M:%S')
            
            query = """
            SELECT 
                timestamp, 
                temperature, 
                humidity, 
                soil1, 
                soil2, 
                soil3, 
                valve, 
                pump
            FROM greenhouse_data 
            WHERE timestamp >= ? 
            AND temperature IS NOT NULL
            AND humidity IS NOT NULL
            AND soil1 IS NOT NULL
            AND soil2 IS NOT NULL
            AND soil3 IS NOT NULL
            ORDER BY timestamp
            """
            
            df = pd.read_sql_query(query, conn, params=(threshold_str,))
            
            if len(df) > 0:
                print(f"? Loaded {len(df)} raw samples from database")
                
                # ===== CLEAN THE DATA: Remove sensor errors =====
                # Remove 0% moisture readings (sensor errors)
                before_clean = len(df)
                df = df[df['soil1'] > 5]
                df = df[df['soil2'] > 5]
                df = df[df['soil3'] > 5]
                after_clean = len(df)
                if before_clean - after_clean > 0:
                    print(f"? Removed {before_clean - after_clean} rows with 0% soil moisture (sensor errors)")
                
                # Remove unrealistic spikes (>95% or <10% after cleaning)
                df = df[df['soil1'].between(10, 95)]
                df = df[df['soil2'].between(10, 95)]
                df = df[df['soil3'].between(10, 95)]
                
                if len(df) < before_clean:
                    print(f"? Removed {before_clean - len(df)} rows with unrealistic moisture values")
                
                df = self.prepare_features(df)
                print(f"? After feature preparation: {len(df)} valid samples with {len(df.columns)} features")
                
                if len(df) < 30:
                    print(f"? Warning: Only {len(df)} valid samples available (minimum 30 recommended)")
            else:
                print("? No data found in the specified time range")
                df = pd.DataFrame()
            
            return df
            
        except Exception as e:
            print(f"? Error loading training data: {e}")
            traceback.print_exc()
            return pd.DataFrame()
            
        finally:
            if conn:
                conn.close()
    
    def select_features_for_training(self, df, zone):
        """Select the most relevant features for each zone - OPTIMIZED PER ZONE"""
        
        # Base features that work well for all zones
        base_features = [
            'temperature', 'humidity',
            'hour_sin', 'hour_cos',
            'day_sin', 'day_cos',
        ]
        
        # Time of day categories (useful for irrigation patterns)
        time_features = [
            'is_night_22_6', 'is_morning_6_10', 'is_midday_10_14',
            'is_afternoon_14_18', 'is_evening_18_22'
        ]
        
        # Irrigation-related features
        irrigation_features = [
            'irrigation_suitability',
            'recent_irrigation',
            'time_since_irrigation'
        ]
        
        # Device state features
        device_features = [
            'valve_state', 'pump_state',
            'valve_duration', 'pump_duration'
        ]
        
        # ===== ZONE-SPECIFIC FEATURE SETS =====
        if zone == 'soil1':
            # Zone A - Fastest drying, top layer
            zone_specific = [
                'temp_humidity',      # Combined effect works well for Zone A
                'evaporation_est',    # Good for fast-drying zone
                'temp_high',          # Sensitive to high temperatures
                'humidity_low',       # Sensitive to dry conditions
                'temp_trend_6',       # Recent temperature changes
                'temp_rolling_mean_6' # Short-term temperature pattern
            ]
            
        elif zone == 'soil2':
            # Zone B - Medium layer, needs different features
            zone_specific = [
                'dew_point',          # Better than temp_humidity for Zone B
                'comfort_index',      # Good for medium zone
                'soil_gradient_1_2',  # Difference from Zone A (influence from above)
                'soil_gradient_2_3',  # Difference from Zone C (influence from below)
                'temp_optimal',       # Moderate temperature range
                'humidity_optimal',   # Moderate humidity range
                'temp_rolling_mean_12', # Longer-term temperature pattern
                'humidity_rolling_mean_12' # Longer-term humidity pattern
            ]
            
        elif zone == 'soil3':
            # Zone C - Slowest drying, deepest layer
            zone_specific = [
                'temp_soil_ratio',        # Temperature relative to soil baseline
                'humidity_soil_ratio',    # Humidity relative to soil baseline
                'temp_low',               # Cooler conditions affect deep soil less
                'humidity_high',          # Humid conditions affect deep soil more
                'soil_gradient_1_3',      # Overall gradient from top to bottom
                'soil_range',              # Overall soil variability indicator
                'temp_trend_12',           # Long-term temperature trend
                'is_night_22_6'            # Nighttime effects on deep soil
            ]
        else:
            zone_specific = []
        
        # Combine all candidate features
        candidate_features = []
        candidate_features.extend(base_features)
        candidate_features.extend(time_features)
        candidate_features.extend(irrigation_features)
        candidate_features.extend(device_features)
        candidate_features.extend(zone_specific)
        
        # Add environmental rolling features if they exist
        rolling_features = [
            'temp_rolling_mean_6', 'temp_rolling_mean_12',
            'humidity_rolling_mean_6', 'humidity_rolling_mean_12',
            'temp_trend_6', 'temp_trend_12'
        ]
        for feature in rolling_features:
            if feature in df.columns:
                candidate_features.append(feature)
        
        # Filter to only include features that exist in the dataframe
        available_features = [f for f in candidate_features if f in df.columns]
        
        print(f"? Selected {len(available_features)} OPTIMIZED features for {zone}:")
        if len(available_features) > 10:
            print(f"  Features: {available_features[:10]}...")
        else:
            print(f"  Features: {available_features}")
        
        # Ensure we have at least some features
        if len(available_features) < 5:
            print(f"? Warning: Only {len(available_features)} features, adding basic fallbacks")
            fallback = ['temperature', 'humidity', 'hour_sin', 'hour_cos']
            for f in fallback:
                if f in df.columns and f not in available_features:
                    available_features.append(f)
        
        return available_features
    
    def train(self, trigger='manual'):
        """Train linear regression models for each soil zone with improved feature selection"""
        try:
            print(f"? Starting linear regression training (trigger: {trigger})...")
            
            # Load training data
            df = self.load_training_data(days=7)
            
            # VALIDATE DATA QUALITY
            if df is None or len(df) < 100:
                print(f"? Not enough data for training. Available samples: {len(df) if df is not None else 0}")
                return False
            
            # Check data variance
            if 'soil1' in df.columns:
                soil1_var = df['soil1'].var()
                if soil1_var < 50:  # Low variance = poor training data
                    print(f"? Low soil moisture variance ({soil1_var:.1f}), data may be too consistent")
                    # Still train, but note this
            
            print(f"? Training with {len(df)} samples and {len(df.columns)} features")
            
            zones_trained = 0
            training_data_details = {}
            r_squared_values = []
            
            for zone in ['soil1', 'soil2', 'soil3']:
                if zone not in df.columns:
                    print(f"? Skipping {zone}: not in data")
                    continue
                
                try:
                    print(f"? Training model for {zone}...")
                    
                    # Select appropriate features for this zone (OPTIMIZED per zone)
                    available_features = self.select_features_for_training(df, zone)
                    
                    if len(available_features) < 3:
                        print(f"? Skipping {zone}: insufficient features ({len(available_features)} available)")
                        continue
                    
                    # Prepare data
                    X = df[available_features].values.astype(np.float64)
                    y = df[zone].values.astype(np.float64)
                    
                    # Remove any NaN or Inf values
                    valid_mask = ~(np.any(np.isnan(X), axis=1) | np.isnan(y) | np.any(np.isinf(X), axis=1) | np.isinf(y))
                    X = X[valid_mask]
                    y = y[valid_mask]
                    
                    if len(X) < 15:
                        print(f"? Skipping {zone}: insufficient valid samples after cleaning ({len(X)} samples)")
                        continue
                    
                    # Store training data details
                    training_data_details[zone] = {
                        'samples': len(X),
                        'features': available_features,
                        'feature_count': len(available_features),
                        'valid_mask_ratio': valid_mask.mean(),
                        'data_range': {
                            'min': float(y.min()),
                            'max': float(y.max()),
                            'mean': float(y.mean()),
                            'std': float(y.std())
                        }
                    }
                    
                    # Scale features
                    scaler = StandardScaler()
                    X_scaled = scaler.fit_transform(X)
                    
                    # Train model with regularization (using sklearn's LinearRegression which uses OLS)
                    model = LinearRegression()
                    model.fit(X_scaled, y)
                    
                    # Calculate performance metrics
                    y_pred = model.predict(X_scaled)
                    residuals = y - y_pred
                    r_squared = model.score(X_scaled, y)
                    mae = np.mean(np.abs(residuals))
                    std_residuals = np.std(residuals)
                    rmse = np.sqrt(np.mean(residuals**2))
                    
                    # Calculate feature importance (absolute coefficient values)
                    feature_importance = dict(zip(available_features, np.abs(model.coef_)))
                    sorted_features = sorted(feature_importance.items(), key=lambda x: x[1], reverse=True)
                    
                    # Calculate training statistics for confidence intervals
                    y_std = np.std(y)
                    
                    # Store previous model performance for comparison
                    previous_r_squared = 0
                    previous_trained_at = None
                    if zone in self.models:
                        previous_r_squared = self.models[zone].get('r_squared', 0)
                        previous_trained_at = self.models[zone].get('trained_at', None)
                    
                    # Store model and metadata with improved tracking
                    self.models[zone] = {
                        'model': model,
                        'scaler': scaler,
                        'feature_columns': available_features,
                        'intercept': float(model.intercept_),
                        'coefficients': model.coef_.tolist(),
                        'r_squared': float(r_squared),
                        'mae': float(mae),
                        'rmse': float(rmse),
                        'std_residuals': float(std_residuals),
                        'y_std': float(y_std),
                        'training_std': float(std_residuals),  # For confidence intervals
                        'trained_samples': len(X),
                        'trained_at': datetime.now().isoformat(),
                        'training_samples': len(df),  # Track how many samples were available
                        'previous_r_squared': previous_r_squared,  # Track previous performance
                        'previous_trained_at': previous_trained_at,  # Track when previous model was trained
                        'improvement': float(r_squared - previous_r_squared) if previous_r_squared > 0 else 0,
                        'training_data_details': training_data_details[zone],
                        'feature_importance': dict(sorted_features[:10]),  # Top 10 features
                        'residual_stats': {
                            'min': float(residuals.min()),
                            'max': float(residuals.max()),
                            'mean': float(residuals.mean()),
                            'std': float(residuals.std())
                        }
                    }
                    
                    self.scalers[zone] = scaler
                    
                    zones_trained += 1
                    r_squared_values.append(r_squared)
                    
                    print(f"?   {zone}:")
                    print(f"?     R²={r_squared:.4f}, MAE={mae:.2f}, RMSE={rmse:.2f}, samples={len(X)}")
                    print(f"?     Top features: {', '.join([f[0] for f in sorted_features[:3]])}")
                    
                    if previous_r_squared > 0:
                        improvement = r_squared - previous_r_squared
                        print(f"?     Previous R²: {previous_r_squared:.4f}, Improvement: {improvement:+.4f}")
                    
                except Exception as e:
                    print(f"?   Error training {zone}: {e}")
                    traceback.print_exc()
                    continue
            
            # Record training history with more details
            training_record = {
                'timestamp': datetime.now().isoformat(),
                'samples_loaded': len(df),
                'samples_used': sum([details.get('samples', 0) for details in training_data_details.values()]),
                'models_trained': zones_trained,
                'zones': list(self.models.keys()),
                'avg_r_squared': float(np.mean(r_squared_values)) if r_squared_values else 0,
                'min_r_squared': float(np.min(r_squared_values)) if r_squared_values else 0,
                'max_r_squared': float(np.max(r_squared_values)) if r_squared_values else 0,
                'trigger': trigger,
                'training_data_details': training_data_details,
                'previous_training_count': len(self.training_history),
                'data_date_range': {
                    'min': df['timestamp'].min().isoformat() if 'timestamp' in df.columns and len(df) > 0 else None,
                    'max': df['timestamp'].max().isoformat() if 'timestamp' in df.columns and len(df) > 0 else None
                },
                'feature_counts': {zone: len(details.get('features', [])) for zone, details in training_data_details.items()}
            }
            
            self.training_history.append(training_record)
            
            # Keep only last 20 training records
            if len(self.training_history) > 20:
                self.training_history = self.training_history[-20:]
            
            print(f"? Training completed: {zones_trained} models trained")
            print(f"? Average R²: {training_record['avg_r_squared']:.4f}")
            print(f"? Training record saved (total records: {len(self.training_history)})")
            
            # AUTOMATICALLY SAVE MODEL STATE AFTER TRAINING
            try:
                # Determine save path
                models_dir = os.path.join(os.path.dirname(__file__), '..', 'models')
                os.makedirs(models_dir, exist_ok=True)
                save_path = os.path.join(models_dir, 'predictor_state.json')
                if self.save_model(save_path):
                    print(f"? Model state automatically saved to {save_path}")
                else:
                    print(f"? Warning: Could not auto-save model state")
            except Exception as save_error:
                print(f"? Warning: Auto-save failed: {save_error}")
            
            return zones_trained > 0
            
        except Exception as e:
            print(f"? Training failed: {e}")
            traceback.print_exc()
            
            # Still record failed training attempt
            self.training_history.append({
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'trigger': trigger,
                'success': False
            })
            
            return False
    
    def build_prediction_features(self, current_data, current_time, feature_columns, lookahead_hours=1):
        """Build feature vector for prediction with enhanced feature engineering"""
        features = []
        
        for feature_name in feature_columns:
            try:
                # Basic features
                if feature_name == 'temperature':
                    features.append(current_data.get('temperature', 25))
                elif feature_name == 'humidity':
                    features.append(current_data.get('humidity', 60))
                elif feature_name == 'valve':
                    features.append(0.0)  # Valve closed for prediction
                elif feature_name == 'pump':
                    features.append(0.0)  # Pump OFF for prediction
                
                # Time features
                elif feature_name == 'hour':
                    features.append(float(current_time.hour))
                elif feature_name == 'day_of_week':
                    features.append(float(current_time.weekday()))
                elif feature_name == 'day_of_year':
                    features.append(float(current_time.timetuple().tm_yday))
                elif feature_name == 'month':
                    features.append(float(current_time.month))
                elif feature_name == 'is_weekend':
                    features.append(float(current_time.weekday() >= 5))
                
                # Cyclical time features
                elif feature_name == 'hour_sin':
                    features.append(np.sin(2 * np.pi * current_time.hour / 24))
                elif feature_name == 'hour_cos':
                    features.append(np.cos(2 * np.pi * current_time.hour / 24))
                elif feature_name == 'day_sin':
                    features.append(np.sin(2 * np.pi * current_time.weekday() / 7))
                elif feature_name == 'day_cos':
                    features.append(np.cos(2 * np.pi * current_time.weekday() / 7))
                elif feature_name == 'month_sin':
                    features.append(np.sin(2 * np.pi * current_time.month / 12))
                elif feature_name == 'month_cos':
                    features.append(np.cos(2 * np.pi * current_time.month / 12))
                
                # Time of day categories
                elif feature_name == 'is_night':
                    features.append(float((current_time.hour >= 0) and (current_time.hour < 6)))
                elif feature_name == 'is_morning':
                    features.append(float((current_time.hour >= 6) and (current_time.hour < 12)))
                elif feature_name == 'is_afternoon':
                    features.append(float((current_time.hour >= 12) and (current_time.hour < 18)))
                elif feature_name == 'is_evening':
                    features.append(float((current_time.hour >= 18) and (current_time.hour <= 23)))
                
                # Enhanced time features for irrigation scheduling
                elif feature_name == 'is_night_22_6':
                    features.append(float((current_time.hour >= 22) or (current_time.hour < 6)))
                elif feature_name == 'is_morning_6_10':
                    features.append(float((current_time.hour >= 6) and (current_time.hour < 10)))
                elif feature_name == 'is_midday_10_14':
                    features.append(float((current_time.hour >= 10) and (current_time.hour < 14)))
                elif feature_name == 'is_afternoon_14_18':
                    features.append(float((current_time.hour >= 14) and (current_time.hour < 18)))
                elif feature_name == 'is_evening_18_22':
                    features.append(float((current_time.hour >= 18) and (current_time.hour < 22)))
                
                # Irrigation suitability score
                elif feature_name == 'irrigation_suitability':
                    hour = current_time.hour
                    if 6 <= hour < 10:   # Early morning - BEST
                        features.append(1.0)
                    elif 16 <= hour < 18: # Late afternoon - GOOD
                        features.append(0.8)
                    elif 10 <= hour < 16: # Midday - OK
                        features.append(0.6)
                    elif 18 <= hour < 22: # Evening - POOR
                        features.append(0.3)
                    else:                 # Night - WORST
                        features.append(0.1)
                
                # Environmental derived features
                elif feature_name == 'temp_humidity':
                    temp = current_data.get('temperature', 25)
                    humidity = current_data.get('humidity', 60)
                    features.append(temp * humidity / 100)
                elif feature_name == 'evaporation_est':
                    temp = current_data.get('temperature', 25)
                    humidity = current_data.get('humidity', 60)
                    features.append(0.5 * (temp / 80) * (1 - humidity / 100))
                elif feature_name == 'dew_point':
                    temp = current_data.get('temperature', 25)
                    humidity = current_data.get('humidity', 60)
                    features.append(temp - ((100 - humidity) / 5))
                elif feature_name == 'comfort_index':
                    temp = current_data.get('temperature', 25)
                    humidity = current_data.get('humidity', 60)
                    features.append(0.5 * (temp + humidity))
                
                # Temperature categories
                elif feature_name == 'temp_low':
                    temp = current_data.get('temperature', 25)
                    features.append(float(temp < 20))
                elif feature_name == 'temp_optimal':
                    temp = current_data.get('temperature', 25)
                    features.append(float((temp >= 20) and (temp <= 30)))
                elif feature_name == 'temp_high':
                    temp = current_data.get('temperature', 25)
                    features.append(float(temp > 30))
                
                # Humidity categories
                elif feature_name == 'humidity_low':
                    humidity = current_data.get('humidity', 60)
                    features.append(float(humidity < 40))
                elif feature_name == 'humidity_optimal':
                    humidity = current_data.get('humidity', 60)
                    features.append(float((humidity >= 40) and (humidity <= 70)))
                elif feature_name == 'humidity_high':
                    humidity = current_data.get('humidity', 60)
                    features.append(float(humidity > 70))
                
                # System state features
                elif feature_name == 'valve_state':
                    features.append(float(current_data.get('valve', 0) > 0))
                elif feature_name == 'pump_state':
                    features.append(float(current_data.get('pump', 0) > 0))
                elif feature_name == 'valve_duration':
                    # Assume no recent irrigation for prediction
                    features.append(0.0)
                elif feature_name == 'pump_duration':
                    features.append(0.0)
                elif feature_name == 'recent_irrigation':
                    features.append(0.0)
                elif feature_name == 'time_since_irrigation':
                    # Large value indicating no recent irrigation
                    features.append(24.0)
                
                # Soil gradient features (for zones that use them)
                elif feature_name == 'soil_gradient_1_2':
                    # For prediction, use current values
                    soil1 = current_data.get('soil1', 50)
                    soil2 = current_data.get('soil2', 50)
                    features.append(soil1 - soil2)
                elif feature_name == 'soil_gradient_2_3':
                    soil2 = current_data.get('soil2', 50)
                    soil3 = current_data.get('soil3', 50)
                    features.append(soil2 - soil3)
                elif feature_name == 'soil_gradient_1_3':
                    soil1 = current_data.get('soil1', 50)
                    soil3 = current_data.get('soil3', 50)
                    features.append(soil1 - soil3)
                elif feature_name == 'soil_range':
                    soil1 = current_data.get('soil1', 50)
                    soil2 = current_data.get('soil2', 50)
                    soil3 = current_data.get('soil3', 50)
                    features.append(max(soil1, soil2, soil3) - min(soil1, soil2, soil3))
                
                # Rolling features (approximated)
                elif feature_name.startswith('temp_rolling_mean_'):
                    # Use current temperature as approximation
                    features.append(current_data.get('temperature', 25))
                elif feature_name.startswith('temp_trend_'):
                    # Assume zero trend for prediction
                    features.append(0.0)
                elif feature_name.startswith('humidity_rolling_mean_'):
                    # Use current humidity as approximation
                    features.append(current_data.get('humidity', 60))
                
                # Interaction features (without soil_avg)
                elif feature_name == 'temp_soil_ratio':
                    temp = current_data.get('temperature', 25)
                    features.append(temp / 25)  # Normalized temperature only
                elif feature_name == 'humidity_soil_ratio':
                    humidity = current_data.get('humidity', 60)
                    features.append(humidity / 60)  # Normalized humidity only
                
                # Default for any other features
                else:
                    features.append(0.0)
                    
            except Exception as e:
                print(f"?   Error building feature '{feature_name}': {e}")
                features.append(0.0)  # Default value
        
        return np.array([features]).astype(np.float64)
    
    def predict_soil_changes(self, current_data, irrigation_duration=0, lookahead_hours=1):
        """Predict soil moisture changes based on current conditions"""
        predictions = {}
        
        try:
            if not self.models:
                return self._fallback_predictions(current_data, irrigation_duration, lookahead_hours)
            
            current_time = datetime.now()
            
            for zone in ['soil1', 'soil2', 'soil3']:
                if zone not in self.models:
                    predictions[f'{zone}_change'] = 0
                    predictions[f'{zone}_predicted'] = current_data.get(zone, 50)
                    predictions[f'{zone}_current'] = current_data.get(zone, 50)
                    predictions[f'{zone}_fallback'] = True
                    continue
                
                try:
                    model_info = self.models[zone]
                    scaler = self.scalers[zone]
                    
                    # Build feature vector for prediction
                    X = self.build_prediction_features(
                        current_data, current_time, 
                        model_info['feature_columns'], lookahead_hours
                    )
                    
                    # Scale features and make prediction
                    X_scaled = scaler.transform(X)
                    predicted_value = float(model_info['model'].predict(X_scaled)[0])
                    current_value = float(current_data.get(zone, 50))
                    
                    # Apply environmental drying adjustment
                    temp = current_data.get('temperature', 25)
                    humidity = current_data.get('humidity', 60)
                    
                    # Enhanced drying calculation
                    temp_factor = max(0.5, temp / 25.0)  # Normalize to 25°C
                    humidity_factor = max(0.1, (100 - humidity) / 100.0)  # Lower humidity = faster drying
                    env_drying_factor = temp_factor * humidity_factor
                    
                    # Time-based drying
                    base_drying_rate = -1.0  # % per hour
                    total_drying = base_drying_rate * env_drying_factor * lookahead_hours
                    
                    # Adjust prediction with drying
                    predicted_with_drying = predicted_value + total_drying
                    
                    # Ensure prediction is realistic
                    max_predicted = current_value - 0.5  # Minimum 0.5% drying
                    predicted_with_drying = min(max_predicted, predicted_with_drying)
                    predicted_with_drying = max(10.0, predicted_with_drying)  # Minimum 10%
                    
                    predictions[f'{zone}_change'] = float(predicted_with_drying - current_value)
                    predictions[f'{zone}_predicted'] = float(predicted_with_drying)
                    predictions[f'{zone}_current'] = float(current_value)
                    predictions[f'{zone}_r_squared'] = model_info['r_squared']
                    predictions[f'{zone}_model_age'] = self._get_model_age(model_info['trained_at'])
                    predictions[f'{zone}_fallback'] = False
                    predictions[f'{zone}_features_used'] = len(model_info['feature_columns'])
                    
                except Exception as e:
                    print(f"? Prediction error for {zone}: {e}")
                    # Use fallback for this specific zone
                    fallback = self._fallback_predictions(current_data, irrigation_duration, lookahead_hours)
                    for key, value in fallback.items():
                        if key.startswith(zone):
                            predictions[key] = value
            
            return predictions
            
        except Exception as e:
            print(f"? General prediction error: {e}")
            return self._fallback_predictions(current_data, irrigation_duration, lookahead_hours)
    
    def _get_model_age(self, trained_at_str):
        """Calculate model age in hours"""
        try:
            trained_at = datetime.fromisoformat(trained_at_str)
            age_hours = (datetime.now() - trained_at).total_seconds() / 3600
            return age_hours
        except:
            return 999  # Large number for unknown age
    
    def predict_with_confidence(self, current_data, irrigation_duration=0, lookahead_hours=1, confidence_level=0.95):
        """Predict soil moisture with REALISTIC confidence intervals - IMPROVED VERSION"""
        try:
            if not self.models:
                print("? No trained models available, using fallback")
                return self._fallback_predictions_with_confidence(current_data, irrigation_duration, lookahead_hours)
            
            predictions = {}
            current_time = datetime.now()
            
            for zone in ['soil1', 'soil2', 'soil3']:
                if zone not in self.models:
                    # Use fallback for this zone
                    fallback = self._fallback_predictions_with_confidence(current_data, irrigation_duration, lookahead_hours)
                    for key, value in fallback.items():
                        if key.startswith(zone):
                            predictions[key] = value
                    continue
                
                try:
                    model_info = self.models[zone]
                    model = model_info['model']
                    scaler = model_info['scaler']
                    
                    # Build feature vector for prediction
                    X = self.build_prediction_features(
                        current_data, current_time, 
                        model_info['feature_columns'], lookahead_hours
                    )
                    
                    # Scale features
                    X_scaled = scaler.transform(X)
                    
                    # Get prediction
                    predicted_value = float(model.predict(X_scaled)[0])
                    current_value = float(current_data.get(zone, 50))
                    
                    # ===== IMPROVED: More realistic drying calculation =====
                    temp = current_data.get('temperature', 25)
                    humidity = current_data.get('humidity', 60)
                    hour = current_time.hour
                    
                    # Base drying rates per zone (% per hour) - more realistic
                    base_drying_rates = {
                        'soil1': -1.2,  # Zone A dries fastest (top layer)
                        'soil2': -0.9,  # Zone B medium
                        'soil3': -0.6   # Zone C slowest (deep layer)
                    }
                    base_rate = base_drying_rates.get(zone, -0.9)
                    
                    # Environmental factors
                    # Temperature factor: higher temp = faster drying
                    temp_factor = 1.0 + (temp - 25) * 0.03  # ±3% per degree from 25°C
                    temp_factor = max(0.7, min(1.3, temp_factor))
                    
                    # Humidity factor: higher humidity = slower drying
                    humidity_factor = 1.0 - (humidity - 60) * 0.01  # -1% per % above 60%
                    humidity_factor = max(0.6, min(1.4, humidity_factor))
                    
                    # Time of day factor (slower at night, faster during peak sun)
                    if hour < 6 or hour > 20:  # Night
                        time_factor = 0.5
                    elif 10 <= hour <= 16:  # Peak daylight
                        time_factor = 1.2
                    else:
                        time_factor = 1.0
                    
                    # Combined drying rate
                    effective_rate = base_rate * temp_factor * humidity_factor * time_factor
                    
                    # Calculate drying over lookahead period
                    total_drying = effective_rate * lookahead_hours
                    
                    # Blend model prediction with physics-based drying
                    # Use model's R² to weight the contribution
                    r_squared = model_info.get('r_squared', 0.3)
                    model_weight = min(0.7, max(0.3, r_squared))  # Between 0.3 and 0.7
                    
                    # Physics-based prediction (current + drying)
                    physics_prediction = current_value + total_drying
                    
                    # Blend predictions
                    blended_prediction = (model_weight * predicted_value) + ((1 - model_weight) * physics_prediction)
                    
                    # Ensure prediction is realistic
                    blended_prediction = max(15.0, min(95.0, blended_prediction))
                    
                    # ===== IMPROVED: More realistic confidence intervals =====
                    # Base uncertainty from model
                    mae = model_info.get('mae', 5.0)
                    std_residuals = model_info.get('std_residuals', mae * 1.2)
                    
                    # Adjust based on model quality and time horizon
                    quality_factor = 1.5 - (r_squared * 0.8)  # Better models have smaller intervals
                    time_factor_ci = 1.0 + (0.2 * (lookahead_hours - 1))  # Increase with time
                    
                    # Environmental uncertainty (more uncertainty in extreme conditions)
                    if abs(temp - 25) > 10 or abs(humidity - 50) > 30:
                        env_uncertainty = 1.3
                    else:
                        env_uncertainty = 1.0
                    
                    # Calculate margin of error
                    margin_of_error = std_residuals * quality_factor * time_factor_ci * env_uncertainty
                    
                    # Cap the margin to realistic values
                    margin_of_error = min(15.0, max(2.0, margin_of_error))
                    
                    # Calculate bounds
                    lower_bound = max(0, blended_prediction - margin_of_error)
                    upper_bound = min(100, blended_prediction + margin_of_error)
                    
                    # Store predictions
                    predictions[f'{zone}_current'] = float(current_value)
                    predictions[f'{zone}_predicted'] = float(blended_prediction)
                    predictions[f'{zone}_change'] = float(blended_prediction - current_value)
                    predictions[f'{zone}_lower_ci'] = float(lower_bound)
                    predictions[f'{zone}_upper_ci'] = float(upper_bound)
                    predictions[f'{zone}_confidence_interval'] = float(margin_of_error)
                    predictions[f'{zone}_confidence_level'] = confidence_level
                    predictions[f'{zone}_r_squared'] = float(r_squared)
                    predictions[f'{zone}_mae'] = float(mae)
                    predictions[f'{zone}_model_weight'] = float(model_weight)
                    predictions[f'{zone}_fallback'] = False
                    predictions[f'{zone}_lookahead_hours'] = lookahead_hours
                    
                    # Store for specific time horizons
                    for hours in [1, 3, 6]:
                        if hours == lookahead_hours:
                            predictions[f'{zone}_predicted_{hours}h'] = float(blended_prediction)
                            predictions[f'{zone}_lower_ci_{hours}h'] = float(lower_bound)
                            predictions[f'{zone}_upper_ci_{hours}h'] = float(upper_bound)
                            predictions[f'{zone}_ci_width_{hours}h'] = float(2 * margin_of_error)
                    
                except Exception as e:
                    print(f"? Prediction error for {zone}: {e}")
                    traceback.print_exc()
                    # Fallback for this zone
                    fallback = self._fallback_predictions_with_confidence(current_data, irrigation_duration, lookahead_hours)
                    for key, value in fallback.items():
                        if key.startswith(zone):
                            predictions[key] = value
            
            return predictions
            
        except Exception as e:
            print(f"? Error in predict_with_confidence: {e}")
            traceback.print_exc()
            return self._fallback_predictions_with_confidence(current_data, irrigation_duration, lookahead_hours)
    
    def _fallback_predictions(self, current_data, irrigation_duration, lookahead_hours=1):
        """Provide fallback predictions when models aren't available"""
        predictions = {}
        
        temp = current_data.get('temperature', 25)
        humidity = current_data.get('humidity', 60)
        
        # Realistic drying rates (negative)
        evaporation_rates = {
            'soil1': -2.0,  # % per hour - Zone A dries fastest
            'soil2': -1.5,  # % per hour - Zone B medium
            'soil3': -1.0   # % per hour - Zone C slowest
        }
        
        for zone in ['soil1', 'soil2', 'soil3']:
            current = current_data.get(zone, 50)
            base_rate = evaporation_rates[zone]
            
            # Adjust based on conditions
            temp_factor = max(0.5, temp / 25)  # Higher temp = faster drying
            humidity_factor = max(0.1, (100 - humidity) / 100)  # Lower humidity = faster drying
            
            drying_rate = base_rate * temp_factor * humidity_factor * lookahead_hours
            
            # Net change is NEGATIVE (drying)
            net_change = drying_rate
            
            predictions[f'{zone}_change'] = float(net_change)
            predictions[f'{zone}_predicted'] = float(max(10, current + net_change))  # Ensure min 10%
            predictions[f'{zone}_current'] = float(current)
            predictions[f'{zone}_fallback'] = True
            predictions[f'{zone}_model_age_hours'] = 999
            predictions[f'{zone}_lookahead_hours'] = lookahead_hours
        
        return predictions
    
    def _fallback_predictions_with_confidence(self, current_data, irrigation_duration, lookahead_hours):
        """Fallback predictions with REALISTIC confidence intervals"""
        predictions = {}
        
        temp = current_data.get('temperature', 25)
        humidity = current_data.get('humidity', 60)
        hour = datetime.now().hour
        
        # Realistic drying rates (% per hour) based on zone and conditions
        base_rates = {
            'soil1': -1.0,
            'soil2': -0.8,
            'soil3': -0.6
        }
        
        for zone in ['soil1', 'soil2', 'soil3']:
            current = current_data.get(zone, 50)
            base_rate = base_rates[zone]
            
            # Adjust for conditions
            temp_factor = 1.0 + (temp - 25) * 0.02
            temp_factor = max(0.8, min(1.2, temp_factor))
            
            humidity_factor = 1.0 - (humidity - 60) * 0.008
            humidity_factor = max(0.7, min(1.3, humidity_factor))
            
            # Night factor (slower drying at night)
            if hour < 6 or hour > 20:
                time_factor = 0.6
            elif 10 <= hour <= 16:
                time_factor = 1.1
            else:
                time_factor = 0.9
            
            # Calculate drying
            drying_rate = base_rate * temp_factor * humidity_factor * time_factor
            total_drying = drying_rate * lookahead_hours
            
            # Prediction
            predicted = max(15.0, min(95.0, current + total_drying))
            
            # Confidence interval - wider for fallback
            margin = 3.0 + (1.0 * lookahead_hours)  # ±3% + 1% per hour
            
            predictions[f'{zone}_current'] = float(current)
            predictions[f'{zone}_predicted'] = float(predicted)
            predictions[f'{zone}_change'] = float(predicted - current)
            predictions[f'{zone}_lower_ci'] = float(max(0, predicted - margin))
            predictions[f'{zone}_upper_ci'] = float(min(100, predicted + margin))
            predictions[f'{zone}_confidence_interval'] = float(margin)
            predictions[f'{zone}_confidence_level'] = 0.90
            predictions[f'{zone}_r_squared'] = 0.3
            predictions[f'{zone}_mae'] = 4.0
            predictions[f'{zone}_model_age_hours'] = 999
            predictions[f'{zone}_fallback'] = True
            predictions[f'{zone}_lookahead_hours'] = lookahead_hours
            
            # Store for different time horizons
            for hours in [1, 3, 6]:
                if hours == lookahead_hours:
                    horizon_margin = 3.0 + (1.5 * hours)  # Wider for longer horizons
                    predictions[f'{zone}_predicted_{hours}h'] = float(predicted)
                    predictions[f'{zone}_lower_ci_{hours}h'] = float(max(0, predicted - horizon_margin))
                    predictions[f'{zone}_upper_ci_{hours}h'] = float(min(100, predicted + horizon_margin))
                    predictions[f'{zone}_ci_width_{hours}h'] = float(2 * horizon_margin)
        
        return predictions
    
    def get_model_info(self):
        """Get information about trained models"""
        info = {
            'models_trained': len(self.models),
            'training_history': self.training_history[-5:] if self.training_history else [],
            'training_history_count': len(self.training_history),
            'current_models': {},
            'overall_stats': {}
        }
        
        if self.models:
            r_squared_values = [m['r_squared'] for m in self.models.values()]
            mae_values = [m['mae'] for m in self.models.values()]
            rmse_values = [m.get('rmse', 0) for m in self.models.values()]
            model_ages = [self._get_model_age(m['trained_at']) for m in self.models.values()]
            
            info['overall_stats'] = {
                'avg_r_squared': float(np.mean(r_squared_values)),
                'min_r_squared': float(np.min(r_squared_values)),
                'max_r_squared': float(np.max(r_squared_values)),
                'avg_mae': float(np.mean(mae_values)),
                'avg_rmse': float(np.mean(rmse_values)),
                'avg_model_age_hours': float(np.mean(model_ages)),
                'oldest_model_age_hours': float(np.max(model_ages)),
                'newest_model_age_hours': float(np.min(model_ages)),
                'total_features_used': sum([len(m['feature_columns']) for m in self.models.values()]),
                'avg_features_per_model': float(np.mean([len(m['feature_columns']) for m in self.models.values()]))
            }
        
        for zone, model_info in self.models.items():
            info['current_models'][zone] = {
                'r_squared': model_info['r_squared'],
                'mae': model_info['mae'],
                'rmse': model_info.get('rmse', None),
                'std_residuals': model_info.get('std_residuals', None),
                'trained_samples': model_info['trained_samples'],
                'training_samples': model_info.get('training_samples', 0),
                'trained_at': model_info['trained_at'],
                'model_age_hours': self._get_model_age(model_info['trained_at']),
                'features': model_info['feature_columns'],
                'feature_count': len(model_info['feature_columns']),
                'top_features': list(model_info.get('feature_importance', {}).keys())[:5],
                'previous_r_squared': model_info.get('previous_r_squared', 0),
                'improvement': model_info.get('improvement', 0),
                'previous_trained_at': model_info.get('previous_trained_at', None),
                'data_stats': model_info.get('training_data_details', {}).get('data_range', {})
            }
        
        return info
    
    def save_model(self, filepath):
        """Save trained models to file with complete improvement tracking metadata"""
        try:
            save_data = {
                'models': {},
                'training_history': self.training_history,
                'config': self.config,
                'saved_at': datetime.now().isoformat(),
                'model_count': len(self.models),
                'feature_counts': {zone: len(info['feature_columns']) for zone, info in self.models.items()},
                # ADD COMPLETE TRAINING HISTORY METADATA FOR IMPROVEMENT TRACKING
                'training_history_metadata': {
                    'previous_r_squared': {zone: info.get('previous_r_squared', 0) for zone, info in self.models.items()},
                    'previous_trained_at': {zone: info.get('previous_trained_at') for zone, info in self.models.items()},
                    'improvement': {zone: info.get('improvement', 0) for zone, info in self.models.items()},
                    'model_versions': {zone: len([t for t in self.training_history if zone in t.get('zones', [])]) for zone in self.models.keys()},
                    'first_trained_at': {zone: self._get_first_training_date(zone) for zone in self.models.keys()}
                },
                'feature_columns': self.feature_columns
            }
            
            for zone, model_info in self.models.items():
                # Extract serializable data
                save_data['models'][zone] = {
                    'feature_columns': model_info['feature_columns'],
                    'intercept': model_info['intercept'],
                    'coefficients': model_info['coefficients'],
                    'scaler_mean': model_info['scaler'].mean_.tolist() if hasattr(model_info['scaler'], 'mean_') else [],
                    'scaler_scale': model_info['scaler'].scale_.tolist() if hasattr(model_info['scaler'], 'scale_') else [],
                    'r_squared': model_info['r_squared'],
                    'mae': model_info['mae'],
                    'rmse': model_info.get('rmse', None),
                    'std_residuals': model_info.get('std_residuals', None),
                    'training_std': model_info.get('training_std', None),
                    'y_std': model_info.get('y_std', None),
                    'trained_samples': model_info['trained_samples'],
                    'training_samples': model_info.get('training_samples', 0),
                    'trained_at': model_info['trained_at'],
                    'previous_r_squared': model_info.get('previous_r_squared', 0),
                    'improvement': model_info.get('improvement', 0),
                    'previous_trained_at': model_info.get('previous_trained_at'),
                    'training_data_details': model_info.get('training_data_details', {}),
                    'feature_importance': model_info.get('feature_importance', {}),
                    'residual_stats': model_info.get('residual_stats', {})
                }
            
            with open(filepath, 'w') as f:
                json.dump(save_data, f, indent=2)
            
            print(f"? Models saved to {filepath}")
            print(f"?   Models: {len(self.models)}, Training records: {len(self.training_history)}")
            print(f"?   Improvement tracking metadata preserved for {len(save_data['training_history_metadata']['previous_r_squared'])} zones")
            return True
            
        except Exception as e:
            print(f"? Error saving models: {e}")
            traceback.print_exc()
            return False
    
    def _get_first_training_date(self, zone):
        """Helper method to find when a zone was first trained"""
        try:
            for record in self.training_history:
                if 'zones' in record and zone in record.get('zones', []):
                    return record.get('timestamp')
            return None
        except:
            return None
    
    def load_model(self, filepath):
        """Load trained models from file with complete improvement tracking metadata"""
        try:
            with open(filepath, 'r') as f:
                save_data = json.load(f)
            
            self.models = {}
            self.scalers = {}
            self.training_history = save_data.get('training_history', [])
            
            # Load training history metadata for improvement tracking
            metadata = save_data.get('training_history_metadata', {})
            previous_r_squared_dict = metadata.get('previous_r_squared', {})
            previous_trained_at_dict = metadata.get('previous_trained_at', {})
            improvement_dict = metadata.get('improvement', {})
            model_versions_dict = metadata.get('model_versions', {})
            first_trained_at_dict = metadata.get('first_trained_at', {})
            
            print(f"? Loading models with improvement tracking metadata")
            print(f"?   Found previous R² data for {len(previous_r_squared_dict)} zones")
            
            for zone, model_data in save_data.get('models', {}).items():
                # Reconstruct scaler
                scaler = StandardScaler()
                if 'scaler_mean' in model_data and 'scaler_scale' in model_data:
                    if model_data['scaler_mean'] and model_data['scaler_scale']:
                        scaler.mean_ = np.array(model_data['scaler_mean'])
                        scaler.scale_ = np.array(model_data['scaler_scale'])
                        scaler.n_features_in_ = len(model_data['scaler_mean'])
                
                # Reconstruct model
                model = LinearRegression()
                model.intercept_ = model_data['intercept']
                model.coef_ = np.array(model_data['coefficients'])
                model.n_features_in_ = len(model_data['coefficients'])
                
                # FIX: Restore previous training metadata with multiple fallback sources
                previous_r_squared = model_data.get('previous_r_squared', 
                                                    previous_r_squared_dict.get(zone, 0))
                previous_trained_at = model_data.get('previous_trained_at', 
                                                      previous_trained_at_dict.get(zone))
                improvement = model_data.get('improvement', 
                                            improvement_dict.get(zone, 0))
                
                # Build model info dictionary with complete metadata
                self.models[zone] = {
                    'model': model,
                    'scaler': scaler,
                    'feature_columns': model_data.get('feature_columns', []),
                    'intercept': model_data.get('intercept', 0),
                    'coefficients': model_data.get('coefficients', []),
                    'r_squared': model_data.get('r_squared', 0),
                    'mae': model_data.get('mae', 0),
                    'rmse': model_data.get('rmse', 0),
                    'std_residuals': model_data.get('std_residuals', 0),
                    'training_std': model_data.get('training_std', 0),
                    'y_std': model_data.get('y_std', 0),
                    'trained_samples': model_data.get('trained_samples', 0),
                    'training_samples': model_data.get('training_samples', 0),
                    'trained_at': model_data.get('trained_at', datetime.now().isoformat()),
                    'previous_r_squared': previous_r_squared,
                    'improvement': improvement,
                    'previous_trained_at': previous_trained_at,
                    'model_version': model_versions_dict.get(zone, 1),
                    'first_trained_at': first_trained_at_dict.get(zone),
                    'training_data_details': model_data.get('training_data_details', {}),
                    'feature_importance': model_data.get('feature_importance', {}),
                    'residual_stats': model_data.get('residual_stats', {})
                }
                
                self.scalers[zone] = scaler
                
                # Log loaded metadata for verification
                print(f"?   Loaded {zone}: R²={self.models[zone]['r_squared']:.4f}, Previous R²={previous_r_squared:.4f}, Improvement={improvement:+.4f}")
            
            print(f"? Models loaded from {filepath}")
            print(f"?   Models: {len(self.models)}, Training records: {len(self.training_history)}")
            print(f"?   Improvement tracking fully restored")
            
            # Also load feature columns from config if available
            if 'feature_columns' in save_data and not hasattr(self, 'feature_columns'):
                self.feature_columns = save_data.get('feature_columns', [])
            
            return True
            
        except Exception as e:
            print(f"? Error loading models: {e}")
            traceback.print_exc()
            return False
    
    def get_training_summary(self):
        """Get a summary of training history with improvement tracking"""
        if not self.training_history:
            return "No training history available"
        
        summary = {
            'total_trainings': len(self.training_history),
            'successful_trainings': len([t for t in self.training_history if t.get('success', True)]),
            'failed_trainings': len([t for t in self.training_history if not t.get('success', True)]),
            'recent_trainings': [],
            'trigger_counts': {},
            'performance_trend': [],
            'improvement_summary': {}
        }
        
        # Calculate improvement trends for each zone
        for zone in ['soil1', 'soil2', 'soil3']:
            if zone in self.models:
                zone_improvements = []
                for record in self.training_history[-10:]:  # Last 10 trainings
                    if 'zones' in record and zone in record.get('zones', []):
                        zone_improvements.append({
                            'timestamp': record.get('timestamp'),
                            'r_squared': record.get('avg_r_squared', 0),
                            'trigger': record.get('trigger')
                        })
                
                summary['improvement_summary'][zone] = {
                    'current_r_squared': self.models[zone].get('r_squared', 0),
                    'previous_r_squared': self.models[zone].get('previous_r_squared', 0),
                    'improvement': self.models[zone].get('improvement', 0),
                    'last_trained': self.models[zone].get('trained_at'),
                    'history': zone_improvements[-3:]  # Last 3 improvements
                }
        
        # Count triggers
        for record in self.training_history:
            trigger = record.get('trigger', 'unknown')
            summary['trigger_counts'][trigger] = summary['trigger_counts'].get(trigger, 0) + 1
            
            # Track performance trend
            if 'avg_r_squared' in record:
                summary['performance_trend'].append({
                    'timestamp': record.get('timestamp', 'unknown'),
                    'avg_r_squared': record.get('avg_r_squared', 0),
                    'models_trained': record.get('models_trained', 0)
                })
        
        # Get recent trainings
        recent = self.training_history[-5:] if len(self.training_history) >= 5 else self.training_history
        for record in recent:
            recent_summary = {
                'timestamp': record.get('timestamp', 'unknown'),
                'models_trained': record.get('models_trained', 0),
                'samples_used': record.get('samples_used', 0),
                'avg_r_squared': record.get('avg_r_squared', 0),
                'trigger': record.get('trigger', 'unknown'),
                'success': record.get('success', True),
                'feature_counts': record.get('feature_counts', {})
            }
            summary['recent_trainings'].append(recent_summary)
        
        return summary
    
    def auto_train_if_needed(self, min_samples=200000, min_r_squared=0.0, max_age_hours=24, 
                            force_periodic_only=True):
        """Automatically train models if conditions are met - PERIODIC ONLY VERSION"""
        try:
            print("? Checking if auto-training is needed...")
            
            # Check if we have any models
            if not self.models:
                print("? No models found, auto-training...")
                return self.train(trigger='auto_no_models')
            
            # PERIODIC ONLY - Check model age ONLY (24 hours)
            oldest_model_age = 0
            for zone, model_info in self.models.items():
                model_age = self._get_model_age(model_info['trained_at'])
                oldest_model_age = max(oldest_model_age, model_age)
            
            # ONLY train if models are older than max_age_hours
            if oldest_model_age > max_age_hours:
                print(f"? PERIODIC TRAINING: Models are {oldest_model_age:.1f} hours old (max: {max_age_hours}), auto-training...")
                return self.train(trigger='periodic_24h')
            
            # If force_periodic_only is False, check other conditions (for backward compatibility)
            if not force_periodic_only:
                # Check model performance (ONLY if not doing periodic-only)
                avg_r_squared = np.mean([m['r_squared'] for m in self.models.values()])
                if avg_r_squared < min_r_squared * 0.5:  # Only train if REALLY bad
                    print(f"? Model performance VERY low (R²={avg_r_squared:.3f}), emergency training...")
                    return self.train(trigger='emergency_low_performance')
                
                # Check if enough new data is available (ONLY if not doing periodic-only)
                df = self.load_training_data(days=1)
                if len(df) >= min_samples * 10:  # Only if LOTS of new data
                    print(f"? {len(df)} new samples available (min: {min_samples*10}), auto-training...")
                    return self.train(trigger='auto_new_data')
            
            print("? Auto-training not needed (periodic only)")
            return False
            
        except Exception as e:
            print(f"? Auto-training check failed: {e}")
            return False


# Example usage
if __name__ == "__main__":
    # Test the predictor
    config = {
        'db_path': 'greenhouse.db',
        'training_days': 7,
        'min_samples': 20,
        'auto_train': {
            'min_samples': 50,
            'min_r_squared': 0.3,
            'max_age_hours': 24,
            'force_periodic_only': True
        }
    }
    
    predictor = SoilMoisturePredictor('greenhouse.db', config)
    
    # Test auto-training
    print("=" * 60)
    print("Testing auto-training logic...")
    print("=" * 60)
    
    auto_trained = predictor.auto_train_if_needed()
    if auto_trained:
        print("? Auto-training successful!")
    else:
        print("? Auto-training not performed or failed")
    
    # Train models if auto-training didn't
    if not predictor.models:
        print("\n? Manual training...")
        if predictor.train(trigger='manual_test'):
            print("? Manual training successful!")
        else:
            print("? Manual training failed")
    
    if predictor.models:
        # Make predictions
        current_conditions = {
            'temperature': 25.5,
            'humidity': 65.0,
            'soil1': 45.0,
            'soil2': 47.0,
            'soil3': 43.0
        }
        
        # Test regular predictions
        predictions = predictor.predict_soil_changes(current_conditions, irrigation_duration=0)
        print(f"\n? Regular predictions:")
        for zone in ['soil1', 'soil2', 'soil3']:
            print(f"  {zone}:")
            print(f"    Current: {predictions.get(f'{zone}_current', 'N/A')}")
            print(f"    Predicted: {predictions.get(f'{zone}_predicted', 'N/A')}")
            print(f"    Change: {predictions.get(f'{zone}_change', 'N/A')}")
            print(f"    Fallback: {predictions.get(f'{zone}_fallback', 'N/A')}")
        
        # Test predictions with confidence intervals
        predictions_ci = predictor.predict_with_confidence(current_conditions, irrigation_duration=0, lookahead_hours=2)
        print(f"\n? Predictions with confidence intervals (IMPROVED):")
        for zone in ['soil1', 'soil2', 'soil3']:
            print(f"  {zone}:")
            print(f"    Current: {predictions_ci.get(f'{zone}_current', 'N/A')}")
            print(f"    Predicted: {predictions_ci.get(f'{zone}_predicted', 'N/A'):.1f}")
            print(f"    Change: {predictions_ci.get(f'{zone}_change', 'N/A'):.1f}")
            print(f"    CI: [{predictions_ci.get(f'{zone}_lower_ci', 'N/A'):.1f}, {predictions_ci.get(f'{zone}_upper_ci', 'N/A'):.1f}]")
            print(f"    Confidence: ±{predictions_ci.get(f'{zone}_confidence_interval', 'N/A'):.1f}")
            print(f"    Model weight: {predictions_ci.get(f'{zone}_model_weight', 'N/A'):.2f}")
            print(f"    R²: {predictions_ci.get(f'{zone}_r_squared', 'N/A'):.3f}")
        
        # Get model info
        info = predictor.get_model_info()
        print(f"\n? Model info:")
        print(f"  Models trained: {info['models_trained']}")
        print(f"  Training history count: {info['training_history_count']}")
        if info['overall_stats']:
            print(f"  Average R²: {info['overall_stats']['avg_r_squared']:.4f}")
            print(f"  Average MAE: {info['overall_stats']['avg_mae']:.2f}")
        
        # Test with irrigation scenario
        print(f"\n? Testing with 2 hours ahead prediction:")
        predictions_irrig = predictor.predict_with_confidence(current_conditions, irrigation_duration=0, lookahead_hours=2)
        for zone in ['soil1', 'soil2', 'soil3']:
            change = predictions_irrig.get(f'{zone}_change', 0)
            print(f"  {zone}: {change:+.1f}% change over 2 hours")
        
    else:
        print("? No models trained, testing fallback predictions")
        
        # Test fallback
        current_conditions = {
            'temperature': 25.5,
            'humidity': 65.0,
            'soil1': 45.0,
            'soil2': 47.0,
            'soil3': 43.0
        }
        
        # Test fallback with confidence intervals
        predictions = predictor.predict_with_confidence(current_conditions, irrigation_duration=0, lookahead_hours=2)
        print(f"\n? Fallback predictions with CI (REALISTIC):")
        for zone in ['soil1', 'soil2', 'soil3']:
            print(f"  {zone}:")
            print(f"    Current: {predictions.get(f'{zone}_current', 'N/A')}")
            print(f"    Predicted: {predictions.get(f'{zone}_predicted', 'N/A'):.1f}")
            print(f"    Change: {predictions.get(f'{zone}_change', 'N/A'):.1f}")
            print(f"    CI: [{predictions.get(f'{zone}_lower_ci', 'N/A'):.1f}, {predictions.get(f'{zone}_upper_ci', 'N/A'):.1f}]")