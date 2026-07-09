import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path(__file__).resolve().parent / "downloads"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}
PDF_EXTS = {".pdf"}
CLASSIFY_KEYWORDS = ["风口研报"]
CLASSIFY_SUBDIR = "风口研报"


def rename_images_with_date(directory: Path) -> None:
    renamed = 0
    skipped = 0
    for f in sorted(directory.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in IMAGE_EXTS:
            continue
        name = f.name
        if len(name) >= 9 and name[4] == name[7] == "-" and name[:4].isdigit() and name[5:7].isdigit():
            skipped += 1
            continue
        if len(name) >= 9 and name[8] == "_" and name[:8].isdigit():
            skipped += 1
            continue
        mtime = f.stat().st_mtime
        from datetime import datetime
        date_str = datetime.fromtimestamp(mtime).strftime("%Y%m%d")
        new_name = f"{date_str}_{name}"
        new_path = f.with_name(new_name)
        if new_path.exists():
            logger.warning("目标文件已存在，跳过: %s", new_path)
            skipped += 1
            continue
        f.rename(new_path)
        logger.info("重命名: %s -> %s", name, new_name)
        renamed += 1
    logger.info("图片重命名完成: 重命名 %d 个，跳过 %d 个", renamed, skipped)


def classify_existing_files(directory: Path) -> None:
    from feishu_bot.analyzer import classify_file
    moved = 0
    skipped = 0
    for f in sorted(directory.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (IMAGE_EXTS | PDF_EXTS):
            continue
        try:
            result = classify_file(
                f,
                keywords=CLASSIFY_KEYWORDS,
                target_subdir=CLASSIFY_SUBDIR,
                base_dir=str(directory),
            )
            if result:
                moved += 1
            else:
                skipped += 1
        except Exception:
            logger.exception("分类失败: %s", f.name)
    logger.info("文件分类完成: 移动 %d 个，未匹配 %d 个", moved, skipped)


def main() -> None:
    if not DOWNLOADS_DIR.exists():
        logger.error("downloads 目录不存在: %s", DOWNLOADS_DIR)
        sys.exit(1)

    logger.info("=== 第1步: 重命名现有图片（添加日期前缀）===")
    rename_images_with_date(DOWNLOADS_DIR)

    logger.info("=== 第2步: 对现有文件进行关键词分类 ===")
    classify_existing_files(DOWNLOADS_DIR)

    logger.info("=== 全部完成 ===")


if __name__ == "__main__":
    main()
