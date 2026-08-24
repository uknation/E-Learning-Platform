from flask import Blueprint, render_template, jsonify, request
from database.models import db, DailyRoadmap
from services.gemini_service import generate_roadmap, analyze_performance
from services.progress_service import award_xp, get_skill_stats
from utils import get_current_user
from datetime import date
import json

roadmap_bp = Blueprint('roadmap', __name__)


@roadmap_bp.route('/roadmap')
def roadmap_page():
    user = get_current_user()
    return render_template('roadmap.html', user=user)


@roadmap_bp.route('/api/roadmap/generate', methods=['POST'])
def generate():
    data = request.get_json()
    user = get_current_user()

    # Update user profile from onboarding form
    user.name = data.get('name', user.name)
    user.goal = data.get('goal', user.goal)
    user.english_level = int(data.get('english_level', user.english_level))
    user.typing_speed = int(data.get('typing_speed', user.typing_speed))
    user.communication_level = int(data.get('communication_level', user.communication_level))
    user.aptitude_level = int(data.get('aptitude_level', user.aptitude_level))
    user.reasoning_level = int(data.get('reasoning_level', user.reasoning_level))
    user.programming_level = int(data.get('programming_level', user.programming_level))
    user.daily_target_minutes = int(data.get('daily_minutes', user.daily_target_minutes))
    user.onboarded = True
    db.session.commit()

    # Generate plan using Gemini
    plan = generate_roadmap(user.to_dict())

    # Check if a roadmap for this day_number already exists
    today_roadmap = DailyRoadmap.query.filter_by(
        user_id=user.id, day_number=user.day_number
    ).first()

    if today_roadmap:
        today_roadmap.plan_json = json.dumps(plan)
        today_roadmap.total_tasks = len(plan.get('tasks', []))
    else:
        today_roadmap = DailyRoadmap(
            user_id=user.id,
            day_number=user.day_number,
            date=date.today(),
            plan_json=json.dumps(plan),
            total_tasks=len(plan.get('tasks', [])),
        )
        db.session.add(today_roadmap)

    db.session.commit()

    return jsonify({'roadmap': today_roadmap.to_dict()})


@roadmap_bp.route('/api/roadmap/today')
def today():
    user = get_current_user()
    today_roadmap = DailyRoadmap.query.filter_by(
        user_id=user.id, day_number=user.day_number
    ).first()

    if not today_roadmap:
        if user.onboarded:
            # Auto-generate next day's roadmap
            plan = generate_roadmap(user.to_dict())
            today_roadmap = DailyRoadmap(
                user_id=user.id,
                day_number=user.day_number,
                date=date.today(),
                plan_json=json.dumps(plan),
                total_tasks=len(plan.get('tasks', [])),
            )
            db.session.add(today_roadmap)
            db.session.commit()
        else:
            return jsonify({'roadmap': None, 'needs_setup': True})

    return jsonify({'roadmap': today_roadmap.to_dict(), 'user': user.to_dict()})


@roadmap_bp.route('/api/roadmap/complete-task', methods=['POST'])
def complete_task():
    data = request.get_json()
    user = get_current_user()
    task_id = data.get('task_id')
    xp_reward = int(data.get('xp_reward', 20))

    day_num = data.get('day_number', user.day_number)
    today_roadmap = DailyRoadmap.query.filter_by(
        user_id=user.id, day_number=day_num
    ).first()

    if not today_roadmap:
        return jsonify({'error': 'No roadmap for today'}), 404

    today_roadmap.complete_task(task_id)
    today_roadmap.xp_earned += xp_reward
    user.add_xp(xp_reward)

    # Check if all tasks done
    if len(today_roadmap.get_completed_tasks()) >= today_roadmap.total_tasks:
        today_roadmap.completed = True
        user.day_number += 1
        # Update phase
        day = user.day_number
        if day <= 15:
            user.current_level = 'Beginner'
        elif day <= 30:
            user.current_level = 'Foundation'
        elif day <= 60:
            user.current_level = 'Intermediate'
        elif day <= 90:
            user.current_level = 'Advanced'
        else:
            user.current_level = 'Job Ready'

        # Bonus XP for completing all tasks
        user.add_xp(50)

    db.session.commit()

    return jsonify({
        'completed_tasks': today_roadmap.get_completed_tasks(),
        'completion_percent': today_roadmap.completion_percent(),
        'all_done': today_roadmap.completed,
        'user_xp': user.xp,
        'user_level': user.level,
    })


@roadmap_bp.route('/api/roadmap/history')
def history():
    user = get_current_user()
    past = DailyRoadmap.query.filter_by(user_id=user.id)\
        .order_by(DailyRoadmap.date.desc()).limit(30).all()

    return jsonify([{
        'date': str(r.date),
        'day_number': r.day_number,
        'completion': r.completion_percent(),
        'xp_earned': r.xp_earned,
        'completed': r.completed,
    } for r in past])


@roadmap_bp.route('/api/roadmap/get')
def get_roadmap():
    user = get_current_user()
    day_number = request.args.get('day_number', type=int)
    
    if day_number is None:
        day_number = user.day_number
        
    roadmap = DailyRoadmap.query.filter_by(user_id=user.id, day_number=day_number).first()
    if not roadmap:
        return jsonify({'roadmap': None})
        
    return jsonify({'roadmap': roadmap.to_dict(), 'user': user.to_dict()})


@roadmap_bp.route('/api/roadmap/add-task', methods=['POST'])
def add_custom_task():
    data = request.get_json()
    user = get_current_user()
    day_num = data.get('day_number', user.day_number)
    
    roadmap = DailyRoadmap.query.filter_by(user_id=user.id, day_number=day_num).first()
    if not roadmap:
        return jsonify({'error': 'Roadmap not found'}), 404
        
    plan = roadmap.get_plan()
    tasks = plan.get('tasks', [])
    
    import uuid
    new_task = {
        'id': f"custom_{uuid.uuid4().hex[:8]}",
        'subject': data.get('subject', 'Custom'),
        'icon': data.get('icon', '🎯'),
        'title': data.get('title', 'Custom Task'),
        'description': data.get('description', ''),
        'duration_minutes': int(data.get('duration_minutes', 15)),
        'type': 'custom',
        'link': data.get('link', '#'),
        'xp_reward': int(data.get('xp_reward', 20))
    }
    
    tasks.append(new_task)
    plan['tasks'] = tasks
    roadmap.plan_json = json.dumps(plan)
    roadmap.total_tasks = len(tasks)
    
    # Mark as incomplete if it was completed but a new task is added
    if roadmap.completed:
        roadmap.completed = False
        
    db.session.commit()
    
    return jsonify({'success': True, 'roadmap': roadmap.to_dict()})


@roadmap_bp.route('/api/roadmap/edit-task', methods=['POST'])
def edit_custom_task():
    data = request.get_json()
    user = get_current_user()
    day_num = data.get('day_number', user.day_number)
    task_id = data.get('task_id')
    
    roadmap = DailyRoadmap.query.filter_by(user_id=user.id, day_number=day_num).first()
    if not roadmap:
        return jsonify({'error': 'Roadmap not found'}), 404
        
    plan = roadmap.get_plan()
    tasks = plan.get('tasks', [])
    
    for t in tasks:
        if t['id'] == task_id:
            t['title'] = data.get('title', t['title'])
            t['description'] = data.get('description', t['description'])
            t['duration_minutes'] = int(data.get('duration_minutes', t['duration_minutes']))
            t['xp_reward'] = int(data.get('xp_reward', t['xp_reward']))
            t['icon'] = data.get('icon', t['icon'])
            t['link'] = data.get('link', t['link'])
            break
            
    plan['tasks'] = tasks
    roadmap.plan_json = json.dumps(plan)
    db.session.commit()
    
    return jsonify({'success': True, 'roadmap': roadmap.to_dict()})


@roadmap_bp.route('/api/roadmap/delete-task', methods=['POST'])
def delete_custom_task():
    data = request.get_json()
    user = get_current_user()
    day_num = data.get('day_number', user.day_number)
    task_id = data.get('task_id')
    
    roadmap = DailyRoadmap.query.filter_by(user_id=user.id, day_number=day_num).first()
    if not roadmap:
        return jsonify({'error': 'Roadmap not found'}), 404
        
    plan = roadmap.get_plan()
    tasks = plan.get('tasks', [])
    
    new_tasks = [t for t in tasks if t['id'] != task_id]
    plan['tasks'] = new_tasks
    roadmap.plan_json = json.dumps(plan)
    roadmap.total_tasks = len(new_tasks)
    
    completed = roadmap.get_completed_tasks()
    if task_id in completed:
        completed.remove(task_id)
        roadmap.completed_tasks = json.dumps(completed)
        
    db.session.commit()
    
    return jsonify({'success': True, 'roadmap': roadmap.to_dict()})


@roadmap_bp.route('/api/roadmap/reorder-tasks', methods=['POST'])
def reorder_custom_tasks():
    data = request.get_json()
    user = get_current_user()
    day_num = data.get('day_number', user.day_number)
    task_ids = data.get('task_ids', [])
    
    roadmap = DailyRoadmap.query.filter_by(user_id=user.id, day_number=day_num).first()
    if not roadmap:
        return jsonify({'error': 'Roadmap not found'}), 404
        
    plan = roadmap.get_plan()
    tasks = plan.get('tasks', [])
    
    task_map = {t['id']: t for t in tasks}
    ordered_tasks = []
    for tid in task_ids:
        if tid in task_map:
            ordered_tasks.append(task_map[tid])
            
    for t in tasks:
        if t['id'] not in task_ids:
            ordered_tasks.append(t)
            
    plan['tasks'] = ordered_tasks
    roadmap.plan_json = json.dumps(plan)
    db.session.commit()
    
    return jsonify({'success': True, 'roadmap': roadmap.to_dict()})
