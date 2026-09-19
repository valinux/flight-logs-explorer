#!/usr/bin/env python3
"""Prepare isolated scan lines for a second local OCR reading.

Optional review tooling: requires Pillow, numpy and PyMuPDF. All coordinates
come from the existing source-linked edition. Outputs are candidates, never
automatically labelled verified. The visible PDF raster is the only OCR input.
"""
import argparse
import json
import math
import re
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "review_work"
SCALE = 5


def read_review():
    html = (ROOT / "Contact_directory_review.html").read_text()
    return json.loads(re.search(r'<script id="dataset" type="application/json">(.*?)</script>', html, re.S)[1])


def deskew(image):
    sample = image.resize((max(1, image.width // 2), max(1, image.height // 2)))
    def score(angle):
        ink = np.array(sample.rotate(float(angle), fillcolor=255)) < 140
        return np.square(ink.sum(axis=1).astype(float)).sum()
    coarse = max(np.arange(-6, 6.1, .3), key=score)
    angle = float(max(np.arange(coarse-.3, coarse+.31, .05), key=score))
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255), angle


def isolate_lines(image):
    counts = (np.array(image) < 140).sum(axis=1)
    threshold = max(6, image.width * .035)
    spans, start = [], None
    for y, count in enumerate(counts):
        if count > threshold and start is None:
            start = y
        if (count <= threshold or y == len(counts)-1) and start is not None:
            if y-start >= 6:
                spans.append([start, y])
            start = None
    if not spans:
        return []
    # Rejoin fragmented strokes within a line, then split touching lines at
    # an ink valley. Keep page pixels; spacing changes only affect OCR input.
    merged = []
    for a, b in spans:
        if merged and a-merged[-1][1] <= 2 and b-merged[-1][0] < 24:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    height = float(np.median([b-a for a, b in merged if b-a >= 14])) if any(b-a >= 14 for a,b in merged) else 25
    out = []
    for a, b in merged:
        while b-a > 1.7*height:
            lo, hi = a+int(.7*height), min(b-int(.5*height), a+int(1.35*height))
            if hi <= lo:
                break
            middle = min(range(lo, hi), key=lambda i: counts[i])
            out.append([a, middle])
            a = middle
        out.append([a, b])
    return out


def prepare(pages):
    dataset = read_review()
    pdf = pymupdf.open(ROOT / "Jeffrey_Epstein_UNKNOWN.pdf")
    WORK.mkdir(exist_ok=True)
    jobs, metadata = [], []
    for p in pages:
        groups = {}
        for record in dataset["records"]:
            for part in record["parts"]:
                if part["page"] == p:
                    groups.setdefault(part["column"], []).append(part["bbox"])
        bounds = []
        for col, boxes in sorted(groups.items()):
            bounds.append([col, min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes)])
        pix = pdf[p-1].get_pixmap(matrix=pymupdf.Matrix(SCALE,SCALE), colorspace=pymupdf.csGRAY)
        full = Image.frombytes("L", [pix.width, pix.height], pix.samples)
        # Find actual gutters inside the overlapping review-crop margins.
        for left, right in zip(bounds, bounds[1:]):
            if left[3] > right[1]:
                lo, hi = int(right[1]*SCALE), int(left[3]*SCALE)
                area = np.array(full.crop((lo, int(min(left[2],right[2])*SCALE), hi+1, int(max(left[4],right[4])*SCALE)))) < 140
                score = area.sum(axis=0).astype(float)
                smooth = np.convolve(score, np.ones(7)/7, mode="same")
                cut = (lo+int(np.argmin(smooth[3:-3]))+3)/SCALE if len(smooth)>6 else (left[3]+right[1])/2
                left[3] = right[1] = cut
        for col,x0,y0,x1,y1 in bounds:
            tag=f"p{p:03d}-c{col}"
            crop=full.crop((int(x0*SCALE),int(y0*SCALE),int(x1*SCALE),int(y1*SCALE)))
            crop=ImageOps.expand(crop,border=20,fill=255)
            cropped,angle=deskew(crop)
            spans=isolate_lines(cropped)
            # Limit OCR canvas height: Vision downsamples very tall images.
            for chunk_index,start in enumerate(range(0,len(spans),20)):
                chunk=spans[start:start+20]
                height=sum(b-a+36 for a,b in chunk)+20
                canvas=Image.new("L",(cropped.width+20,height),255)
                entries=[];cursor=18
                for a,b in chunk:
                    top=max(0,a-1);bottom=min(cropped.height,b+1)
                    canvas.paste(cropped.crop((0,top,cropped.width,bottom)),(10,cursor))
                    rotated_y=(a+b)/2
                    original_y=(rotated_y-crop.height/2)*math.cos(math.radians(angle))+crop.height/2
                    entries.append({"top":cursor,"bottom":cursor+bottom-top,"source_y":y0+(original_y-20)/SCALE})
                    cursor+=b-a+36
                image_path=WORK/f"{tag}-{chunk_index}.png"
                canvas.save(image_path)
                output=WORK/f"{tag}-{chunk_index}.json"
                jobs.append({"image":str(image_path),"output":str(output)})
                metadata.append({"page":p,"column":col,"bbox":[x0,y0,x1,y1],"angle":angle,
                                 "image":str(image_path),"output":str(output),"height":height,"lines":entries})
        print(f"Prepared PDF page {p}: {len(bounds)} columns",flush=True)
    (WORK/"jobs.json").write_text(json.dumps(jobs))
    (WORK/"metadata.json").write_text(json.dumps(metadata,indent=1))


def collect():
    records=read_review()["records"]
    columns={}
    for job in json.loads((WORK/"metadata.json").read_text()):
        path=Path(job["output"])
        if not path.exists():
            continue
        grouped={}
        for observation in json.loads(path.read_text()):
            box=observation["bbox"]
            center=(box[1]+box[3])/2*job["height"]
            index=min(range(len(job["lines"])),key=lambda i:abs((job["lines"][i]["top"]+job["lines"][i]["bottom"])/2-center))
            grouped.setdefault(index,[]).append((box[0],observation["text"]))
        for index,readings in sorted(grouped.items()):
            text=" ".join(t for _,t in sorted(readings))
            columns.setdefault((job["page"],job["column"]),[]).append({"y":job["lines"][index]["source_y"],"text":text})
    candidates=[]
    for record in records:
        if not any((part["page"],part["column"]) in columns for part in record["parts"]):
            continue
        parts=[]
        for part in record["parts"]:
            lines=columns.get((part["page"],part["column"]),[])
            readings=[line["text"] for line in sorted(lines,key=lambda l:l["y"]) if part["bbox"][1]<=line["y"]<part["bbox"][3]]
            parts.append({"page":part["page"],"column":part["column"],"lines":readings})
        candidates.append({"id":record["id"],"name":record["name"],"parts":parts})
    candidate_path = WORK / "candidates.json"
    candidate_tmp = candidate_path.with_suffix(".tmp.json")
    candidate_tmp.write_text(json.dumps(candidates,ensure_ascii=False,indent=1))
    candidate_tmp.replace(candidate_path)
    print(f"Collected {len(candidates)} candidate records; source review still required.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("pages",nargs="*",type=int)
    parser.add_argument("--collect",action="store_true")
    args=parser.parse_args()
    if args.collect:
        collect()
    else:
        prepare(args.pages or list(range(1,93)))
