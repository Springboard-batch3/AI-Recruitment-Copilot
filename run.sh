#!/usr/bin/env bash
cd "$(dirname "$0")"
./backend/.venv/bin/streamlit run dashboard/app.py
