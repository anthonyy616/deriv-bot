In case you're confused, this is a personal trading bot of mine I have cloned from the deriv/weltrade project i delivered from a client. I'll use this to automate either between synthetics or crpto at a time of my choice. I can host the UI on vercel too, since it's only me that'll be using it I have to edit the .env file to my credentials and also update it on vercel if need be. 

To test, run mt5_bridge.py, depending on the broker you might not need a bridge but also run main.py too, as localhost:8000 (port) is there for testing through the FASTAPI server on server.py
