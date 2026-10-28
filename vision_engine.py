# -*- coding: utf-8 -*-
"""Conservative CV for user-supplied Trickster tower footage. No trained detector.
This is a heuristic, not a guarantee of collision-free navigation.
"""
import math
import time
from pathlib import Path
import cv2
import numpy as np

HERE=Path(__file__).resolve().parent

def rect_overlap(a,b):
    ax,ay,aw,ah=a;bx,by,bw,bh=b
    ix=max(0,min(ax+aw,bx+bw)-max(ax,bx));iy=max(0,min(ay+ah,by+bh)-max(ay,by))
    return ix*iy/max(1,min(aw*ah,bw*bh))

def nms(items, dist=65):
    keep=[]
    for c in sorted(items,key=lambda o:o['confidence'], reverse=True):
        if all(math.hypot(c['x']-k['x'],c['y']-k['y'])>dist for k in keep):keep.append(c)
    return keep

class VisionEngine:
    def __init__(self):
        self.portal_template=cv2.imread(str(HERE/'templates'/'portal1.png'),cv2.IMREAD_GRAYSCALE)
        self.coin_template=cv2.imread(str(HERE/'assets'/'coin_gold_stack.png'))
        self.ghost_templates=[cv2.imread(str(p),cv2.IMREAD_GRAYSCALE) for p in sorted((HERE/'assets').glob('ghost_*.png')) if p.stem not in ('ghost_1','ghost_9')]
        self.ghost_templates=[x for x in self.ghost_templates if x is not None]
        self.turtle_templates=[(cv2.imread(str(HERE/'templates'/f'turtle{i}.png'),cv2.IMREAD_GRAYSCALE),0) for i in (1,2)]
        self.turtle_templates += [(cv2.imread(str(HERE/'assets'/name),cv2.IMREAD_GRAYSCALE),foot) for name,foot in [('turtle_shell_ref.png',35),('turtle_shell_large_ref.png',35),('turtle_front_ref.png',0)]]
        self.obstacle_templates=[]
        for kind in ('stairs','skull'):
            for path in sorted((HERE/'assets').glob(kind+'_*.png')):
                ref=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
                if ref is not None:self.obstacle_templates.append((kind,ref))

    def portal_candidates(self,frame):
        h,w=frame.shape[:2]; scale=1.0
        # The exact blue ring is much more discriminative than cyan color alone:
        # wheel flashes, pillars and buff auras otherwise look like portals.
        if self.portal_template is None:return []
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
        pts=[]
        # Window width is not sprite scale. Check several actual sprite sizes.
        for mult in (.72,.86,1.0,1.13):
            temp=cv2.resize(self.portal_template,None,fx=mult,fy=mult,interpolation=cv2.INTER_AREA)
            th,tw=temp.shape[:2]
            if th>=h or tw>=w:continue
            corr=cv2.matchTemplate(gray,temp,cv2.TM_CCOEFF_NORMED)
            for _ in range(4):
                _,score,_,loc=cv2.minMaxLoc(corr)
                if score<.62:break
                x,y=loc
                roi=hsv[y:y+th,x:x+tw]
                cyan=cv2.inRange(roi,(83,55,115),(114,255,255))
                valid=np.count_nonzero(cyan)>=max(65,roi.shape[0]*roi.shape[1]*.026)
                if valid:
                    pts.append({'x':int(x+tw/2),'y':int(y+th*.62),
                                'radius':int(max(tw,th)*.60+65),
                                'box':[x,y,tw,th],'kind':'portal','confidence':float(score)})
                cv2.rectangle(corr,(max(0,x-tw//2),max(0,y-th//2)),
                              (min(corr.shape[1]-1,x+tw),min(corr.shape[0]-1,y+th)),0,-1)
        return nms(pts,90)

    def ghost_candidates(self,frame):
        h,w=frame.shape[:2]; scale=1.0
        # Low resolution match to reduce CPU load; ghost shape rarely matches strongly.
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        half=cv2.resize(gray,(max(1,w//2),max(1,h//2)),interpolation=cv2.INTER_AREA)
        result=[]
        for source in self.ghost_templates:
            for k in (.72,.85,1.0,1.15):
                ww=max(9,round(source.shape[1]*scale*k/2));hh=max(14,round(source.shape[0]*scale*k/2))
                if ww>=half.shape[1] or hh>=half.shape[0]:continue
                temp=cv2.resize(source,(ww,hh),interpolation=cv2.INTER_AREA)
                if np.std(temp)<8:continue
                corr=cv2.matchTemplate(half,temp,cv2.TM_CCOEFF_NORMED)
                for _ in range(18):
                    _,score,_,loc=cv2.minMaxLoc(corr)
                    if score<.79:break
                    x,y=loc;xx=int(2*(x+ww/2));yy=int(2*(y+hh))
                    if 90<yy<h-20 and 20<xx<w-20:
                        result.append({'x':xx,'y':yy,'confidence':min(.89,float(score)),
                                       'kind':'ghost','box':[2*x,2*y,2*ww,2*hh]})
                    cv2.rectangle(corr,(max(0,x-ww//2),max(0,y-hh//2)),(min(corr.shape[1]-1,x+ww),min(corr.shape[0]-1,y+hh)),0,-1)
        result += self._ghost_heads(frame)
        return nms(result,55)

    def _ghost_heads(self,frame):
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        half=cv2.resize(gray,None,fx=.5,fy=.5)
        out=[]
        for name in ('ghost_ref_a.png','ghost_ref_b.png','ghost_ref_c.png','ghost_back_ref.png','ghost_2.png','ghost_6.png','ghost_7.png','ghost_8.png'):
            src=cv2.imread(str(HERE/'assets'/name),cv2.IMREAD_GRAYSCALE)
            if src is None:continue
            src=src[:max(25,int(src.shape[0]*.57)),:]
            for scale in (.72,.85,.95,1.,1.05,1.15,1.3):
                temp=cv2.resize(src,None,fx=scale*.5,fy=scale*.5)
                th,tw=temp.shape
                if th>=half.shape[0] or tw>=half.shape[1]:continue
                corr=cv2.matchTemplate(half,temp,cv2.TM_CCOEFF_NORMED)
                for _ in range(20):
                    _,score,_,loc=cv2.minMaxLoc(corr)
                    if score<.81:break
                    x,y=loc;bx,by=2*x,2*y
                    # Head match is contextualized by a whole expected body.
                    bh=round(th*2/ .57);bw=tw*2
                    if by>55 and by+bh<frame.shape[0]-5:
                        out.append(dict(x=bx+bw//2,y=by+bh,box=[bx,by,bw,bh],kind='ghost',confidence=float(score)))
                    cv2.rectangle(corr,(max(0,x-tw//2),max(0,y-th//2)),(x+tw,y+th),0,-1)
        return out

    def obstacle_candidates(self,frame):
        """Mark explicit stairs/skull silhouettes as no-travel zones.
        Conservative candidate, not a semantic/guaranteed collision map.
        """
        h,w=frame.shape[:2]; scale=1.0
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        reduced=cv2.resize(gray,(max(1,w//2),max(1,h//2)))
        results=[]
        for kind,t in self.obstacle_templates:
            for k in (.72,.86,1.,1.15):
                src=cv2.resize(t,(max(16,round(t.shape[1]*scale*k/2)),
                                       max(16,round(t.shape[0]*scale*k/2))))
                if src.shape[0]>=reduced.shape[0] or src.shape[1]>=reduced.shape[1]:continue
                r=cv2.matchTemplate(reduced,src,cv2.TM_CCOEFF_NORMED)
                for _ in range(2):
                    _,score,_,loc=cv2.minMaxLoc(r)
                    # Lower threshold blocks movement more often; never proves path safe.
                    threshold=.71 if kind=='stairs' else .73
                    if score<threshold:break
                    x,y=loc;th,tw=src.shape
                    cx=int((x+tw/2)*2);cy=int((y+th/2)*2)
                    rad=int((max(tw,th)*1.5+70*scale))
                    results.append({'x':cx,'y':cy,'radius':rad,
                                    'box':[2*x,2*y,2*tw,2*th],
                                    'kind':kind,'confidence':float(score)})
                    cv2.rectangle(r,(max(0,x-tw//2),max(0,y-th//2)),
                                  (min(r.shape[1]-1,x+tw),min(r.shape[0]-1,y+th)),0,-1)
        return nms(results,int(95*scale))

    def coin_candidates(self,frame,center):
        """Find **nearby gold stacks only** using appearance + local warm-gold pixels.

        The previous bronze template often did not match the bright coin sprite,
        despite the user having gold on the floor. We now look for groups of
        small yellow pixels near the registered character's feet and reject
        isolated sparkles. Screen coordinates do not imply ground distance.
        """
        h,w=frame.shape[:2]
        fx,fy=int(center[0]),int(center[1]+43)
        rx,ry=108,98
        x0,x1=max(0,fx-rx),min(w,fx+rx)
        y0,y1=max(0,fy-ry),min(h,fy+ry)
        if x1-x0<45 or y1-y0<45:return []
        roi=frame[y0:y1,x0:x1]
        hsv=cv2.cvtColor(roi,cv2.COLOR_BGR2HSV)
        # Gold piles are warm yellow/orange. Grey ghosts, purple drops and
        # blue dungeon details have almost no pixels in this band.
        warm=cv2.inRange(hsv,(8,75,95),(37,255,255))
        warm=cv2.morphologyEx(warm,cv2.MORPH_OPEN,np.ones((2,2),np.uint8))
        # Stacked coins form separated small rings; bridge 5px gaps.
        grouped=cv2.morphologyEx(warm,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
        count,labels,stats,cents=cv2.connectedComponentsWithStats(grouped,8)
        out=[]
        for i in range(1,count):
            x,y,bw,bh,area=map(int,stats[i])
            if area<105 or bw<12 or bh<11 or bw>89 or bh>76:continue
            xx,yy=x0+x+bw//2,y0+y+bh//2
            # Ellipse centered on the character's feet, not the whole screen.
            if ((xx-fx)/92.)**2+((yy-fy)/83.)**2>1:continue
            tile=warm[y:y+bh,x:x+bw]
            golden=int(np.count_nonzero(tile))
            density=golden/max(1,bw*bh)
            if golden<68 or density<.16:continue
            # Reject yellow avatar accents near the registered body position.
            if abs(xx-int(center[0]))<30 and yy<int(center[1])+35:continue
            gold_hsv=hsv[y:y+bh,x:x+bw]
            dark_gold=int(np.count_nonzero((gold_hsv[:,:,0]>=8)&(gold_hsv[:,:,0]<=37)&
                (gold_hsv[:,:,1]>75)&(gold_hsv[:,:,2]>95)&(gold_hsv[:,:,2]<170)))
            # Bright damage numbers are yellow but lack brown coin outlines.
            if dark_gold/max(1,golden)<.10:continue
            # Lower clusters are more likely to be loose floor gold than
            # yellow headgear; still a heuristic and never triggers walking.
            score=min(.97,.52+.005*min(65,golden)+.16*min(1,density))
            out.append({'x':xx,'y':yy,'kind':'coin','confidence':round(float(score),3),
                        'gold_pixels':golden})
        # If color segmentation fails, retain *high confidence* sprite matching
        # strictly within the same near-feet ellipse as a secondary path.
        if self.coin_template is not None and not out:
            for k in (.75,1.,1.25):
                tw=max(12,round(self.coin_template.shape[1]*k))
                th=max(13,round(self.coin_template.shape[0]*k))
                if tw>=roi.shape[1] or th>=roi.shape[0]:continue
                ref=cv2.resize(self.coin_template,(tw,th),interpolation=cv2.INTER_AREA)
                corr=cv2.matchTemplate(roi,ref,cv2.TM_CCOEFF_NORMED)
                _,score,_,loc=cv2.minMaxLoc(corr)
                xx,yy=x0+loc[0]+tw//2,y0+loc[1]+th//2
                if score>=.76 and ((xx-fx)/92.)**2+((yy-fy)/83.)**2<=1:
                    patch=warm[loc[1]:loc[1]+th,loc[0]:loc[0]+tw]
                    if patch.size and np.count_nonzero(patch)>=65:
                        out.append({'x':xx,'y':yy,'kind':'coin','confidence':round(float(score),3)})
        return nms(out,26)

    def _turtle_shapes(self,frame):
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        small=cv2.resize(gray,None,fx=.5,fy=.5)
        result=[]
        for source,foot in self.turtle_templates:
            if source is None:continue
            for scale in (.55,.72,.85,1.,1.15,1.3,1.5):
                temp=cv2.resize(source,None,fx=scale*.5,fy=scale*.5)
                th,tw=temp.shape
                if th>=small.shape[0] or tw>=small.shape[1]:continue
                corr=cv2.matchTemplate(small,temp,cv2.TM_CCOEFF_NORMED)
                for _ in range(16):
                    _,score,_,loc=cv2.minMaxLoc(corr)
                    if score<.67:break
                    x,y=loc
                    result.append(dict(x=2*x+tw,y=2*(y+th)+round(foot*scale),kind='turtle',confidence=float(score),box=[2*x,2*y,2*tw,2*th]))
                    cv2.rectangle(corr,(max(0,x-tw//2),max(0,y-th//2)),(x+tw,y+th),0,-1)
        return result

    @staticmethod
    def on_ground(frame,m):
        h,w=frame.shape[:2]
        x,y,bw,bh=map(int,m['box'])
        if x<0 or y<0 or x+bw>w or y+bh>h or y<55:return False
        roi=frame[y:y+bh,x:x+bw]
        if roi.size==0:return False
        # Black eyes/hands are bright details embedded in a contiguous void;
        # a correlation peak by itself must never authorize a mob or movement.
        if np.mean(np.max(roi,axis=2)<32)>.20:return False
        foot=frame[max(0,m['y']-7):min(h,m['y']+8),max(0,m['x']-12):min(w,m['x']+13)]
        return foot.size>100

    def analyze(self,frame):
        portals=self.portal_candidates(frame)
        obstacles=self.obstacle_candidates(frame)
        monsters=nms(self._turtle_shapes(frame)+self.ghost_candidates(frame),65)
        monsters=[m for m in monsters if self.on_ground(frame,m)
                  and not any(z['kind']=='portal' and rect_overlap(m['box'],z['box'])>.35 for z in portals)]
        return {'monsters':monsters,'portals':portals,'obstacles':obstacles}

class BackgroundTracker:
    def __init__(self):
        self.prev=None;self.world=np.zeros(2,float);self.good=0;self.bad=0
        self.confident=False;self.valid_updates=0;self.last_step=0.;self.last_valid=0.
    def reset(self):
        self.prev=None;self.world[:]=0;self.good=0;self.bad=0
        self.confident=False;self.valid_updates=0;self.last_step=0.;self.last_valid=0.
    def snapshot(self):return (float(self.world[0]),float(self.world[1]),self.valid_updates)
    def update(self,frame,center):
        h,w=frame.shape[:2]
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        tiny=cv2.resize(gray,(w//2,h//2),interpolation=cv2.INTER_AREA)
        if self.prev is None or self.prev.shape!=tiny.shape:
            self.prev=tiny;self.confident=False;self.good=0;return False
        mask=np.ones_like(self.prev,dtype=np.uint8)*255
        mask[:int(mask.shape[0]*.14),:]=0
        cv2.circle(mask,(int(center[0]/2),int(center[1]/2)),int(80),0,-1)
        p0=cv2.goodFeaturesToTrack(self.prev,maxCorners=300,qualityLevel=.012,minDistance=7,mask=mask)
        prev=self.prev;self.prev=tiny
        if p0 is None or len(p0)<24:self.bad+=1;self.good=0;self.confident=False;return False
        p1,st,_=cv2.calcOpticalFlowPyrLK(prev,tiny,p0,None,winSize=(19,19),maxLevel=3)
        if p1 is None:self.bad+=1;self.good=0;self.confident=False;return False
        a=p0.reshape(-1,2)[st.ravel()==1];b=p1.reshape(-1,2)[st.ravel()==1]
        if len(a)<22:self.bad+=1;self.good=0;self.confident=False;return False
        disp=b-a
        median=np.median(disp,axis=0)
        errors=np.linalg.norm(disp-median,axis=1)
        good=disp[errors<2.6]
        if len(good)<18 or len(good)/len(disp)<.48:
            self.bad+=1;self.good=0;self.confident=False;return False
        step=2*np.median(good,axis=0)
        if np.linalg.norm(step)>55*w/1600:
            self.bad+=1;self.good=0;self.confident=False;return False
        self.world-=step  # static scenery moves opposite to player world movement
        self.last_step=float(np.linalg.norm(step));self.last_valid=time.monotonic()
        self.valid_updates+=1
        self.good+=1;self.bad=0;self.confident=self.good>=3
        return self.confident

class Navigator:
    def __init__(self,leash=760):
        self.tracker=BackgroundTracker();self.leash=leash
        self.last_target=None;self.last_reason='';self.search_dir=0;self.blocked_directions=[]
        self.last_choice_at=0;self.last_choice_heading=None;self.goal_world=None;self.goal_at=0;self.goal_count=0
        self.last_pack_heading=None;self.last_pack_seen=0.;self.explore_heading=0.;self.explore_since=0.;self.explore_index=0;self.search_world=[]
        self.search_last_click=None;self.last_search_reason='' 
    def reset(self):
        self.tracker.reset();self.last_target=None;self.blocked_directions=[];self.goal_world=None;self.goal_at=0;self.goal_count=0
        self.last_pack_heading=None;self.last_pack_seen=0.;self.explore_heading=0.;self.explore_since=0.;self.explore_index=0;self.search_world=[];self.search_last_click=None
    def mark_blocked(self,target,anchor,now):
        theta=math.atan2(target[1]-anchor[1],target[0]-anchor[0])
        self.blocked_directions.append((theta,now+22.0))
        self.blocked_directions=self.blocked_directions[-12:]

    def is_blacklisted(self,target,anchor):
        now=time.monotonic()
        self.blocked_directions=[x for x in self.blocked_directions if x[1]>now]
        theta=math.atan2(target[1]-anchor[1],target[0]-anchor[0])
        return any(abs(math.atan2(math.sin(theta-a),math.cos(theta-a)))<.31 for a,_ in self.blocked_directions)

    def safe_detour(self,frame,anchor,base,portals,obstacles):
        # Explore a limited fan around the same monster heading; no random walk.
        v=np.asarray([base[0]-anchor[0],base[1]-anchor[1]],float)
        norm=float(np.linalg.norm(v))
        if norm<1:return None
        theta=math.atan2(v[1],v[0])
        s=max(.85,min(1.08,frame.shape[1]/1600.))
        # Long safe tangents first; no tiny aimless zig-zags.
        for off in (.30,-.30,.55,-.55,.85,-.85,1.12,-1.12):
            for d in (min(240*s,max(145*s,norm*.85)),135*s):
                cand=(int(anchor[0]+math.cos(theta+off)*d),int(anchor[1]+math.sin(theta+off)*d))
                if not self.is_blacklisted(cand,anchor) and self.visible_floor(frame,anchor,cand,portals,obstacles):
                    return cand
        return None

    def escape_from_stuck(self,frame,anchor,failed_target,obs):
        """Try a conservative reverse vector, then perpendicular directions.
        Failure produces NO click, not a speculative move into a hazard.
        """
        if anchor is None:return None,'no registered player anchor'
        dx=failed_target[0]-anchor[0];dy=failed_target[1]-anchor[1]
        theta=math.atan2(dy,dx)+math.pi
        scale=frame.shape[1]/1600.
        portals=obs.get('portals',[]);obstacles=obs.get('obstacles',[])
        for dangle in (0.,-.65,.65,-1.20,1.20):
            a=theta+dangle
            dist=145*max(.85,min(1.0,scale))
            t=(int(anchor[0]+math.cos(a)*dist),int(anchor[1]+math.sin(a)*dist))
            if self.is_blacklisted(t,anchor):continue
            if self.visible_floor(frame,anchor,t,portals,obstacles):
                return t,'reverse/side safe floor candidate'
        return None,'no candidate passed floor checks'

    def visible_floor(self,frame,center,target,portals,obstacles):
        h,w=frame.shape[:2];cx,cy=center;tx,ty=target
        edge=48
        if not (edge<tx<w-edge and 100<ty<h-edge):return False
        # Exact segment-to-circle test, including the starting point. Sparse
        # point checks could skip a narrow portal between samples.
        delta=np.array([tx-cx,ty-cy],float);length=float(np.dot(delta,delta))
        for z in portals+obstacles:
            v=np.array([z['x']-cx,z['y']-cy],float)
            t=float(np.clip(np.dot(v,delta)/max(1.,length),0,1))
            if np.linalg.norm(v-t*delta)<z['radius']+20:return False
        # Check the WHOLE click corridor at <=4px spacing, even near the player.
        # Reject clipped/unknown patches, void and bright untextured desktop.
        for t in np.linspace(0,1,max(2,int(length**.5/4)+1)):
            x=round(cx+(tx-cx)*t);y=round(cy+(ty-cy)*t)
            patch=frame[y-10:y+11,x-10:x+11]
            if patch.shape[:2]!=(21,21):return False
            if np.mean(np.max(patch,axis=2)<33)>.16:return False
            hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
            floor=(hsv[:,:,0]>=95)&(hsv[:,:,0]<=140)&(hsv[:,:,1]>25)&(hsv[:,:,2]>32)
            if np.mean(floor)<.18:return False
        return True
    def _path_grid(self,frame,anchor,target,portals,obstacles):
        """A* over visible 28px cells: avoid obvious void, rings and detected stairs.
        A screenshot cannot prove game walkability. A failed path returns None.
        """
        import heapq
        h,w=frame.shape[:2];cell=28
        gw=(w+cell-1)//cell;gh=(h+cell-1)//cell
        # Compute black-void area fraction, not single pixels that can be black
        # inside a player / monster / item sprite.
        dark=(np.max(frame,axis=2)<33).astype(np.float32)
        fractions=cv2.resize(dark,(gw,gh),interpolation=cv2.INTER_AREA)
        blocked=(fractions>.26)
        blocked[:max(4,95//cell),:]=True
        blocked[-1,:]=True;blocked[:,0]=True;blocked[:,-1]=True
        # Non-navigable structures, expanded to avoid stepping *through* them.
        for z in portals+obstacles:
            xx,yy,rr=float(z['x']),float(z['y']),float(z['radius'])+28
            y0=max(0,int((yy-rr)//cell));y1=min(gh,int((yy+rr)//cell)+1)
            x0=max(0,int((xx-rr)//cell));x1=min(gw,int((xx+rr)//cell)+1)
            for iy in range(y0,y1):
                for ix in range(x0,x1):
                    if math.hypot((ix+.5)*cell-xx,(iy+.5)*cell-yy)<rr:blocked[iy,ix]=True
        def idx(p):return (max(0,min(gw-1,int(p[0]//cell))),max(0,min(gh-1,int(p[1]//cell))))
        st=idx(anchor);en=idx(target)
        # The character can stand over an effect/dark sprite; accept start cell.
        for y in range(max(0,st[1]-1),min(gh,st[1]+2)):
            for x in range(max(0,st[0]-1),min(gw,st[0]+2)):
                if math.hypot(x-st[0],y-st[1])<1.01:blocked[y,x]=False
        if blocked[en[1],en[0]]:return None
        q=[(0.,st)];dist={st:0.};prev={};visited=set()
        steps=((1,0),(0,1),(-1,0),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1))
        budget=min(3000,gw*gh)
        while q and len(visited)<budget:
            _,a=heapq.heappop(q)
            if a in visited:continue
            visited.add(a)
            if a==en:break
            for dx,dy in steps:
                b=(a[0]+dx,a[1]+dy)
                if not(1<=b[0]<gw-1 and 3<=b[1]<gh-1) or blocked[b[1],b[0]]:continue
                if dx and dy and (blocked[a[1],b[0]] or blocked[b[1],a[0]]):continue
                nd=dist[a]+math.hypot(dx,dy)
                if nd<dist.get(b,1e9):
                    dist[b]=nd;prev[b]=a
                    hcost=math.hypot(en[0]-b[0],en[1]-b[1])
                    heapq.heappush(q,(nd+hcost,b))
        if en not in dist:return None
        path=[en]
        while path[-1]!=st:
            if path[-1] not in prev:return None
            path.append(prev[path[-1]])
        path.reverse()
        # Long-range route, but only click a visible nearby waypoint.
        waypoint=path[min(len(path)-1,9)]
        # Goal may be already reached; don't click a monster directly.
        if len(path)<3:return None
        p=((waypoint[0]+.5)*cell,(waypoint[1]+.5)*cell)
        # Validate a straight click segment. Obstacle may bend the grid route.
        for j in range(min(9,len(path)-1),0,-1):
            c=path[j];p=((c[0]+.5)*cell,(c[1]+.5)*cell)
            if self.visible_floor(frame,anchor,p,portals,obstacles):return (int(p[0]),int(p[1]))
        return None

    def _floor_explore(self,frame,anchor,portals,obstacles,reason='search'):
        """Conservative exploration when there is no confirmed group or no route.
        Never click unseen screen areas or known danger regions. Uses the camera
        displacement as a rough memory, not an absolute map position.
        """
        if not self.tracker.confident:return None,'camera motion uncertain; no exploration'
        h,w=frame.shape[:2]
        now=time.monotonic()
        world=self.tracker.world
        # Prefer an estimated return toward initial F9 location only when
        # camera displacement is tracked. F9 is NOT a world-coordinate waypoint.
        if self.tracker.confident and np.linalg.norm(world)>self.leash*.72:
            direction=math.atan2(-world[1],-world[0]);reason='approximate return toward F9 area'
        elif self.goal_world is not None and now-self.goal_at<18:
            vec=self.goal_world-world
            if np.linalg.norm(vec)>140:
                direction=math.atan2(vec[1],vec[0]);reason='continue last known pack direction'
            else:direction=None
        elif self.last_pack_heading is not None and now-self.last_pack_seen<16:
            direction=self.last_pack_heading;reason='search direction of last seen pack'
        else:
            direction=None
        # A deterministic bounded 12-heading sweep prevents random oscillation.
        # Rotate after a failed direction or when no map progress is possible.
        if direction is None:
            if self.explore_since==0 or now-self.explore_since>8.5:
                self.explore_index=(self.explore_index+1)%12
                self.explore_since=now
            direction=self.explore_index*math.pi/6
            reason='visible-floor search, sector {}'.format(self.explore_index+1)
        # Try aim/side deviations instead of rejecting a whole viewport.
        # Candidate may be 160-320px away, A* returns a nearer safe waypoint.
        offsets=(0.,-.38,.38,-.78,.78,-1.18,1.18,-1.62,1.62,math.pi)
        distset=(min(330,max(160,int(w*.26))),210,145)
        best=None;bestscore=-1.e9;grid_attempts=0
        for off in offsets:
            ang=direction+off
            for dist in distset:
                target=(int(anchor[0]+math.cos(ang)*dist),int(anchor[1]+math.sin(ang)*dist))
                if self.is_blacklisted(target,anchor):continue
                # Only destinations on known screen ground are candidates.
                if not self.visible_floor(frame,anchor,target,portals,obstacles):
                    # Keep route-finding bounded: pathfinding in the live input
                    # thread must never postpone F3 for seconds.
                    if grid_attempts>=3:continue
                    grid_attempts+=1
                    route=self._path_grid(frame,anchor,target,portals,obstacles)
                    if route is None:continue
                else:route=target
                if self.is_blacklisted(route,anchor):continue
                rx,ry=route
                drift=np.array([rx-anchor[0],ry-anchor[1]],dtype=float)
                if np.linalg.norm(drift)<75:continue
                # Prevent targeting exactly the same unproductive screen point.
                recent_penalty=0.
                if self.search_last_click is not None:
                    if math.hypot(rx-self.search_last_click[0],ry-self.search_last_click[1])<40:recent_penalty=1.4
                # Account for recently visited estimated positions only when tracking reliable.
                visited_penalty=0.
                if self.tracker.confident:
                    estimated=world+drift
                    self.search_world=[(x,t) for x,t in self.search_world if now-t<100]
                    if self.search_world:
                        nearest=min(np.linalg.norm(estimated-x) for x,t in self.search_world)
                        if nearest<105:visited_penalty=1.
                score=3-abs(off)*1.05+min(1.,np.linalg.norm(drift)/280.)-recent_penalty-visited_penalty
                if score>bestscore:bestscore=score;best=route
        if best is not None:
            self.search_last_click=best
            if self.tracker.confident:self.search_world.append((world+np.array([best[0]-anchor[0],best[1]-anchor[1]],float),now))
            if bestscore<1.4:
                self.explore_index=(self.explore_index+2)%12
                self.explore_since=now
            return best,reason+' / visible-ground waypoint'
        self.explore_index=(self.explore_index+2)%12
        self.explore_since=now
        return None,'no visibly safe floor path; stopped movement click'

    def choose(self,frame,anchor,obs):
        """Evaluate the ENTIRE frame for the densest known monsters.
        Commit briefly to a group to prevent zigzagging, then re-evaluate each
        fresh frame. Path is conservative and never treated as collision proof.
        """
        if not self.tracker.confident:return None,'camera motion uncertain; movement held'
        h,w=frame.shape[:2];now=time.monotonic()
        monsters=[m for m in obs.get('monsters',[]) if m.get('kind') in ('turtle','ghost') and m.get('confidence',0)>=(.67 if m.get('kind')=='turtle' else .79)
                  and 75<m['y']<h-22 and 12<m['x']<w-12]
        portals=obs.get('portals',[]);obstacles=obs.get('obstacles',[])
        if not monsters:
            # Do not freeze forever on a cleared screen. Search ONLY visible
            # walkable-looking floor; keep last goal for a short time.
            return self._floor_explore(frame,anchor,portals,obstacles,'no monsters visible')
        # Global density scan: sliding disk centered on EACH candidate throughout
        # the full viewport; stronger large packs beat two nearby monsters.
        clusters=[];used=set()
        for m in monsters:
            group=[z for z in monsters if math.hypot(z['x']-m['x'],z['y']-m['y'])<=185]
            keys=tuple(sorted(id(z) for z in group))
            if keys in used:continue
            used.add(keys)
            n=len(group);weight=sum(z['confidence'] for z in group)
            gx=sum(z['x']*z['confidence'] for z in group)/max(.1,weight)
            gy=sum(z['y']*z['confidence'] for z in group)/max(.1,weight)
            r=math.hypot(gx-anchor[0],gy-anchor[1])
            score=n*10 + weight*.5 + min(r/250,1.5)
            clusters.append((score,n,r,gx,gy))
        clusters.sort(reverse=True)
        if not clusters:return self._floor_explore(frame,anchor,portals,obstacles,'no monster group')
        # World-location estimate is relative to camera motion, not absolute.
        world=self.tracker.world
        if not hasattr(self,'goal_world'):self.goal_world=None
        if not hasattr(self,'goal_at'):self.goal_at=0
        if not hasattr(self,'goal_count'):self.goal_count=0
        retained=None
        if self.goal_world is not None and now-self.goal_at<6 and self.tracker.confident:
            for c in clusters:
                _,n,r,gx,gy=c
                estimated=world+np.array([gx-anchor[0],gy-anchor[1]])
                if np.linalg.norm(estimated-self.goal_world)<155:
                    retained=c;break
        # Switch only if another pack has two extra monsters; avoid 1px oscillation.
        if retained and clusters[0][1]<retained[1]+2:
            clusters.remove(retained);clusters.insert(0,retained)
        # Do not override an ongoing stuck escape with the same blocked heading.
        for score,n,r,gx,gy in clusters:
            if r<105:
                # If this is the strongest visible pack, stay in wheel range.
                # If a clearly larger distant group exists, pursue that one.
                if n>=2 and (not clusters or clusters[0][1]<=n):
                    return None,f'pack of {n} already in wheel range'
                continue
            direction=np.asarray([gx-anchor[0],gy-anchor[1]],float)/max(1,r)
            goal=(int(gx-direction[0]*80),int(gy-direction[1]*80))
            if self.is_blacklisted(goal,anchor):continue
            waypoint=self._path_grid(frame,anchor,goal,portals,obstacles)
            if waypoint is None:
                # A* not infallible; allow a direct short, visible route.
                d=min(285,max(115,r-85))
                candidate=(int(anchor[0]+direction[0]*d),int(anchor[1]+direction[1]*d))
                if self.visible_floor(frame,anchor,candidate,portals,obstacles):waypoint=candidate
            if waypoint is None or self.is_blacklisted(waypoint,anchor):continue
            estimated=world+np.array([gx-anchor[0],gy-anchor[1]])
            self.goal_world=estimated;self.goal_at=now;self.goal_count=n
            self.last_target=(int(gx),int(gy))
            self.last_pack_heading=math.atan2(gy-anchor[1],gx-anchor[0]);self.last_pack_seen=now
            self.explore_since=0.
            return waypoint,f'FULL-SCREEN pack {n} mobs toward ({int(gx)},{int(gy)}), distance={r:.0f}px'
        # Group exists, but a direct route is blocked. Move along visible
        # passable-looking floor via intermediate search, rather than idling.
        if clusters:
            _,n,r,gx,gy=clusters[0]
            if r>=110:
                self.last_pack_heading=math.atan2(gy-anchor[1],gx-anchor[0]);self.last_pack_seen=now
        return None,'detected pack has no validated route; movement held'
