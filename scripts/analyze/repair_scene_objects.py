"""오등록된 물체를 바로잡는다 -- scene_repair 의 제안을 CLI 에서 확인·적용.

화면(Repair 활동탭)이 같은 일을 하지만, 화면 없이 확인만 하고 싶을 때와
여러 데이터셋을 한 번에 훑을 때를 위해 남긴다.

    python scripts/analyze/repair_scene_objects.py <데이터셋 경로>
    python scripts/analyze/repair_scene_objects.py <데이터셋 경로> --apply

--apply 없이는 아무것도 쓰지 않는다. 제안은 좁은 조건에서만 나오지만
(scene_repair.suggest_object_fix 참고), 최종 판단은 기준 사진을 본 사람이
한다 -- 그래서 기본이 dry-run 이다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gello.scene.props import props_by_id                       # noqa: E402
from gello.scene.scene_format import iter_scene_files           # noqa: E402
from gello.scene.scene_repair import (                          # noqa: E402
    apply_object_fix,
    audit_scene,
    suggest_object_fix,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", type=Path, help="scene_*.hdf5 가 있는 데이터셋 폴더")
    ap.add_argument("--apply", action="store_true", help="실제로 고친다")
    args = ap.parse_args()

    props = props_by_id()
    files = iter_scene_files(args.root)
    if not files:
        print(f"scene 파일이 없다: {args.root}")
        return 1

    found = 0
    for path in files:
        fix = suggest_object_fix(path, props)
        if fix is None:
            continue
        found += 1
        # 이 정정이 없애는 것만 센다 -- strict 로 세면 나중에 정한 어휘
        # 규칙(그릇 목적지 on 금지) 위반이 섞여서 수가 부풀려진다.
        def _bad() -> int:
            return sum(v.episodes for v in
                       audit_scene(path, props, strict_relation=False))
        before = _bad()
        print(f"{fix.scene_id}  {fix.old_id} -> {fix.new_id}")
        print(f"    사유: {fix.reason}")
        print(f"    기록 불일치 에피소드: {before}")
        if args.apply:
            apply_object_fix(path, fix.old_id, fix.new_id)
            print(f"    고쳤다 -- 기록 불일치 {before} -> {_bad()}")

    if not found:
        print(f"제안할 정정이 없다 (scene {len(files)}개 검사)")
    elif not args.apply:
        print("\n--apply 를 붙이면 실제로 고친다 (기준 사진을 먼저 확인할 것)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
