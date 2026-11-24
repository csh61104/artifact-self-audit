#!/usr/bin/env python3
"""产物确定性扫描：交付前对产物做机器可检项检查。

用法:
    python scan_artifact.py <产物路径> [--json] [--max-files N]

产物路径可以是单个文件，也可以是一个目录（目录会逐个文件扫描）。
只读操作：不写入、不删除、不联网、不启动子进程。
退出码: 0=无发现, 1=有发现, 2=输入或用法错误

分流规则（魔数优先）：先读文件头 16 字节按魔数判断类型，命中即走结构分支，不再依赖
扩展名白名单——白名单是打地鼠，漏一个扩展名就误报一片（2026-10-14 的 .apk 即此）。
扩展名只在魔数嗅探不出时兜底，用于兜住「已损坏到认不出魔数」的二进制文件。
例外：.md/.txt/.rst 等散文类扩展名优先按文本审，魔数冲突记 INFO 备查。

已知限制: 按文本特征匹配。为降低误报，脚本内置四类自动降噪（见 find_placeholders）：
代码围栏内的内容跳过；Markdown 待办清单的 "[ ]" 不算空引用；同一行出现多个占位符词、
或同一个词重复出现，视为在列举而不是残留；被引号包住、或所在行含说明词的命中降为 INFO
并标注「疑似规则说明」。降噪后仍需人工确认，不能替代第 2 关的事实核验。
非散文类文件（源码、配置等）只做编码、命名和通用残留检查，跳过 Markdown 结构类检查；
通用残留检查仍按文本匹配，源码里出现的真实待办（如 "# TODO: 补测试"）照常报出。
二进制文档（docx / xlsx / pptx / pdf / 压缩包 / 音视频 / 位图等）不做文本解码，
改为结构可开性检查：ZIP 容器验完整性与条目、非 ZIP 压缩包验魔数、PDF 验头尾、
老式 Office 验 OLE 魔数、媒体验魔数，并保留命名检查。
乱码按替换字符占比分级：>1% 判 FAIL、>0.1% 判 WARN、更少记 INFO。
结构化文本（.json/.yaml/.yml）另做语法合法性校验：JSON 用标准库解析，语法不合法即 FAIL。
教训（2026-10-15）：app-config.json 的自定义提示词里混入 4 个未转义的裸引号，整份配置
解析失败，而当时脚本零检出——因为 .json 属非散文类，只走了编码与命名检查，从不验证语法。
YAML 在缺 pyyaml 时降级：只查 Tab 缩进这一必然错误，并记 INFO 说明未完整校验。
"""

import argparse
import json
import os
import re
import sys
import zipfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 占位符与待办残留。级别 WARN 表示命中即问题；INFO 表示仅统计。
# 英文标记词（TODO/FIXME/TBD）两侧不得是单词字符或路径连接符——`real-todo-in-source`
# 这类 kebab-case 标识符、`code/todo.py` 这类文件名里含 todo 都不是待办（2026-10-15 实测误报）。
# XXX 不加此限：它是占位符号，出现在任何位置都可能是残留，宁可报 INFO 让人定夺。
PLACEHOLDER_PATTERNS = [
    ("TODO", r"(?<![\w\-/\\])TODO(?![\w\-/\\])", "WARN"),
    ("FIXME", r"(?<![\w\-/\\])FIXME(?![\w\-/\\])", "WARN"),
    ("TBD", r"(?<![\w\-/\\])TBD(?![\w\-/\\])", "WARN"),
    ("XXX", r"\bXXX+\b", "WARN"),
    ("待补", r"待补[充全]?", "WARN"),
    ("待填", r"待填(?!写|表)", "WARN"),
    ("示例文本", r"示例文本|示例文字|示例内容", "WARN"),
    ("占位符标记", r"[\[【]\s*占位[^\]】\n]{0,6}[\]】]", "WARN"),
    ("双花括号", r"\{\{[^\n{}]{0,40}\}\}", "WARN"),
    ("Lorem ipsum", r"lorem\s+ipsum", "WARN"),
    ("空方括号", r"\[\s*\]", "WARN"),
    ("连续问号", r"[?？]{2,}", "WARN"),
    ("待核实标注", r"待核实|待确认|存疑", "INFO"),
]

# 只在散文类文件里有意义的检查：源码里的 []、??、{{}} 属于语法，不是残留。
# （JSX 的 style={{...}}、Vue 插值、模板语法都会命中「双花括号」，对代码文件必然误报。）
PROSE_ONLY_CHECKS = {"空方括号", "连续问号", "双花括号"}

# 降噪用的辅助判据
TODO_ITEM_RE = re.compile(r"^\s*[-*+]\s*\[[ xX]?\]")  # Markdown 待办清单
EXPLAIN_HINT_RE = re.compile(
    r"例如|比如|举例|讲解|说明|规则|清单|检查项|误报|占位符|残留"
    # 谈论「名字」的句子不是写作残留：降「毛选-XXX 技能名」这类专有名词误报（2026-10-15）
    r"|技能|命名|名称|代号|标识|模板"
)
QUOTE_CHARS = "\"'“”‘’「」『』【】"

# 只有这些扩展名才做 Markdown 结构类检查（空章节、代码围栏、断链）。
PROSE_EXTENSIONS = {".md", ".markdown", ".txt", ".rst"}

# 结构化文本：除通用检查外，另做语法合法性校验（解析失败即 FAIL）。
# 2026-10-15 教训：app-config.json 因 4 个未转义裸引号解析失败，脚本当时零检出——
# .json 属非散文类，此前只做编码与命名检查，从不验证语法是否成立。
STRUCTURED_EXTENSIONS = {
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}

# 二进制文档不能按文本解码（硬解会产生替换字符），改为结构与可开性检查。
# 教训（2026-10-14）：这份白名单漏一个扩展名，该类型文件就会落进文本分支被硬解，
# 报出成片的假阳性——当时 .apk 未收录，一个正常安装包被报成 1 FAIL + 27 WARN。
# 因此本表求全不求精：只要是 ZIP 容器，一律收录。
ARCHIVE_EXTENSIONS = {
    # Office / OpenDocument / 电子书 / XMind
    ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm",
    ".odt", ".ods", ".odp", ".epub", ".xmind",
    # 通用压缩包与安装包（均为 ZIP 容器）
    ".zip", ".apk", ".aab", ".ipa", ".jar", ".war", ".whl", ".nupkg", ".vsix",
    # 三维与设计类容器（3MF 为 ZIP）
    ".3mf",
}

# 非 ZIP 的压缩包：只验魔数、判断“能否打开、扩展名是否相符”，不做条目完整性检查。
NON_ZIP_ARCHIVE_MAGIC = {
    ".7z": (lambda h: h[:6] == b"7z\xbc\xaf\x27\x1c", "7-Zip"),
    ".rar": (lambda h: h[:4] == b"Rar!" and h[4:6] == b"\x1a\x07", "RAR"),
    ".gz": (lambda h: h[:2] == b"\x1f\x8b", "GZip"),
    ".tgz": (lambda h: h[:2] == b"\x1f\x8b", "GZip"),
    ".xz": (lambda h: h[:6] == b"\xfd7zXZ\x00", "XZ"),
    ".bz2": (lambda h: h[:3] == b"BZh", "BZip2"),
}
NON_ZIP_ARCHIVE_EXTENSIONS = set(NON_ZIP_ARCHIVE_MAGIC)
PDF_EXTENSIONS = {".pdf"}
OLE_EXTENSIONS = {".doc", ".xls", ".ppt"}

# 音视频与位图：同样是二进制，按文本解码必然误报（曾把 MP4 判成 FAIL）。
# 这类产物只验魔数，判断“文件是否可开、扩展名是否相符”。
MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".m4a", ".mkv", ".webm", ".avi", ".flv",
    ".mp3", ".wav", ".flac", ".ogg", ".aac",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
}
BINARY_DOC_EXTENSIONS = (ARCHIVE_EXTENSIONS | NON_ZIP_ARCHIVE_EXTENSIONS
                         | PDF_EXTENSIONS | OLE_EXTENSIONS | MEDIA_EXTENSIONS)

# 扩展名 -> (魔数判定函数, 可读名称)
MEDIA_MAGIC = {
    ".mp4": (lambda h: h[4:8] == b"ftyp", "MP4"),
    ".mov": (lambda h: h[4:8] == b"ftyp", "MOV"),
    ".m4v": (lambda h: h[4:8] == b"ftyp", "MP4"),
    ".m4a": (lambda h: h[4:8] == b"ftyp", "M4A"),
    ".mkv": (lambda h: h[:4] == b"\x1a\x45\xdf\xa3", "Matroska"),
    ".webm": (lambda h: h[:4] == b"\x1a\x45\xdf\xa3", "WebM"),
    ".avi": (lambda h: h[:4] == b"RIFF" and h[8:12] == b"AVI ", "AVI"),
    ".wav": (lambda h: h[:4] == b"RIFF" and h[8:12] == b"WAVE", "WAV"),
    ".flac": (lambda h: h[:4] == b"fLaC", "FLAC"),
    ".ogg": (lambda h: h[:4] == b"OggS", "OGG"),
    ".mp3": (lambda h: h[:3] == b"ID3" or h[:1] == b"\xff", "MP3"),
    ".aac": (lambda h: h[:1] == b"\xff" or h[:3] == b"ID3", "AAC"),
    ".png": (lambda h: h[:8] == b"\x89PNG\r\n\x1a\n", "PNG"),
    ".jpg": (lambda h: h[:3] == b"\xff\xd8\xff", "JPEG"),
    ".jpeg": (lambda h: h[:3] == b"\xff\xd8\xff", "JPEG"),
    ".gif": (lambda h: h[:4] == b"GIF8", "GIF"),
    ".webp": (lambda h: h[:4] == b"RIFF" and h[8:12] == b"WEBP", "WebP"),
    ".bmp": (lambda h: h[:2] == b"BM", "BMP"),
    ".tif": (lambda h: h[:4] in (b"II*\x00", b"MM\x00*"), "TIFF"),
    ".tiff": (lambda h: h[:4] in (b"II*\x00", b"MM\x00*"), "TIFF"),
}

# 英文词必须带 \b 词边界，否则 copyright.md / drafting.md 会被误判成副本文件。
# （2026-10-14 修复：原写法用裸 copy / draft 匹配，实测误报。）
TEMP_NAME_PATTERNS = [
    r"^~\$", r"\.tmp$", r"\.bak$", r"\.old$", r"\.orig$", r"\.swp$",
    r"副本$", r"拷贝$", r"草稿", r"\bdraft\b", r"\bcopy\b", r"\s\(\d+\)$",
    r"未命名", r"\buntitled\b", r"新建",
]

WINDOWS_ILLEGAL = set('<>:"/\\|?*')

LOCAL_LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\n]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(\S.*)$")
FENCE_RE = re.compile(r"^\s*```")

# 嗅探专用魔数表：只留不会误判的强魔数。
# 排除 mp3/aac（0xFF 帧头过宽，任何以 0xFF 开头的文件都会命中）与 bmp（"BM" 仅两字节）。
SNIFF_MEDIA_MAGIC = {k: v for k, v in MEDIA_MAGIC.items()
                     if k not in (".mp3", ".aac", ".bmp")}


def sniff_binary_kind(head):
    """按文件头魔数判断二进制类型，不信任扩展名。

    返回 "zip" / "pdf" / "ole" / "media:<ext>" / "<非 ZIP 压缩包 ext>" 之一，未命中返回 None。
    扩展名白名单是“打地鼠”——漏一个就误报一片；魔数嗅探才是治本。
    新增类型只需往对应魔数表加一行，不必再动分流逻辑。
    """
    if head[:2] == b"PK":
        return "zip"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return "ole"
    for ext, (checker, _label) in NON_ZIP_ARCHIVE_MAGIC.items():
        if checker(head):
            return ext
    for ext, (checker, _label) in SNIFF_MEDIA_MAGIC.items():
        if checker(head):
            return "media:" + ext
    return None


def check_binary_document(path, kind=None):
    """二进制文档只做结构可开性检查：压缩包验 ZIP、PDF 验头尾、老 Office 验魔数。

    kind 由 sniff_binary_kind 按魔数给出，优先于扩展名；kind 为 None 时退回扩展名，
    以便兜住「扩展名是二进制类型、但内容已损坏到嗅探不出魔数」的文件（此时应报结构异常）。
    """
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(16)
            fh.seek(max(0, size - 2048))
            tail = fh.read()
    except OSError as exc:
        return [{"check": "文件不可读", "level": "FAIL", "line": 0, "snippet": str(exc)}]

    findings = []
    if kind == "zip" or (kind is None and ext in ARCHIVE_EXTENSIONS):
        if head[:2] != b"PK":
            findings.append({
                "check": "压缩包结构异常", "level": "FAIL", "line": 0,
                "snippet": "缺少 PK 头，文件可能损坏或扩展名不符",
            })
        else:
            try:
                with zipfile.ZipFile(path) as zf:
                    bad = zf.testzip()
                    names = zf.namelist()
                if bad:
                    findings.append({
                        "check": "压缩包条目损坏", "level": "FAIL", "line": 0,
                        "snippet": "损坏条目：" + bad,
                    })
                elif not names:
                    findings.append({
                        "check": "压缩包为空", "level": "FAIL", "line": 0,
                        "snippet": "ZIP 内没有任何条目",
                    })
            except zipfile.BadZipFile:
                findings.append({
                    "check": "文件无法打开", "level": "FAIL", "line": 0,
                    "snippet": "ZIP 结构损坏，文档打不开",
                })
    elif kind in NON_ZIP_ARCHIVE_EXTENSIONS or (
            kind is None and ext in NON_ZIP_ARCHIVE_EXTENSIONS):
        key = kind if kind in NON_ZIP_ARCHIVE_EXTENSIONS else ext
        checker, label = NON_ZIP_ARCHIVE_MAGIC.get(key, (None, "压缩包"))
        if checker is None or not checker(head):
            findings.append({
                "check": "压缩包结构异常", "level": "FAIL", "line": 0,
                "snippet": label + " 魔数校验未通过，文件可能损坏或扩展名不符",
            })
    elif kind == "pdf" or (kind is None and ext in PDF_EXTENSIONS):
        if not head.startswith(b"%PDF"):
            findings.append({
                "check": "PDF 结构异常", "level": "FAIL", "line": 0,
                "snippet": "缺少 %PDF 头，文件可能损坏",
            })
        elif b"%%EOF" not in tail:
            findings.append({
                "check": "PDF 可能被截断", "level": "WARN", "line": 0,
                "snippet": "尾部未找到 %%EOF 结束标记",
            })
    elif kind == "ole" or (kind is None and ext in OLE_EXTENSIONS):
        if head[:4] != b"\xd0\xcf\x11\xe0":
            findings.append({
                "check": "老式 Office 结构异常", "level": "WARN", "line": 0,
                "snippet": "缺少 OLE 复合文档魔数",
            })
    else:
        # 媒体：魔数嗅探命中则用嗅探结果，否则退回扩展名
        media_ext = None
        if isinstance(kind, str) and kind.startswith("media:"):
            media_ext = kind.split(":", 1)[1]
        elif kind is None and ext in MEDIA_EXTENSIONS:
            media_ext = ext
        if media_ext:
            magic = MEDIA_MAGIC.get(media_ext)
            if magic is None:
                return findings
            checker, label = magic
            if size < 16 or not checker(head):
                findings.append({
                    "check": "媒体文件结构异常", "level": "FAIL", "line": 0,
                    "snippet": label + " 魔数校验未通过，文件可能损坏或扩展名不符",
                })
    return findings


def decode_text(raw):
    """按常见编码尝试解码，返回 (文本, 编码名, 是否有替换字符)。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        return text, enc, ("\ufffd" in text)
    return raw.decode("utf-8", errors="replace"), "未知(有损)", True


def decode_forbidden_visible(text):
    """检测控制字符和不可见字符。"""
    bad = []
    for i, ch in enumerate(text):
        code = ord(ch)
        if ch in "\n\r\t":
            continue
        if code < 32 or code == 127:
            bad.append((i, hex(code)))
    return bad[:10]


def check_structured_text(path, raw, ext):
    """结构化文本（JSON / YAML）语法合法性校验。

    JSON 用标准库解析，失败即 FAIL 并给出行列位置——配置类文件一旦解析失败，
    所有依赖它的功能都会静默失效，属最高优先级问题。
    YAML 缺 pyyaml 时降级：只查「Tab 缩进」这一 YAML 必然错误，并记 INFO 说明未完整校验，不产生误报。
    扩展名与真实格式不符时不在此处报错，交由上游的分流逻辑处理。
    """
    kind = STRUCTURED_EXTENSIONS.get(ext)
    if kind is None:
        return []
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return []  # 解码失败交给通用编码检查，避免同一问题报两次

    findings = []
    if kind == "json":
        if not text.strip():
            findings.append({
                "check": "JSON 为空文件", "level": "FAIL", "line": 0,
                "snippet": "内容为空，解析必然失败",
            })
            return findings
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            findings.append({
                "check": "JSON 语法错误", "level": "FAIL",
                "line": exc.lineno or 0,
                "snippet": "第 %d 行第 %d 列：%s —— 文件已无法解析，依赖它的功能会静默失效"
                           % (exc.lineno, exc.colno, exc.msg),
            })
    elif kind == "yaml":
        try:
            import yaml  # type: ignore
        except ImportError:
            yaml = None
        if yaml is not None:
            try:
                yaml.safe_load(text)
            except Exception as exc:  # yaml.YAMLError 及其子类
                first = (str(exc).splitlines() or ["解析失败"])[0]
                findings.append({
                    "check": "YAML 语法错误", "level": "FAIL", "line": 0,
                    "snippet": first[:160],
                })
        else:
            tab_lines = [i + 1 for i, ln in enumerate(text.split("\n"))
                         if ln.startswith("\t")]
            if tab_lines:
                findings.append({
                    "check": "YAML 缩进违规", "level": "FAIL", "line": tab_lines[0],
                    "snippet": "YAML 不允许用 Tab 缩进（首个在第 %d 行，共 %d 行）"
                               % (tab_lines[0], len(tab_lines)),
                })
            findings.append({
                "check": "YAML 未完整校验", "level": "INFO", "line": 0,
                "snippet": "本机缺 pyyaml，仅检查了 Tab 缩进等基础项，语法未完整验证",
            })
    return findings


def _fence_map(lines):
    """标记每一行是否位于代码围栏内（围栏行本身也算）。"""
    inside = {}
    state = False
    for i, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            inside[i] = True
            state = not state
            continue
        inside[i] = state
    return inside


def _is_quoted(text, start, end):
    """命中词是否被引号包住：多半是在“谈论”这个词，而不是真的残留。"""
    if start <= 0 or end >= len(text):
        return False
    return text[start - 1] in QUOTE_CHARS and text[end] in QUOTE_CHARS


def _inline_code_spans(line):
    """返回一行内行内代码段（`...`）的 (起, 止) 相对区间列表。

    行内代码里的是"引用原文"，不是本文件自己的写作残留——文档经常引用 API 返回值、
    命令行输出、待办样例（如日志里写 `"choices":[]` 复述接口返回），这类命中一律降噪。
    2026-10-15 新增：此前只跳代码围栏，行内代码未覆盖，导致每次引用代码都被报。
    """
    spans = []
    idx = 0
    while True:
        s = line.find("`", idx)
        if s == -1:
            break
        e = line.find("`", s + 1)
        if e == -1:
            break
        spans.append((s, e))
        idx = e + 1
    return spans


def find_placeholders(text, is_prose):
    lines = text.split("\n")
    fences = _fence_map(lines)

    raw = []
    for name, pattern, level in PLACEHOLDER_PATTERNS:
        if not is_prose and name in PROSE_ONLY_CHECKS:
            continue
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            start, end = m.start(), m.end()
            line_no = text.count("\n", 0, start) + 1
            snippet = text[max(0, start - 20):end + 20].replace("\n", " ")
            raw.append({
                "check": name,
                "level": level,
                "line": line_no,
                "snippet": snippet.strip(),
                "start": start,
                "end": end,
            })

    names_per_line = {}
    counts_per_line = {}
    for h in raw:
        names_per_line.setdefault(h["line"], set()).add(h["check"])
        counts_per_line[h["line"]] = counts_per_line.get(h["line"], 0) + 1

    hits = []
    for h in raw:
        line_no = h["line"]
        line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""

        # 降噪 1：代码围栏内属于示例代码，不报
        if fences.get(line_no):
            continue
        # 降噪 2：Markdown 待办清单的 "[ ]" 是清单语法，不是空引用
        if h["check"] == "空方括号" and TODO_ITEM_RE.match(line_text):
            continue

        noisy = False
        if len(names_per_line.get(line_no, ())) >= 2 or counts_per_line.get(line_no, 0) >= 2:
            noisy = True  # 同一行罗列多个占位符词或重复出现，多半是在讲规则
        elif _is_quoted(text, h["start"], h["end"]) or EXPLAIN_HINT_RE.search(line_text):
            noisy = True
        else:
            # 降噪 3：行内代码段内的命中是引用原文，不是本文件自己的残留
            rel = h["start"] - (text.rfind("\n", 0, h["start"]) + 1)
            if any(s < rel < e for s, e in _inline_code_spans(line_text)):
                noisy = True

        if noisy and h["level"] == "WARN":
            h["level"] = "INFO"
            h["snippet"] = h["snippet"] + "　［疑似规则说明或举例，已自动降噪，需人工确认］"

        h.pop("start", None)
        h.pop("end", None)
        hits.append(h)
    return hits


def find_empty_sections(text, fences=None):
    lines = text.split("\n")
    if fences is None:
        fences = _fence_map(lines)
    headings = []
    for idx, line in enumerate(lines):
        # 代码围栏内的 "# 注释" 不是 Markdown 标题，必须跳过（2026-10-14 修复）
        if fences.get(idx + 1):
            continue
        m = HEADING_RE.match(line.strip())
        if m:
            headings.append((idx, len(m.group(1)), m.group(2).strip()))
    findings = []
    for pos, (idx, level, title) in enumerate(headings):
        if pos + 1 < len(headings):
            next_idx, next_level = headings[pos + 1][0], headings[pos + 1][1]
            if next_level > level:
                # 后面跟的是更深的子标题，这是容器标题，不算空章节
                continue
        else:
            next_idx = len(lines)
        body = [ln.strip() for ln in lines[idx + 1:next_idx] if ln.strip()]
        if not body:
            findings.append({
                "check": "空章节",
                "level": "FAIL",
                "line": idx + 1,
                "snippet": "#" * level + " " + title,
            })
    return findings


def find_unclosed_fences(text):
    count = sum(1 for line in text.split("\n") if FENCE_RE.match(line))
    if count % 2 == 1:
        return [{
            "check": "未闭合代码围栏",
            "level": "WARN",
            "line": 0,
            "snippet": "代码围栏计数为奇数(%d)，疑似有未闭合的 ```" % count,
        }]
    return []


def find_broken_links(text, base_dir, fences=None):
    findings = []
    if fences is None:
        fences = _fence_map(text.split("\n"))
    for m in LOCAL_LINK_RE.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        # 代码围栏内的方括号链接是示例语法，不是断链（2026-10-14 修复）
        if fences.get(line_no):
            continue
        target = m.group(2).strip()
        if target.startswith(("http://", "https://", "mailto:", "#", "data:")):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if not path:
            continue
        resolved = os.path.normpath(os.path.join(base_dir, path))
        if not os.path.exists(resolved):
            findings.append({
                "check": "断链引用",
                "level": "FAIL",
                "line": line_no,
                "snippet": "%s -> %s" % (m.group(1)[:30], target),
            })
    return findings


def check_name(name):
    findings = []
    if name != name.strip():
        findings.append({
            "check": "命名单侧空格", "level": "WARN", "line": 0, "snippet": name,
        })
    bad = sorted(set(ch for ch in name if ch in WINDOWS_ILLEGAL))
    if bad:
        findings.append({
            "check": "命名非法字符", "level": "WARN", "line": 0,
            "snippet": name + " 含 " + " ".join(bad),
        })
    for pattern in TEMP_NAME_PATTERNS:
        if re.search(pattern, name, flags=re.IGNORECASE):
            findings.append({
                "check": "疑似临时文件或历史版本", "level": "WARN", "line": 0,
                "snippet": name,
            })
            break
    return findings


def scan_file(path):
    result = {"path": path, "findings": [], "stats": {}, "encoding": None}
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        result["findings"].append({
            "check": "文件不可读", "level": "FAIL", "line": 0, "snippet": str(exc),
        })
        return result

    result["stats"]["size_bytes"] = size
    if size == 0:
        result["findings"].append({
            "check": "空文件", "level": "FAIL", "line": 0, "snippet": "0 字节",
        })
        return result

    ext = os.path.splitext(path)[1].lower()

    # 只读文件头做魔数嗅探，不整读大文件（媒体文件可能几百 MB）
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError as exc:
        result["findings"].append({
            "check": "文件不可读", "level": "FAIL", "line": 0, "snippet": str(exc),
        })
        return result

    kind = sniff_binary_kind(head)

    # 散文类扩展名优先：.md/.txt 即便文件头命中魔数，也按文本审（用户已声明是文本）。
    # 冲突本身记成 INFO，避免「改名绕过」被静默放过。
    if kind is not None and ext in PROSE_EXTENSIONS:
        result["findings"].append({
            "check": "扩展名与内容不符", "level": "INFO", "line": 1,
            "snippet": "扩展名 %s 属文本类，文件头却命中 %s 魔数，已按文本审查" % (ext, kind),
        })
        kind = None

    # 魔数优先分流；kind 为 None 时用扩展名兜底（兜住损坏到嗅探不出魔数的二进制文件）
    if kind is not None or ext in BINARY_DOC_EXTENSIONS:
        result["encoding"] = "二进制"
        result["stats"]["binary_doc"] = True
        result["stats"]["prose_checks"] = False
        result["stats"]["sniffed_kind"] = kind
        result["findings"].extend(check_binary_document(path, kind))
        result["findings"].extend(check_name(os.path.basename(path)))
        return result

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        result["findings"].append({
            "check": "文件不可读", "level": "FAIL", "line": 0, "snippet": str(exc),
        })
        return result

    # 结构化文本先做语法校验：语法一旦不成立，后续字面检查的意义就有限了
    result["findings"].extend(check_structured_text(path, raw, ext))
    if ext in STRUCTURED_EXTENSIONS:
        result["stats"]["structured"] = STRUCTURED_EXTENSIONS[ext]

    text, encoding, lossy = decode_text(raw)
    result["encoding"] = encoding
    result["stats"]["lines"] = text.count("\n") + 1
    result["stats"]["chars"] = len(text)
    if lossy:
        # 按替换字符占比分级：一个坏字节与整篇乱码不该同等对待（原写法一律 FAIL，过严）。
        bad = text.count("\ufffd")
        ratio = bad / max(1, len(text))
        if ratio > 0.01:
            level, why = "FAIL", "疑似整篇乱码"
        elif ratio > 0.001:
            level, why = "WARN", "存在个别无法解码的字节"
        else:
            level, why = "INFO", "仅极少数字节异常，可能是巧合"
        result["findings"].append({
            "check": "编码或乱码风险", "level": level, "line": 1,
            "snippet": "解码为 %s 时出现 %d 个替换字符（占比 %.3f%%），%s" % (
                encoding, bad, ratio * 100, why),
        })

    ctrl = decode_forbidden_visible(text)
    if ctrl:
        result["findings"].append({
            "check": "不可见控制字符", "level": "WARN", "line": 1,
            "snippet": "发现 %d 处，首个位置 %d 字符码 %s" % (len(ctrl), ctrl[0][0], ctrl[0][1]),
        })

    is_prose = ext in PROSE_EXTENSIONS
    result["stats"]["prose_checks"] = is_prose

    result["findings"].extend(find_placeholders(text, is_prose))
    if is_prose:
        # 围栏映射只算一次，三个检查共用。此前只有占位符检查跳围栏，
        # 导致围栏内的 "# 注释" 被当标题、"[示例](x.md)" 被当断链（2026-10-14 修复）。
        fences = _fence_map(text.split("\n"))
        result["findings"].extend(find_empty_sections(text, fences))
        result["findings"].extend(find_unclosed_fences(text))
        result["findings"].extend(find_broken_links(
            text, os.path.dirname(os.path.abspath(path)), fences))
    result["findings"].extend(check_name(os.path.basename(path)))

    lines = text.split("\n")
    result["stats"]["headings"] = sum(1 for line in lines if HEADING_RE.match(line.strip()))
    result["stats"]["tables"] = sum(1 for line in lines if line.strip().startswith("|"))
    result["stats"]["code_fences"] = sum(1 for line in lines if FENCE_RE.match(line))
    return result


def collect_targets(path, max_files):
    if os.path.isfile(path):
        return [path], []
    targets = []
    skipped = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".workbuddy")]
        for name in files:
            full = os.path.join(root, name)
            if len(targets) >= max_files:
                skipped.append(full)
                continue
            targets.append(full)
    return targets, skipped


def main():
    parser = argparse.ArgumentParser(
        description="产物确定性扫描：只读检查占位符、空章节、编码、断链、命名与结构。"
    )
    parser.add_argument("path", help="产物文件或目录的路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--max-files", type=int, default=500, help="目录模式下的文件数上限")
    args = parser.parse_args()

    if not os.path.exists(args.path):
        print("输入错误：路径不存在 -> %s" % args.path)
        return 2

    targets, skipped = collect_targets(args.path, args.max_files)
    if not targets:
        print("输入错误：目录下没有可扫描的文件 -> %s" % args.path)
        return 2

    results = [scan_file(p) for p in targets]

    if args.json:
        print(json.dumps({
            "target": args.path,
            "scanned": len(targets),
            "skipped": len(skipped),
            "results": results,
        }, ensure_ascii=False, indent=2))
    else:
        print("产物确定性扫描")
        print("目标：%s" % args.path)
        print("扫描文件数：%d%s" % (len(targets), "，超出上限未扫描 %d 个" % len(skipped) if skipped else ""))
        print("-" * 60)
        total = {"FAIL": 0, "WARN": 0, "INFO": 0}
        for item in results:
            findings = item["findings"]
            for f in findings:
                total[f["level"]] = total.get(f["level"], 0) + 1
            if item["stats"].get("binary_doc"):
                note = " / 二进制文档，仅结构与命名检查"
            elif not item["stats"].get("prose_checks"):
                note = " / 非散文类，仅通用检查"
            else:
                note = ""
            header = "%s  [编码 %s / %s 行 / %s 字节%s]" % (
                item["path"], item["encoding"],
                item["stats"].get("lines", "-"), item["stats"].get("size_bytes", "-"),
                note,
            )
            hard = [f for f in findings if f["level"] in ("FAIL", "WARN")]
            print(header)
            if not hard:
                print("    无发现")
            for f in findings:
                loc = "第 %s 行" % f["line"] if f["line"] else "文件级"
                print("    [%s] %s（%s）：%s" % (f["level"], f["check"], loc, f["snippet"]))
        print("-" * 60)
        print("汇总：FAIL %d 项，WARN %d 项，INFO %d 项" % (
            total["FAIL"], total["WARN"], total["INFO"]))
        if total["FAIL"] or total["WARN"]:
            print("结论：存在机器可检项问题，需在六关审查中按级别处置")
        else:
            print("结论：确定性检查未发现问题（不代表事实与专业正确性已通过）")

    return 1 if any(
        f["level"] in ("FAIL", "WARN") for r in results for f in r["findings"]
    ) else 0


if __name__ == "__main__":
    sys.exit(main())
