"""Generate the README banner / social preview SVG.

Usage: python scripts/media/banner.py 1280 360 > docs/images/banner.svg   (1280 640 for the social preview)
See build.sh for the PNG render.
"""
import sys
W,H = int(sys.argv[1]), int(sys.argv[2]); social = H > 500
oy = (H-360)//2  # vertical offset for the content block
def card(x,y,user,gpu,state,color):
    return f'''
  <g transform="translate({x},{y})">
    <rect width="250" height="64" rx="12" fill="#161a2b" stroke="#2c3350"/>
    <circle cx="30" cy="32" r="15" fill="#22283f"/>
    <text x="30" y="37.5" text-anchor="middle" class="t" font-size="15" font-weight="700" fill="#c9cff0">{user[0].upper()}</text>
    <text x="56" y="28" class="t" font-size="16" font-weight="600" fill="#e8ebf7">{user}</text>
    <text x="56" y="48" class="m" font-size="12.5" fill="#8a92b2">{gpu}</text>
    <circle cx="170" cy="32" r="5" fill="{color}"/>
    <text x="181" y="36.5" class="m" font-size="12" fill="{color}">{state}</text>
  </g>'''
nx, ny = 760, oy+148
cards = [(980, oy+60, "alice", "GPU · T4", "RUNNING", "#f0c45c"),
         (980, oy+148, "bob", "GPU · P100", "QUEUED", "#8fa3ff"),
         (980, oy+236, "chen", "TPU · v5e-8", "COMPLETE", "#5ed697")]
paths = "".join(
    f'<path d="M{nx+130},{ny+32} C{nx+175},{ny+32} {x-45},{y+32} {x},{y+32}" stroke="url(#wire)" stroke-width="2.2" fill="none"/>'
    f'<circle cx="{x}" cy="{y+32}" r="3.5" fill="#5ed697"/>'
    for x,y,*_ in cards)
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0d1020"/><stop offset="1" stop-color="#141833"/>
    </linearGradient>
    <radialGradient id="glow1" cx="0.78" cy="0.45" r="0.45">
      <stop offset="0" stop-color="#6d5dfc" stop-opacity="0.28"/><stop offset="1" stop-color="#6d5dfc" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glow2" cx="0.08" cy="0.05" r="0.5">
      <stop offset="0" stop-color="#2fbf8a" stop-opacity="0.14"/><stop offset="1" stop-color="#2fbf8a" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="wire" gradientUnits="userSpaceOnUse" x1="890" y1="0" x2="980" y2="0"><stop offset="0" stop-color="#8b7bff"/><stop offset="1" stop-color="#5ed697"/></linearGradient>
    <linearGradient id="title" x1="0" x2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#c8c0ff"/></linearGradient>
    <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
      <path d="M32 0H0V32" fill="none" stroke="#ffffff" stroke-opacity="0.035"/>
    </pattern>
    <style>
      .t {{ font-family: 'Inter','Noto Sans','Segoe UI',Helvetica,Arial,sans-serif; }}
      .m {{ font-family: 'JetBrains Mono','DejaVu Sans Mono',Menlo,Consolas,monospace; }}
    </style>
  </defs>
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  <rect width="{W}" height="{H}" fill="url(#grid)"/>
  <rect width="{W}" height="{H}" fill="url(#glow1)"/>
  <rect width="{W}" height="{H}" fill="url(#glow2)"/>

  <g transform="translate(80,{oy})">
    <rect x="0" y="74" width="196" height="30" rx="15" fill="#6d5dfc" fill-opacity="0.16" stroke="#8b7bff" stroke-opacity="0.5"/>
    <text x="98" y="94" text-anchor="middle" class="m" font-size="13" fill="#c8c0ff">open source · MIT</text>
    <text x="-3" y="176" class="t" font-size="74" font-weight="700" fill="url(#title)" letter-spacing="-1.5">KaggleFleet</text>
    <text x="0" y="222" class="t" font-size="23" fill="#b7bdd6">Run local Jupyter notebooks on Kaggle GPUs</text>
    <text x="0" y="252" class="t" font-size="23" fill="#b7bdd6">across your research team's own accounts.</text>
    <text x="0" y="300" class="m" font-size="15" fill="#5ed697">$ <tspan fill="#8a92b2">pipx install kaggle-fleet</tspan></text>
  </g>

  {paths}
  <g transform="translate({nx},{ny})">
    <rect width="130" height="64" rx="12" fill="#1b1f36" stroke="#6d5dfc" stroke-opacity="0.7"/>
    <rect x="16" y="16" width="26" height="32" rx="4" fill="none" stroke="#c8c0ff" stroke-width="2"/>
    <path d="M22 26h14M22 32h14M22 38h9" stroke="#c8c0ff" stroke-width="2" stroke-linecap="round"/>
    <text x="54" y="29" class="m" font-size="13" fill="#e8ebf7">train</text>
    <text x="54" y="46" class="m" font-size="13" fill="#8a92b2">.ipynb</text>
  </g>
  {"".join(card(*c) for c in cards)}
  {'<text x="80" y="%d" class="t" font-size="15" fill="#6c7393">CLI · local web panel · JSON for AI agents  —  not affiliated with Kaggle or Google</text>' % (H-50) if social else ''}
</svg>'''
print(svg)
