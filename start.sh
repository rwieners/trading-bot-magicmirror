#!/bin/bash
# Trading Bot + MagicMirror Startup Script
# Usage: ./start.sh [options]
#   No args: Start all (bot, web-ui, magic-mirror-api, logs)
#   --bot-only: Start only the bot
#   --ui-only: Start only the web UI
#   --mm-only: Start only the MagicMirror Portfolio API
#   --logs-only: Show live logs
#   --status: Show status of all services
#   --stop: Stop all services
#   --restart: Restart all services (or --restart bot/ui/monitor/mm)

set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_DIR/venv"
BOT_LOG="$PROJECT_DIR/logs/bot.log"
WEBUI_LOG="$PROJECT_DIR/logs/webui.log"
MONITOR_LOG="$PROJECT_DIR/logs/monitor.log"
MM_API_LOG="$PROJECT_DIR/logs/mm_api.log"
BOT_LOCK="$PROJECT_DIR/logs/bot.lock"
BOT_PID_FILE="$PROJECT_DIR/logs/bot.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Ensure venv exists
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}✗ Virtual environment not found!${NC}"
    echo "Please create it first:"
    echo "  python3 -m venv venv"
    exit 1
fi

activate_venv() {
    source "$VENV_DIR/bin/activate"
}

start_bot() {
    echo -e "${BLUE}▶ Starting Trading Bot...${NC}"
    activate_venv
    
    # Check if bot is already running via lock file
    if [ -f "$BOT_LOCK" ]; then
        OLD_PID=$(cat "$BOT_PID_FILE" 2>/dev/null || echo "")
        if [ -n "$OLD_PID" ] && ps -p "$OLD_PID" > /dev/null 2>&1; then
            echo -e "${YELLOW}⚠ Bot already running (PID: $OLD_PID)${NC}"
            return 0
        else
            echo -e "${YELLOW}⚠ Stale lock file found, cleaning up...${NC}"
            rm -f "$BOT_LOCK" "$BOT_PID_FILE"
        fi
    fi
    
    # Double-check via pgrep (matches Python path variations)
    if pgrep -f "broker\.bot" > /dev/null; then
        echo -e "${YELLOW}⚠ Bot process detected, killing stale instances...${NC}"
        pkill -f "broker\.bot" || true
        sleep 2
    fi
    
    # Load environment variables
    set -a
    source "$PROJECT_DIR/.env"
    set +a
    
    # Start bot in background with caffeinate to prevent Mac sleep
    nohup caffeinate -i python3 -m broker.bot > "$BOT_LOG" 2>&1 &
    BOT_PID=$!
    
    # Create lock file and save PID
    touch "$BOT_LOCK"
    echo "$BOT_PID" > "$BOT_PID_FILE"
    
    # Wait for bot to initialize
    sleep 3
    
    if ps -p "$BOT_PID" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Bot started (PID: $BOT_PID)${NC}"
        return 0
    else
        echo -e "${RED}✗ Failed to start bot${NC}"
        rm -f "$BOT_LOCK" "$BOT_PID_FILE"
        return 1
    fi
}

start_webui() {
    echo -e "${BLUE}▶ Starting Web UI...${NC}"
    activate_venv
    
    # Check if web UI is already running
    if pgrep -f "scripts/web_ui.py" > /dev/null; then
        echo -e "${YELLOW}⚠ Web UI already running${NC}"
        return 0
    fi
    
    # Start web UI in background
    nohup python3 "$PROJECT_DIR/scripts/web_ui.py" > "$WEBUI_LOG" 2>&1 &
    WEBUI_PID=$!
    
    # Wait for web UI to start
    sleep 2
    
    if pgrep -f "scripts/web_ui.py" > /dev/null; then
        echo -e "${GREEN}✓ Web UI started (PID: $WEBUI_PID)${NC}"
        echo -e "  ${BLUE}Dashboard:${NC} http://localhost:8000"
        return 0
    else
        echo -e "${RED}✗ Failed to start Web UI${NC}"
        return 1
    fi
}

start_mm_api() {
    echo -e "${BLUE}▶ Starting MagicMirror Portfolio API...${NC}"
    activate_venv
    
    # Check if MM API is already running
    if pgrep -f "portfolio_api.py" > /dev/null; then
        echo -e "${YELLOW}⚠ MagicMirror API already running${NC}"
        return 0
    fi
    
    # Load environment variables
    set -a
    source "$PROJECT_DIR/.env"
    set +a
    
    # Start MagicMirror Portfolio API in background
    nohup python3 "$PROJECT_DIR/portfolio_api.py" > "$MM_API_LOG" 2>&1 &
    MM_PID=$!
    
    # Wait for API to start
    sleep 2
    
    if pgrep -f "portfolio_api.py" > /dev/null; then
        echo -e "${GREEN}✓ MagicMirror API started (PID: $MM_PID)${NC}"
        echo -e "  ${BLUE}Portfolio API:${NC} http://localhost:8090/portfolio"
        return 0
    else
        echo -e "${RED}✗ Failed to start MagicMirror API${NC}"
        return 1
    fi
}

start_monitor() {
    echo -e "${BLUE}▶ Starting Bot Monitor...${NC}"
    activate_venv
    
    # Check if monitor is already running
    if pgrep -f "scripts/bot_monitor.py" > /dev/null; then
        echo -e "${YELLOW}⚠ Monitor already running${NC}"
        return 0
    fi
    
    # Start monitor in background
    nohup python3 "$PROJECT_DIR/scripts/bot_monitor.py" > "$MONITOR_LOG" 2>&1 &
    MONITOR_PID=$!
    
    # Wait for monitor to start
    sleep 1
    
    if pgrep -f "scripts/bot_monitor.py" > /dev/null; then
        echo -e "${GREEN}✓ Monitor started (PID: $MONITOR_PID)${NC}"
        return 0
    else
        echo -e "${RED}✗ Failed to start Monitor${NC}"
        return 1
    fi
}

show_logs() {
    echo -e "${BLUE}▶ Showing live bot logs (Ctrl+C to exit)${NC}"
    echo -e "${YELLOW}---${NC}\n"
    tail -f "$BOT_LOG"
}

show_status() {
    echo -e "\n${BLUE}════════════════════════════════════════${NC}"
    echo -e "${BLUE}Trading Bot Status${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}\n"
    
    # Check bot
    if [ -f "$BOT_PID_FILE" ] && ps -p "$(cat "$BOT_PID_FILE" 2>/dev/null)" > /dev/null 2>&1; then
        BOT_PID=$(cat "$BOT_PID_FILE")
        echo -e "${GREEN}✓ Bot:${NC}      RUNNING (PID: $BOT_PID)"
    elif pgrep -f "broker\.bot" > /dev/null; then
        BOT_PID=$(pgrep -f "broker\.bot" | head -1)
        echo -e "${YELLOW}⚠ Bot:${NC}      RUNNING (PID: $BOT_PID) - no lock file"
    else
        echo -e "${RED}✗ Bot:${NC}      STOPPED"
    fi
    
    # Check Web UI
    if pgrep -f "scripts/web_ui.py" > /dev/null; then
        WEBUI_PID=$(pgrep -f "scripts/web_ui.py")
        echo -e "${GREEN}✓ Web UI:${NC}   RUNNING (PID: $WEBUI_PID) - http://localhost:8000"
    else
        echo -e "${RED}✗ Web UI:${NC}   STOPPED"
    fi
    
    # Check Monitor
    if pgrep -f "scripts/bot_monitor.py" > /dev/null; then
        MONITOR_PID=$(pgrep -f "scripts/bot_monitor.py")
        echo -e "${GREEN}✓ Monitor:${NC}   RUNNING (PID: $MONITOR_PID)"
    else
        echo -e "${RED}✗ Monitor:${NC}   STOPPED"
    fi
    
    # Check MagicMirror API
    if pgrep -f "portfolio_api.py" > /dev/null; then
        MM_PID=$(pgrep -f "portfolio_api.py")
        echo -e "${GREEN}✓ MM API:${NC}    RUNNING (PID: $MM_PID) - http://localhost:8090/portfolio"
    else
        echo -e "${RED}✗ MM API:${NC}    STOPPED"
    fi
    
    # Check database
    if [ -f "$PROJECT_DIR/logs/trades.db" ]; then
        echo -e "${GREEN}✓ Database:${NC}  OK"
    else
        echo -e "${RED}✗ Database:${NC}  NOT FOUND"
    fi
    
    # Show recent log lines
    echo -e "\n${BLUE}Last 5 Bot Log Entries:${NC}"
    tail -5 "$BOT_LOG" | sed 's/^/  /'
    
    echo -e "\n${BLUE}════════════════════════════════════════${NC}\n"
}

stop_bot() {
    if pgrep -f "broker\.bot" > /dev/null; then
        pkill -f "broker\.bot"
        echo -e "${GREEN}✓ Bot stopped${NC}"
    else
        echo -e "${YELLOW}⚠ Bot was not running${NC}"
    fi
    rm -f "$BOT_LOCK" "$BOT_PID_FILE"
}

stop_webui() {
    if pgrep -f "scripts/web_ui.py" > /dev/null; then
        pkill -f "scripts/web_ui.py"
        echo -e "${GREEN}✓ Web UI stopped${NC}"
    else
        echo -e "${YELLOW}⚠ Web UI was not running${NC}"
    fi
}

stop_monitor() {
    if pgrep -f "scripts/bot_monitor.py" > /dev/null; then
        pkill -f "scripts/bot_monitor.py"
        echo -e "${GREEN}✓ Monitor stopped${NC}"
    else
        echo -e "${YELLOW}⚠ Monitor was not running${NC}"
    fi
}

stop_mm_api() {
    if pgrep -f "portfolio_api.py" > /dev/null; then
        pkill -f "portfolio_api.py"
        echo -e "${GREEN}✓ MagicMirror API stopped${NC}"
    else
        echo -e "${YELLOW}⚠ MagicMirror API was not running${NC}"
    fi
}

stop_services() {
    echo -e "${BLUE}▶ Stopping all services...${NC}"
    stop_bot
    stop_webui
    stop_monitor
    stop_mm_api
    echo -e "${GREEN}All services stopped${NC}"
}

restart_services() {
    local target="${1:-all}"
    
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo -e "${YELLOW}♻ Restarting ${target}...${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}\n"
    
    case "$target" in
        bot)
            stop_bot
            sleep 2
            start_bot
            ;;
        ui|webui)
            stop_webui
            sleep 1
            start_webui
            ;;
        monitor)
            stop_monitor
            sleep 1
            start_monitor
            ;;
        mm|magic-mirror)
            stop_mm_api
            sleep 1
            start_mm_api
            ;;
        all)
            stop_services
            echo -e "\n${BLUE}Waiting for processes to terminate...${NC}"
            sleep 3
            echo ""
            start_monitor
            start_bot
            start_webui
            start_mm_api
            ;;
        *)
            echo -e "${RED}Unknown restart target: $target${NC}"
            echo -e "Usage: ./start.sh --restart [bot|ui|monitor|mm|all]"
            return 1
            ;;
    esac
    
    echo -e "\n${GREEN}✓ Restart complete${NC}\n"
}

show_help() {
    cat << EOF
${BLUE}Trading Bot Startup Script${NC}

${YELLOW}Usage:${NC}
  ./start.sh [options]

${YELLOW}Options:${NC}
  (no args)     Start bot + web UI
  --bot-only    Start only the trading bot
  --ui-only     Start only the web dashboard
  --mm-only     Start only the MagicMirror Portfolio API
  --logs        Show live bot logs (tail -f)
  --trades      Show live trade viewer
  --status      Show status of all services
  --stop        Stop all running services
  --restart     Restart all services
  --restart bot Restart only the bot
  --restart ui  Restart only the web UI
  --restart mm  Restart only the MagicMirror API
  --help        Show this help message

${YELLOW}Examples:${NC}
  ./start.sh                    # Start everything (incl. MagicMirror API)
  ./start.sh --bot-only         # Start just the bot
  ./start.sh --mm-only          # Start just the MagicMirror API
  ./start.sh --logs             # Show live logs
  ./start.sh --status           # Check what's running
  ./start.sh --stop             # Stop everything
  ./start.sh --restart          # Restart everything
  ./start.sh --restart bot      # Restart just the bot
  ./start.sh --restart mm       # Restart just the MagicMirror API

${YELLOW}Web Dashboard:${NC}
  Once started, access at: http://localhost:8000

${YELLOW}MagicMirror Portfolio API:${NC}
  Once started, access at: http://localhost:8090/portfolio

${YELLOW}Database Viewer:${NC}
  ./scripts/view_trades.py      # One-time view
  ./scripts/watch_trades.sh     # Auto-refresh view (10s)

EOF
}

# Main logic
cd "$PROJECT_DIR"
mkdir -p logs

case "${1:-}" in
    --bot-only)
        start_bot
        ;;
    --ui-only)
        start_webui
        ;;
    --mm-only)
        start_mm_api
        ;;
    --logs)
        show_logs
        ;;
    --trades)
        activate_venv
        python3 scripts/view_trades.py
        ;;
    --status)
        show_status
        ;;
    --stop)
        stop_services
        ;;
    --restart)
        restart_services "${2:-all}"
        ;;
    --help|-h)
        show_help
        ;;
    "")
        echo -e "${BLUE}════════════════════════════════════════${NC}"
        echo -e "${GREEN}Trading Bot + MagicMirror Startup${NC}"
        echo -e "${BLUE}════════════════════════════════════════${NC}\n"
        
        start_monitor
        start_bot
        start_webui
        start_mm_api
        
        echo -e "\n${GREEN}════════════════════════════════════════${NC}"
        echo -e "${GREEN}✓ All services started successfully!${NC}"
        echo -e "${GREEN}════════════════════════════════════════${NC}\n"
        
        echo -e "${BLUE}Access Points:${NC}"
        echo -e "  📊 Web Dashboard:    ${YELLOW}http://localhost:8000${NC}"
        echo -e "  🪞 MagicMirror API:  ${YELLOW}http://localhost:8090/portfolio${NC}"
        echo -e "  📝 Bot Logs:         ${YELLOW}tail -f logs/bot.log${NC}"
        echo -e "  💰 Trades:           ${YELLOW}./scripts/view_trades.py${NC}"
        
        echo -e "\n${BLUE}Useful Commands:${NC}"
        echo -e "  ${YELLOW}./start.sh --logs${NC}       - Show live bot logs"
        echo -e "  ${YELLOW}./start.sh --trades${NC}     - Show completed trades"
        echo -e "  ${YELLOW}./start.sh --status${NC}     - Check service status"
        echo -e "  ${YELLOW}./start.sh --stop${NC}       - Stop all services"
        
        echo -e "\n${BLUE}════════════════════════════════════════${NC}\n"
        ;;
    *)
        echo -e "${RED}Unknown option: $1${NC}"
        show_help
        exit 1
        ;;
esac
