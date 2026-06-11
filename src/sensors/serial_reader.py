# -*- coding: utf-8 -*-
"""
Serial communication with ESP32
"""

import serial
import json
import time
import threading
from config import SERIAL_PORTS, SERIAL_BAUD

class SerialReader:
    def __init__(self, data_callback=None, error_callback=None):
        self.ser = None
        self.data_callback = data_callback
        self.error_callback = error_callback
        self.running = False
        self.thread = None
        
    def connect(self):
        """Connect to ESP32 on available serial port"""
        for port in SERIAL_PORTS:
            try:
                self.ser = serial.Serial(port, SERIAL_BAUD, timeout=1)
                print(f"? Connected to ESP32 on {port}")
                time.sleep(2)  # Allow device initialization
                return True
            except serial.SerialException:
                continue
        
        print("?? ESP32 not found on any serial port")
        return False
    
    def disconnect(self):
        """Disconnect from ESP32"""
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None
    
    def read_data(self):
        """Read data from serial connection"""
        if not self.ser or not self.ser.is_open:
            return None
        
        try:
            if self.ser.in_waiting:
                line = self.ser.readline().decode().strip()
                if line:
                    data = json.loads(line)
                    return data
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"?? Serial read error: {e}")
        
        return None
    
    def send_command(self, command):
        """Send command to ESP32"""
        if not self.ser or not self.ser.is_open:
            print("?? Cannot send command: Serial connection is down")
            return False
        
        try:
            command_str = json.dumps(command) + "\n"
            self.ser.write(command_str.encode())
            print(f"? Sent to ESP32: {command_str.strip()}")
            return True
        except serial.SerialException as e:
            print(f"?? Serial write error: {e}")
            return False
    
    def start_reading(self):
        """Start continuous reading in background thread"""
        self.running = True
        self.thread = threading.Thread(target=self._reading_loop, daemon=True)
        self.thread.start()
        print("? Serial reader thread started")
    
    def stop_reading(self):
        """Stop continuous reading"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        self.disconnect()
    
    def _reading_loop(self):
        """Background reading loop"""
        while self.running:
            try:
                if self.ser is None or not self.ser.is_open:
                    if not self.connect():
                        time.sleep(5)
                        continue
                
                data = self.read_data()
                if data and self.data_callback:
                    self.data_callback(data)
                
                time.sleep(0.01)
                
            except serial.SerialException:
                print("?? ESP32 disconnected during read")
                self.disconnect()
                time.sleep(5)
                
            except Exception as e:
                print(f"?? Unexpected serial error: {e}")
                if self.error_callback:
                    self.error_callback(e)
                time.sleep(1)