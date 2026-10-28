"""Fast, testable HUD readings and window-size-aware calibration."""
from pathlib import Path
import json
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent

def valid_points(points, size):
    try:
        w,h = size
        return (len(points)==4 and all(len(p)==2 and 0<=p[0]<w and 0<=p[1]<h for p in points)
                and all(points[i+1][0]-points[i][0]>=80 and abs(points[i+1][1]-points[i][1])<=6 for i in (0,2)))
    except (TypeError,ValueError,IndexError):
        return False

class Calibration:
    def __init__(self, path):
        self.path = Path(path)
        self.points = []
        self.size = None
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data,dict) and data.get('version')==2:
                self.size = tuple(data['size'])
                if valid_points(data['points'],self.size): self.points = data['points']
        except (OSError,ValueError,KeyError,TypeError):
            pass
    def valid_for(self, size):
        return self.size == tuple(size) and valid_points(self.points,size)
    def save(self, points, size):
        if not valid_points(points,size):
            raise ValueError('invalid gauge points')
        from bridge import atomic_text
        data = dict(version=2, size=list(size), points=points)
        atomic_text(self.path,json.dumps(data,indent=2))
        self.points = points
        self.size = tuple(size)

def read_gauge(frame, points, which):
    h,w = frame.shape[:2]
    (x1,y1),(x2,y2) = points
    x1,x2,y = int(x1),int(x2),round((y1+y2)/2)
    if x2-x1<80 or x1<0 or x2>w or y<3 or y>=h-3:
        return None
    crop = frame[y-2:y+3,x1:x2]
    # A blank capture is unknown, never a valid low-mana decision.
    if float(np.max(crop)) < 30:
        return None
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    hue,sat,val=cv2.split(hsv)
    mask = (((hue<=13)|(hue>=170))&(sat>90)&(val>65)) if which=='hp' else ((hue>=92)&(hue<=138)&(sat>60)&(val>55))
    cols=np.mean(mask,axis=0)>=.4
    cols=np.convolve(cols,np.ones(3)/3,mode='same')>=.4
    # The bar must start at the calibrated left end; isolated late color isn't fill.
    if not np.any(cols[:8]):
        return 0. if np.count_nonzero(cols)<4 else None
    far=0;gap=0
    for i,filled in enumerate(cols):
        if filled: far=i+1;gap=0
        else:
            gap+=1
            if gap>=7: break
    return min(1.,far/len(cols))

def read_status(frame, points):
    if len(points)!=4: return None,None
    return read_gauge(frame,points[:2],'hp'), read_gauge(frame,points[2:],'mp')

class BuffIcons:
    """Tri-state flags. A failed match is UNKNOWN without independent HUD proof.

    Search fixed sprite sizes near the top-left HUD rather than using window
    width as a scale. Missing can be asserted only when >=2 other reference
    icons are strongly visible in the same row. Fallback timers cover unknown.
    """
    def __init__(self, root=ROOT):
        self.samples=[cv2.imread(str(Path(root)/'assets'/f'buff_{i}.png')) for i in range(1,5)]
    def read(self, frame):
        h,w=frame.shape[:2]
        roi=frame[:min(h,150),:min(w,360)]
        if h<180 or w<400:
            return [None]*4,[0.]*4,False
        matches=[]
        for source in self.samples:
            best=(0.,(0,0))
            if source is not None:
                for scale in (.85,.95,1.,1.05,1.15):
                    temp=cv2.resize(source,None,fx=scale,fy=scale)
                    if temp.shape[0]>=roi.shape[0] or temp.shape[1]>=roi.shape[1]: continue
                    score=cv2.matchTemplate(roi,temp,cv2.TM_CCOEFF_NORMED)
                    _,value,_,loc=cv2.minMaxLoc(score)
                    if value>best[0]: best=(float(value),loc)
            matches.append(best)
        # Avoid confusing the quickslot sidebar with the horizontal buff row.
        strong=[loc for score,loc in matches if score>=.82]
        row_ok=len(strong)>=2 and max(p[1] for p in strong)-min(p[1] for p in strong)<=5
        row_y=float(np.median([p[1] for p in strong])) if strong else -999.
        flags=[]
        for score,loc in matches:
            if score>=.82 and (not row_ok or abs(loc[1]-row_y)<=5): flags.append(True)
            elif row_ok and score<.55: flags.append(False)
            else: flags.append(None)
        return flags,[round(x[0],3) for x in matches],row_ok
