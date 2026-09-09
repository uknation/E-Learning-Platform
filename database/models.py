"""
SkillRise AI — Database Models (MongoDB Integrated)
Provides transparent MongoDB routing using SQLAlchemy-compatible adapters
"""

import os
import sys
import json
import types
from datetime import datetime, date
from pymongo import MongoClient
from bson.objectid import ObjectId

# ─── MongoDB Adapter Classes ────────────────────────

class MongoSortDescriptor:
    def __init__(self, field, direction):
        self.field = field
        self.direction = direction

class MongoColumn:
    def __init__(self, type_class=None, primary_key=False, nullable=True, default=None, ForeignKey=None):
        self.type_class = type_class
        self.primary_key = primary_key
        self.nullable = nullable
        self.default = default
        self.ForeignKey = ForeignKey
        self.field_name = None
        self.model_class = None
        
    def desc(self):
        return MongoSortDescriptor(self.field_name, -1)
        
    def asc(self):
        return MongoSortDescriptor(self.field_name, 1)
        
    def __eq__(self, other):
        return MongoFilterExpression(self, '==', other)

class MongoFilterExpression:
    def __init__(self, left, op, right):
        self.left = left
        self.op = op
        self.right = right
        
    def to_mongo(self):
        col_name = None
        if hasattr(self.left, 'column') and self.left.column:
            col_name = self.left.column.field_name
        elif hasattr(self.left, 'field_name') and self.left.field_name:
            col_name = self.left.field_name
            
        if col_name:
            if isinstance(self.right, (date, datetime)):
                # Full day matching for MongoDB
                start_dt = datetime(self.right.year, self.right.month, self.right.day, 0, 0, 0)
                end_dt = datetime(self.right.year, self.right.month, self.right.day, 23, 59, 59)
                return {col_name: {'$gte': start_dt, '$lte': end_dt}}
            else:
                return {col_name: self.right}
        return {}

class MongoFuncExpr:
    def __init__(self, func_name, column):
        self.func_name = func_name
        self.column = column
        
    def __eq__(self, other):
        return MongoFilterExpression(self, '==', other)

class MongoFunc:
    def sum(self, column):
        return MongoFuncExpr('sum', column)
        
    def max(self, column):
        return MongoFuncExpr('max', column)
        
    def date(self, column):
        return MongoFuncExpr('date', column)
        
    def __getattr__(self, name):
        return lambda column: MongoFuncExpr(name, column)

# Monkeypatch the standard sqlalchemy module
sq_mod = sys.modules.get('sqlalchemy') or types.ModuleType('sqlalchemy')
sq_mod.func = MongoFunc()
sys.modules['sqlalchemy'] = sq_mod

class MongoQuery:
    def __init__(self, model_class, db_conn):
        self.model_class = model_class
        self.db_conn = db_conn
        self.collection = db_conn[model_class.__tablename__]
        self.filters = {}
        self.sort_rules = []
        self._limit = None
        
    def filter_by(self, **kwargs):
        for k, v in kwargs.items():
            self.filters[k] = v
        return self
        
    def filter(self, *expressions):
        for expr in expressions:
            if isinstance(expr, MongoFilterExpression):
                self.filters.update(expr.to_mongo())
        return self
        
    def order_by(self, *args):
        for arg in args:
            if isinstance(arg, MongoSortDescriptor):
                self.sort_rules.append((arg.field, arg.direction))
            elif isinstance(arg, str):
                self.sort_rules.append((arg, 1))
        return self
        
    def limit(self, count):
        self._limit = count
        return self
        
    def first(self):
        results = self.all()
        return results[0] if results else None
        
    def get(self, ident):
        return self.filter_by(id=int(ident)).first()
        
    def all(self):
        try:
            cursor = self.collection.find(self.filters)
            if self.sort_rules:
                cursor = cursor.sort(self.sort_rules)
            if self._limit:
                cursor = cursor.limit(self._limit)
                
            models = []
            for doc in cursor:
                instance = self.model_class()
                for k, v in doc.items():
                    if k == '_id':
                        instance._id_val = str(v)
                    else:
                        col = self.model_class._columns().get(k)
                        if col and col.type_class == 'date_type' and isinstance(v, datetime):
                            v = v.date()
                        setattr(instance, k, v)
                models.append(instance)
            return models
        except Exception as e:
            print(f"MongoDB query all() failed, returning empty list: {e}")
            return []
        
    def count(self):
        try:
            return self.collection.count_documents(self.filters)
        except Exception as e:
            print(f"MongoDB query count() failed, returning 0: {e}")
            return 0

class MongoAggregateQuery:
    def __init__(self, db_conn, func_expr):
        self.db_conn = db_conn
        self.func_expr = func_expr
        self.model_class = func_expr.column.model_class
        self.collection = db_conn[self.model_class.__tablename__]
        self.filters = {}
        
    def filter_by(self, **kwargs):
        for k, v in kwargs.items():
            self.filters[k] = v
        return self
        
    def scalar(self):
        try:
            col_name = self.func_expr.column.field_name
            func_name = self.func_expr.func_name
            
            pipeline = []
            if self.filters:
                pipeline.append({'$match': self.filters})
                
            group_id = None
            if func_name == 'sum':
                pipeline.append({'$group': {'_id': group_id, 'result': {'$sum': f"${col_name}"}}})
            elif func_name == 'max':
                pipeline.append({'$group': {'_id': group_id, 'result': {'$max': f"${col_name}"}}})
                
            res = list(self.collection.aggregate(pipeline))
            if res:
                return res[0]['result']
            return 0
        except Exception as e:
            print(f"MongoDB query scalar() failed, returning 0: {e}")
            return 0

class MongoSession:
    def __init__(self, db_conn):
        self.db_conn = db_conn
        
    def query(self, *args):
        if len(args) == 1 and isinstance(args[0], MongoFuncExpr):
            return MongoAggregateQuery(self.db_conn, args[0])
        return MongoQuery(args[0], self.db_conn)
        
    def add(self, obj):
        self._write_obj(obj)
        
    def delete(self, obj):
        self._delete_obj(obj)
        
    def commit(self):
        pass
        
    def rollback(self):
        pass
        
    def _write_obj(self, obj):
        try:
            collection = self.db_conn[obj.__tablename__]
            data = {}
            for name, col in obj._columns().items():
                val = getattr(obj, name, col.default)
                if isinstance(val, (date, datetime)):
                    if isinstance(val, date) and not isinstance(val, datetime):
                        val = datetime(val.year, val.month, val.day)
                data[name] = val
                
            if getattr(obj, 'id', None) is not None:
                data['id'] = obj.id
                
            if getattr(obj, '_id_val', None) is not None:
                collection.update_one({'_id': ObjectId(obj._id_val)}, {'$set': data})
            else:
                if 'id' not in data or data['id'] is None:
                    last_doc = collection.find_one(sort=[('id', -1)])
                    new_id = (last_doc['id'] + 1) if (last_doc and 'id' in last_doc) else 1
                    data['id'] = new_id
                    obj.id = new_id
                    
                res = collection.insert_one(data)
                obj._id_val = str(res.inserted_id)
        except Exception as e:
            print(f"MongoDB write failed (running in offline fallback mode): {e}")
            
    def _delete_obj(self, obj):
        try:
            collection = self.db_conn[obj.__tablename__]
            if getattr(obj, '_id_val', None) is not None:
                collection.delete_one({'_id': ObjectId(obj._id_val)})
            elif getattr(obj, 'id', None) is not None:
                collection.delete_one({'id': obj.id})
        except Exception as e:
            print(f"MongoDB delete failed (running in offline fallback mode): {e}")

class MongoQueryDescriptor:
    def __get__(self, instance, owner):
        return MongoQuery(owner, db.db_conn)

class MongoModel:
    __tablename__ = None
    query = MongoQueryDescriptor()
    
    def __init__(self, **kwargs):
        # Initialize default values directly in instance dict
        for name, col in self._columns().items():
            default_val = col.default() if callable(col.default) else col.default
            self.__dict__[name] = default_val
        # Apply overrides
        for k, v in kwargs.items():
            self.__dict__[k] = v
                
    @classmethod
    def _columns(cls):
        cols = {}
        for k, v in cls.__dict__.items():
            if isinstance(v, MongoColumn):
                cols[k] = v
        return cols
        
    @property
    def id(self):
        if not hasattr(self, '_id_val_int'):
            self._id_val_int = None
        return self._id_val_int
        
    @id.setter
    def id(self, val):
        self._id_val_int = val
        
    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        for name, attr in cls.__dict__.items():
            if isinstance(attr, MongoColumn):
                attr.field_name = name
                attr.model_class = cls

class MongoSQLAlchemy:
    def __init__(self, app=None):
        self.client = None
        self.db_conn = None
        self.Model = MongoModel
        self.Column = MongoColumn
        self.Integer = 'integer_type'
        self.String = lambda *args, **kwargs: 'string_type'
        self.Text = 'text_type'
        self.DateTime = 'datetime_type'
        self.Date = 'date_type'
        self.Boolean = 'boolean_type'
        self.Float = 'float_type'
        self.func = MongoFunc()
        self.ForeignKey = lambda *args, **kwargs: None
        
        if app:
            self.init_app(app)
            
    def init_app(self, app):
        uri = os.getenv('MONGODB_URI', '')
        if not uri:
            raise ValueError("MONGODB_URI is not set in environment variables.")
        self.client = MongoClient(uri)
        self.db_conn = self.client.get_database('elearning_db')
        self.session = MongoSession(self.db_conn)
        
    def create_all(self):
        pass

db = MongoSQLAlchemy()

# ─── SkillRise AI Models ────────────────────────────

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default='Student')
    goal = db.Column(db.String(200), default='Improve Skills')
    avatar = db.Column(db.String(10), default='🎓')
    current_level = db.Column(db.String(50), default='Beginner')
    day_number = db.Column(db.Integer, default=1)
    xp = db.Column(db.Integer, default=0)
    level = db.Column(db.Integer, default=1)
    streak = db.Column(db.Integer, default=0)
    last_activity_date = db.Column(db.Date, default=date.today)
    daily_target_minutes = db.Column(db.Integer, default=60)
    badges = db.Column(db.Text, default='[]')  # JSON list
    active_dates = db.Column(db.Text, default='[]')  # JSON list of dates ('YYYY-MM-DD')

    # Skill levels (1-10)
    english_level = db.Column(db.Integer, default=1)
    typing_speed = db.Column(db.Integer, default=20)  # WPM
    communication_level = db.Column(db.Integer, default=1)
    aptitude_level = db.Column(db.Integer, default=1)
    reasoning_level = db.Column(db.Integer, default=1)
    programming_level = db.Column(db.Integer, default=1)
    verbal_level = db.Column(db.Integer, default=1)

    email = db.Column(db.String(100), default='')
    password = db.Column(db.String(100), default='')

    # Onboarding completed
    onboarded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_badges(self):
        return json.loads(self.badges)

    def add_badge(self, badge_name):
        badges = self.get_badges()
        if badge_name not in badges:
            badges.append(badge_name)
            self.badges = json.dumps(badges)
            return True
        return False

    def add_xp(self, points):
        self.xp += points
        new_level = (self.xp // 500) + 1
        self.level = new_level

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'goal': self.goal,
            'avatar': self.avatar,
            'current_level': self.current_level,
            'day_number': self.day_number,
            'xp': self.xp,
            'level': self.level,
            'streak': self.streak,
            'daily_target_minutes': self.daily_target_minutes,
            'badges': self.get_badges(),
            'english_level': self.english_level,
            'typing_speed': self.typing_speed,
            'communication_level': self.communication_level,
            'aptitude_level': self.aptitude_level,
            'reasoning_level': self.reasoning_level,
            'programming_level': self.programming_level,
            'verbal_level': self.verbal_level,
            'onboarded': self.onboarded,
            'active_dates': json.loads(self.active_dates) if self.active_dates else [],
        }


class DailyRoadmap(db.Model):
    __tablename__ = 'daily_roadmaps'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    day_number = db.Column(db.Integer, nullable=False)
    date = db.Column(db.Date, default=date.today)
    plan_json = db.Column(db.Text, nullable=False)  # JSON plan
    completed_tasks = db.Column(db.Text, default='[]')  # JSON list
    total_tasks = db.Column(db.Integer, default=0)
    xp_earned = db.Column(db.Integer, default=0)
    completed = db.Column(db.Boolean, default=False)

    def get_plan(self):
        return json.loads(self.plan_json)

    def get_completed_tasks(self):
        return json.loads(self.completed_tasks)

    def complete_task(self, task_id):
        tasks = self.get_completed_tasks()
        if task_id not in tasks:
            tasks.append(task_id)
            self.completed_tasks = json.dumps(tasks)

    def completion_percent(self):
        if self.total_tasks == 0:
            return 0
        return round((len(self.get_completed_tasks()) / self.total_tasks) * 100)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'day_number': self.day_number,
            'date': str(self.date),
            'plan': self.get_plan(),
            'completed_tasks': self.get_completed_tasks(),
            'total_tasks': self.total_tasks,
            'completion_percent': self.completion_percent(),
            'xp_earned': self.xp_earned,
            'completed': self.completed,
        }


class QuizResult(db.Model):
    __tablename__ = 'quiz_results'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject = db.Column(db.String(50), nullable=False)
    difficulty = db.Column(db.String(20), default='medium')
    score = db.Column(db.Integer, default=0)
    total = db.Column(db.Integer, default=10)
    accuracy = db.Column(db.Float, default=0.0)
    time_taken = db.Column(db.Integer, default=0)  # seconds
    weak_topics = db.Column(db.Text, default='[]')  # JSON
    answers_json = db.Column(db.Text, default='[]')  # JSON
    date = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'subject': self.subject,
            'difficulty': self.difficulty,
            'score': self.score,
            'total': self.total,
            'accuracy': self.accuracy,
            'time_taken': self.time_taken,
            'weak_topics': json.loads(self.weak_topics),
            'date': self.date.isoformat() if isinstance(self.date, datetime) else str(self.date),
        }


class TypingResult(db.Model):
    __tablename__ = 'typing_results'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    wpm = db.Column(db.Float, default=0.0)
    accuracy = db.Column(db.Float, default=0.0)
    correct_chars = db.Column(db.Integer, default=0)
    wrong_chars = db.Column(db.Integer, default=0)
    mode = db.Column(db.String(50), default='sentences')
    duration = db.Column(db.Integer, default=60)  # seconds
    is_personal_best = db.Column(db.Boolean, default=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'wpm': self.wpm,
            'accuracy': self.accuracy,
            'correct_chars': self.correct_chars,
            'wrong_chars': self.wrong_chars,
            'mode': self.mode,
            'duration': self.duration,
            'is_personal_best': self.is_personal_best,
            'date': self.date.isoformat() if isinstance(self.date, datetime) else str(self.date),
        }


class CommunicationSession(db.Model):
    __tablename__ = 'communication_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    mode = db.Column(db.String(50), default='daily_conversation')
    user_text = db.Column(db.Text, nullable=False)
    ai_feedback = db.Column(db.Text, nullable=False)  # JSON
    grammar_score = db.Column(db.Float, default=0.0)
    vocabulary_score = db.Column(db.Float, default=0.0)
    clarity_score = db.Column(db.Float, default=0.0)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'mode': self.mode,
            'user_text': self.user_text,
            'feedback': json.loads(self.ai_feedback),
            'grammar_score': self.grammar_score,
            'vocabulary_score': self.vocabulary_score,
            'clarity_score': self.clarity_score,
            'date': self.date.isoformat() if isinstance(self.date, datetime) else str(self.date),
        }


class ProgressEntry(db.Model):
    __tablename__ = 'progress_entries'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject = db.Column(db.String(50), nullable=False)
    metric_name = db.Column(db.String(50), nullable=False)
    value = db.Column(db.Float, nullable=False)
    date = db.Column(db.Date, default=date.today)

    def to_dict(self):
        return {
            'subject': self.subject,
            'metric_name': self.metric_name,
            'value': self.value,
            'date': str(self.date),
        }


class AIRecommendation(db.Model):
    __tablename__ = 'ai_recommendations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    recommendation_json = db.Column(db.Text, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    def get_recommendation(self):
        return json.loads(self.recommendation_json)


class CoachMessage(db.Model):
    __tablename__ = 'coach_messages'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'user' or 'assistant'
    content = db.Column(db.Text, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'role': self.role,
            'content': self.content,
            'date': self.date.isoformat() if isinstance(self.date, datetime) else str(self.date),
        }


class ATSScan(db.Model):
    __tablename__ = 'ats_scans'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    target_role = db.Column(db.String(100), default='')
    score = db.Column(db.Integer, default=0)
    matched_skills = db.Column(db.Text, default='[]')
    missing_skills = db.Column(db.Text, default='[]')
    analysis_json = db.Column(db.Text, default='{}')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_analysis(self):
        return json.loads(self.analysis_json) if self.analysis_json else {}

    def to_dict(self):
        return {
            'id': self.id,
            'target_role': self.target_role,
            'score': self.score,
            'matched_skills': json.loads(self.matched_skills) if self.matched_skills else [],
            'missing_skills': json.loads(self.missing_skills) if self.missing_skills else [],
            'analysis': self.get_analysis(),
            'created_at': self.created_at.isoformat() if isinstance(self.created_at, datetime) else str(self.created_at),
        }

