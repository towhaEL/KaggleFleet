"""Render scenes.json from demo_capture.py into docs/images/demo.gif.

Usage: python scripts/media/demo_render.py WORK_DIR/scenes.json docs/images/demo.gif   (needs Pillow)
"""
import json, re, sys
from PIL import Image, ImageDraw, ImageFont
scenes = json.load(open(sys.argv[1])); out = sys.argv[2]
FS=16; F=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",FS)
FB=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",FS)
CW=F.getlength("M"); LH=22; COLS=96; ROWS=22; PAD=22; BAR=36
W=int(COLS*CW+2*PAD); H=BAR+ROWS*LH+2*PAD-6
BG=(15,17,26); BAR_BG=(28,31,44); FG=(214,219,232); DIM=(110,118,140)
GREEN=(94,214,151); YEL=(240,196,92); CYAN=(110,190,255); VIOLET=(176,148,255); RED=(240,110,110)
def style(line):
    """Return list of (text, color, bold) segments."""
    if line.startswith("\x00$"):
        cmd=line[2:]
        if cmd.startswith("#"): return [("$ ",GREEN,True),(cmd,DIM,False)]
        return [("$ ",GREEN,True),(cmd,FG,True)]
    if line.startswith("[+]"): return [("[+]",GREEN,True),(line[3:],FG,False)]
    if re.match(r"^-+\+",line): return [(line,DIM,False)]
    if " | " in line and ("Account ID" in line or "Notebook File" in line): return [(line,CYAN,True)]
    if line.startswith(("Configured","Discovered","Job Status for")): return [(line,VIOLET,True)]
    if line.startswith("[") and "s]" in line[:9]:
        i=line.index("]")+1; return [(line[:i],DIM,False),(line[i:],FG,False)]
    m=re.match(r"^(\s+Status:\s+)(\w+)",line)
    if m:
        c={"RUNNING":YEL,"QUEUED":YEL,"COMPLETE":GREEN,"DOWNLOADED":GREEN,"ERROR":RED}.get(m.group(2),FG)
        return [(m.group(1),DIM,False),(m.group(2),c,True),(line[m.end():],FG,False)]
    m=re.match(r"^(\s+[A-Za-z ]+:)(.*)$",line)
    if m: return [(m.group(1),DIM,False),(m.group(2),FG,False)]
    return [(line,FG,False)]
def frame(lines, cursor=False):
    im=Image.new("RGB",(W,H),BG); d=ImageDraw.Draw(im)
    d.rectangle([0,0,W,BAR],fill=BAR_BG)
    for i,c in enumerate([(237,106,94),(245,191,79),(98,197,84)]):
        d.ellipse([16+i*22,BAR//2-6,28+i*22,BAR//2+6],fill=c)
    t="kfleet — ~/vision-paper"; d.text(((W-F.getlength(t))/2,BAR//2-9),t,font=F,fill=DIM)
    view=lines[-ROWS:]
    for r,line in enumerate(view):
        x=PAD; y=BAR+PAD-4+r*LH
        for text,col,b in style(line):
            text=text[:COLS]; d.text((x,y),text,font=FB if b else F,fill=col); x+=CW*len(text)
        if cursor and r==len(view)-1:
            d.rectangle([x+1,y+2,x+CW-1,y+LH-3],fill=FG)
    return im
frames=[]; durs=[]
def add(lines,ms,cursor=False): frames.append(frame(lines,cursor)); durs.append(ms)
buf=[]
for i,s in enumerate(scenes):
    if s.get("clear"): buf=[]
    if s.get("comment"):
        buf.append("\x00$"+s["comment"]); add(buf,900); buf.append("")
    buf.append("\x00$"); add(buf,500,True)
    cmd=s["cmd"]
    for k in range(1,len(cmd)+1,2):
        buf[-1]="\x00$"+cmd[:k]; add(buf,40,True)
    buf[-1]="\x00$"+cmd; add(buf,350,True)
    outl=s["out"].splitlines()
    buf.extend(outl); buf.append("")
    add(buf, int(min(3800, 1200+160*len(outl))))
buf.append("\x00$"); add(buf,2500,True)
# GIF: shared palette keeps size small
keys=[f for f,d in zip(frames,durs) if d>=900]
sheet=Image.new("RGB",(W,H*len(keys)))
for i,f in enumerate(keys): sheet.paste(f,(0,H*i))
pal=sheet.quantize(colors=128,method=Image.Quantize.FASTOCTREE)
q=[f.quantize(palette=pal,dither=Image.Dither.NONE) for f in frames]
q[0].save(out,save_all=True,append_images=q[1:],duration=durs,loop=0,optimize=True,disposal=1)
print(W,H,len(frames),sum(durs)/1000,"s")
