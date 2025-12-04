import os
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from pymongo import MongoClient
from datetime import datetime, timedelta
from bson.objectid import ObjectId

# --- Configuration ---
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'attendance_tracking_db'
ACTIVITY_TIMEOUT_MINUTES = 5 # Students are considered offline if no activity is recorded within this time

# --- App Setup ---
app = Flask(__name__, template_folder='templates')
CORS(app)

# --- Database Initialization ---
try:
    # Use explicit port/host if MONGO_URI is default for better debugging clarity
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ismaster')
    db = client[DB_NAME]
    events_collection = db['events']
    print(f"--- MongoDB Connection Successful: Connected to DB '{DB_NAME}' ---")
except Exception as e:
    print(f"--- MongoDB Connection Error: Failed to connect to server. Ensure MongoDB is running on {MONGO_URI} ---")
    print(f"Error details: {e}")
    client = None
    db = None

# --- Mock User Data ---
MOCK_USERS = {
    "Leni": {"id": "MCA-428", "name": "Leni E", "role": "student", "password": "123"},
    "teacher": {"id": "tch-99b3", "name": "Professor Jenifer Jose", "role": "teacher", "password": "admin"},
    "Soni": {"id": "MCA-112", "name": "Soni Priya", "role": "student", "password": "123"},
}

# --- Serve Homepage ---
@app.route('/')
def home():
    """Renders the single-page HTML application."""
    # NOTE: You must provide an index.html file in a 'templates' folder for this to work.
    return render_template('index.html')

# --- API Routes ---

@app.route('/api/authenticate', methods=['POST'])
def authenticate():
    """Authenticate student or teacher and return role info."""
    if not client:
        return jsonify({"success": False, "message": "Database connection failed."}), 503
            
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    user = MOCK_USERS.get(username)

    if user and user['password'] == password:
        print(f"User authenticated: {user['name']} ({user['role']})")
        return jsonify({
            "success": True,
            "data": {
                "id": user['id'],
                "name": user['name'],
                "role": user['role']
            }
        })
    else:
        return jsonify({"success": False, "message": "Invalid username or password."}), 401


@app.route('/api/track_attendance', methods=['POST'])
def track_attendance():
    """Receive and store attendance events from students."""
    if not client:
        return jsonify({"success": False, "message": "Database connection failed."}), 503
    try:
        data = request.get_json()
        if not all(k in data for k in ['user_id', 'event_type', 'metadata']):
            return jsonify({"success": False, "message": "Missing required fields (user_id, event_type, metadata)."}), 400

        data['server_timestamp'] = datetime.utcnow()
        result = events_collection.insert_one(data)
        print(f"Attendance event recorded for {data['user_id']} ({data['event_type']}). ID: {result.inserted_id}")
        return jsonify({"success": True, "message": "Event recorded."}), 200

    except Exception as e:
        print(f"Error tracking attendance: {e}")
        return jsonify({"success": False, "message": "Internal server error during event storage."}), 500

# --- NEW ENDPOINT: Explicitly records a logout event ---
@app.route('/api/logout_attendance', methods=['POST'])
def logout_attendance():
    """Explicitly record a logout event."""
    if not client:
        return jsonify({"success": False, "message": "Database connection failed."}), 503
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({"success": False, "message": "Missing user_id for logout event."}), 400

        logout_event = {
            'user_id': user_id,
            'event_type': 'logout', # Use a distinct event type
            'metadata': {
                # This specific status is the KEY for the teacher dashboard
                'client_status': 'logged_out', 
                'tab_focused': False,
                # Add a mock client timestamp for completeness
                'client_timestamp': datetime.utcnow().isoformat() 
            },
            'server_timestamp': datetime.utcnow()
        }
        
        result = events_collection.insert_one(logout_event)
        print(f"Explicit logout event recorded for {user_id}. ID: {result.inserted_id}")
        return jsonify({"success": True, "message": "Logout event recorded."}), 200

    except Exception as e:
        print(f"Error recording logout attendance: {e}")
        return jsonify({"success": False, "message": "Internal server error during logout event storage."}), 500
# ----------------------------------------------------

# --- NEW UTILITY ENDPOINT: Clear all events for testing ---
@app.route('/api/admin/clear_events', methods=['POST'])
def clear_all_events():
    """Clears all documents from the events collection. FOR TESTING ONLY."""
    if not client:
        return jsonify({"success": False, "message": "Database connection failed."}), 503
    try:
        # Warning: This is a destructive operation!
        result = events_collection.delete_many({})
        print(f"!!! CLEARED {result.deleted_count} old attendance records for a fresh start. !!!")
        return jsonify({"success": True, "message": f"Cleared {result.deleted_count} attendance records."}), 200
    except Exception as e:
        print(f"Error clearing attendance records: {e}")
        return jsonify({"success": False, "message": "Internal server error during database cleanup."}), 500
# --------------------------------------------------------


@app.route('/api/admin/students', methods=['GET'])
def get_student_data():
    """Fetch latest attendance status for all students (teacher dashboard)."""
    if not client:
        return jsonify({"success": False, "message": "Database connection failed."}), 503
    try:
        # Get all student IDs and names
        all_students = {user_data['id']: user_data['name'] for user_data in MOCK_USERS.values() if user_data['role'] == 'student'}
        student_ids = list(all_students.keys())
        
        # 1. Aggregation pipeline to find the LATEST event for each student
        pipeline = [
            # Only consider events for known students
            {'$match': {'user_id': {'$in': student_ids}}},
            # Sort by timestamp, newest first
            {'$sort': {'server_timestamp': -1}},
            # Group by user_id, taking the first (newest) event
            {'$group': {'_id': '$user_id', 'latest_event': {'$first': '$$ROOT'}}} 
        ]
        
        latest_events_agg = list(events_collection.aggregate(pipeline))
        
        # 2. Map latest events to a dictionary for quick lookup by ID
        latest_events_map = {}
        for event_group in latest_events_agg:
            user_id = event_group['_id']
            event = event_group['latest_event']
            
            # Convert ObjectId to string for JSON serialization
            if '_id' in event and isinstance(event['_id'], ObjectId):
                event['_id'] = str(event['_id']) 
            
            latest_events_map[user_id] = event
        
        status_report = []
        
        # Define the threshold for "fresh" activity
        timeout_threshold = datetime.utcnow() - timedelta(minutes=ACTIVITY_TIMEOUT_MINUTES)
        
        # *** MODIFICATION START: Define IST Offset ***
        IST_OFFSET = timedelta(hours=5, minutes=30)
        # *** MODIFICATION END ***
        
        # 3. Iterate over ALL students from MOCK_USERS to build the complete report
        for user_id, name in all_students.items():
            event = latest_events_map.get(user_id)
            
            # Default status for students with no events
            if not event:
                status_report.append({
                    "id": user_id,
                    "name": name,
                    "status": "Never Logged In",
                    "focus": "N/A",
                    "last_event": "None",
                    "timestamp": "N/A"
                })
                continue
            
            # Process event data
            client_status = event['metadata'].get('client_status', 'N/A').lower()
            tab_focused = event['metadata'].get('tab_focused', False)
            
            # --- Status determination logic with Staleness Check ---
            if client_status == 'logged_out':
                status = 'Logged Out'
                focus = 'N/A'
            # Check for timeout ONLY if the student is not explicitly logged out
            elif event['server_timestamp'] < timeout_threshold:
                # This student's last recorded active/idle event is stale
                status = f'Offline (Inactive)'
                focus = 'N/A'
            elif client_status == 'active':
                status = 'Active'
                focus = 'Focused' if tab_focused else 'Blurred'
            elif client_status == 'idle':
                status = 'Idle'
                focus = 'Focused' if tab_focused else 'Blurred'
            else:
                # Handles any unexpected status types found in the database
                status = f'Unknown ({client_status.title()})'
                focus = 'N/A'
            # --------------------------------------------------------
            
            # *** MODIFICATION START: Convert UTC to IST ***
            utc_timestamp = event['server_timestamp']
            ist_timestamp = utc_timestamp + IST_OFFSET 
            # *** MODIFICATION END ***
            
            status_report.append({
                "id": user_id,
                "name": name,
                "status": status,
                "focus": focus,
                "last_event": event['event_type'],
                # *** MODIFICATION START: Use IST Timestamp ***
                "timestamp": ist_timestamp.isoformat()
                # *** MODIFICATION END ***
            })
            
        print(f"Admin report generated for {len(status_report)} students.")
        return jsonify({"success": True, "data": status_report})
    
    except Exception as e:
        print(f"--- Error fetching student data: {e} ---")
        return jsonify({"success": False, "message": "Failed to retrieve student data."}), 500


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5000, use_reloader=False)