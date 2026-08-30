import { describe, expect, it } from "vitest";
import { translateText } from "./i18n";

describe("interface localization", () => {
  it("translates fixed interface labels into English", () => {
    expect(translateText("概览", "en")).toBe("Overview");
    expect(translateText("项目 Skills 工作区", "en")).toBe("Project Skills workspace");
    expect(translateText("营销增长与社媒", "en")).toBe("Marketing, growth, and social media");
  });

  it("preserves the Chinese interface when Chinese is selected", () => {
    expect(translateText("添加 Git 来源", "zh-CN")).toBe("添加 Git 来源");
  });

  it("does not rewrite source-authored or user-entered content", () => {
    expect(translateText("这是项目自己的中文需求与说明", "en")).toBe("这是项目自己的中文需求与说明");
  });

  it("preserves surrounding whitespace on localized fragments", () => {
    expect(translateText("  已同步  ", "en")).toBe("  In sync  ");
  });
});
