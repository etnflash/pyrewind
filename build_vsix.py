"""VS Code 확장 패키지(.vsix)를 만든다. Node.js / vsce 없이 파이썬만으로 동작.

    python build_vsix.py                 # dist/pyrewind-<버전>.vsix 생성
    python build_vsix.py --install       # 만든 뒤 VS Code 에 바로 설치 (code 명령 필요)

.vsix 는 zip 파일이며, 안에 extension/ 폴더와 매니페스트 두 개가 들어간다.
파이썬 트레이서(pyrewind)는 extension/python/ 아래에 함께 넣는다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "vscode-extension"
PKG = ROOT / "pyrewind"

CONTENT_TYPES = {
    ".json": "application/json", ".js": "application/javascript", ".md": "text/markdown",
    ".py": "text/plain", ".html": "text/html", ".txt": "text/plain", ".png": "image/png",
    ".vsixmanifest": "text/xml",
}


def files_to_pack() -> list[tuple[Path, str]]:
    out = []
    for f in sorted(EXT.rglob("*")):
        rel = f.relative_to(EXT).parts
        if f.is_file() and not {"node_modules", ".vscode", "test"} & set(rel):
            out.append((f, "extension/" + f.relative_to(EXT).as_posix()))
    for f in sorted(PKG.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            out.append((f, "extension/python/pyrewind/" + f.relative_to(PKG).as_posix()))
    out.append((ROOT / "LICENSE", "extension/LICENSE.txt"))
    return out


def manifest(pkg: dict) -> str:
    repo = pkg.get("repository", {}).get("url", "")
    links = ""
    if repo:
        links = (f'<Property Id="Microsoft.VisualStudio.Services.Links.Source" Value="{escape(repo)}" />'
                 f'<Property Id="Microsoft.VisualStudio.Services.Links.GitHub" Value="{escape(repo)}" />'
                 f'<Property Id="Microsoft.VisualStudio.Services.Links.Repository" Value="{escape(repo)}" />')
    if pkg.get("bugs", {}).get("url"):
        links += f'<Property Id="Microsoft.VisualStudio.Services.Links.Support" Value="{escape(pkg["bugs"]["url"])}" />'
    if pkg.get("homepage"):
        links += f'<Property Id="Microsoft.VisualStudio.Services.Links.Learn" Value="{escape(pkg["homepage"])}" />'
    banner = pkg.get("galleryBanner", {})
    if banner:
        links += (f'<Property Id="Microsoft.VisualStudio.Services.Branding.Color" Value="{escape(banner.get("color", ""))}" />'
                  f'<Property Id="Microsoft.VisualStudio.Services.Branding.Theme" Value="{escape(banner.get("theme", "dark"))}" />')
    icon = f"<Icon>extension/{pkg['icon']}</Icon>" if pkg.get("icon") else ""
    icon_asset = (f'<Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/{pkg["icon"]}" Addressable="true" />'
                  if pkg.get("icon") else "")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{pkg['name']}" Version="{pkg['version']}" Publisher="{pkg['publisher']}" />
    <DisplayName>{escape(pkg['displayName'])}</DisplayName>
    <Description xml:space="preserve">{escape(pkg['description'])}</Description>
    <Tags>{escape(','.join(pkg.get('keywords', [])))}</Tags>
    <Categories>{escape(','.join(pkg.get('categories', [])))}</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{pkg['engines']['vscode']}" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value="" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace" />
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value="" />
      {links}
    </Properties>
    <License>extension/LICENSE.txt</License>
    {icon}
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code" />
  </Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true" />
    <Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE.txt" Addressable="true" />
    {icon_asset}
  </Assets>
</PackageManifest>
"""


def content_types(paths: list[str]) -> str:
    exts = sorted({Path(p).suffix.lower() for p in paths if Path(p).suffix} | {".vsixmanifest"})
    rows = "".join(f'<Default Extension="{e}" ContentType="{CONTENT_TYPES.get(e, "application/octet-stream")}"/>' for e in exts)
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">{rows}</Types>')


def build() -> Path:
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8-sig"))
    files = files_to_pack()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"{pkg['name']}-{pkg['version']}.vsix"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("extension.vsixmanifest", manifest(pkg))
        z.writestr("[Content_Types].xml", content_types([a for _, a in files]))
        for src, arc in files:
            z.write(src, arc)
    print(f"만듦: {out}  (파일 {len(files)}개)")
    return out


def main() -> int:
    out = build()
    if "--install" in sys.argv:
        code = shutil.which("code") or shutil.which("code.cmd")
        if not code:
            print("code 명령을 찾을 수 없습니다. VS Code 에서 '확장 → … → VSIX에서 설치' 로 설치하세요.")
            return 1
        subprocess.run([code, "--install-extension", str(out), "--force"], check=True)
        print("설치 완료! VS Code 에서 'Developer: Reload Window' 를 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
