#!/usr/bin/env python3
"""
Verify the new system setup
"""

import sys
import os
from pathlib import Path

def check_structure():
    """Check if all required files and directories exist"""
    print("="*60)
    print("VERIFYING SYSTEM STRUCTURE")
    print("="*60)
    
    base_dir = Path(__file__).parent.parent
    issues = []
    
    # Required directories
    required_dirs = [
        base_dir / "src",
        base_dir / "src/database",
        base_dir / "src/sensors",
        base_dir / "src/ml",
        base_dir / "src/resources",
        base_dir / "src/api",
        base_dir / "src/frontend",
        base_dir / "src/utils",
        base_dir / "templates",
        base_dir / "templates/admin",
        base_dir / "static",
        base_dir / "data/database",
        base_dir / "data/backups",
        base_dir / "data/ml_models",
        base_dir / "logs",
    ]
    
    for dir_path in required_dirs:
        if dir_path.exists():
            print(f"? {dir_path.relative_to(base_dir)}")
        else:
            print(f"? {dir_path.relative_to(base_dir)} - MISSING")
            issues.append(f"Directory missing: {dir_path}")
    
    # Required files
    required_files = [
        base_dir / "app.py",
        base_dir / "config.py",
        base_dir / "run.py",
        base_dir / "requirements.txt",
        base_dir / "src/database/__init__.py",
        base_dir / "src/database/models.py",
        base_dir / "src/database/setup.py",
        base_dir / "templates/admin/login.html",
        base_dir / "templates/base.html",
    ]
    
    print("\nChecking files:")
    for file_path in required_files:
        if file_path.exists():
            print(f"? {file_path.relative_to(base_dir)}")
        else:
            print(f"? {file_path.relative_to(base_dir)} - MISSING")
            issues.append(f"File missing: {file_path}")
    
    # Check database
    db_path = base_dir / "data/database/greenhouse.db"
    if db_path.exists():
        print(f"? Database exists: {db_path}")
        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            print(f"? Database has {len(tables)} tables")
            conn.close()
        except Exception as e:
            print(f"? Database error: {e}")
            issues.append(f"Database error: {e}")
    else:
        print("? Database not found (this might be OK for fresh install)")
    
    # Check Python modules can be imported
    print("\nChecking Python imports:")
    try:
        import sys
        sys.path.insert(0, str(base_dir / "src"))
        
        imports_to_check = [
            ("config", "config"),
            ("database.models", "DatabaseManager"),
            ("sensors.data_processor", "DataProcessor"),
        ]
        
        for module, item in imports_to_check:
            try:
                exec(f"from {module} import {item}")
                print(f"? {module}.{item}")
            except ImportError as e:
                print(f"? {module}.{item} - {e}")
                issues.append(f"Import error: {module}.{item} - {e}")
    except Exception as e:
        print(f"? Import check failed: {e}")
        issues.append(f"Import check failed: {e}")
    
    print("\n" + "="*60)
    if issues:
        print(f"ISSUES FOUND: {len(issues)}")
        for i, issue in enumerate(issues, 1):
            print(f"{i}. {issue}")
        return False
    else:
        print("? ALL CHECKS PASSED!")
        return True

if __name__ == "__main__":
    success = check_structure()
    sys.exit(0 if success else 1)
