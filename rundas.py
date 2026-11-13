import os
import time
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, HTTPServer
import subprocess
import socket

# SETTINGS
MAP_HTML = "digitalTwinSite.html"
MAP_PORT = 8080

# Streamlit apps
PREDICTION_APP = "wasteprediction.py"
#SIMULATION_APP = "city_simulation_ors.py"
SIMULATION_APP = "updatedsimu.py"

PREDICTION_PORT = 8501
SIMULATION_PORT = 8502 


# HELPERS
def port_in_use(port):
    """Check if port is already used"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) == 0

def start_streamlit(app_file, port):
    """Start a Streamlit app on a specific port"""
    try:
        print(f" Starting Streamlit app: {app_file} on port {port}")
        cmd = f"streamlit run {app_file} --server.port {port} --server.headless true"
        subprocess.Popen(cmd, shell=True)
    except Exception as e:
        print(f" Failed to start {app_file}: {e}")

def start_html_server():
    """Start a simple local HTTP server for your map"""
    try:
        handler = SimpleHTTPRequestHandler
        httpd = HTTPServer(("", MAP_PORT), handler)
        print(f" Serving {MAP_HTML} on http://localhost:{MAP_PORT}/{MAP_HTML}")
        httpd.serve_forever()
    except OSError as e:
        print(f" Port {MAP_PORT} in use, change MAP_PORT if needed.")
        raise e


if __name__ == "__main__":
    for port in [MAP_PORT, PREDICTION_PORT, SIMULATION_PORT]:
        if port_in_use(port):
            print(f" Port {port} already in use. Please stop that process or change ports.")
            exit(1)

    # Start both Streamlit apps (forecast + simulation)
    threading.Thread(target=start_streamlit, args=(PREDICTION_APP, PREDICTION_PORT), daemon=True).start()
    threading.Thread(target=start_streamlit, args=(SIMULATION_APP, SIMULATION_PORT), daemon=True).start()

    time.sleep(5)

    threading.Thread(target=start_html_server, daemon=True).start()
    time.sleep(2)

    # Open HTML dashboard
    map_url = f"http://localhost:{MAP_PORT}/{MAP_HTML}"
    print(f" Opening dashboard at {map_url}")
    webbrowser.open(map_url)

    print("\n Both Streamlit apps are now running:")
    print(f"   Prediction → http://localhost:{PREDICTION_PORT}")
    print(f"   Simulation → http://localhost:{SIMULATION_PORT}\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n Shutting down servers.")
