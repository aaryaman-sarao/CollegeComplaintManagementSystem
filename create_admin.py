"""
create_admin.py
---------------
Run once to create an admin account in the database.
Usage: python create_admin.py
"""
import pymysql
import pymysql.cursors
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv
import os

load_dotenv()

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST", "localhost"),
    port=int(os.getenv("MYSQL_PORT", 3306)),
    user=os.getenv("MYSQL_USER", "root"),
    password=os.getenv("MYSQL_PASSWORD", ""),
    database=os.getenv("MYSQL_DB", "complaint_db"),
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

with conn.cursor() as cur:
    # Check existing admins
    cur.execute("SELECT user_id, full_name, email FROM users WHERE role = 'admin'")
    existing = cur.fetchall()

    if existing:
        print("Admin account(s) already exist:")
        for u in existing:
            print(f"  ID={u['user_id']}  Name={u['full_name']}  Email={u['email']}")
        print("\nNo new account created.")
    else:
        pw_hash = generate_password_hash("Admin@1234")
        cur.execute(
            """
            INSERT INTO users (full_name, email, password_hash, role)
            VALUES (%s, %s, %s, 'admin')
            """,
            ("Admin", "admin@college.edu", pw_hash),
        )
        conn.commit()
        print("=" * 45)
        print("  Admin account created successfully!")
        print("=" * 45)
        print("  Email    :  admin@college.edu")
        print("  Password :  Admin@1234")
        print("  Login at :  http://127.0.0.1:5000/auth/login")
        print("=" * 45)

conn.close()
