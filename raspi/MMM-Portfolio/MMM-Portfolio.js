Module.register("MMM-Portfolio", {
  defaults: {
    apiEndpoint: "http://localhost:8090/portfolio",
    updateInterval: 60 * 1000,
    currency: "EUR",
    showPositions: true,
    showPnL: true,
    showTimestamp: true,
  },

  start: function () {
    this.portfolioData = null;
    this.loaded = false;
    this.errorMessage = null;
    this.getData();
    this.scheduleUpdate();
  },

  getStyles: function () {
    return ["MMM-Portfolio.css"];
  },

  scheduleUpdate: function () {
    setInterval(() => {
      this.getData();
    }, this.config.updateInterval);
  },

  getData: function () {
    this.sendSocketNotification("GET_PORTFOLIO", {
      url: this.config.apiEndpoint,
    });
  },

  socketNotificationReceived: function (notification, payload) {
    if (notification === "PORTFOLIO_DATA") {
      if (payload && payload.error && !payload.portfolio_value) {
        this.errorMessage = payload.error;
      } else {
        this.portfolioData = payload;
        this.errorMessage = null;
      }
      this.loaded = true;
      this.updateDom();
    }
  },

  getDom: function () {
    var wrapper = document.createElement("div");
    wrapper.className = "mmm-portfolio";

    if (!this.loaded) {
      wrapper.innerHTML = "Lade Portfolio…";
      wrapper.className += " dimmed light small";
      return wrapper;
    }

    if (this.errorMessage) {
      wrapper.innerHTML = this.errorMessage;
      wrapper.className += " dimmed light small";
      return wrapper;
    }

    if (!this.portfolioData) {
      wrapper.innerHTML = "Keine Daten verfügbar";
      wrapper.className += " dimmed light small";
      return wrapper;
    }

    if (this.portfolioData.error) {
      wrapper.innerHTML = this.portfolioData.error;
      wrapper.className += " dimmed light small";
      return wrapper;
    }

    var data = this.portfolioData;

    // P&L info
    if (this.config.showPnL) {
      var pnlDiv = document.createElement("div");
      pnlDiv.className = "portfolio-pnl";

      if (data.unrealized_pnl !== undefined) {
        var unrealized = document.createElement("span");
        unrealized.className =
          "pnl-value " + (data.unrealized_pnl >= 0 ? "positive" : "negative");
        unrealized.innerHTML =
          "Offen: " + this.formatPnl(data.unrealized_pnl) + " €";
        pnlDiv.appendChild(unrealized);
      }

      if (data.realized_pnl !== undefined) {
        if (data.unrealized_pnl !== undefined) {
          var sep = document.createElement("span");
          sep.className = "pnl-separator";
          sep.innerHTML = " | ";
          pnlDiv.appendChild(sep);
        }
        var realized = document.createElement("span");
        realized.className =
          "pnl-value " + (data.realized_pnl >= 0 ? "positive" : "negative");
        realized.innerHTML =
          "Realisiert: " + this.formatPnl(data.realized_pnl) + " €";
        pnlDiv.appendChild(realized);
      }

      wrapper.appendChild(pnlDiv);
    }

    // Positions count
    if (this.config.showPositions && data.positions !== undefined) {
      var posDiv = document.createElement("div");
      posDiv.className = "portfolio-positions";
      posDiv.innerHTML = data.positions + " Positionen";
      wrapper.appendChild(posDiv);
    }

    // Timestamp
    if (this.config.showTimestamp && data.timestamp) {
      var tsDiv = document.createElement("div");
      tsDiv.className = "portfolio-timestamp";
      var date = new Date(data.timestamp);
      tsDiv.innerHTML =
        "Letztes Update: " +
        date.toLocaleTimeString("de-DE", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
      wrapper.appendChild(tsDiv);
    }

    // Iteration
    if (data.iteration !== null && data.iteration !== undefined) {
      var iterDiv = document.createElement("div");
      iterDiv.className = "portfolio-iteration";
      iterDiv.innerHTML = "Iteration: " + data.iteration;
      wrapper.appendChild(iterDiv);
    }

    // Trading mode & scalping profit
    if (data.trading_mode) {
      var modeDiv = document.createElement("div");
      modeDiv.className = "portfolio-mode";
      var modeText = data.trading_mode.charAt(0).toUpperCase() + data.trading_mode.slice(1);
      if (data.scalping_profit_abs !== null && data.scalping_profit_abs !== undefined) {
        modeText += " · Target: " + this.formatNumber(data.scalping_profit_abs) + " €";
      }
      modeDiv.innerHTML = modeText;
      wrapper.appendChild(modeDiv);
    }

    return wrapper;
  },

  formatNumber: function (num) {
    if (num === null || num === undefined) return "–";
    return Number(num).toLocaleString("de-DE", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  },

  formatPnl: function (num) {
    if (num === null || num === undefined) return "–";
    var prefix = num >= 0 ? "+" : "";
    return (
      prefix +
      Number(num).toLocaleString("de-DE", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    );
  },
});
