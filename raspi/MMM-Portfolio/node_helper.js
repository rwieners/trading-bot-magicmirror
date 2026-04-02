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

    client
      .get(url, function (res) {
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
              error: "JSON parse error",
            });
          }
        });
      })
      .on("error", function (e) {
        self.sendSocketNotification("PORTFOLIO_DATA", {
          error: "API nicht erreichbar",
        });
      });
  },
});
