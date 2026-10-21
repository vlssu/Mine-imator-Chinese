#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把中文化资源打包进上游 Mine-imator 发行包。

子命令：
    resolve  从仓库标签推导构建类型、版本号、汉化文件路径与上游仓库
    build    解压上游发行包 -> 替换语言/字体/粒子 -> 重新打包 -> 生成发布说明
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUILDS = {
    "CC": {
        "repo": "mbandersmc/Mine-imator-2.0.2-Continuation-Build",
        "name": "Continuation Build",
        "dir": "translations/2.0.2/CC{v}/chinese.milanguage",
        "fallback": None,
    },
    "SU": {
        "repo": "YogaindoCR/Mine-imator-2.0.2-Simply-Upscaled-Build",
        "name": "Simply Upscaled Build",
        "dir": "translations/2.0.2/SU{v}/chinese.milanguage",
        "fallback": None,
    },
    "CB": {
        "repo": "",
        "name": "Community Build",
        "dir": "translations/2.0.2/CB{v}/chinese.milanguage",
        "fallback": "translations/2.0.2/chinese.milanguage",
    },
}

FONT_DIR = "font"
PARTICLE_DIR = "Particles"


def flat_map(data, prefix=""):
    """把嵌套 dict 拉平成 {路径: 值}。"""
    out = {}
    for key, value in data.items():
        path = prefix + "/" + key
        if isinstance(value, dict):
            out.update(flat_map(value, path))
        else:
            out[path] = value
    return out


def parse_tag(tag):
    m = re.match(r"^2\.0\.2-(CC|SU|CB)(.+)$", tag)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def resolve(tag):
    build, version = parse_tag(tag)
    if build is None:
        raise SystemExit(
            "无法从标签 %r 解析构建类型。期望形如 2.0.2-CC1.0.14 / 2.0.2-SU1.0.14.0 / 2.0.2-CB1.0.0" % tag
        )

    info = BUILDS[build]
    if not info["repo"]:
        raise SystemExit(
            "%s 没有可用的上游 GitHub 仓库，无法自动打包（标签：%s）" % (info["name"], tag)
        )

    lang = ROOT / info["dir"].format(v=version)
    if not lang.exists() and info["fallback"]:
        lang = ROOT / info["fallback"]
    if not lang.exists():
        raise SystemExit("找不到该版本的汉化文件：%s" % lang)

    return {
        "build": build,
        "version": version,
        "upstream_repo": info["repo"],
        "upstream_name": info["name"],
        "upstream_tag": version,
        "translation": lang,
        "release_tag": tag,
        "title": "2.0.2 %s %s 简体中文版" % (info["name"], version),
    }


def write_outputs(pairs, out_path):
    lines = ["%s=%s" % (k, v) for k, v in pairs.items()]
    text = "\n".join(lines) + "\n"
    if out_path:
        with open(out_path, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)


def output_name(upstream_name):
    """沿用仓库既有产物命名：CC 用 .vlssu 替换 .win-x64，SU 追加 _vlssu。"""
    path = Path(upstream_name)
    stem = path.stem
    if ".win-x64" in stem:
        stem = stem.replace(".win-x64", ".vlssu")
    elif not stem.endswith("_vlssu"):
        stem = stem + "_vlssu"
    return stem + path.suffix


def find_app_root(tmp):
    """返回发行包内的程序根目录（含 Data/Languages/english.milanguage）。"""
    for path in tmp.rglob("english.milanguage"):
        if path.parent.name == "Languages" and path.parent.parent.name == "Data":
            return path.parent.parent.parent
    raise SystemExit("上游发行包内未找到 Data/Languages/english.milanguage，包结构可能已变化")


def load_language(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def patch_languages(root, lang_bytes):
    """写入汉化文件并注册语言；同时把默认语言改为中文。"""
    lang_dir = root / "Data" / "Languages"
    original = lang_dir / "english.milanguage"

    # 原版英文另存一份，供用户在语言菜单里切回英文
    backup = lang_dir / "english_original.milanguage"
    if not backup.exists():
        shutil.copyfile(original, backup)

    # Mine-imator 的默认语言被硬编码为 english.milanguage，覆盖它才能做到开箱即中文
    with open(original, "wb") as fh:
        fh.write(lang_bytes)
    with open(lang_dir / "chinese.milanguage", "wb") as fh:
        fh.write(lang_bytes)

    meta_path = root / "Data" / "languages.midata"
    meta = {"languages": []}
    if meta_path.exists():
        with open(meta_path, encoding="utf-8-sig") as fh:
            meta = json.load(fh)
    entries = [e for e in meta.get("languages", []) if e.get("filename") not in
               ("chinese.milanguage", "english.milanguage", "english_original.milanguage")]
    entries.insert(0, {"name": "简体中文", "locale": "zh-CN", "filename": "chinese.milanguage"})
    entries.insert(1, {"name": "English", "locale": "en-US", "filename": "english_original.milanguage"})
    meta["languages"] = entries
    with open(meta_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent="\t")
        fh.write("\n")
    return lang_dir


def replace_fonts(root):
    """用仓库内的 CJK 字体替换上游拉丁字体。"""
    font_src = ROOT / FONT_DIR
    font_dst = root / "Data" / "Fonts"
    if not font_src.is_dir():
        raise SystemExit("仓库内缺少字体目录：%s" % font_src)
    if not font_dst.is_dir():
        raise SystemExit("上游包内缺少 Data/Fonts 目录")

    expected = {p.name for p in font_dst.iterdir() if p.suffix.lower() == ".ttf"}
    provided = {p.name for p in font_src.glob("*.ttf")}
    missing = sorted(expected - provided)
    if missing:
        raise SystemExit("字体缺失，界面会出现方块字：%s" % ", ".join(missing))

    replaced = []
    for name in sorted(expected):
        shutil.copyfile(font_src / name, font_dst / name)
        replaced.append(name)
    return replaced


def replace_particles(root):
    """用中文命名的粒子预设替换上游英文预设。"""
    src = ROOT / PARTICLE_DIR
    dst = root / "Particles"
    if not src.is_dir():
        raise SystemExit("仓库内缺少粒子目录：%s" % src)
    dst.mkdir(parents=True, exist_ok=True)

    removed = []
    for path in dst.glob("*.miparticles"):
        removed.append(path.name)
        path.unlink()

    added = []
    for path in sorted(src.glob("*.miparticles")):
        shutil.copyfile(path, dst / path.name)
        added.append(path.name)
    return removed, added


def check_keys(english_path, lang_path, allow_missing):
    en = flat_map(load_language(english_path))
    zh = flat_map(load_language(lang_path))
    missing = [k for k in en if k not in zh]
    extra = [k for k in zh if k not in en]
    if missing or extra:
        print("键比对：英文 %d / 中文 %d" % (len(en), len(zh)))
        for key in missing[:30]:
            print("  缺失 %s = %r" % (key, en[key]))
        for key in extra[:30]:
            print("  多余 %s = %r" % (key, zh[key]))
        if missing and not allow_missing:
            raise SystemExit("汉化文件缺少 %d 个键，已中止打包（可用 --allow-missing 强制打包）" % len(missing))
    return len(en), len(zh), len(missing), len(extra)


def zip_dir(src, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(src).as_posix())


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_notes(meta, lang_header, stats, out_zip, fonts, removed, added):
    size_mb = out_zip.stat().st_size / 1048576
    lines = [
        "## 汉化更新：Mine-imator 2.0.2 %s %s" % (meta["upstream_name"], meta["version"]),
        "",
        "**下载即中文**：解压后双击 `Mine-imator.exe` 直接就是简体中文界面。",
        "",
        "- 上游版本：`%s`（[发布页](https://github.com/%s/releases/tag/%s)）"
        % (meta["version"], meta["upstream_repo"], meta["upstream_tag"]),
        "- 汉化文件：`%s`" % Path(meta["translation"]).relative_to(ROOT).as_posix(),
        "- 汉化版本：`%s`，最后更新 `%s`" % (lang_header.get("forversion", "?"), lang_header.get("lastchange", "?")),
        "- 文本条目：英文 %d / 中文 %d，缺失 %d，多余 %d" % stats[:4],
        "- 已内置中文字体 %d 个（Noto / Rubik CJK，解决界面方块字）" % len(fonts),
        "- 已内置中文粒子预设 %d 个（移除英文预设 %d 个）" % (len(added), len(removed)),
        "",
        "### 使用方法",
        "",
        "1. 下载 `%s`（%.1f MB）并解压到任意目录；" % (out_zip.name, size_mb),
        "2. 双击 `Mine-imator.exe`，启动即为简体中文；",
        "3. 想用英文时，在 `设置 → 语言` 里选择 `English`。",
        "",
        "> 汉化仍在持续维护，翻译问题欢迎在 Issue 中反馈。",
        "",
        "SHA-256：`%s`" % sha256(out_zip),
    ]
    return "\n".join(lines) + "\n"


def cmd_resolve(args):
    meta = resolve(args.tag)
    write_outputs(
        {
            "build": meta["build"],
            "version": meta["version"],
            "upstream_repo": meta["upstream_repo"],
            "upstream_tag": meta["upstream_tag"],
            "upstream_name": meta["upstream_name"],
            "translation": Path(meta["translation"]).relative_to(ROOT).as_posix(),
            "release_tag": meta["release_tag"],
            "title": meta["title"],
        },
        args.out,
    )


def cmd_build(args):
    meta = resolve(args.tag)
    lang_path = Path(meta["translation"])
    lang_bytes = lang_path.read_bytes()
    header = load_language(lang_path).get("file/", {})

    upstream_zip = Path(args.zip)
    if not upstream_zip.exists():
        raise SystemExit("上游发行包不存在：%s" % upstream_zip)

    work = Path(args.work)
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    with zipfile.ZipFile(upstream_zip) as zf:
        zf.extractall(work)

    app_root = find_app_root(work)
    original_english = app_root / "Data" / "Languages" / "english.milanguage"
    original_bytes = original_english.read_bytes()

    patch_languages(app_root, lang_bytes)
    stats = check_keys(
        app_root / "Data" / "Languages" / "english_original.milanguage", lang_path, args.allow_missing
    )
    fonts = replace_fonts(app_root)
    removed, added = replace_particles(app_root)

    # 键比对基准用的是刚备份的原版英文，这里确保备份内容未被污染
    if (app_root / "Data" / "Languages" / "english_original.milanguage").read_bytes() != original_bytes:
        raise SystemExit("原版英文语言文件备份异常")

    out_dir = Path(args.out_dir)
    out_zip = out_dir / output_name(upstream_zip.name)
    zip_dir(work, out_zip)

    notes = build_notes(meta, header, stats, out_zip, fonts, removed, added)
    notes_path = Path(args.notes)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(notes, encoding="utf-8")

    print("上游发行包：%s" % upstream_zip.name)
    print("打包完成：%s（%.1f MB）" % (out_zip, out_zip.stat().st_size / 1048576))
    print("发布说明：%s" % notes_path)
    if args.gh_output:
        write_outputs({"asset": out_zip.as_posix(), "asset_name": out_zip.name}, args.gh_output)


def main():
    parser = argparse.ArgumentParser(description="Mine-imator 中文化发行包打包工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p_resolve = sub.add_parser("resolve", help="解析标签信息")
    p_resolve.add_argument("--tag", required=True)
    p_resolve.add_argument("--out", default="", help="写入 GITHUB_OUTPUT 的文件路径")
    p_resolve.set_defaults(func=cmd_resolve)

    p_build = sub.add_parser("build", help="生成中文化发行包")
    p_build.add_argument("--tag", required=True)
    p_build.add_argument("--zip", required=True, help="上游发行包路径")
    p_build.add_argument("--out-dir", required=True, help="成品输出目录")
    p_build.add_argument("--notes", required=True, help="输出发布说明路径")
    p_build.add_argument("--work", default=".pack-work", help="解压用临时目录")
    p_build.add_argument("--gh-output", default="", help="写入 GITHUB_OUTPUT 的文件路径")
    p_build.add_argument("--allow-missing", action="store_true", help="允许缺失键")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
