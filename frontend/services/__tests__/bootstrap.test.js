import { beforeEach, describe, expect, it } from "vitest";
import "../../services/bootstrap.js";

describe("StoryLingoBootstrap", () => {
  const bootstrap = window.StoryLingoBootstrap;

  beforeEach(() => {
    document.body.innerHTML = '<div id="app-bootstrap" role="status" aria-busy="true">載入中…</div>';
    bootstrap.setPhase("BOOTSTRAPPING");
  });

  it("route resolved 仍維持 neutral gate 且保持 busy", () => {
    bootstrap.routeResolved("platform");

    expect(bootstrap.phase).toBe("ROUTE_RESOLVED");
    expect(bootstrap.target).toBe("platform");
    expect(document.querySelector("#app-bootstrap").hidden).toBe(false);
    expect(document.querySelector("#app-bootstrap").getAttribute("aria-busy")).toBe("true");
    expect(document.documentElement.dataset.storylingoBootstrap).toBe("ROUTE_RESOLVED");
  });

  it("view visible 後才隱藏 neutral gate", () => {
    bootstrap.viewVisible("platform");

    expect(bootstrap.phase).toBe("VIEW_VISIBLE");
    expect(bootstrap.target).toBe("platform");
    expect(document.querySelector("#app-bootstrap").hidden).toBe(true);
    expect(document.querySelector("#app-bootstrap").getAttribute("aria-busy")).toBe("false");
  });
});
