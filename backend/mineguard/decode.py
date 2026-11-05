"""Frame ingestion through the macOS system decoder (AVFoundation via PyObjC).

No FFmpeg or OpenCV is used: the available OpenCV wheels bundle a GPL-configured FFmpeg (see docs/MG-VS01-dependency-bom.md).
Codec/patent exposure of the OS decoder remains open gate G-6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import CoreFoundation as CF
import objc

DECODER_ID = "avfoundation-avassetreader"


class UnsupportedMedia(Exception):
    msg_id = "mg.refusal.unsupported_media"


@dataclass(frozen=True)
class MediaInfo:
    container: str
    codec_fourcc: str
    native_width: int
    native_height: int
    nominal_fps: float
    duration_s: float
    timescale: int


@dataclass(frozen=True)
class DecodedFrame:
    index: int
    pts_ticks: int
    timescale: int
    rgb: np.ndarray  # H x W x 3 uint8, top-left origin


def _av():
    import AVFoundation as AV
    import CoreMedia as CM
    import Quartz as Q
    from Foundation import NSURL

    return AV, CM, Q, NSURL


def _open(path: str):
    AV, CM, Q, NSURL = _av()
    asset = AV.AVURLAsset.URLAssetWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    tracks = asset.tracksWithMediaType_(AV.AVMediaTypeVideo)
    if len(tracks) != 1:
        raise UnsupportedMedia(f"expected exactly one video track, found {len(tracks)}")
    track = tracks[0]
    t = track.preferredTransform()
    if (t.a, t.b, t.c, t.d) != (1.0, 0.0, 0.0, 1.0):
        raise UnsupportedMedia("rotated or mirrored video tracks are not supported in VS-01")
    return AV, CM, Q, asset, track


def _fourcc(code: int) -> str:
    return code.to_bytes(4, "big").decode("latin-1")


def probe(path: str) -> MediaInfo:
    AV, CM, Q, asset, track = _open(path)
    descs = track.formatDescriptions()
    if not descs:
        raise UnsupportedMedia("no format description")
    size = track.naturalSize()
    return MediaInfo(
        container="mp4",
        codec_fourcc=_fourcc(CM.CMFormatDescriptionGetMediaSubType(descs[0])),
        native_width=int(size.width),
        native_height=int(size.height),
        nominal_fps=float(track.nominalFrameRate()),
        duration_s=float(CM.CMTimeGetSeconds(asset.duration())),
        timescale=int(track.naturalTimeScale()),
    )


def iter_frames(path: str, width: int, height: int) -> Iterator[DecodedFrame]:
    """Yield every decoded frame in presentation order, scaled by the decoder to width x height."""
    AV, CM, Q, asset, track = _open(path)
    reader, _ = AV.AVAssetReader.assetReaderWithAsset_error_(asset, None)
    settings = {
        Q.kCVPixelBufferPixelFormatTypeKey: Q.kCVPixelFormatType_32BGRA,
        Q.kCVPixelBufferWidthKey: width,
        Q.kCVPixelBufferHeightKey: height,
    }
    out = AV.AVAssetReaderTrackOutput.assetReaderTrackOutputWithTrack_outputSettings_(track, settings)
    reader.addOutput_(out)
    if not reader.startReading():
        raise UnsupportedMedia(f"decoder refused to start: {reader.error()}")
    index = 0
    timescale = None
    try:
        while True:
            # Each frame is handled inside its own autorelease pool and every Core Media reference is dropped before the frame is
            # yielded. Without this, the system decoder service (VTDecoderXPCService) kept every decoded frame (~3.5 MB each, ~2.7 GB per
            # pass over the test clip) until the client process exited: a fixed leak that exhausted memory after a dozen passes.
            with objc.autorelease_pool():
                sb = out.copyNextSampleBuffer()
                if sb is None:
                    break
                owned = [sb]  # copyNextSampleBuffer returns a +1 reference that PyObjC does not release for this Core Foundation type
                try:
                    pb = CM.CMSampleBufferGetImageBuffer(sb)
                    if pb is None:
                        continue
                    pts = CM.CMSampleBufferGetPresentationTimeStamp(sb)
                    if timescale is None:
                        timescale = int(pts.timescale)
                    elif int(pts.timescale) != timescale:
                        raise UnsupportedMedia("mixed timescales are not supported in VS-01")
                    pts_value = int(pts.value)
                    Q.CVPixelBufferLockBaseAddress(pb, 1)
                    try:
                        h, w = Q.CVPixelBufferGetHeight(pb), Q.CVPixelBufferGetWidth(pb)
                        stride = Q.CVPixelBufferGetBytesPerRow(pb)
                        base = Q.CVPixelBufferGetBaseAddress(pb)
                        raw = np.frombuffer(base.as_buffer(h * stride), dtype=np.uint8).reshape(h, stride // 4, 4)
                        rgb = np.ascontiguousarray(raw[:, :w, 2::-1])
                        del raw, base
                    finally:
                        Q.CVPixelBufferUnlockBaseAddress(pb, 1)
                finally:
                    CF.CFRelease(owned.pop())  # balance the +1 from the copy; the Python proxy releases its own retain when dropped
                    pb = None
                    sb = None
            yield DecodedFrame(index=index, pts_ticks=pts_value, timescale=timescale, rgb=rgb)
            index += 1
    finally:
        if reader.status() == 1:  # reading
            reader.cancelReading()
    if reader.status() == 3:  # failed
        raise UnsupportedMedia(f"decoder failed: {reader.error()}")
