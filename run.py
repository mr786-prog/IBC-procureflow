import uvicorn
import os
import sys

if __name__ == "__main__":
    print("================================================================")
    print("  Starting Industrial Bolt Centre (IBC) — ProcureFlow Web Server")
    print("  Host: http://127.0.0.1:8000")
    print("  Manager Console:  http://127.0.0.1:8000/app/manager/")
    print("  Employee Portal:  http://127.0.0.1:8000/app/employee/")
    print("  Login:            http://127.0.0.1:8000/login")
    print("================================================================")
    uvicorn.run("procureflow.app.main:app", host="127.0.0.1", port=8000, reload=True)
