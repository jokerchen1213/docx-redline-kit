# engine/ooxml.py — .docx 解包/打包 + OOXML 命名空间助手
# unpack_document / pack_document / validate_document 源自
# contract-review/scripts/{ooxml_unpack,ooxml_pack}.py（删去各自 print / __main__）
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import defusedxml.ElementTree as ET

WNS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XMLNS = "http://www.w3.org/XML/1998/namespace"


def q(tag):
    return "{%s}%s" % (WNS, tag)


def mkdtemp_compat(prefix):
    """建临时目录。win32 上不用 tempfile.mkdtemp：Python 3.12+ 会给目录设一条「仅当前用户」的显式 ACL
    （不含继承项），沙箱里授权用的能力 SID 被排除，目录建得出、往里写文件被拒。这里改为在
    tempfile.gettempdir() 下用随机名 os.mkdir（不带 mode，沿用父目录的继承 ACL），重名重试。
    其他平台行为不变。"""
    if sys.platform != "win32":
        return tempfile.mkdtemp(prefix=prefix)
    base = tempfile.gettempdir()
    for _ in range(100):
        path = os.path.join(base, prefix + os.urandom(4).hex())
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("建不了临时目录：%s\\%s*" % (base, prefix))


DOC_REQUIRED = {
    ".docx": ["[Content_Types].xml", "word/document.xml", "word/_rels/document.xml.rels"],
    ".pptx": ["[Content_Types].xml", "ppt/presentation.xml"],
    ".xlsx": ["[Content_Types].xml", "xl/workbook.xml"],
}


def unpack_document(input_file: str, output_dir: str) -> None:
    """把 Office 文件解包到目录。"""
    input_path = Path(input_file)
    output_path = Path(output_dir)
    if not input_path.is_file():
        raise FileNotFoundError(f"Input file not found: {input_file}")
    output_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(input_path, "r") as archive:
        archive.extractall(output_path)


def pack_document(input_dir: str, output_file: str, validate: bool = False) -> bool:
    """把解包目录重新打包为 Office 文件（.docx/.pptx/.xlsx）。"""
    input_path = Path(input_dir)
    output_path = Path(output_file)
    if not input_path.is_dir():
        raise ValueError(f"{input_dir} is not a directory")
    if output_path.suffix.lower() not in DOC_REQUIRED:
        raise ValueError(f"{output_file} must be a .docx, .pptx, or .xlsx file")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(input_path.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(input_path))
    if validate:
        if not validate_document(output_path):
            output_path.unlink(missing_ok=True)
            return False
    return True


def validate_document(doc_path: Path) -> bool:
    """轻量校验：必需文件存在且 XML 可解析。"""
    suffix = doc_path.suffix.lower()
    required = DOC_REQUIRED.get(suffix, [])
    try:
        with zipfile.ZipFile(doc_path, "r") as archive:
            names = set(archive.namelist())
            missing = [name for name in required if name not in names]
            if missing:
                print(f"Validation error: missing files: {missing}", file=sys.stderr)
                return False
            for name in required:
                if name.endswith((".xml", ".rels")):
                    with archive.open(name) as handle:
                        try:
                            ET.parse(handle)
                        except ET.ParseError as exc:
                            print(f"Validation error: invalid XML in {name}: {exc}", file=sys.stderr)
                            return False
    except zipfile.BadZipFile as exc:
        print(f"Validation error: invalid zip file: {exc}", file=sys.stderr)
        return False
    return True
