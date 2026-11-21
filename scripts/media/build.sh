#!/usr/bin/env bash
# Regenerate docs/images/{banner.svg,banner.png,social-preview.png,demo.gif}.
# Needs: python3, Pillow (or uv), and Google Chrome/Chromium for the PNG render.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"; IMG="$REPO/docs/images"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
PY="python3"; python3 -c "import PIL" 2>/dev/null || PY="uvx --with pillow python"
CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser)}"

render() {  # name width height output.png
  python3 "$HERE/banner.py" "$2" "$3" > "$WORK/$1.svg"
  printf '<html><body style="margin:0">%s</body></html>' "$(cat "$WORK/$1.svg")" > "$WORK/$1.html"
  "$CHROME" --headless=new --disable-gpu --no-sandbox --hide-scrollbars --force-device-scale-factor=2 \
    --window-size="$2,$3" --screenshot="$WORK/$1.png" "file://$WORK/$1.html" 2>/dev/null
  $PY -c "
from PIL import Image; im=Image.open('$WORK/$1.png').convert('RGB')
im.quantize(palette=im.quantize(colors=256,method=Image.Quantize.FASTOCTREE),dither=Image.Dither.FLOYDSTEINBERG).save('$4',optimize=True)"
}
python3 "$HERE/banner.py" 1280 360 > "$IMG/banner.svg"
render banner 1280 360 "$IMG/banner.png"
render social 1280 640 "$IMG/social-preview.png"

python3 "$HERE/demo_capture.py" "$WORK/demo" > /dev/null
$PY "$HERE/demo_render.py" "$WORK/demo/scenes.json" "$IMG/demo.gif"
