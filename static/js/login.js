(function () {
  "use strict";
  const { t } = I18n;
  const form = document.getElementById("login-form");
  const input = document.getElementById("password");
  const errorEl = document.getElementById("login-error");

  I18n.bindSelect(document.getElementById("lang-select"));
  I18n.init();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errorEl.hidden = true;
    try {
      const res = await fetch("api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: input.value }),
      });
      if (res.ok) {
        location.replace("./");
        return;
      }
      const data = await res.json().catch(() => ({}));
      errorEl.textContent = t("errors." + (data.error || "invalid_password"), data.params);
    } catch (err) {
      errorEl.textContent = t("errors.network");
    }
    errorEl.hidden = false;
    input.select();
  });
})();
