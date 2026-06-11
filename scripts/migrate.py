#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migration script to move from old structure to new structure
"""

import os
import shutil
import sqlite3
from pathlib import Path
import json

def migrate_data():
    """Migrate data from old structure to new"""
    print("===========================================")
    print("GREENHOUSE SYSTEM MIGRATION")
    print("===========================================")
    
    # Define paths
    old_dir = Path("../GREENHOUSE")
    new_dir = Path(".")
    
    if not old_dir.exists():
        print("?? Old GREENHOUSE directory not found")
        return False
    
    print("? Found old directory:", old_dir)
    print("? New directory:", new_dir)
    
    # 1. Copy database
    old_db = old_dir / "greenhouse.db"
    new_db = new_dir / "data" / "database" / "greenhouse.db"
    
    if old_db.exists():
        print(f"? Copying database...")
        shutil.copy2(old_db, new_db)
        print(f"  Copied {old_db} to {new_db}")
    else:
        print("? No existing database found")
    
    # 2. Copy ML models
    old_ml_dir = old_dir / "greenhouse_ml"
    new_ml_dir = new_dir / "data" / "ml_models"
    
    if old_ml_dir.exists():
        print(f"? Copying ML models...")
        if not new_ml_dir.exists():
            new_ml_dir.mkdir(parents=True)
        
        for file in old_ml_dir.iterdir():
            if file.is_file():
                shutil.copy2(file, new_ml_dir / file.name)
                print(f"  Copied {file.name}")
    
    # 3. Copy LR models
    old_lr_dir = old_dir / "lr_models"
    new_lr_dir = new_dir / "data" / "ml_models" / "lr"
    
    if old_lr_dir.exists():
        print(f"? Copying LR models...")
        if not new_lr_dir.exists():
            new_lr_dir.mkdir(parents=True)
        
        for file in old_lr_dir.iterdir():
            if file.is_file():
                shutil.copy2(file, new_lr_dir / file.name)
                print(f"  Copied {file.name}")
    
    # 4. Copy Q-learning model
    old_q_model = old_dir / "q_learning_model.pkl"
    new_q_model = new_dir / "data" / "ml_models" / "q_learning.pkl"
    
    if old_q_model.exists():
        print(f"? Copying Q-learning model...")
        shutil.copy2(old_q_model, new_q_model)
    
    # 5. Copy HTML templates
    print(f"? Copying HTML templates...")
    templates_to_copy = {
        'graphs.html': 'templates/admin/graphs.html',
        'graphs_enhanced.html': 'templates/admin/graphs_enhanced.html',
        'resource_graphs.html': 'templates/admin/resource_graphs.html'
    }
    
    for old_name, new_path in templates_to_copy.items():
        old_file = old_dir / old_name
        new_file = new_dir / new_path
        
        if old_file.exists():
            shutil.copy2(old_file, new_file)
            print(f"  Copied {old_name}")
    
    print("===========================================")
    print("MIGRATION COMPLETE!")
    print("===========================================")
    
    # Verify database
    if new_db.exists():
        try:
            conn = sqlite3.connect(new_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
            count = cursor.fetchone()[0]
            conn.close()
            print(f"? Database verified: {count} records")
        except Exception as e:
            print(f"?? Database verification failed: {e}")
    
    return True

if __name__ == "__main__":
    migrate_data()