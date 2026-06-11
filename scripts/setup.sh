#!/bin/bash
# Setup script for Greenhouse Monitoring System

echo "==========================================="
echo "GREENHOUSE MONITORING SYSTEM - SETUP"
echo "==========================================="

# Check Python version
echo "? Checking Python version..."
python3 --version
if [ $? -ne 0 ]; then
    echo "?? Python 3 is required but not installed"
    exit 1
fi

# Create virtual environment
echo "? Creating virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Install dependencies
echo "? Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create necessary directories
echo "? Creating directories..."
mkdir -p data/database data/backups data/ml_models logs templates/{admin,partials} static/{css,js}

# Copy existing database if it exists
if [ -f "../GREENHOUSE/greenhouse.db" ]; then
    echo "? Copying existing database..."
    cp ../GREENHOUSE/greenhouse.db data/database/
else
    echo "? No existing database found, will create new one"
fi

# Copy HTML templates if they exist
if [ -f "../GREENHOUSE/graphs.html" ]; then
    echo "? Copying HTML templates..."
    cp ../GREENHOUSE/graphs.html templates/admin/
    cp ../GREENHOUSE/graphs_enhanced.html templates/admin/
    cp ../GREENHOUSE/resource_graphs.html templates/admin/
fi

# Set permissions
echo "? Setting permissions..."
chmod +x run.py
chmod +x scripts/*.sh

echo "==========================================="
echo "SETUP COMPLETE!"
echo "==========================================="
echo ""
echo "To start the system:"
echo "1. Activate virtual environment: source venv/bin/activate"
echo "2. Run: python run.py"
echo ""
echo "Or use: ./run.py"
echo "==========================================="