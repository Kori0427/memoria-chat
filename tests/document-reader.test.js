const { extractText, MAX_FILE_CHARS, _deps } = require("../lib/document-reader");

describe("lib/document-reader", () => {
  // 保存原始依赖，每次测试后恢复
  const origPDFParse = _deps.PDFParse;
  const origMammoth = _deps.mammothExtractRawText;

  afterEach(() => {
    _deps.PDFParse = origPDFParse;
    _deps.mammothExtractRawText = origMammoth;
  });

  // ===== 纯文本类型 =====
  describe("TXT / MD / CSV / JSON extraction", () => {
    it("extracts plain text from .txt buffer", async () => {
      const buf = Buffer.from("Hello World");
      const result = await extractText(buf, ".txt");
      expect(result.text).toBe("Hello World");
      expect(result.pages).toBeNull();
      expect(result.originalChars).toBe(11);
      expect(result.truncated).toBe(false);
    });

    it("extracts text from .md buffer", async () => {
      const buf = Buffer.from("# Title\n\nParagraph");
      const result = await extractText(buf, ".md");
      expect(result.text).toBe("# Title\n\nParagraph");
    });

    it("extracts text from .csv buffer", async () => {
      const buf = Buffer.from("name,age\nAlice,30");
      const result = await extractText(buf, ".csv");
      expect(result.text).toBe("name,age\nAlice,30");
    });

    it("extracts text from .json buffer", async () => {
      const buf = Buffer.from('{"key":"value"}');
      const result = await extractText(buf, ".json");
      expect(result.text).toBe('{"key":"value"}');
    });

    it("strips UTF-8 BOM", async () => {
      const buf = Buffer.from("\ufeffHello BOM");
      const result = await extractText(buf, ".txt");
      expect(result.text).toBe("Hello BOM");
    });
  });

  // ===== 清洗逻辑 =====
  describe("text cleanup", () => {
    it("merges consecutive spaces", async () => {
      const buf = Buffer.from("word1   word2     word3");
      const result = await extractText(buf, ".txt");
      expect(result.text).toBe("word1 word2 word3");
    });

    it("merges 3+ blank lines into 2", async () => {
      const buf = Buffer.from("line1\n\n\n\nline2");
      const result = await extractText(buf, ".txt");
      expect(result.text).toBe("line1\n\nline2");
    });

    it("trims leading and trailing whitespace", async () => {
      const buf = Buffer.from("   hello   ");
      const result = await extractText(buf, ".txt");
      expect(result.text).toBe("hello");
    });

    it("preserves tabs", async () => {
      const buf = Buffer.from("col1\tcol2\tcol3");
      const result = await extractText(buf, ".txt");
      expect(result.text).toBe("col1\tcol2\tcol3");
    });
  });

  // ===== 截断 =====
  describe("truncation", () => {
    it("truncates text exceeding MAX_FILE_CHARS", async () => {
      const longText = "x".repeat(MAX_FILE_CHARS + 1000);
      const buf = Buffer.from(longText);
      const result = await extractText(buf, ".txt");
      expect(result.text.length).toBe(MAX_FILE_CHARS);
      expect(result.truncated).toBe(true);
      expect(result.originalChars).toBe(MAX_FILE_CHARS + 1000);
    });

    it("does not truncate text within limit", async () => {
      const text = "a".repeat(MAX_FILE_CHARS);
      const buf = Buffer.from(text);
      const result = await extractText(buf, ".txt");
      expect(result.text.length).toBe(MAX_FILE_CHARS);
      expect(result.truncated).toBe(false);
    });
  });

  // ===== 不支持的格式 =====
  describe("unsupported extension", () => {
    it("throws for unknown extension", async () => {
      const buf = Buffer.from("data");
      await expect(extractText(buf, ".xyz")).rejects.toThrow("Unsupported file extension: .xyz");
    });
  });

  // ===== PDF (通过 _deps 注入 mock) =====
  describe("PDF extraction", () => {
    function mockPDFParse(loadResult, getTextResult) {
      _deps.PDFParse = class {
        constructor(opts) { this.options = opts; }
        async load() { return loadResult; }
        async getText() { return getTextResult; }
        destroy() {}
      };
    }

    it("extracts text and page count from PDF", async () => {
      mockPDFParse(
        { numPages: 3 },
        { text: "PDF content here", total: 3, pages: [] },
      );
      const buf = Buffer.from("fake-pdf");
      const result = await extractText(buf, ".pdf");
      expect(result.text).toBe("PDF content here");
      expect(result.pages).toBe(3);
    });

    it("handles empty PDF text", async () => {
      mockPDFParse(
        { numPages: 1 },
        { text: "", total: 1, pages: [] },
      );
      const buf = Buffer.from("fake-pdf");
      const result = await extractText(buf, ".pdf");
      expect(result.text).toBe("");
      expect(result.originalChars).toBe(0);
    });

    it("propagates pdf-parse errors on load", async () => {
      _deps.PDFParse = class {
        constructor() {}
        async load() { throw new Error("encrypted"); }
        async getText() { return { text: "" }; }
        destroy() {}
      };
      const buf = Buffer.from("fake-pdf");
      await expect(extractText(buf, ".pdf")).rejects.toThrow("encrypted");
    });

    it("passes Uint8Array data to PDFParse constructor", async () => {
      let capturedOpts = null;
      _deps.PDFParse = class {
        constructor(opts) { capturedOpts = opts; }
        async load() { return { numPages: 1 }; }
        async getText() { return { text: "ok" }; }
        destroy() {}
      };
      const buf = Buffer.from("test");
      await extractText(buf, ".pdf");
      expect(capturedOpts.data).toBeInstanceOf(Uint8Array);
    });

    it("applies text cleanup to PDF output", async () => {
      mockPDFParse(
        { numPages: 1 },
        { text: "line1\n\n\n\nline2   extra" },
      );
      const buf = Buffer.from("fake");
      const result = await extractText(buf, ".pdf");
      expect(result.text).toBe("line1\n\nline2 extra");
    });
  });

  // ===== DOCX =====
  describe("DOCX extraction", () => {
    it("extracts text from DOCX", async () => {
      let capturedArg = null;
      _deps.mammothExtractRawText = async (arg) => {
        capturedArg = arg;
        return { value: "Word document text" };
      };
      const buf = Buffer.from("fake-docx");
      const result = await extractText(buf, ".docx");
      expect(result.text).toBe("Word document text");
      expect(result.pages).toBeNull();
      expect(capturedArg).toEqual({ buffer: buf });
    });

    it("handles empty DOCX", async () => {
      _deps.mammothExtractRawText = async () => ({ value: "" });
      const buf = Buffer.from("fake-docx");
      const result = await extractText(buf, ".docx");
      expect(result.text).toBe("");
    });

    it("propagates mammoth errors", async () => {
      _deps.mammothExtractRawText = async () => { throw new Error("corrupted"); };
      const buf = Buffer.from("fake-docx");
      await expect(extractText(buf, ".docx")).rejects.toThrow("corrupted");
    });

    it("applies text cleanup to DOCX output", async () => {
      _deps.mammothExtractRawText = async () => ({ value: "  text   with   spaces  " });
      const buf = Buffer.from("fake");
      const result = await extractText(buf, ".docx");
      expect(result.text).toBe("text with spaces");
    });
  });

  // ===== 常量 =====
  describe("constants", () => {
    it("exports MAX_FILE_CHARS as 28000", () => {
      expect(MAX_FILE_CHARS).toBe(28_000);
    });
  });
});
