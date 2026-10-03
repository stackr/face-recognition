export const recognitionOutcomes: Record<string, string> = {
  no_face: '얼굴 미검출', ambiguous: '얼굴 연결 보류', quality_rejected: '품질 기준 미달',
  collecting_samples: '비교 사진 수집 중', matched: '유사도 후보', below_threshold: '비교 기준 미달',
  gallery_empty: '등록 얼굴 없음', search_unavailable: '비교 서비스 연결 실패',
  insufficient_consensus: '프레임 간 비교 불일치'
};
export const recognitionReasons: Record<string, string> = {
  no_face: '얼굴 미검출', multiple_faces: '여러 얼굴 검출', face_too_small: '얼굴이 작음',
  face_clipped: '얼굴 영역이 잘림', invalid_face_box: '얼굴 영역 오류', blurred: '흐린 영상',
  too_dark: '너무 어두움', too_bright: '너무 밝음', low_confidence: '검출 신뢰도 부족',
  landmark_geometry: '얼굴 특징점 불안정', pose_exceeded: '얼굴 각도 기준 초과',
  pose_unavailable: '자세 측정 불가', quality_below_threshold: '종합 품질 부족', cache_capacity: '검사 용량 제한'
};
