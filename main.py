import uvicorn
import os
import sys

if __name__ == "__main__":
    # Ensure the root directory is in the python path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    
    print("🚀 Starting Bot Server...")
    print("-----------------------------------")
    print("ENTRY POINT: api/server.py")
    print("ENGINE:      Polling Mode (Active)")
    print("UI:          http://localhost:8000")
    print("-----------------------------------")

    # Run the API Server
    # This automatically starts the 'TradingEngine' via the @app.on_event("startup") 
    # defined in api/server.py
    try:
        uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=True)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")