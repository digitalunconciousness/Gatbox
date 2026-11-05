// The one script the archive's pages use: sending a PDF. A form with data-upload names where it goes; the file is sent as it
// is in one request (the tunnel takes 100 MB a request; the pages cap a PDF at 90 MB), and the page reloads to show it.
// Nothing is kept in the browser.
(function () {
  "use strict";
  var MAX = 90 * 1024 * 1024;
  document.querySelectorAll("form[data-upload]").forEach(function (form) {
    var out = form.querySelector("[data-status]");
    function say(text, bad) {
      out.textContent = text;
      out.className = bad ? "bad" : "dim";
    }
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var file = form.elements.file.files[0];
      if (!file) { say("Choose a PDF first.", true); return; }
      if (file.size > MAX) { say("That file is " + Math.round(file.size / 1048576) + " MB; the limit here is 90 MB.", true); return; }
      var title = form.elements.title.value.trim() || file.name.replace(/\.pdf$/i, "");
      var query = "?title=" + encodeURIComponent(title) + "&kind=" + encodeURIComponent(form.elements.kind.value);
      var button = form.querySelector("button");
      button.disabled = true;
      say("Sending " + file.name + "…");
      fetch(form.dataset.upload + query, {
        method: "POST",
        headers: { "X-CSRF": form.dataset.csrf, "Content-Type": "application/pdf", "Accept": "application/json" },
        body: file,
        credentials: "same-origin"
      }).then(function (r) {
        return r.json().then(function (j) { return { ok: r.ok, body: j }; });
      }).then(function (r) {
        if (r.ok) { window.location = form.dataset.done; return; }
        say(r.body.error || "The archive refused that file.", true);
        button.disabled = false;
      }).catch(function () {
        say("The upload didn't get through. Check the connection and try again.", true);
        button.disabled = false;
      });
    });
  });
})();
