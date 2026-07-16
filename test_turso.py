import libsql

# Put your Turso credentials here temporarily for testing
DATABASE_URL = "libsql://dlmlassessmentdb-digitalleanabler.aws-ap-northeast-1.turso.io"
AUTH_TOKEN = "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3ODQyMjA0MzgsImlkIjoiMDE5ZjZiZDEtZTYwMS03ZDFhLWFiM2QtYjVlMDczZTVlYjBjIiwia2lkIjoieUpGZnFnY2c3eXNERENrVjl0Q1NGWXBaNjBXQThxM3gtQnUxR0FFc0lOUSIsInJpZCI6IjkyM2FmOGNhLWZkNjQtNDQzMS1iN2ZkLTQ3YWM4ZDAxOTgwYSJ9.ShINXlAMVYVVI9uMJY7E_8Jj9x0TWD0ebY0BWswaudmC_rnMqUWAFqg0Ons3txtq1joyDDDfhBwQXkOuDX1MBw"


print("Connecting to:")
print(DATABASE_URL)

try:
    conn = libsql.connect(
        DATABASE_URL,
        auth_token=AUTH_TOKEN
    )

    print("Connection created successfully")

    # Simple test query
    result = conn.execute("SELECT 1")

    row = result.fetchone()

    print("Query result:")
    print(row)

    # List tables
    print("\nTables:")

    result = conn.execute(
        """
        SELECT name 
        FROM sqlite_master 
        WHERE type='table'
        ORDER BY name
        """
    )

    for row in result.fetchall():
        print(row[0])

    print("\nTurso connection test PASSED")

except Exception as e:
    print("\nTurso connection test FAILED")
    print(type(e).__name__)
    print(e)