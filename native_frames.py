#!/usr/bin/env python3
"""Local AVFoundation frames: rotated, HDR to SDR, sRGB, actual presentation time.

No external asset references are permitted. Only newly encoded pixels are saved;
source metadata is never copied into a preview.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import sys
import tempfile


def extract(source, at_ms, output):
    import objc
    import AVFoundation as AV
    import CoreMedia as CM
    import Quartz as Q
    from Foundation import NSURL
    # PyObjC 13's generated metadata omits this deprecated API's actualTime out
    # annotation. Match the installed SDK declaration, within this process only.
    objc.registerMetaDataForSelector(
        b'AVAssetImageGenerator', b'copyCGImageAtTime:actualTime:error:',
        {'arguments': {2: {'type': CM.CMTime.__typestr__},
                       3: {'type': b'o^' + CM.CMTime.__typestr__},
                       4: {'type': b'o^@'}}})
    source = Path(source).resolve(strict=True)
    output = Path(output)
    if not source.is_file() or at_ms < 0 or output.is_symlink():
        raise ValueError('Invalid local frame input or output')
    output.parent.mkdir(parents=True, exist_ok=True)
    with objc.autorelease_pool():
        asset = AV.AVURLAsset.URLAssetWithURL_options_(
            NSURL.fileURLWithPath_(str(source)),
            {AV.AVURLAssetReferenceRestrictionsKey: AV.AVAssetReferenceRestrictionForbidAll,
             AV.AVURLAssetPreferPreciseDurationAndTimingKey: True})
        generator = AV.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
        generator.setAppliesPreferredTrackTransform_(True)
        generator.setDynamicRangePolicy_(AV.AVAssetImageGeneratorDynamicRangePolicyForceSDR)
        generator.setMaximumSize_((2048, 2048))
        generator.setRequestedTimeToleranceBefore_(CM.kCMTimeZero)
        generator.setRequestedTimeToleranceAfter_(CM.kCMTimeZero)
        image, actual, error = generator.copyCGImageAtTime_actualTime_error_(
            CM.CMTimeMake(int(at_ms), 1000), None, None)
        seconds = CM.CMTimeGetSeconds(actual)
        if image is None or error is not None or not math.isfinite(seconds) or seconds < 0:
            raise RuntimeError('Native local video frame decode failed')
        width, height = Q.CGImageGetWidth(image), Q.CGImageGetHeight(image)
        context = Q.CGBitmapContextCreate(
            None, width, height, 8, width * 4,
            Q.CGColorSpaceCreateWithName(Q.kCGColorSpaceSRGB),
            Q.kCGImageAlphaPremultipliedLast | Q.kCGBitmapByteOrder32Big)
        if context is None:
            raise RuntimeError('Cannot create color-managed frame')
        Q.CGContextDrawImage(context, ((0, 0), (width, height)), image)
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.png', delete=False) as stream:
            temporary = Path(stream.name)
        try:
            destination = Q.CGImageDestinationCreateWithURL(
                NSURL.fileURLWithPath_(str(temporary.resolve())), 'public.png', 1, None)
            Q.CGImageDestinationAddImage(destination, Q.CGBitmapContextCreateImage(context),
                                       None)
            if not Q.CGImageDestinationFinalize(destination):
                raise RuntimeError('Cannot encode color-managed frame')
            from PIL import Image
            with Image.open(temporary) as pixels:
                pixels.load()
                pixels.convert('RGB').save(temporary,'JPEG',quality=95)
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return {'decoder': 'AVFoundation', 'dynamic_range': 'forceSDR', 'color_space': 'sRGB',
                'requested_at_ms': int(at_ms), 'actual_at_ms': round(seconds * 1000),
                'width': width, 'height': height, 'external_references': 'forbidden'}


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit('Usage: native_frames.py SOURCE AT_MS OUTPUT')
    try:
        print(json.dumps(extract(sys.argv[1], int(sys.argv[2]), sys.argv[3])))
    except Exception:
        raise SystemExit('Native frame extraction failed; no color-unsafe fallback was used.')
