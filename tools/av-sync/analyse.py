import sys
def load(p):
    F=[]; A=[]
    for l in open(p):
        t=l.split()
        if t[0]=='F': F.append(int(t[1]))
        elif t[0]=='A': A.append((int(t[1]), float(t[2])))
    return F, A
def onsets(A):
    if not A: return []
    lv=sorted(r for _,r in A); beep=lv[int(len(lv)*0.995)]   # loud blocks are ~3% of time
    hi, lo = 0.5*beep, 0.15*beep; out=[]; quiet=0
    for t,r in A:
        if r>hi and quiet>=200: out.append(t)
        quiet = quiet+1 if r<lo else 0
    return out, beep
res={}
for name,p in (("INPUT  (camera 1 flows)",sys.argv[1]),("OUTPUT (programme flows)",sys.argv[2])):
    F,A=load(p); B,beep=onsets(A); o=[]
    for b in B:
        f=min(F,key=lambda x:abs(x-b)) if F else None
        if f is not None and abs(b-f)<450e6: o.append((b-f)/1e6)
    o.sort(); res[name]=(F,B)
    print(f"{name}: {len(F)} flashes, {len(B)} beeps (beep level {beep:.3f}); audio-minus-video " + (f"median {o[len(o)//2]:+.1f} ms, range {o[0]:+.1f} .. {o[-1]:+.1f} ms over {len(o)} pairs" if o else "n/a"))
(Fi,Bi),(Fo,Bo)=res.values()
def lat(a,b): l=sorted((min(b,key=lambda x:abs(x-v))-v)/1e6 for v in a if b); return l[len(l)//2] if l else float('nan')
print(f"latency input->programme: video {lat(Fi,Fo):.1f} ms, audio {lat(Bi,Bo):.1f} ms")
