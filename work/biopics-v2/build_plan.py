import json, random
import cv2
def solid(path):
    im=cv2.imread(path,0)
    return im is None or im.std()<12
random.seed(1199)
d=json.load(open('prompt.json')); p=d['prompt']
t=json.JSONDecoder().raw_decode(p[p.find('TEMPLATE SLOTS:')+16:].strip())[0]
fz=json.load(open('footage.json'))
clips={c['clip_id']:c for c in fz['clips']}
films={'c00':'Hacksaw Ridge','c01':'Wolf of Wall Street','c02':'Moneyball','c03':'Theory of Everything','c04':'Goodfellas','c05':'BLACK (needs a film)'}
PAD=0.5
def scenes(cid, need):
    c=clips[cid]; dur=c['duration_us']/1e6
    if cid=='c05': return list(c['scenes'])
    return [s for s in c['scenes'] if s['start_us']/1e6+need+PAD<=dur and s['end_us']/1e6<dur-8 and not solid(s['thumbnail']) and s['end_us']/1e6-s['start_us']/1e6>=min(need,3)]
used={cid:set() for cid in clips}
def pick(cid, need, prefer_long=False):
    cand=[s for s in scenes(cid,need) if s['scene_id'] not in used[cid] and s['start_us']/1e6>5]
    if not cand: cand=scenes(cid,need)
    if prefer_long: cand.sort(key=lambda s:-(s['end_us']/1e6-s['start_us']/1e6)); s=cand[0]
    else: s=random.choice(cand)
    used[cid].add(s['scene_id']); return s['scene_id']
media=t['media']; text=t['text']
feat=[m for m in media if m['duration_s']>3 and m['duration_s']<10]
feat.sort(key=lambda m:m['at_s'])
order=['c00','c01','c02','c03','c04']; random.shuffle(order)
order+= ['c05','c05','c05']  # no more films: black placeholders
plan_media=[]; notes=[]
entries=[]
for i,(m,cid) in enumerate(zip(feat,order)):
    sid=pick('c05',m['duration_s'])
    plan_media.append({'slot_id':m['slot_id'],'clip_id':'c05','scene_id':sid})
    entries.append((m['at_s'],m['at_s']+m['duration_s'],cid,m['slot_id'][:8],sid))
def entry_for(at):
    for a,b,cid,_,_ in entries:
        if a-0.1<=at<b: return cid
    return entries[-1][2]
for m in media:
    if any(m['slot_id']==x['slot_id'] for x in plan_media): continue
    if m['duration_s']>60: cid,sid='c05',pick('c05',m['duration_s'])      # full-length background layer
    elif m['at_s']<14.3 and m['duration_s']>10: cid,sid='c02',pick('c02',m['duration_s'])  # roulette bed
    elif m['at_s']<15.43: cid,sid=entries[0][2],pick(entries[0][2],m['duration_s'])  # reveal
    else: cid=entry_for(m['at_s']); sid=pick(cid,m['duration_s'])
    plan_media.append({'slot_id':m['slot_id'],'clip_id':cid,'scene_id':sid})
ratings=random.sample([7.2,7.6,7.9,8.3,8.7,9.1,9.4,9.8],8)
badges=sorted([x for x in text if x['current_text'].endswith('/10')],key=lambda x:x['at_s'])
plan_text=[{'slot_id':b['slot_id'],'new_text':f'{r}/10'} for b,r in zip(badges,ratings)]
plan_text+=[{'slot_id':x['slot_id'],'new_text':v} for x in text for k,v in
  {'EA9D296E':'Biopic','FC2F3C48':'THE GREATEST','C2762736':'BIOPICS OF ALL TIME'}.items() if x['slot_id'].startswith(k)]
plan={'job_name':'biopics-v1','rationale':'Genre roulette lands on Biopic. 8 entries: quick cuts in each window from one film, PiP card box and full-length layer black as placeholders for title cards; entries 6-8 black pending three more films; ratings randomised 7-10.','media':plan_media,'text':plan_text}
json.dump(plan,open('plan.json','w'),indent=1)
for (a,b,cid,sl,sid),r in zip(entries,ratings): print(f'{a:5.2f}-{b:5.2f}s  slot {sl}  {films[cid]:40s} {sid:5s} {r}/10')
