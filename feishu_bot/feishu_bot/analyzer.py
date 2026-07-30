import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
_PDF_EXTS = {".pdf"}


def _init_tesseract() -> bool:
    try:
        import pytesseract
    except ImportError:
        logger.info("未安装 pytesseract，文件分类的本地图片 OCR 将跳过；飞书 OCR API 不受影响。")
        return False

    import shutil as _shutil
    if _shutil.which("tesseract"):
        return True

    if os.name == "nt":
        common_paths = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
        for p in common_paths:
            if Path(p).exists():
                pytesseract.pytesseract.tesseract_cmd = p
                logger.info("已自动配置 Tesseract 路径: %s", p)
                return True

    logger.info("未配置 Tesseract，文件分类的本地图片 OCR 将跳过；飞书 OCR API 不受影响。")
    return False


_TESSERACT_AVAILABLE = _init_tesseract()


def extract_text_from_pdf(file_path: Path) -> str:
    try:
        import pdfplumber
    except ImportError:
        logger.warning("pdfplumber 未安装，无法提取 PDF 文本。请运行: pip install pdfplumber")
        return ""

    text_parts: list[str] = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_parts.append(page_text)
    except Exception:
        logger.exception("PDF 文本提取失败: %s", file_path)

    return "\n".join(text_parts)


def extract_text_from_image(file_path: Path) -> str:
    if not _TESSERACT_AVAILABLE:
        logger.debug("Tesseract 不可用，跳过文件分类图片 OCR: %s", file_path)
        return ""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        logger.warning("pytesseract 或 Pillow 未安装，无法进行图片 OCR。请运行: pip install pytesseract Pillow")
        return ""

    try:
        img = Image.open(str(file_path))
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        return text
    except Exception:
        logger.exception("图片 OCR 提取失败: %s", file_path)
        return ""


def extract_text(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in _PDF_EXTS:
        return extract_text_from_pdf(file_path)
    if ext in _IMAGE_EXTS:
        return extract_text_from_image(file_path)
    logger.info("不支持的文件类型，跳过文本提取: %s", ext)
    return ""


def classify_file(
    file_path: Path,
    *,
    keywords: list[str],
    target_subdir: str,
    base_dir: str,
) -> Optional[Path]:
    """提取文件文本，若包含任一关键词则移动到 base_dir/target_subdir/ 下。

    Returns:
        移动后的新路径（如果匹配），否则返回 None。
    """
    if not keywords:
        return None

    text = extract_text(file_path)
    if not text:
        return None

    matched_keyword = None
    for kw in keywords:
        if kw in text:
            matched_keyword = kw
            break

    if not matched_keyword:
        return None

    logger.info("文件匹配关键词 [%s]: %s", matched_keyword, file_path.name)

    dest_dir = Path(base_dir) / target_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / file_path.name
    if dest_path.exists():
        stem, suffix = file_path.stem, file_path.suffix
        for i in range(1, 10000):
            candidate = dest_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dest_path = candidate
                break

    try:
        shutil.move(str(file_path), str(dest_path))
        logger.info("文件已移动到分类目录: %s -> %s", file_path, dest_path)
        return dest_path
    except Exception:
        logger.exception("文件移动失败: %s -> %s", file_path, dest_path)
        return None


def recognize_image_via_feishu(client, image_path: Path) -> str:
    """使用飞书 OCR API 识别图片中的文字。

    Args:
        client: lark.Client 实例（已配置 app_id/app_secret）
        image_path: 图片文件路径

    Returns:
        识别出的文本（多行用 \\n 拼接），失败返回空字符串
    """
    import base64
    from lark_oapi.api.optical_char_recognition.v1.model import (
        BasicRecognizeImageRequest,
        BasicRecognizeImageRequestBody,
    )

    try:
        with open(str(image_path), "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")

        request = (
            BasicRecognizeImageRequest.builder()
            .request_body(
                BasicRecognizeImageRequestBody.builder()
                .image(image_b64)
                .build()
            )
            .build()
        )

        response = client.optical_char_recognition.v1.image.basic_recognize(request)
        if not response.success():
            logger.warning(
                "飞书OCR失败 code=%s msg=%s log_id=%s",
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return ""

        text_list = response.data.text_list if response.data else []
        return "\n".join(text_list)
    except Exception:
        logger.exception("飞书OCR异常: %s", image_path)
        return ""
