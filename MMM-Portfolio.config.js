/*
 * Konfigurationsdatei für das MMM-Portfolio Modul
 * Diese Datei kann als Vorlage für die MagicMirror config.js dienen.
 */

module.exports = {
  module: "MMM-Portfolio",
  header: "Trading Portfolio",
  position: "top_right",
  config: {
    apiEndpoint: "http://localhost:8090/portfolio",
    updateInterval: 60 * 1000,
    currency: "EUR",
    showPositions: true,
    showPnL: true,
    showTimestamp: true,
  }
};
