#!/usr/bin/env python3
"""
Mock Data Generator for Docker Development
Creates sample drone position data for local development/testing.
"""

import sqlite3
import random
import os
from datetime import datetime, timedelta

DB_PATH = "/app/data/mock_collector.db"

def create_mock_database():
    """Create mock collector database with sample data"""
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    # Remove existing database to regenerate fresh data
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    conn = sqlite3.connect(DB_PATH)
    
    # Create table matching the expected schema
    conn.execute("""
        CREATE TABLE IF NOT EXISTS remoteid(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            mac_address TEXT,
            uas_id TEXT,
            session_id TEXT,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            operator_id TEXT,
            operator_latitude REAL,
            operator_longitude REAL
        )
    """)
    
    # Sample drone IDs
    drone_ids = [
        "DJI-001-TEST",
        "DJI-002-TEST", 
        "SKYDIO-001-TEST",
        "AUTEL-001-TEST",
        "PARROT-001-TEST",
    ]
    
    # Base location (San Francisco area - matching docker config)
    base_lat = 37.7749
    base_lon = -122.4194
    
    # Generate 24 hours of data
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=24)
    
    current_time = start_time
    while current_time < end_time:
        for drone_id in drone_ids:
            # Generate random movement for each drone
            # Each drone moves differently
            drone_idx = drone_ids.index(drone_id)
            
            # Offset each drone's path
            lat_offset = (drone_idx - 2) * 0.01
            lon_offset = (drone_idx - 2) * 0.01
            
            # Add some random movement
            lat_noise = random.uniform(-0.001, 0.001)
            lon_noise = random.uniform(-0.001, 0.001)
            
            # Simulate altitude changes (100-400 feet)
            altitude = 100 + (drone_idx * 50) + random.uniform(-20, 20)
            
            # Operator is near the flight path
            op_lat = base_lat + lat_offset + random.uniform(-0.005, 0.005)
            op_lon = base_lon + lon_offset + random.uniform(-0.005, 0.005)
            
            conn.execute("""
                INSERT INTO remoteid 
                (timestamp, mac_address, uas_id, session_id, latitude, longitude, altitude,
                 operator_id, operator_latitude, operator_longitude)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                current_time,
                f"AA:BB:CC:DD:EE:{drone_idx:02X}",
                drone_id,
                f"SESSION-{drone_idx}-{current_time.strftime('%Y%m%d')}",
                base_lat + lat_offset + lat_noise + (current_time.hour * 0.0001),
                base_lon + lon_offset + lon_noise + (current_time.hour * 0.0001),
                altitude,
                f"OP-{drone_id}",
                op_lat,
                op_lon
            ))
        
        # Increment time (every 2 minutes)
        current_time += timedelta(minutes=2)
    
    conn.commit()
    
    # Count records
    count = conn.execute("SELECT COUNT(*) FROM remoteid").fetchone()[0]
    print(f"Created mock database with {count} records at {DB_PATH}")
    
    conn.close()

if __name__ == "__main__":
    print("Generating mock data for Docker development...")
    create_mock_database()
    print("Done! Mock data ready for sync.")
