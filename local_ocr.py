#!/usr/bin/env python3
"""Apple Vision OCR and coarse face-region guard; CPU only, no identity or network."""
from __future__ import annotations

import json
from pathlib import Path
import sys


def recognize(path):
    import Vision
    from Foundation import NSURL
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRevision_(Vision.VNRecognizeTextRequestRevision3)
    request.setUsesLanguageCorrection_(False)
    request.setRecognitionLanguages_(['zh-Hans', 'en-US'])
    request.setAutomaticallyDetectsLanguage_(True)
    request.setMinimumTextHeight_(0.006)
    request.setUsesCPUOnly_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(str(Path(path).resolve(strict=True))), None)
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError('Local Vision OCR failed')
    result = []
    for observation in request.results() or []:
        candidate = observation.topCandidates_(1)[0]
        box = observation.boundingBox()
        result.append({'text': str(candidate.string()), 'confidence': float(candidate.confidence()),
                       'bbox': [box.origin.x, box.origin.y, box.size.width, box.size.height]})
    return result


def face_regions(path):
    """Only count/area for an occlusion policy, never identity or embeddings."""
    import Vision
    from Foundation import NSURL
    request=Vision.VNDetectFaceRectanglesRequest.alloc().init()
    request.setUsesCPUOnly_(True)
    handler=Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(str(Path(path).resolve(strict=True))),None)
    success,error=handler.performRequests_error_([request],None)
    if not success:raise RuntimeError('Local face-region guard failed')
    areas=[float(o.boundingBox().size.width*o.boundingBox().size.height) for o in request.results() or []]
    return {'detected_regions':len(areas),'largest_area_fraction':max(areas,default=0.0),
            'purpose':'coarse occlusion policy only; no identity, landmarks or embeddings'}


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--faces':
        print(json.dumps(face_regions(sys.argv[2])))
    elif len(sys.argv)==2:
        print(json.dumps(recognize(sys.argv[1]),ensure_ascii=False))
    else:raise SystemExit('Usage: local_ocr.py IMAGE | --faces IMAGE')
