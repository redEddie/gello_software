"""Doctor feature -- 찍은 뒤에 바로잡는 화면.

닥터는 **scene 파일이 자기에 대해 하는 주장**을 실제와 대조한다. 주장이
세 가지라 닥터도 셋이다 (2026-09-07 사용자 정리):

    "나는 knu-1.2.0 이다"          <-> 파일 내용        스키마 닥터 (#47)
    "나는 이 계획을 채운다"        <-> 계획의 목표 수   진행 닥터 (#48)
    "초록 그릇이 이렇게 놓여 있다" <-> 문장과 기준 사진 기록 닥터 (#50)

기록 닥터와 진행 닥터가 있다. 처방이 다르다 -- 기록은 파일을 고치고, 진행은
고칠 것이 없어서 "더 찍어라" 뿐이다 (그래서 조작자를 그 scene 으로 데려간다). 나머지 둘이 붙을 자리는 중앙 탭이고,
활동탭은 하나다 -- 운용자에게는 "내 데이터셋 어디가 잘못됐나" 라는 하나의
질문이라 세 군데를 뒤지게 하면 안 된다.

닥터는 진단만 하지 않는다. 진단된 것을 고치는 데까지가 닥터다.
"""
from apps.workspace.features.doctor.ops import DoctorOps
from apps.workspace.features.doctor.page import build_doctor
from apps.workspace.features.doctor.progress_tab import build_progress_tab
from apps.workspace.features.doctor.record_tab import build_record_tab

__all__ = ["DoctorOps", "build_doctor", "build_progress_tab",
           "build_record_tab"]
