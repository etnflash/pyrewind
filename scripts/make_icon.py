"""확장 아이콘(vscode-extension/icon.png, 128x128)을 만든다.  python scripts/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 512  # 크게 그린 뒤 줄여서 가장자리를 부드럽게


def main() -> None:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # 배경: 위→아래로 짙어지는 파란 둥근 사각형
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        gd.line([(0, y), (S, y)], fill=(int(48 - 20 * t), int(105 - 45 * t), int(152 - 40 * t), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([16, 16, S - 16, S - 16], radius=96, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    yellow, light = (255, 212, 59, 255), (255, 255, 255, 235)
    cy, h = S // 2, 120
    # 되감기 삼각형 두 개 (◀◀)
    d.polygon([(250, cy - h), (250, cy + h), (110, cy)], fill=light)
    d.polygon([(400, cy - h), (400, cy + h), (260, cy)], fill=yellow)
    # 아래쪽 진행 막대 = 실행 단계
    for i, x in enumerate(range(110, 410, 60)):
        d.rounded_rectangle([x, 410, x + 44, 432], radius=10, fill=yellow if i < 3 else (255, 255, 255, 90))

    out = ROOT / "vscode-extension" / "icon.png"
    img.resize((128, 128), Image.LANCZOS).save(out)
    print("만듦:", out)


if __name__ == "__main__":
    main()
