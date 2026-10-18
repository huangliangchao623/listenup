"""构建 Android APK（不依赖 Gradle）。

为什么手动调 aapt2 / d8 / apksigner：
  Gradle + Android Gradle Plugin 会去 Maven 拉几十个依赖，网络稍有波动就失败，
  而且版本冲突排查起来很费时间。这个应用只有一个 Activity、几个资源文件，
  直接用官方命令行工具链打包又快又可控。

流程：
  aapt2 compile → aapt2 link → javac → d8 → 塞进 APK → zipalign → apksigner
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
SDK = ROOT / "tools" / "android" / "sdk"
BUILD_TOOLS = SDK / "build-tools" / "34.0.0"
PLATFORM = SDK / "platforms" / "android-34" / "android.jar"
JAVA_HOME = Path(r"C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot")

BUILD = ANDROID / "build"
OUT = ROOT / "dist" / "android"
APK_NAME = "听力训练.apk"
KEYSTORE = ANDROID / "listenup.jks"
KS_PASS = "listenup2024"
KS_ALIAS = "listenup"


def tool(name: str) -> str:
    p = BUILD_TOOLS / name
    if not p.exists():
        raise SystemExit(f"缺少构建工具：{p}")
    return str(p)


def run(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["JAVA_HOME"] = str(JAVA_HOME)
    env["PATH"] = f"{JAVA_HOME / 'bin'};{env.get('PATH', '')}"
    env.setdefault("TEMP", str(ROOT / "tools" / "tmp"))
    env.setdefault("TMP", env["TEMP"])
    r = subprocess.run(args, cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, encoding="utf-8",
                       encoding="utf-8", errors="replace", env=env)
    if check and r.returncode != 0:
        print(f"  命令失败：{' '.join(args[:3])}…")
        print("  stdout:", (r.stdout or "")[-1500:])
        print("  stderr:", (r.stderr or "")[-1500:])
        raise SystemExit(1)
    return r


def main() -> int:
    print("=" * 60)
    print("  构建 Android APK")
    print("=" * 60)

    if not PLATFORM.exists():
        raise SystemExit(f"找不到 android.jar：{PLATFORM}")

    for d in ("compiled", "gen", "obj", "dex"):
        p = BUILD / d
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)

    # ---------- 1. 编译资源 ----------
    print("[1/7] aapt2 compile")
    res_zip = BUILD / "compiled" / "res.zip"
    run([tool("aapt2.exe"), "compile", "--dir",
         str(ANDROID / "res"), "-o", str(res_zip)])
    print(f"      {res_zip.stat().st_size:,} B")

    # ---------- 2. 链接资源，生成 R.java 和未签名 APK ----------
    print("[2/7] aapt2 link")
    apk_unsigned = BUILD / "app-unsigned.apk"
    gen = BUILD / "gen"
    run([
        tool("aapt2.exe"), "link",
        "-o", str(apk_unsigned),
        "-I", str(PLATFORM),
        "--manifest", str(ANDROID / "AndroidManifest.xml"),
        "--java", str(gen),
        "--min-sdk-version", "24",
        "--target-sdk-version", "34",
        str(res_zip),
    ])
    print(f"      {apk_unsigned.stat().st_size:,} B")

    # ---------- 3. 编译 Java ----------
    print("[3/7] javac")
    sources = [str(gen / "com" / "listenup" / "app" / "R.java")]
    sources += [str(p) for p in (ANDROID / "java").rglob("*.java")]
    obj = BUILD / "obj"
    run([
        str(JAVA_HOME / "bin" / "javac.exe"),
        "-source", "8", "-target", "8",
        "-bootclasspath", str(PLATFORM),
        "-encoding", "UTF-8",
        "-d", str(obj),
        *sources,
    ])
    classes = list(obj.rglob("*.class"))
    print(f"      {len(classes)} 个 class 文件")

    # ---------- 4. dex ----------
    print("[4/7] d8")
    dex_dir = BUILD / "dex"
    run([
        tool("d8.bat"),
        "--lib", str(PLATFORM),
        "--min-api", "24",
        "--output", str(dex_dir),
        *[str(c) for c in classes],
    ])
    dex = dex_dir / "classes.dex"
    print(f"      classes.dex {dex.stat().st_size:,} B")

    # ---------- 5. 把 dex 放进 APK ----------
    print("[5/7] 打包 classes.dex")
    with zipfile.ZipFile(apk_unsigned, "a", zipfile.ZIP_DEFLATED) as z:
        # 如果已存在同名条目，先移除（aapt2 不会生成它，稳妥起见）
        names = set(z.namelist())
        if "classes.dex" in names:
            z.remove("classes.dex")
        z.write(dex, "classes.dex")
    print(f"      {apk_unsigned.stat().st_size:,} B")

    # ---------- 6. zipalign ----------
    print("[6/7] zipalign")
    aligned = BUILD / "app-aligned.apk"
    if aligned.exists():
        aligned.unlink()
    run([tool("zipalign.exe"), "-f", "-p", "4",
         str(apk_unsigned), str(aligned)])
    print(f"      {aligned.stat().st_size:,} B")

    # ---------- 7. 签名 ----------
    print("[7/7] 签名")
    OUT.mkdir(parents=True, exist_ok=True)
    final = OUT / APK_NAME
    if final.exists():
        final.unlink()

    if not KEYSTORE.exists():
        print("      生成签名密钥…")
        run([
            str(JAVA_HOME / "bin" / "keytool.exe"),
            "-genkeypair", "-keystore", str(KEYSTORE),
            "-alias", KS_ALIAS,
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", KS_PASS, "-keypass", KS_PASS,
            "-dname", "CN=ListenUp, OU=ListenUp, O=ListenUp, L=Xian, ST=Shaanxi, C=CN",
        ])

    run([
        tool("apksigner.bat"), "sign",
        "--ks", str(KEYSTORE),
        "--ks-pass", f"pass:{KS_PASS}",
        "--key-pass", f"pass:{KS_PASS}",
        "--ks-key-alias", KS_ALIAS,
        "--out", str(final),
        str(aligned),
    ])

    # 验证签名
    v = run([tool("apksigner.bat"), "verify", "--verbose", str(final)], check=False)
    ok = "Verifies" in (v.stdout or "") or v.returncode == 0

    print()
    print("=" * 60)
    print(f"  构建完成：{final}")
    print(f"  大小：{final.stat().st_size:,} B "
          f"({final.stat().st_size / 1048576:.1f} MB)")
    print(f"  签名校验：{'通过' if ok else '未通过'}")
    print("=" * 60)
    if not ok:
        print(v.stdout[-800:])
        print(v.stderr[-800:])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
