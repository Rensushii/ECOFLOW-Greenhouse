
import numpy as np
import json
import pickle
import os
import random
from datetime import datetime, timedelta
import pandas as pd
from collections import defaultdict

class QLearningSimulator:
    def __init__(self, config):
        self.config = config
        self.q_table = None
        self.state_space_size = None
        self.action_space_size = len(config['actions'])
        self.exploration_rate = config['exploration_rate']
        self.learning_rate = config['learning_rate']
        self.discount_factor = config['discount_factor']
        self.soil_bins = config['soil_bins']
        self.temp_bins = config['temp_bins']
        self.humidity_bins = config['humidity_bins']
        self._initialize_state_space()
        self.training_history = {'episodes': [], 'rewards': [], 'exploration_rates': []}
    
    def _initialize_state_space(self):
        self.state_space_size = (
            self.soil_bins ** 3 *
            self.temp_bins *
            self.humidity_bins *
            4 *
            self.action_space_size
        )
        self.q_table = np.zeros((self.state_space_size, self.action_space_size))
        print(f"Initialized Q-table with {self.state_space_size} states")
    
    def discretize_state(self, sensor_data):
        soil1_bin = np.digitize(sensor_data.get('soil1', 50), bins=np.linspace(0, 100, self.soil_bins))
        soil2_bin = np.digitize(sensor_data.get('soil2', 50), bins=np.linspace(0, 100, self.soil_bins))
        soil3_bin = np.digitize(sensor_data.get('soil3', 50), bins=np.linspace(0, 100, self.soil_bins))
        temp_bin = np.digitize(sensor_data.get('temperature', 25), bins=np.linspace(10, 40, self.temp_bins))
        humidity_bin = np.digitize(sensor_data.get('humidity', 60), bins=np.linspace(20, 90, self.humidity_bins))
        hour = datetime.now().hour
        time_bin = 0 if 5 <= hour < 11 else 1 if 11 <= hour < 17 else 2 if 17 <= hour < 23 else 3
        prev_action = sensor_data.get('prev_action', 0)
        action_bin = self.config['actions'].index(prev_action) if prev_action in self.config['actions'] else 0
        state_index = (
            soil1_bin * (self.soil_bins ** 2) +
            soil2_bin * self.soil_bins +
            soil3_bin +
            temp_bin * (self.soil_bins ** 3) +
            humidity_bin * (self.soil_bins ** 3 * self.temp_bins) +
            time_bin * (self.soil_bins ** 3 * self.temp_bins * self.humidity_bins) +
            action_bin * (self.soil_bins ** 3 * self.temp_bins * self.humidity_bins * 4)
        )
        return min(state_index, self.state_space_size - 1)
    
    def calculate_reward(self, soil_values, action_duration, zone_weights):
        reward = 0
        config = self.config['rewards']
        for i, (soil, zone_name) in enumerate(zip(soil_values, ['A', 'B', 'C'])):
            weight = zone_weights[zone_name]['weight']
            if 40 <= soil <= 60:
                reward += config['optimal_soil'] * weight
            elif soil < 30:
                reward += config['too_dry'] * weight
            elif soil > 70:
                reward += config['too_wet'] * weight
        if action_duration > 0:
            water_used = action_duration * 2
            if water_used > 20:
                reward += config['waste_water'] * (water_used / 10)
            reward += config['pump_energy'] * (action_duration / 5)
        return reward
    
    def simulate_soil_dynamics(self, current_soil, action_duration, zone_weights, temperature, humidity):
        new_soil_levels = []
        for i, (soil, zone_name) in enumerate(zip(current_soil, ['A', 'B', 'C'])):
            crop_factor = zone_weights[zone_name]['crop_factor']
            evaporation = 0.5 * (temperature / 30) * (1 - humidity / 100)
            irrigation_effect = 0
            if action_duration > 0:
                if zone_name == 'A':
                    irrigation_effect = action_duration * 0.8 * crop_factor
                elif zone_name == 'B':
                    irrigation_effect = action_duration * 1.0 * crop_factor
                else:
                    irrigation_effect = action_duration * 0.7 * crop_factor
            new_soil = soil - evaporation + irrigation_effect
            new_soil = max(0, min(100, new_soil))
            new_soil_levels.append(new_soil)
        return new_soil_levels
    
    def train(self, episodes=None):
        if episodes is None:
            episodes = self.config['episodes']
        print(f"Training Q-Learning with {episodes} episodes")
        for episode in range(episodes):
            state = {
                'soil1': random.uniform(20, 80),
                'soil2': random.uniform(20, 80),
                'soil3': random.uniform(20, 80),
                'temperature': random.uniform(15, 35),
                'humidity': random.uniform(30, 80),
                'prev_action': 0
            }
            total_reward = 0
            state_index = self.discretize_state(state)
            if random.uniform(0, 1) < self.exploration_rate:
                action_idx = random.randint(0, self.action_space_size - 1)
            else:
                action_idx = np.argmax(self.q_table[state_index])
            action_duration = self.config['actions'][action_idx]
            current_soil = [state['soil1'], state['soil2'], state['soil3']]
            next_soil = self.simulate_soil_dynamics(
                current_soil, action_duration,
                self.config['zones'],
                state['temperature'], state['humidity']
            )
            reward = self.calculate_reward(next_soil, action_duration, self.config['zones'])
            total_reward += reward
            next_state = state.copy()
            next_state.update({
                'soil1': next_soil[0],
                'soil2': next_soil[1],
                'soil3': next_soil[2],
                'prev_action': action_duration
            })
            next_state_index = self.discretize_state(next_state)
            old_value = self.q_table[state_index, action_idx]
            next_max = np.max(self.q_table[next_state_index])
            new_value = old_value + self.learning_rate * (reward + self.discount_factor * next_max - old_value)
            self.q_table[state_index, action_idx] = new_value
            self.exploration_rate = max(self.config['min_exploration_rate'], self.exploration_rate * self.config['exploration_decay'])
            if episode % 1000 == 0:
                print(f"Episode {episode}/{episodes}, Exploration: {self.exploration_rate:.3f}")
        print("Q-Learning training completed")
        return self.training_history
    
    def get_action(self, sensor_data):
        state_index = self.discretize_state(sensor_data)
        action_idx = np.argmax(self.q_table[state_index])
        action_duration = self.config['actions'][action_idx]
        return action_duration
    
    def save_model(self, filepath):
        model_data = {
            'q_table': self.q_table.tolist(),
            'exploration_rate': self.exploration_rate,
            'config': self.config,
            'training_history': self.training_history,
            'timestamp': datetime.now().isoformat()
        }
        with open(filepath, 'wb') as f:
            pickle.dump(model_data, f)
        print(f"Model saved to {filepath}")
    
    def load_model(self, filepath):
        if os.path.exists(filepath):
            with open(filepath, 'rb') as f:
                model_data = pickle.load(f)
            self.q_table = np.array(model_data['q_table'])
            self.exploration_rate = model_data['exploration_rate']
            self.training_history = model_data.get('training_history', {})
            print(f"Model loaded from {filepath}")
            return True
        return False
