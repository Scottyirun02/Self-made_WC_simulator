@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements-world-cup.txt
python -m streamlit run world_cup_app.py
