(function () {
  "use strict";

  const config = window.CHEM_LOGIN_CONFIG || {};
  const callbackPath = config.callbackPath || "/login";
  document.getElementById("redirectUrl").value = `${window.location.origin}${callbackPath}`;

  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

  const script = document.createElement("script");
  script.src = config.fluidScript;
  script.async = true;
  document.body.appendChild(script);
}());
