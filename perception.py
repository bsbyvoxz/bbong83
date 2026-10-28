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

def read_gauge_info(frame, points, which):
    """Use a stable interior band, not two blue border rows in a 5px union.

    Each row is measured separately. A candidate needs at least three adjacent
    rows with a consistent fill and a colored left rail. Thin always-full
    borders and text rows cannot make the whole gauge read 100 percent.
    """
    h,w=frame.shape[:2]
    (x1,y1),(x2,y2)=points
    x1,x2,y=int(x1),int(x2),round((y1+y2)/2)
    unknown=dict(value=None,row=None,rows=0,quality='unknown')
    if x2-x1<80 or x1<0 or x2>w or y<9 or y>=h-9:return unknown
    crop=frame[y-8:y+9,x1:x2]
    if crop.size==0 or float(np.max(crop))<30:return unknown
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);hue,sat,val=cv2.split(hsv)
    mask=(((hue<=13)|(hue>=170))&(sat>90)&(val>95)) if which=='hp' else ((hue>=92)&(hue<=138)&(sat>60)&(val>95))
    measurements=[]
    for row in range(mask.shape[0]):
        left=val[row,3:20][mask[row,3:20]]
        if len(left)<8:measurements.append(None);continue
        bright=mask[row]&(val[row]>=max(95,float(np.median(left))*.65))
        cols=np.convolve(bright.astype(float),np.ones(3)/3,mode='same')>=.6
        if np.mean(cols[3:20])<.5:
            measurements.append(None);continue
        far=0;gap=0
        for i,filled in enumerate(cols):
            if filled:far=i+1;gap=0
            else:
                gap+=1
                if gap>=6:break
        measurements.append(min(1.,far/len(cols)))
    groups=[]
    for start in range(len(measurements)):
        values=[]
        for end in range(start,len(measurements)):
            value=measurements[end]
            if value is None:break
            if values and max(values+[value])-min(values+[value])>.035:break
            values.append(value)
            if len(values)>=3:
                middle=(start+end)/2
                score=len(values)*3.-abs(middle-8)*.3
                groups.append((score,float(np.median(values)),round(y-8+middle),len(values)))
    if not groups:
        # Empty MP still has a bright blue outline around a dark blue interior.
        # Require both; a blank/gray capture must remain unknown.
        if which=='mp':
            outline=np.mean(mask,axis=1)>.85
            dark=((hue>=92)&(hue<=138)&(sat>60)&(val>=20)&(val<=95))
            empty=np.mean(dark,axis=1)>.8
            for first in range(len(empty)-2):
                if np.count_nonzero(outline)>=1 and np.all(empty[first:first+3]):
                    return dict(value=0.,row=y-8+first+1,rows=3,quality='empty_blue_interior')
        return unknown
    _,value,row,count=max(groups)
    return dict(value=value,row=row,rows=count,quality='interior_band')


def read_gauge(frame, points, which):
    return read_gauge_info(frame,points,which)['value']

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
