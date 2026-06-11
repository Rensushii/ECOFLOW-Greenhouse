# -*- coding: utf-8 -*-
"""
Flask route definitions
"""

from flask import render_template, jsonify, request, session, redirect, url_for
import hashlib
import secrets
from functools import wraps
from config import ADMIN_PASSWORD, API_KEY, FLASK_SECRET_KEY

def create_routes(app, sensor_manager, ml_manager, resource_tracker, frontend_sync, db_manager):
    """Create all Flask routes"""
    
    # Authentication decorator
    def login_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('admin_logged_in'):
                return redirect(url_for('login', next=request.url))
            return f(*args, **kwargs)
        return decorated_function
    
    # API key check decorator
    def api_key_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            incoming_key = request.headers.get('X-Api-Key')
            if incoming_key != API_KEY and request.args.get('key') != API_KEY:
                return jsonify({"error": "Unauthorized"}), 401
            return f(*args, **kwargs)
        return decorated_function
    
    # ==================== Main Pages ====================
    
    @app.route('/')
    def kiosk():
        """Simplified kiosk interface for users"""
        return render_template('kiosk.html')
    
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Admin login page"""
        if request.method == 'POST':
            password = request.form.get('password', '')
            password_hash = hashlib.sha256(password.encode()).hexdigest()
            admin_hash = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
            
            if password_hash == admin_hash:
                session['admin_logged_in'] = True
                return redirect(url_for('admin'))
            else:
                return render_template('admin/login.html', error="Invalid password")
        
        return render_template('admin/login.html')
    
    @app.route('/logout')
    def logout():
        """Logout admin"""
        session.pop('admin_logged_in', None)
        return redirect(url_for('kiosk'))
    
    @app.route('/admin')
    @login_required
    def admin():
        """Full admin dashboard"""
        return render_template('admin/dashboard.html')
    
    @app.route('/graphs')
    @login_required
    def graphs():
        """Graphs page"""
        return render_template('admin/graphs.html')
    
    @app.route('/graphs/enhanced')
    @login_required
    def graphs_enhanced():
        """Enhanced graphs page"""
        return render_template('admin/graphs_enhanced.html')
    
    # ==================== API Endpoints ====================
    
    @app.route('/api/sensors')
    def sensors():
        """Get current sensor data"""
        sensor_data = sensor_manager.get_current_data()
        return jsonify(sensor_data)
    
    @app.route('/api/control', methods=['POST'])
    @api_key_required
    def control():
        """Control pump/valve"""
        data = request.json
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        # Determine format
        if 'device' in data and 'state' in data:
            # Frontend API format
            target_device = data.get('device')
            target_state = data.get('state')
            
            # Convert to internal format
            if target_device == 'valve':
                data = {"valve": "on" if target_state.upper() == "ON" else "off"}
            elif target_device == 'pump':
                data = {"pump": "on" if target_state.upper() == "ON" else "off"}
            else:
                return jsonify({"error": "Unknown device. Use 'valve' or 'pump'"}), 400
        
        # Send command to ESP32
        success = sensor_manager.send_command(data)
        
        if success:
            return jsonify({
                "success": True,
                "message": "Command executed",
                "command_sent": data,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            })
        else:
            return jsonify({"error": "Serial connection is down"}), 503
    
    @app.route('/api/data')
    @api_key_required
    def get_data():
        """Get historical sensor data"""
        hours = request.args.get('hours', type=int)
        limit = request.args.get('limit', 100, type=int)
        
        if hours:
            data = db_manager.get_data_range(hours=hours, limit=limit)
        else:
            data = db_manager.get_latest_data(limit=limit)
        
        return jsonify(data)
    
    @app.route('/api/ml/status')
    def ml_status():
        """Get ML system status"""
        if ml_manager:
            status = ml_manager.get_system_status()
            status["ml_enabled"] = True
            return jsonify(status)
        else:
            return jsonify({
                "error": "ML system not enabled",
                "ml_enabled": False,
                "system_state": "disabled"
            })
    
    @app.route('/api/ml/decision')
    def ml_decision():
        """Get current ML irrigation decision"""
        if not ml_manager:
            return jsonify({
                "error": "ML system not enabled",
                "action": 0,
                "reason": "ML system disabled"
            })
        
        sensor_data = sensor_manager.get_current_data()
        if not sensor_data:
            return jsonify({
                "error": "No sensor data available",
                "action": 0,
                "reason": "No sensor data"
            })
        
        decision = ml_manager.make_irrigation_decision(sensor_data)
        return jsonify(decision)
    
    @app.route('/api/ml/train', methods=['POST'])
    @api_key_required
    def train_ml():
        """Retrain ML models"""
        if not ml_manager:
            return jsonify({"error": "ML system not enabled"}), 400
        
        success = ml_manager.train_all_models()
        
        if success:
            return jsonify({
                "success": True,
                "message": "All ML models retrained successfully"
            })
        else:
            return jsonify({"error": "Failed to retrain models"}), 500
    
    @app.route('/api/resources')
    def get_resources():
        """Get current resource consumption"""
        if resource_tracker:
            resources = resource_tracker.get_resource_usage()
            return jsonify({
                'success': True,
                'resources': resources
            })
        else:
            return jsonify({'error': 'Resource tracking not available'}), 500
    
    @app.route('/api/resources/reset', methods=['POST'])
    @api_key_required
    def reset_resources():
        """Reset resource counters"""
        if resource_tracker:
            resources = resource_tracker.reset_counters()
            return jsonify({
                'success': True,
                'message': 'Resource counters reset',
                'resources': resources
            })
        else:
            return jsonify({'error': 'Resource tracking not available'}), 500
    
    @app.route('/api/ai/decisions')
    def ai_decisions():
        """Get AI decision history"""
        if ml_manager and hasattr(ml_manager, 'decision_tracker'):
            decisions = ml_manager.decision_tracker.get_recent_decisions(limit=20)
            schedules = ml_manager.decision_tracker.get_upcoming_schedules()
            
            return jsonify({
                'success': True,
                'decisions': decisions,
                'upcoming_schedules': schedules
            })
        else:
            return jsonify({'error': 'AI decision tracking not available'}), 500
    
    @app.route('/api/check-commands')
    @api_key_required
    def check_commands():
        """Manually trigger command check"""
        return jsonify({
            "success": True,
            "message": "Command check endpoint",
            "processed_commands": len(frontend_sync.processed_commands)
        })
    
    @app.route('/api/clear-commands', methods=['POST'])
    @api_key_required
    def clear_commands():
        """Clear processed commands cache"""
        cleared = frontend_sync.clear_command_cache()
        return jsonify({
            "success": True,
            "message": f"Cleared {cleared} processed commands",
            "cleared_commands": cleared
        })
    
    @app.route('/api/sync-status')
    def sync_status():
        """Get current sync status"""
        return jsonify({
            "sync_active": True,
            "processed_commands": len(frontend_sync.processed_commands),
            "data_send_interval": DATA_SEND_INTERVAL,
            "command_check_interval": "5 seconds"
        })
    
    @app.route('/api/database-stats')
    def database_stats():
        """Get database statistics"""
        try:
            db_info = db_manager.get_database_info()
            return jsonify({
                'success': True,
                'database_info': db_info
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/backup-db', methods=['POST'])
    @api_key_required
    def backup_db():
        """Create database backup"""
        try:
            # Implementation depends on your backup logic
            return jsonify({
                "success": True,
                "message": "Backup functionality not implemented yet"
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    return app