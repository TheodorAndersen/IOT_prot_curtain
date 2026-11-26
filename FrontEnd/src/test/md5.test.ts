import { describe, expect, it } from "vitest";
import { md5 } from "../utils/md5";

describe("md5", () => {
  it("matches backend seeded demo password hash", () => {
    expect(md5("curtain")).toBe("2805ba35a46fd703543fb61625cd9393");
    expect(md5("")).toBe("d41d8cd98f00b204e9800998ecf8427e");
  });
});
