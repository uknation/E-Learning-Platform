"""
SkillRise AI — Authentication & Admin Routes (MongoDB Integrated)
"""

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from services.mongodb_service import save_lead, get_leads, delete_lead

auth_admin_bp = Blueprint('auth_admin', __name__)

@auth_admin_bp.route('/landing')
def landing_page():
    """Serve the public landing page."""
    return render_template('landing.html')

@auth_admin_bp.route('/login')
def login_page():
    """Redirect to the single-page landing page with login action."""
    return redirect(url_for('auth_admin.landing_page') + '?action=login')

@auth_admin_bp.route('/api/auth/login', methods=['POST'])
def login():
    """Authenticate student or admin and set session cookies."""
    data = request.get_json() or {}
    role = data.get('role', 'student')
    username = data.get('username')
    password = data.get('password')

    if role == 'admin':
        # Default simple admin credentials
        if username == 'admin' and password == 'admin123':
            session['logged_in'] = True
            session['user_role'] = 'admin'
            session['username'] = 'Admin'
            return jsonify({'status': 'success', 'redirect': '/landing'})
        else:
            return jsonify({'error': 'Invalid admin credentials'}), 401
    else:
        # Default simple student login to bypass auth for the demo
        if username == 'student' and password == 'student123':
            session['logged_in'] = True
            session['user_role'] = 'student'
            session['username'] = 'Student'
            try:
                from database.models import User
                u = User.query.filter_by(name='Student').first()
                if u:
                    session['user_id'] = u.id
            except Exception:
                pass
            return jsonify({'status': 'success', 'redirect': '/'})

        # Dynamic database check for registered students
        try:
            from database.models import User
            user = User.query.filter_by(email=username).first() or User.query.filter_by(name=username).first()
            if user and user.password == password:
                session['logged_in'] = True
                session['user_role'] = 'student'
                session['username'] = user.name
                session['user_id'] = user.id
                return jsonify({'status': 'success', 'redirect': '/'})
        except Exception as e:
            print(f"Database authentication check failed (running offline): {e}")

        return jsonify({'error': 'Invalid student credentials'}), 401

@auth_admin_bp.route('/api/auth/register', methods=['POST'])
def register():
    """Register a new student user."""
    data = request.get_json() or {}
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    confirm_password = data.get('confirm_password')

    if not name or not email or not password or not confirm_password:
        return jsonify({'error': 'Please fill all fields'}), 400

    if password != confirm_password:
        return jsonify({'error': 'Passwords do not match'}), 400

    import re
    if (len(password) < 8 or
        not re.search(r"[A-Z]", password) or
        not re.search(r"[a-z]", password) or
        not re.search(r"[0-9]", password) or
        not re.search(r"[^A-Za-z0-9]", password)):
        return jsonify({'error': 'Password is too weak. It must be at least 8 characters long and contain uppercase, lowercase, numbers, and special characters.'}), 400

    try:
        from database.models import User, db
        existing = User.query.filter_by(email=email).first()
        if existing:
            return jsonify({'error': 'User with this email already exists'}), 400

        new_user = User(
            name=name,
            email=email,
            password=password,
            goal='Become job-ready in 90 days'
        )
        db.session.add(new_user)
        db.session.commit()

        # Log in automatically after registration
        session['logged_in'] = True
        session['user_role'] = 'student'
        session['username'] = name
        session['user_id'] = new_user.id
        return jsonify({'status': 'success', 'redirect': '/'})
    except Exception as e:
        print(f"User registration failed: {e}")
        return jsonify({'error': 'Database registration failed'}), 500

@auth_admin_bp.route('/api/auth/logout', methods=['POST'])
def logout():
    """Clear session data."""
    session.clear()
    return jsonify({'status': 'success'})

@auth_admin_bp.route('/api/landing/submit', methods=['POST'])
def submit_inquiry():
    """Submit landing page inquiry and save in MongoDB Atlas."""
    data = request.get_json() or {}
    name = data.get('name')
    email = data.get('email')
    goal = data.get('goal')

    if not name or not email or not goal:
        return jsonify({'error': 'Please fill all fields'}), 400

    lead = {
        'name': name,
        'email': email,
        'goal': goal
    }
    
    lead_id = save_lead(lead)
    if lead_id:
        return jsonify({'status': 'success', 'id': lead_id})
    else:
        return jsonify({'error': 'Database save failed'}), 500

@auth_admin_bp.route('/admin')
def admin_page():
    """Redirect admin requests directly to the single-page view."""
    if not session.get('logged_in') or session.get('user_role') != 'admin':
        return redirect(url_for('auth_admin.landing_page') + '?action=login')
    return redirect(url_for('auth_admin.landing_page'))

@auth_admin_bp.route('/api/admin/leads')
def api_get_leads():
    """Retrieve registered student users from MongoDB Atlas."""
    if not session.get('logged_in') or session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        from database.models import User
        from datetime import datetime
        users = User.query.all()
        user_list = []
        for u in users:
            dt = u.created_at
            if isinstance(dt, datetime):
                date_str = dt.isoformat()
            else:
                date_str = str(dt) if dt else datetime.utcnow().isoformat()
                
            user_list.append({
                '_id': getattr(u, '_id_val', str(u.id)),
                'name': u.name,
                'email': u.email or 'student@skillrise.ai',
                'password': u.password or 'student123',
                'goal': u.goal or 'Become job-ready in 90 days',
                'created_at': date_str
            })
        return jsonify(user_list)
    except Exception as e:
        print(f"Error fetching users: {e}")
        return jsonify([])

@auth_admin_bp.route('/api/admin/delete-lead', methods=['POST'])
def api_delete_lead():
    """Delete a student user account from MongoDB Atlas."""
    if not session.get('logged_in') or session.get('user_role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.get_json() or {}
    lead_id = data.get('id')
    
    if not lead_id:
        return jsonify({'error': 'Missing user ID'}), 400
        
    try:
        from database.models import db
        from bson.objectid import ObjectId
        
        # Delete using MongoDB collection directly
        try:
            db.db_conn.users.delete_one({'_id': ObjectId(lead_id)})
        except Exception:
            pass
            
        try:
            db.db_conn.users.delete_one({'id': int(lead_id)})
        except Exception:
            pass
            
        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"Failed to delete student: {e}")
        return jsonify({'error': str(e)}), 500
