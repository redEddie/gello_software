"""활동 키 -> 우측 패널 빌더.

우측 패널은 활동탭마다 자기 구현을 갖는다 (2026-09-07 사용자 결정, 규칙의
정본은 layout.build_right 의 주석). 여기 없는 활동은 세션 페이지를 함께
쓴다 -- 아직 자기 것을 안 만든 것이지 "정보를 골라 보여주는" 것이 아니다.

**패널이 담는 것은 그 화면에서 할 수 있는 일(동작)이다** -- 정보만 있는
패널은 상호작용할 것이 없어 아무도 안 본다 (2026-09-07 사용자 지적).
격자 칸·재생 버튼 같은 직접 조작은 만지는 자리에 남는다.
"""
from apps.workspace.features.doctor.right_panel import build_doctor_right
from apps.workspace.features.scene.right_panel import build_configure_right

RIGHT_BUILDERS = {
    "configure": build_configure_right,
    "doctor": build_doctor_right,
}

__all__ = ["RIGHT_BUILDERS"]
