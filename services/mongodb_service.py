"""
SkillRise AI — MongoDB Integration Service
"""

import os
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

load_dotenv()

_mongo_client = None
_db = None

def get_db():
    global _mongo_client, _db
    if _db is None:
        uri = os.getenv('MONGODB_URI', '')
        if not uri:
            raise ValueError("MONGODB_URI is not set in environment variables.")
        
        import certifi
        # Connect to the MongoDB cluster with certifi CA bundle and bypass invalid certificates
        _mongo_client = MongoClient(uri, tls=True, tlsAllowInvalidCertificates=True)
        # Use 'elearning_db' database
        _db = _mongo_client.get_database('elearning_db')
    return _db

def save_lead(lead_data):
    """Save an inquiry lead submitted from the landing page."""
    try:
        db = get_db()
        # Add a timestamp to the lead
        from datetime import datetime
        lead_data['created_at'] = datetime.utcnow().isoformat()
        
        result = db.leads.insert_one(lead_data)
        return str(result.inserted_id)
    except Exception as e:
        print(f"Error saving lead to MongoDB: {e}")
        return None

def get_leads():
    """Retrieve all inquiry leads sorted by submission time."""
    try:
        db = get_db()
        leads_cursor = db.leads.find().sort('created_at', -1)
        leads_list = []
        for doc in leads_cursor:
            doc['_id'] = str(doc['_id'])
            leads_list.append(doc)
        return leads_list
    except Exception as e:
        print(f"Error fetching leads from MongoDB: {e}")
        return []

def delete_lead(lead_id):
    """Delete a lead document by its ID."""
    try:
        db = get_db()
        result = db.leads.delete_one({'_id': ObjectId(lead_id)})
        return result.deleted_count > 0
    except Exception as e:
        print(f"Error deleting lead from MongoDB: {e}")
        return False

# Quick connection test
if __name__ == '__main__':
    try:
        print("Testing MongoDB Connection...")
        db = get_db()
        # Ping the database
        db.command('ping')
        print("Successfully connected to MongoDB Atlas!")
    except Exception as ex:
        print(f"MongoDB connection test failed: {ex}")
