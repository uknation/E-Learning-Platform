"""
SkillRise AI — Shared Utilities
Provides get_current_user without circular imports.
"""

from datetime import date, timedelta
from database.models import db, User


def get_current_user():
    """Get the currently logged-in student user, or fall back to the first user."""
    try:
        from flask import session
        user_id = session.get('user_id')
        user = None
        
        if user_id:
            user = User.query.get(user_id)
        if not user:
            username = session.get('username')
            if username:
                user = User.query.filter_by(name=username).first()
        if not user:
            user = User.query.first()
            
        if user:
            today = date.today()
            last = user.last_activity_date
            if last != today:
                if last == today - timedelta(days=1):
                    user.streak += 1
                else:
                    user.streak = 1
                user.last_activity_date = today
                user.add_xp(10)
                db.session.commit()
        else:
            # Create a user in-memory if query succeeded but user table is empty
            user = User(id=1, name="Student", goal="Become job-ready in 90 days")
        return user
    except Exception as e:
        print(f"MongoDB connection failed, falling back to mock user: {e}")
        # Return fallback mock user so page renders gracefully
        return User(
            id=1,
            name="Student (Offline Mode)",
            goal="Connect to MongoDB to save progress",
            xp=120,
            level=1,
            streak=3,
            badges='["first_quiz"]'
        )
