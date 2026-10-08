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


def decode_image(content):
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
    return image


def analyze_reference(analyzer, content):
    image = decode_image(content)
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


def find_faces_in_photo(analyzer, content, settings, cancel, gallery, allowed_person_ids):
    """Find reference candidates per face; keep uploaded photos ephemeral."""
    from app.worker.uploaded_faces import UploadedFaceAnalyzer
    from app.worker.video_faces import VideoTestError, detect_all

    image = decode_image(content)
    try:
        detected = detect_all(
            analyzer.models,
            image,
            settings.face_test_max_faces_per_frame,
            cancel,
            detection_threshold=settings.video_face_detection_threshold,
            min_face_size=settings.video_face_min_size,
            full_frame_fallback=True,
        )
    except VideoTestError as exc:
        if exc.code == "face_limit_exceeded":
            raise ReferenceRejected(exc.code) from None
        raise RuntimeError("Photo analysis interrupted") from None
    snapshot = gallery.snapshot()
    threshold = settings.face_match_threshold
    faces = []
    for index, face in enumerate(detected, 1):
        if cancel.is_set():
            raise RuntimeError("Photo analysis interrupted")
        bbox = np.round(face["bbox"], 1).tolist()
        candidate = UploadedFaceAnalyzer(analyzer, {tuple(bbox): face}).inspect(image, bbox)
        matches = []
        if candidate.aligned is not None:
            vector = normalized_embedding(analyzer.embed(candidate.aligned))
            searched = gallery.search_snapshot(
                vector,
                snapshot,
                allowed_person_ids=allowed_person_ids,
                limit=10,
            )
            matches = searched["matches"]
        faces.append(
            {
                "index": index,
                "bbox": bbox,
                "quality": candidate.metadata,
                "matches": matches,
            }
        )
    height, width = image.shape[:2]
    scale = min(1, 1600 / max(height, width))
    preview = (
        cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
    )
    ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise RuntimeError("Photo encoding failed")
    return {
        "faces": faces,
        "threshold": threshold,
        "gallery_revision": snapshot[0][0],
        "image_width": width,
        "image_height": height,
        "preview_data_url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode(),
    }
