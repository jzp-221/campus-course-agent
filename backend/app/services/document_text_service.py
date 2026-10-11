"""文档文本层抽取与确定性分块，不访问数据库、网络或配置。"""

from io import BytesIO
from pathlib import PurePath
import re

from app.common.exceptions import BusinessException


SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".pdf", ".docx"})
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50


def _clean_text(text: str) -> str:
    """保留段落和单个换行，去除无意义的控制符及重复空白。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise BusinessException("PDF 文件已加密，无法抽取文本", code=400)
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except BusinessException:
        raise
    except Exception as exc:
        raise BusinessException("PDF 文件损坏或格式不正确，无法抽取文本", code=400) from exc

    if not _clean_text(text):
        raise BusinessException(
            "PDF 未抽取到有效文本，可能为扫描版 PDF；本阶段不支持 OCR，请上传带文字层的 PDF",
            code=400,
        )
    return text


def _docx_text(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(BytesIO(data))
        parts = []
        # 按正文顺序抽取段落及表格，避免把所有表格移到文档末尾。
        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                parts.append(Paragraph(child, document).text)
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                parts.extend("\t".join(cell.text for cell in row.cells) for row in table.rows)
        return "\n".join(parts)
    except Exception as exc:
        raise BusinessException("DOCX 文件损坏或格式不正确，无法抽取文本", code=400) from exc


def extract_text(filename: str, data: bytes) -> str:
    """抽取支持格式的文本；不能抽取有效文本时明确失败，禁止空文档入库。"""
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise BusinessException("仅支持 .txt、.md、.pdf、.docx 文件", code=400)
    if not data:
        raise BusinessException("文件为空，无法抽取文本", code=400)

    if suffix in {".txt", ".md"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise BusinessException("文本文件必须使用 UTF-8 编码", code=400) from exc
    elif suffix == ".pdf":
        text = _pdf_text(data)
    else:
        text = _docx_text(data)

    cleaned = _clean_text(text)
    if not cleaned:
        raise BusinessException("文件未抽取到有效文本，不能入库空文档", code=400)
    return cleaned


def _section_windows(text: str) -> list[str]:
    """只在同一章节内滑动窗口，保留原有 500/50 字符边界。"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end]
        # 不对窗口单独 strip，确保分块边界和重叠内容可重复。
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def chunk_text(text: str, *, filename: str | None = None) -> list[str]:
    """按二、三级标题分块，超长章节才用 500/50 窗口。

    提供文件名时，每个窗口增加「文档名 · 章节标题」前缀；正文窗口
    仍最多 500 字符，前缀不计入窗口大小。无标题文本沿用固定窗口。
    """
    text = _clean_text(text)
    if not text:
        raise BusinessException("文档文本为空，不能生成分块", code=400)

    document_name = _clean_text(filename) if filename else None
    chunks = []
    for section in re.split(r"(?m)^(?=#{2,3}\s)", text):
        section = section.strip()
        if not section:
            continue
        heading = re.match(r"#{2,3}\s+([^\n]+)", section)
        title = re.sub(r"\s+#+\s*$", "", heading.group(1)).strip() if heading else "正文"
        prefix = f"{document_name} · {title}\n" if document_name else ""
        chunks.extend(prefix + chunk for chunk in _section_windows(section))
    return chunks
