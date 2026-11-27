import { afterEach, describe, expect, it, vi } from "vitest";
import { newRequestId } from "./ids";

describe("newRequestId", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is 32 hex characters", () => {
    expect(newRequestId()).toMatch(/^[0-9a-f]{32}$/);
  });

  it("works without crypto.randomUUID (plain http on a non-localhost address)", () => {
    // A crypto object as an insecure context sees it: getRandomValues only.
    const real = globalThis.crypto;
    vi.stubGlobal("crypto", { getRandomValues: real.getRandomValues.bind(real) });
    const id = newRequestId();
    expect(id).toMatch(/^[0-9a-f]{32}$/);
    expect(newRequestId()).not.toBe(id);
  });
});
