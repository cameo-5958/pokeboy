import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { loadDotEnv, parseDotEnv } from "./env.js";

test("parseDotEnv handles comments, blanks, quotes, and equals in values", () => {
  const parsed = parseDotEnv(
    [
      "# comment",
      "",
      "PLAIN=value",
      "SPACED = padded value ",
      'DQ="quoted value"',
      "SQ='single # not comment'",
      "URL=https://example.test/?a=b=c",
      "EMPTY=",
      "NOEQUALS",
      "=nokey",
    ].join("\n"),
  );
  assert.deepEqual(parsed, {
    PLAIN: "value",
    SPACED: "padded value",
    DQ: "quoted value",
    SQ: "single # not comment",
    URL: "https://example.test/?a=b=c",
    EMPTY: "",
  });
});

test("loadDotEnv fills process.env without overriding", () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "envtest-")), ".env");
  fs.writeFileSync(file, "ENV_TEST_FRESH=from-file\nENV_TEST_TAKEN=from-file\n");
  process.env.ENV_TEST_TAKEN = "from-env";
  delete process.env.ENV_TEST_FRESH;
  loadDotEnv(file);
  assert.equal(process.env.ENV_TEST_FRESH, "from-file");
  assert.equal(process.env.ENV_TEST_TAKEN, "from-env");
});

test("loadDotEnv tolerates a missing file", () => {
  loadDotEnv("/nonexistent/definitely/.env");
});
