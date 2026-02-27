const { PDFParse } = require("pdf-parse");
const mammoth = require("mammoth");

const MAX_FILE_CHARS = 28_000; // 预留 2000 给用户消息，validator 限制 30000

// 依赖引用：测试时可通过 _deps 替换
const _deps = { PDFParse, mammothExtractRawText: mammoth.extractRawText };

/**
 * 从文件 buffer 中提取纯文本。
 * @param {Buffer} buffer - 文件内容
 * @param {string} ext - 小写扩展名，含点（".pdf" / ".docx" / ".txt"）
 * @returns {Promise<{text: string, pages: number|null, originalChars: number, truncated: boolean}>}
 */
async function extractText(buffer, ext) {
  let rawText = "";
  let pages = null;

  switch (ext) {
    case ".pdf": {
      const parser = new _deps.PDFParse({ data: new Uint8Array(buffer) });
      try {
        const doc = await parser.load();
        pages = doc.numPages || null;
        const result = await parser.getText();
        rawText = result.text || "";
      } finally {
        parser.destroy();
      }
      break;
    }
    case ".docx": {
      const result = await _deps.mammothExtractRawText({ buffer });
      rawText = result.value || "";
      break;
    }
    case ".txt":
    case ".md":
    case ".csv":
    case ".json": {
      rawText = buffer.toString("utf8");
      // 去 BOM
      if (rawText.charCodeAt(0) === 0xfeff) {
        rawText = rawText.slice(1);
      }
      break;
    }
    default:
      throw new Error(`Unsupported file extension: ${ext}`);
  }

  // 清洗：去控制字符（保留换行和制表）、合并连续空行
  rawText = rawText
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, "") // 去 NUL 等控制字符
    .replace(/[^\S\n\t]+/g, " ")     // 连续空格合并
    .replace(/\n{3,}/g, "\n\n")      // 3+ 空行合并为 2
    .trim();

  const originalChars = rawText.length;
  let truncated = false;

  if (originalChars > MAX_FILE_CHARS) {
    rawText = rawText.slice(0, MAX_FILE_CHARS);
    truncated = true;
  }

  return {
    text: rawText,
    pages,
    originalChars,
    truncated,
  };
}

module.exports = { extractText, MAX_FILE_CHARS, _deps };
