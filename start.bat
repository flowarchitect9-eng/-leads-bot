@echo off
title High-Speed Telegram Lead Enricher Bot
echo ====================================================
echo Starting High-Speed Telegram Lead Enricher Bot...
echo ====================================================
echo Installing/Verifying dependencies...
pip install -r requirements.txt
cls
echo ====================================================
echo Bot is running! Waiting for CSV files on Telegram...
echo ====================================================
python bot.py
pause
