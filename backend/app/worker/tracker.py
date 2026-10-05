"""Pinned Ultralytics ByteTrack adapter with per-camera IDs and wall-time expiry."""

from itertools import count
from types import SimpleNamespace


class CameraTracker:
    def __init__(self, settings):
        from ultralytics.trackers.byte_tracker import BYTETracker, STrack

        # Upstream's default counter is process-global and resets on construction.
        # Override its two hooks so starting another camera cannot reuse live IDs.
        identifiers = count(1)

        class LocalTrack(STrack):
            @staticmethod
            def next_id():
                return next(identifiers)

        class LocalByteTracker(BYTETracker):
            track_class = LocalTrack

            @staticmethod
            def reset_id():
                pass

        self.tracker = LocalByteTracker(
            SimpleNamespace(
                track_high_thresh=settings.track_high_threshold,
                track_low_thresh=settings.track_low_threshold,
                new_track_thresh=settings.new_track_threshold,
                track_buffer=max(1, round(settings.track_lost_seconds * settings.detection_fps)),
                match_thresh=0.8,
                fuse_score=True,
            )
        )
        self.last_update = None
        self.fps = settings.detection_fps
        self.lost_seconds = settings.track_lost_seconds
        self.seen = {}

    def live_ids(self):
        return {
            track.track_id for track in self.tracker.tracked_stracks + self.tracker.lost_stracks
        }

    def update(self, boxes, frame, now):
        # Predict for missed analysis ticks, bounded by retention. Long gaps expire
        # tracks before association, so identities cannot survive a stalled stream.
        tracker = self.tracker
        for attr in ("tracked_stracks", "lost_stracks"):
            retained = []
            for track in getattr(tracker, attr):
                if now - self.seen.get(track.track_id, now) > self.lost_seconds:
                    track.mark_removed()
                else:
                    retained.append(track)
            setattr(tracker, attr, retained)
        if self.last_update is not None:
            skipped = max(
                0, min(round((now - self.last_update) * self.fps) - 1, tracker.max_frames_lost)
            )
            for _ in range(skipped):
                tracker.multi_predict(tracker.tracked_stracks + tracker.lost_stracks)
            tracker.frame_id += skipped
        self.last_update = now
        rows = tracker.update(boxes, frame)
        live = {track.track_id for track in tracker.tracked_stracks + tracker.lost_stracks}
        self.seen = {
            identifier: when for identifier, when in self.seen.items() if identifier in live
        }
        for row in rows:
            self.seen[int(row[4])] = now
        return [
            {
                "track_id": int(row[4]),
                "bbox": [round(float(x), 1) for x in row[:4]],
                "confidence": round(float(row[5]), 3),
            }
            for row in rows
        ]

    def predict(self, image, now):
        """Project active boxes between detector ticks without new detections."""
        height, width = image.shape[:2]
        rows = []
        for track in self.tracker.tracked_stracks:
            if now - self.seen.get(track.track_id, now) > self.lost_seconds:
                continue
            mean = track.mean.copy()
            elapsed = max(0, now - (self.last_update if self.last_update is not None else now)) * self.fps
            mean[:4] += mean[4:] * elapsed
            box_width, box_height = max(1, mean[2] * mean[3]), max(1, mean[3])
            x, y = mean[:2]
            bbox = [
                max(0, min(width, x - box_width / 2)),
                max(0, min(height, y - box_height / 2)),
                max(0, min(width, x + box_width / 2)),
                max(0, min(height, y + box_height / 2)),
            ]
            if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                rows.append(
                    {
                        "track_id": track.track_id,
                        "bbox": [round(float(v), 1) for v in bbox],
                        "confidence": round(float(track.score), 3),
                    }
                )
        return rows
