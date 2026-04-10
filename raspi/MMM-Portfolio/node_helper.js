var NodeHelper = require("node_helper");
var http = require("http");
var https = require("https");

module.exports = NodeHelper.create({
  socketNotificationReceived: function (notification, payload) {
    if (notification === "GET_PORTFOLIO") {
      this.fetchPortfolio(payload.url);
    }
  },

  fetchPortfolio: function (url) {
    var self = this;
    var client = url.startsWith("https") ? https : http;

    var req = client
      .get(url, { timeout: 30000 }, function (res) {
        var body = "";
        res.on("data", function (chunk) {
          body += chunk;
        });
        res.on("end", function () {
          try {
            var data = JSON.parse(body);
            self.sendSocketNotification("PORTFOLIO_DATA", data);
          } catch (e) {
            self.sendSocketNotification("PORTFOLIO_DATA", {
              error: "JSON parse error: " + e.message,
            });
          }
        });
      })
      .on("error", function (e) {
        self.sendSocketNotification("PORTFOLIO_DATA", {
          error: "API nicht erreichbar: " + e.message,
        });
      })
      .on("timeout", function () {
        req.destroy();
        self.sendSocketNotification("PORTFOLIO_DATA", {
          error: "API Timeout (30s)",
        });
      });
  },
});
