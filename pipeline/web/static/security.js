/* One same-origin CSRF adapter for existing fetch calls and ordinary HTML forms. */
(() => {
  'use strict';
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, document.baseURI);
    const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    if (url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
      if (token) headers.set('X-CSRF-Token', token);
      options = { ...options, headers };
    }
    return originalFetch(input, options);
  };
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.method.toLowerCase() !== 'post') return;
    if (new URL(form.action, document.baseURI).origin !== location.origin || !token) return;
    let field = form.querySelector('input[name="csrf_token"]');
    if (!field) {
      field = document.createElement('input');
      field.type = 'hidden'; field.name = 'csrf_token'; form.appendChild(field);
    }
    field.value = token;
  }, true);
})();
