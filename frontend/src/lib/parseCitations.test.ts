import { describe, expect, it } from "vitest";

import { parseCitations } from "./parseCitations";

describe("parseCitations", () => {
  it("returns an empty list for an empty string", () => {
    expect(parseCitations("")).toEqual([]);
  });

  it("returns one text segment for plain text", () => {
    expect(parseCitations("Revenue grew last year.")).toEqual([
      { type: "text", content: "Revenue grew last year." },
    ]);
  });

  it("splits text around one citation marker", () => {
    expect(parseCitations("Revenue grew [1] last year.")).toEqual([
      { type: "text", content: "Revenue grew " },
      { type: "citation", index: 1 },
      { type: "text", content: " last year." },
    ]);
  });

  it("handles a marker at the start and at the end", () => {
    expect(parseCitations("[2]middle[10]")).toEqual([
      { type: "citation", index: 2 },
      { type: "text", content: "middle" },
      { type: "citation", index: 10 },
    ]);
  });

  it("parses multiple and adjacent markers in order", () => {
    expect(parseCitations("A [1] B [2][3] C")).toEqual([
      { type: "text", content: "A " },
      { type: "citation", index: 1 },
      { type: "text", content: " B " },
      { type: "citation", index: 2 },
      { type: "citation", index: 3 },
      { type: "text", content: " C" },
    ]);
  });

  it.each(["See [abc] here", "See [1 here", "See [] here", "See [1a] here", "See [-1] here"])(
    "keeps a malformed marker as text: %s",
    (input) => {
      expect(parseCitations(input)).toEqual([{ type: "text", content: input }]);
    },
  );

  it("does not carry state between calls", () => {
    const first = parseCitations("x [1]");
    const second = parseCitations("x [1]");
    expect(second).toEqual(first);
  });
});
