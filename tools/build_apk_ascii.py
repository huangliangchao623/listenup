"""在纯英文路径下构建 APK。

## 为什么需要绕这一圈

Android 的构建工具（aapt2 / d8 / javac）对**非 ASCII 路径**支持很差。
本项目安装在 `C:\\英语听力训练\\`，直接构建会报：

    aapt2.exe compile --dir C:\\英语听力训练\\构建产物\\android\\res
    error: failed to open directory: 系统找不到指定的文件。 (2)

目录明明存在，是工具自己处理不了中文路径。

所以先复制到 `C:\\apkbuild`（纯英文），在那里构建，最后把 APK 拷回来。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_ANDROID = ROOT / "构建产物" / "android"
WORK = Path("C:/apkbuild")            # 必须是纯英文路径
SDK = ROOT / "tools" / "android" / "sdk"
BT = SDK / "build-tools" / "34.0.0"
PLATFORM = SDK / "platforms" / "android-34" / "android.jar"
JAVA_HOME = Path(r"C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot")

KS = "listenup.jks"
KS_PASS = "listenup2024"
KS_ALIAS = "listenup"
OUT_NAME = "听力训练-v1.2.apk"


def run(args, cwd=None, check=True):
    env = dict(os.environ)
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = f"{JAVA_HOME / 'bin'};{env.get('PATH', '')}"
    env["TEMP"] = "C:/apkbuild/tmp"
    env["TMP"] = env["TEMP"]
    r = subprocess.run(args, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if check and r.returncode != 0:
        print(f"  命令失败: {' '.join(str(a) for a in args[:3])}")
        print("  stdout:", (r.stdout or "")[-1200:])
        print("  stderr:", (r.stderr or "")[-1200:])
        raise SystemExit(1)
    return r


def main() -> int:
    print("=" * 56)
    print("  构建 APK（英文路径）")
    print("=" * 56)

    if not SRC_ANDROID.exists():
        raise SystemExit(f"找不到 android 源码: {SRC_ANDROID}")
    if not PLATFORM.exists():
        raise SystemExit(f"找不到 android.jar: {PLATFORM}")

    print(f"[1/6] 准备英文工作目录 {WORK}")
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    (WORK / "tmp").mkdir(parents=True)
    shutil.copytree(SRC_ANDROID / "java", WORK / "java")
    shutil.copytree(SRC_ANDROID / "res", WORK / "res")
    shutil.copy2(SRC_ANDROID / "AndroidManifest.xml", WORK / "AndroidManifest.xml")
    shutil.copy2(SRC_ANDROID / KS, WORK / KS)
    (WORK / "gen").mkdir()

    print("[2/6] aapt2 compile")
    res_zip = WORK / "res.zip"
    run([str(BT / "aapt2.exe"), "compile", "--dir", str(WORK / "res"),
         "-o", str(res_zip)])

    print("[3/6] aapt2 link")
    apk_unsigned = WORK / "app-unsigned.apk"
    run([str(BT / "aapt2.exe"), "link",
         "-o", str(apk_unsigned), "-I", str(PLATFORM),
         "--manifest", str(WORK / "AndroidManifest.xml"),
         "--java", str(WORK / "gen"),
         "--min-sdk-version", "24", "--target-sdk-version", "34",
         str(res_zip)])

    print("[4/6] javac + d8")
    sources = [str(p) for p in (WORK / "gen").rglob("*.java")]
    sources += [str(p) for p in (WORK / "java").rglob("*.java")]
    run([str(JAVA_HOME / "bin" / "javac.exe"),
         "-source", "8", "-target", "8",
         "-bootclasspath", str(PLATFORM),
         "-encoding", "UTF-8", "-d", str(WORK / "obj"), *sources])
    classes = [str(p) for p in (WORK / "obj").rglob("*.class")]
    (WORK / "dex").mkdir(exist_ok=True)
    run([str(BT / "d8.bat"), "--lib", str(PLATFORM), "--min-api", "24",
         "--output", str(WORK / "dex"), *classes])

    print("[5/6] 打包 + 对齐")
    import zipfile
    with zipfile.ZipFile(apk_unsigned, "a", zipfile.ZIP_DEFLATED) as z:
        if "classes.dex" in z.namelist():
            z.remove("classes.dex")
        z.write(WORK / "dex" / "classes.dex", "classes.dex")
    aligned = WORK / "app-aligned.apk"
    run([str(BT / "zipalign.exe"), "-f", "-p", "4",
         str(apk_unsigned), str(aligned)])

    print("[6/6] 签名")
    final_work = WORK / OUT_NAME
    run([str(BT / "apksigner.bat"), "sign",
         "--ks", str(WORK / KS), "--ks-pass", f"pass:{KS_PASS}",
         "--key-pass", f"pass:{KS_PASS}", "--ks-key-alias", KS_ALIAS,
         "--out", str(final_work), str(aligned)])

    v = run([str(BT / "apksigner.bat"), "verify", "--verbose", str(final_work)],
            check=False)
    ok = "Verifies" in (v.stdout or "")

    # 拷到项目目录
    out_dir = ROOT / "构建产物"
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / OUT_NAME
    shutil.copy2(final_work, final)

    print()
    print("=" * 56)
    print(f"  ✓ {final}")
    print(f"  大小 {final.stat().st_size:,} B  "
          f"({final.stat().st_size / 1024:.1f} KB)")
    print(f"  签名 {'通过' if ok else '未通过'}")
    print("=" * 56)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
