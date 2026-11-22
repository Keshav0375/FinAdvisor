import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import App from "@/App";

describe("<App />", () => {
  beforeAll(() => {
    // jsdom has no layout engine, so it does not implement scrollIntoView.
    window.HTMLElement.prototype.scrollIntoView = () => {};
  });

  afterEach(() => cleanup());

  it("renders the header and the chat window (MVP header-user flow)", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "FinAdvisor" })).toBeTruthy();
    expect(screen.getByText("Compliance-Aware Wealth Advisor")).toBeTruthy();
    expect(screen.getByRole("main")).toBeTruthy();
  });
});
