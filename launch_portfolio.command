#!/bin/bash
# IPSA Portfolio Manager — Double-click launcher
# Opens the app locally at http://localhost:8501

cd "$(dirname "$0")"

echo "Starting IPSA Portfolio Manager..."
python3 -m streamlit run app.py --server.address localhost --server.port 8501 &
APP_PID=$!

sleep 3
open http://localhost:8501

echo "App running (PID $APP_PID). Close this window to stop the server."
wait $APP_PID
