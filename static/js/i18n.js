(function () {
  "use strict";
  const KEY = "pifan.lang";
  const LANGUAGES = {
    en: "English",
    tr: "Türkçe",
    de: "Deutsch",
    fr: "Français",
    es: "Español",
    it: "Italiano",
    pt: "Português",
    ru: "Русский",
    zh: "中文",
    ja: "日本語",
  };
  const base = new URL("../i18n/", document.currentScript.src);
  const cache = {};
  const listeners = [];
  let lang = "en";
  let dict = {};
  let fallback = {};

  function detect() {
    try {
      const saved = localStorage.getItem(KEY);
      if (saved && LANGUAGES[saved]) return saved;
    } catch (e) { /* private mode */ }
    for (const l of navigator.languages || [navigator.language || "en"]) {
      const code = String(l).toLowerCase().split("-")[0];
      if (LANGUAGES[code]) return code;
    }
    return "en";
  }

  async function load(code) {
    if (!cache[code]) {
      const res = await fetch(new URL(code + ".json", base));
      if (!res.ok) throw new Error("i18n " + code + ": " + res.status);
      cache[code] = await res.json();
    }
    return cache[code];
  }

  function t(key, params) {
    let s = dict[key] ?? fallback[key] ?? key;
    if (params) {
      s = s.replace(/\{(\w+)\}/g, (m, name) => (params[name] !== undefined ? params[name] : m));
    }
    return s;
  }

  function apply(root) {
    root = root || document;
    root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle); });
    root.querySelectorAll("[data-i18n-aria]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.i18nAria)); });
    root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    document.title = t("app.title");
  }

  async function setLang(code) {
    if (!LANGUAGES[code]) code = "en";
    try {
      fallback = await load("en");
      dict = code === "en" ? fallback : await load(code);
    } catch (e) {
      console.error(e);
      code = "en";
      dict = fallback;
    }
    lang = code;
    document.documentElement.lang = code;
    try { localStorage.setItem(KEY, code); } catch (e) { /* private mode */ }
    apply();
    listeners.forEach((fn) => fn(code));
  }

  function bindSelect(select) {
    select.innerHTML = "";
    for (const [code, name] of Object.entries(LANGUAGES)) {
      const opt = document.createElement("option");
      opt.value = code;
      opt.textContent = name;
      select.appendChild(opt);
    }
    select.value = lang;
    select.addEventListener("change", () => setLang(select.value));
    listeners.push((code) => { select.value = code; });
  }

  window.I18n = {
    t,
    apply,
    setLang,
    bindSelect,
    init: () => setLang(detect()),
    onChange: (fn) => listeners.push(fn),
    get lang() { return lang; },
  };
})();
