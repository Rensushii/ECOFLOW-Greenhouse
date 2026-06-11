#!/usr/bin/env python3
"""
Migrate data from old system to new system
"""

import os
import shutil
import sqlite3
from pathlib import Path
import sys

def main():
    print("===========================================")
    print("DATA MIGRATION SCRIPT")
    print("===========================================")
    
    # Define paths
    old_dir = Path("/home/group4/GREENHOUSE")
    new_dir = Path("/home/group4/GREENHOUSE_NEW")
    
    if not old_dir.exists():
        print("?? Old directory not found:", old_dir)
        return False
    
    if not new_dir.exists():
        print("?? New directory not found:", new_dir)
        return False
    
    # 1. Copy database
    print("\n1. Copying database...")
    old_db = old_dir / "greenhouse.db"
    new_db = new_dir / "data" / "database" / "greenhouse.db"
    
    if old_db.exists():
        # Backup new database if exists
        if new_db.exists():
            backup = new_dir / "data" / "database" / "greenhouse.db.backup"
            shutil.copy2(new_db, backup)
            print(f"  Backed up new database to: {backup}")
        
        # Copy old database
        shutil.copy2(old_db, new_db)
        print(f"  Copied database: {old_db} -> {new_db}")
        
        # Verify the copy
        if new_db.exists():
            try:
                conn = sqlite3.connect(new_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM greenhouse_data")
                count = cursor.fetchone()[0]
                conn.close()
                print(f"  Verified: {count} records in database")
            except Exception as e:
                print(f"  Warning: Could not verify database: {e}")
    else:
        print("  No existing database found")
    
    # 2. Copy ML models
    print("\n2. Copying ML models...")
    
    # Create ML models directory
    ml_dir = new_dir / "data" / "ml_models"
    ml_dir.mkdir(exist_ok=True)
    
    # Copy q-learning model
    q_model = old_dir / "q_learning_model.pkl"
    if q_model.exists():
        shutil.copy2(q_model, ml_dir / "q_learning_model.pkl")
        print(f"  Copied: q_learning_model.pkl")
    
    # Copy greenhouse_ml directory
    gh_ml = old_dir / "greenhouse_ml"
    if gh_ml.exists() and gh_ml.is_dir():
        new_gh_ml = ml_dir / "greenhouse_ml"
        if new_gh_ml.exists():
            shutil.rmtree(new_gh_ml)
        shutil.copytree(gh_ml, new_gh_ml)
        print(f"  Copied directory: greenhouse_ml/")
    
    # Copy lr_models directory
    lr_models = old_dir / "lr_models"
    if lr_models.exists() and lr_models.is_dir():
        new_lr_models = ml_dir / "lr_models"
        if new_lr_models.exists():
            shutil.rmtree(new_lr_models)
        shutil.copytree(lr_models, new_lr_models)
        print(f"  Copied directory: lr_models/")
    
    # 3. Copy configuration if exists
    print("\n3. Copying configuration...")
    ml_config = old_dir / "ml_config.py"
    if ml_config.exists():
        # We'll just read it to see if we need to adjust anything
        print(f"  Found ml_config.py (check if any custom settings)")
    
    # 4. List what was copied
    print("\n" + "="*60)
    print("MIGRATION SUMMARY")
    print("="*60)
    
    # Check what we have
    print("\nNew system contains:")
    
    # Database
    if new_db.exists():
        try:
            conn = sqlite3.connect(new_db)
            cursor = conn.cursor()
            
            # List tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            print(f"  Database: {new_db}")
            print(f"  Tables: {len(tables)}")
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
                count = cursor.fetchone()[0]
                print(f"    - {table[0]}: {count} records")
            
            conn.close()
        except Exception as e:
            print(f"  Database check failed: {e}")
    
    # ML models
    ml_files = list(ml_dir.rglob("*"))
    if ml_files:
        print(f"  ML Models: {len(ml_files)} files")
        for file in ml_files[:10]:  # Show first 10
            if file.is_file():
                print(f"    - {file.relative_to(ml_dir)}")
        if len(ml_files) > 10:
            print(f"    ... and {len(ml_files) - 10} more")
    
    print("\n" + "="*60)
    print("MIGRATION COMPLETE!")
    print("="*60)
    
    print("\nNext steps:")
    print("1. Verify the system structure:")
    print("   cd /home/group4/GREENHOUSE_NEW")
    print("   python -c \"from src.database.setup import initialize_database; initialize_database()\"")
    print("\n2. Start the system:")
    print("   python run.py")
    print("\n3. Access the dashboard:")
    print("   http://localhost:5000/")
    print("   Admin: http://localhost:5000/login (password: ecoflow)")
    
    return True

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n?? Migration failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
