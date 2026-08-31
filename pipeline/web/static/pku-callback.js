(function () {
  const hash = window.location.hash || "";
  const marker = "#/iaaa_login";
  const queryStart = hash.indexOf("?");
  if (!hash.startsWith(marker) || queryStart < 0) return;

  const params = new URLSearchParams(hash.slice(queryStart + 1));
  const token = params.get("token");
  if (!token) return;

  // Remove the short-lived credential from the address bar before sending it.
  window.history.replaceState({}, document.title, window.location.pathname + window.location.search);

  fetch("/auth/pku/callback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: token }),
  })
    .then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) throw new Error(data.error || "北大认证回调失败");
        if (!data.next_url) throw new Error("服务端没有返回下一步地址");
        window.location.replace(data.next_url);
      });
    })
    .catch(function (error) {
      document.body.innerHTML = "<main style='padding:32px;font-family:system-ui'>" +
        "<h1>北大认证回调失败</h1><p>" + error.message + "</p></main>";
    });
})();
