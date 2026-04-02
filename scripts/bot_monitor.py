#!/usr/bin/env python3
"""
Bot Monitor Service - Überwacht den Trading Bot und startet ihn neu falls nötig
Läuft im Hintergrund und prüft alle 30 Sekunden ob der Bot noch aktiv ist
"""
import subprocess
import time
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

# Konfiguration
BOT_CHECK_INTERVAL = 30  # Sekunden zwischen Überprüfungen
BOT_PROCESS_NAME = "python3 -m broker.bot"
BOT_LOG_FILE = "/Users/rene/dev/Broker/logs/bot.log"
MONITOR_LOG_FILE = "/Users/rene/dev/Broker/logs/monitor.log"
PROJECT_DIR = "/Users/rene/dev/Broker"

def log_message(msg):
    """Schreib Nachricht in Log-Datei"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    print(log_msg)
    
    with open(MONITOR_LOG_FILE, 'a') as f:
        f.write(log_msg + "\n")

def is_bot_running():
    """Überprüfe ob Bot-Prozess läuft"""
    try:
        result = subprocess.run(
            ["pgrep", "-f", BOT_PROCESS_NAME],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception as e:
        log_message(f"ERROR checking bot process: {e}")
        return False

def start_bot():
    """Starte den Trading Bot"""
    try:
        log_message("🤖 Starting Trading Bot...")
        os.chdir(PROJECT_DIR)
        
        # Starte Bot im Hintergrund
        with open(BOT_LOG_FILE, 'a') as log:
            process = subprocess.Popen(
                ["bash", "-c", "source venv/bin/activate && python3 -m broker.bot"],
                stdout=log,
                stderr=log,
                preexec_fn=os.setsid
            )
        
        time.sleep(5)  # Warte bis Bot initialisiert
        
        if is_bot_running():
            log_message("✅ Bot started successfully (PID: process group started)")
            return True
        else:
            log_message("❌ Bot failed to start")
            return False
            
    except Exception as e:
        log_message(f"❌ ERROR starting bot: {e}")
        return False

def monitor_bot():
    """Hauptschleife - Überwache Bot kontinuierlich"""
    log_message("=" * 60)
    log_message("🔍 Bot Monitor Service started")
    log_message(f"Check interval: {BOT_CHECK_INTERVAL} seconds")
    log_message("=" * 60)
    
    bot_was_running = False
    
    try:
        while True:
            is_running = is_bot_running()
            
            if is_running:
                if not bot_was_running:
                    log_message("✅ Bot is running (recovered or started)")
                    bot_was_running = True
                # Stille Überprüfung wenn läuft
            else:
                log_message("⚠️  Bot process not found - RESTARTING...")
                bot_was_running = False
                start_bot()
            
            time.sleep(BOT_CHECK_INTERVAL)
            
    except KeyboardInterrupt:
        log_message("🛑 Monitor service stopped by user")
    except Exception as e:
        log_message(f"❌ Monitor error: {e}")
    finally:
        log_message("Monitor service terminated")

def cleanup(signum, frame):
    """Handle Signals gracefully"""
    log_message("🛑 Received shutdown signal")
    sys.exit(0)

if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)
    
    # Starte Monitoring
    monitor_bot()
