"use strict";

function createBootstrapController() {
  const viewListeners = new Set();
  return {
    phase: "BOOTSTRAPPING",
    target: null,
    onViewVisible(listener) {
      if (typeof listener !== "function") return () => {};
      viewListeners.add(listener);
      if (this.phase === "VIEW_VISIBLE") queueMicrotask(() => listener(this.target));
      return () => viewListeners.delete(listener);
    },
    setPhase(phase, target = null) {
      this.phase = phase;
      this.target = target;
      document.documentElement.dataset.storylingoBootstrap = phase;
      const state = document.querySelector("#app-bootstrap");
      if (!state) return;
      state.hidden = phase === "VIEW_VISIBLE";
      state.setAttribute("aria-busy", phase === "VIEW_VISIBLE" ? "false" : "true");
    },
    routeResolved(target) {
      if (this.phase === "BOOTSTRAPPING") this.setPhase("ROUTE_RESOLVED", target);
    },
    viewVisible(target) {
      if (this.phase === "BOOTSTRAPPING") this.setPhase("ROUTE_RESOLVED", target);
      this.setPhase("VIEW_VISIBLE", target);
      document.dispatchEvent(new CustomEvent("storylingo:view-visible", { detail: { target } }));
      viewListeners.forEach((listener) => listener(target));
    },
  };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  window.StoryLingoBootstrap = window.StoryLingoBootstrap || createBootstrapController();
  document.documentElement.dataset.storylingoBootstrap = window.StoryLingoBootstrap.phase;
}
