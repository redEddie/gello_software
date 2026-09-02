"""에피소드 품질 판정 어휘.

씬 파일에도 데이터셋 동기화에도 변환 스크립트에도 나오는 공용 낱말이라
어느 한 기능 폴더에 두면 화살표가 양쪽으로 생긴다 -- 실제로 이 상수 하나
때문에 gello/data -> gello/scene 순환이 있었다 (2026-09-02).

여기는 아무것도 임포트하지 않는 잎사귀다. 새 상태를 추가할 때는
QUALITY_STATUSES 에도 넣어야 검증이 그것을 받아들인다.
"""

QUALITY_SUCCESS = "success"
QUALITY_FAILED = "failed"
QUALITY_BAD_DATA = "bad_data"      # 데이터 자체가 불량 (프레임 stall, 크롭 사고 등)
QUALITY_RETAKE = "retake"          # 다시 찍기로 한 것 -- 새 에피소드가 대체한다
QUALITY_DEPRECATED = "deprecated"  # 규칙 변경 등으로 배포에서 제외

QUALITY_STATUSES = (
    QUALITY_SUCCESS,
    QUALITY_FAILED,
    QUALITY_BAD_DATA,
    QUALITY_RETAKE,
    QUALITY_DEPRECATED,
)
