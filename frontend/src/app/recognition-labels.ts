export const recognitionOutcomes: Record<string, string> = {
  no_face: 'No face detected', ambiguous: 'Face association pending', quality_rejected: 'Below quality threshold',
  collecting_samples: 'Collecting comparison images', matched: 'Similarity candidate', below_threshold: 'Below comparison threshold',
  gallery_empty: 'No registered faces', search_unavailable: 'Comparison service unavailable',
  insufficient_consensus: 'Inconsistent matches across frames'
};
export const recognitionReasons: Record<string, string> = {
  no_face: 'No face detected', multiple_faces: 'Multiple faces detected', face_too_small: 'Face too small',
  face_clipped: 'Face region clipped', invalid_face_box: 'Invalid face region', blurred: 'Blurry video',
  too_dark: 'Too dark', too_bright: 'Too bright', low_confidence: 'Low detection confidence',
  landmark_geometry: 'Unstable facial landmarks', pose_exceeded: 'Face pose exceeds limits',
  pose_unavailable: 'Pose unavailable', quality_below_threshold: 'Low overall quality', cache_capacity: 'Analysis capacity limit'
};
