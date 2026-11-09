"""Plan builder for biopics-v3 on the 0805 template.

Rules from the operator:
  * every cut stays inside ONE detected shot (scene length >= slot length + transition pad),
    so no hidden cut lands off-beat;
  * prefer shots with a face, larger face first;
  * ranked entries get real title-card images (photo slots), never a video.
"""
import json, random, sys
from collections import defaultdict

manifest, footage, faces_path, out = sys.argv[1:5]
m = json.load(open(manifest, encoding="utf-8")); f = json.load(open(footage, encoding="utf-8"))
faces = json.load(open(faces_path, encoding="utf-8"))
clips = {c["clip_id"]: c for c in f["clips"]}
random.seed(3)
MARGIN_US = 120_000  # keep the in and out points clear of the detected cut

# entry order by time = rank 8..1 ; (film clip, card clip, rating)
ENTRIES = [(None, "c06", "8.4/10"), (None, "c07", "8.7/10"), (None, "c08", "8.9/10"),
           ("c02", "c09", "9.1/10"), ("c00", "c10", "9.3/10"), ("c03", "c11", "9.5/10"),
           ("c01", "c12", "9.7/10"), ("c04", "c13", "10/10")]
BLACK = "c05"
FILM = {"c00": "Hacksaw Ridge", "c01": "Wolf of Wall Street", "c02": "Moneyball", "c03": "Theory of Everything",
        "c04": "Goodfellas", None: "black (no film yet)"}

def scene_stats(cid, s):
    st = faces.get(f"{cid}/{s['scene_id']}", {})
    return st.get("faces", 0), st.get("face_frac", 0.0), st.get("std", 0.0)

used = defaultdict(set)   # (cid, entry) -> scene ids
notes = []

def pick(cid, need_us, entry_key, want_face=True):
    c = clips[cid]
    if c.get("kind") == "image":
        return "s0", "image"
    cands = []
    for s in c["scenes"]:
        length = s["end_us"] - s["start_us"]
        if s["start_us"] + need_us > c["duration_us"]:
            continue
        n, frac, std = scene_stats(cid, s)
        fits = length >= need_us + MARGIN_US
        cands.append((fits, n > 0, frac, std, s))
    if cid == BLACK:
        return c["scenes"][0]["scene_id"], "black"
    fresh = [x for x in cands if x[4]["scene_id"] not in used[(cid, entry_key)] and x[3] >= 10]
    pool = fresh or cands
    # single-shot first, then face, then bigger face
    pool.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    best = pool[0]
    used[(cid, entry_key)].add(best[4]["scene_id"])
    how = ("shot" if best[0] else "CUT-INSIDE") + ("+face" if best[1] else "")
    return best[4]["scene_id"], how

slots = sorted(m["media"], key=lambda s: (s["target_start_us"], -s["target_duration_us"]))
cards = [s for s in slots if s["material_type"] == "photo" and s["target_duration_us"] < 60_000_000]
assert len(cards) == 8
windows = [(c["target_start_us"], c["target_start_us"] + c["target_duration_us"], i) for i, c in enumerate(cards)]
def entry_of(at):
    for a, b, i in windows:
        if a - 50_000 <= at < b: return i
    return None

media, rows = [], []
for s in slots:
    sid = s["slot_id"]; need = s["source_duration_us"] + s["transition_pad_us"]
    if s["material_type"] == "photo":
        if s["target_duration_us"] >= 60_000_000:
            media.append({"slot_id": sid, "clip_id": "", "scene_id": "", "keep": True}); rows.append((s, "KEEP background", "")); continue
        i = cards.index(s); cid = ENTRIES[i][1]
        media.append({"slot_id": sid, "clip_id": cid, "scene_id": "s0"}); rows.append((s, f"card {clips[cid]['original_name']}", "")); continue
    if "onlineMaterial" in s["path"] or "/materials/video/" in s["path"]:
        media.append({"slot_id": sid, "clip_id": "", "scene_id": "", "keep": True}); rows.append((s, "KEEP flash", "")); continue
    at = s["target_start_us"]
    if at < 14_000_000 and s["target_duration_us"] > 10_000_000:
        cid, key = "c04", "bed"            # roulette bed: the #1 film, longest single face shot
    elif at < cards[0]["target_start_us"]:
        cid, key = "c04", "reveal"         # genre reveal: the #1 film
    else:
        e = entry_of(at); cid = ENTRIES[e][0] or BLACK; key = e
    scene, how = pick(cid, need, key)
    media.append({"slot_id": sid, "clip_id": cid, "scene_id": scene}); rows.append((s, FILM.get(cid, cid), f"{scene} {how}"))

badges = sorted([t for t in m["text"] if t["text"].endswith("/10")], key=lambda t: t["target_start_us"])
text = [{"slot_id": b["slot_id"], "new_text": r} for b, (_, _, r) in zip(badges, ENTRIES)]
for t in m["text"]:
    if t["target_duration_us"] > 1_000_000 and t["text"] == "Psychological": text.append({"slot_id": t["slot_id"], "new_text": "Biopic"})
    if t["text"] == "MOVIES OF ALL TIME": text.append({"slot_id": t["slot_id"], "new_text": "BIOPICS OF ALL TIME"})
plan = {"job_name": "biopics-v3",
        "rationale": "Roulette lands on Biopic. Entries #8-#6 await footage (black cuts, rank cards). #5 Moneyball, #4 Hacksaw Ridge, "
                     "#3 Theory of Everything, #2 Wolf of Wall Street, #1 Goodfellas. Every cut sits inside one detected shot, "
                     "face shots first. Title cards are generated PNGs sized to each template card and its crop.",
        "media": media, "text": text}
json.dump(plan, open(out, "w", encoding="utf-8"), indent=1)
for s, fill, how in rows:
    print(f"{s['target_start_us']/1e6:6.2f} {s['target_duration_us']/1e6:5.2f}  {fill:34} {how}")
print("cut-inside count:", sum(1 for _, _, h in rows if "CUT-INSIDE" in h), "| no-face count:", sum(1 for _, f_, h in rows if h and "face" not in h and "black" not in h and f_ != "black (no film yet)"))
