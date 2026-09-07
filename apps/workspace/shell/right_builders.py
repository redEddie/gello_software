"""활동 키 -> 우측 패널 빌더.

우측 패널은 활동탭마다 자기 구현을 갖는다 (2026-09-07 사용자 결정, 규칙의
정본은 layout.build_right 의 주석). 여기 없는 활동은 세션 페이지를 함께
쓴다 -- 아직 자기 것을 안 만든 것이지 "정보를 골라 보여주는" 것이 아니다.

Doctor 가 첫 구현이다.
"""
from apps.workspace.features.doctor.right_panel import build_doctor_right

RIGHT_BUILDERS = {
    "doctor": build_doctor_right,
}

__all__ = ["RIGHT_BUILDERS"]
