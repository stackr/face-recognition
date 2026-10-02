"""Bounded image decoding on the GPU scheduler, never on the HTTP thread."""

import base64
import io

import cv2
import numpy as np
from PIL import Image, ImageOps, JpegImagePlugin, PngImagePlugin, UnidentifiedImageError

from app.core.face_data import MODEL_VERSION, normalized_embedding


class ReferenceRejected(ValueError):
    def __init__(self, code, quality=None):
        self.code, self.quality = code, quality
        super().__init__(code)


def analyze_reference(analyzer, content):
    try:
        # Ultralytics replaces Image.open globally with a HEIF auto-install hook.
        # Use only Pillow's pinned JPEG/PNG decoders, including for invalid input.
        if content.startswith(b"\xff\xd8\xff"):
            decoder = JpegImagePlugin.JpegImageFile
        elif content.startswith(b"\x89PNG\r\n\x1a\n"):
            decoder = PngImagePlugin.PngImageFile
        else:
            raise ReferenceRejected("invalid_image")
        with decoder(io.BytesIO(content)) as source:
            if (
                max(source.size) > 4096
                or source.width * source.height > 12_000_000
                or getattr(source, "n_frames", 1) != 1
            ):
                raise ReferenceRejected("image_dimensions_exceeded")
            source.load()
            image = cv2.cvtColor(
                np.asarray(ImageOps.exif_transpose(source).convert("RGB")), cv2.COLOR_RGB2BGR
            )
    except ReferenceRejected:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        raise ReferenceRejected("invalid_image") from None
    candidate = analyzer.inspect_reference(image)
    if candidate.metadata["status"] != "accepted":
        reasons = candidate.metadata["reasons"]
        code = (
            reasons[0]
            if reasons and reasons[0] in {"no_face", "multiple_faces"}
            else "quality_rejected"
        )
        raise ReferenceRejected(code, candidate.metadata)
    vector = normalized_embedding(analyzer.embed(candidate.aligned))
    ok, jpeg = cv2.imencode(".jpg", candidate.aligned, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise RuntimeError("Reference encoding failed")
    return {
        "quality": candidate.metadata,
        "model_version": MODEL_VERSION,
        "embedding": vector.tolist(),
        "aligned_jpeg": base64.b64encode(jpeg).decode(),
    }
