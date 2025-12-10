import uvicorn
import os
import sys

# Import the app object directly to ensure it loads correctly
# If your folder structure is different, you might need to adjust this import
# Assuming your server.py is inside a folder named 'api'
from api.server import app

if __name__ == "__main__":
    # Ensure the root directory is in the python path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    
    print("🚀 Starting Bot Server...")
    print("-----------------------------------")
    print("ENTRY POINT: api/server.py")
    print("ENGINE:      Polling Mode (Active)")
    print("URL:         http://45.144.242.97:800") 
    print("-----------------------------------")

    try:
        # UPDATED: host="0.0.0.0" opens it to the web
        # UPDATED: port=800 is the port we opened in the firewall
        uvicorn.run(app, host="0.0.0.0", port=800)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")