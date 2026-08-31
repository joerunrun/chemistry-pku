(function () {
  "use strict";

  // The original demo reports analytics and creates a debug GUI. The login
  // page keeps neither UI, so provide tiny no-op compatibility hooks.
  window.ga = window.ga || function () {};

  function controller(element) {
    return {
      __li: element,
      domElement: element,
      name: function () { return this; },
      onFinishChange: function () { return this; },
      listen: function () { return this; },
    };
  }

  function GUI() {
    this.domElement = document.createElement("div");
    this.domElement.hidden = true;
    document.body.appendChild(this.domElement);
  }

  GUI.prototype.add = function () {
    const element = document.createElement("div");
    this.domElement.appendChild(element);
    return controller(element);
  };
  GUI.prototype.addColor = GUI.prototype.add;
  GUI.prototype.addFolder = function () { return this; };
  GUI.prototype.close = function () {};

  window.dat = window.dat || {};
  window.dat.GUI = GUI;
}());
