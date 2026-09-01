"""Dataset tree, episode selection, deletion, and relabel operations."""

from __future__ import annotations

from pathlib import Path

import h5py
from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox, QTreeWidgetItem

from gello.data.episode_stats import TASK_DEV_LIMIT
from gello.data.libero_format import hdf5_repack_status, renumber_episodes
from gello.gui.gui_widgets import repo_id_error
from gello.gui.i18n import tr
from gello.gui.scene_gallery import invalidate_scene_thumbs
from gello.scene.scene_format import (
    delete_scene_episodes,
    iter_scene_files,
    list_scene_episodes,
    read_scene_metadata,
)


class DatasetOps:
    """Dataset tree, episode selection, deletion, and relabel operations."""

    def __init__(self, win) -> None:
        self.win = win

    # -------------------------------------------------------------------- root
    def dataset_root(self) -> Path:
        """Dataset 페이지의 폴더 -- 전용 입력이 있으면 그것, 없으면 수집 경로.
        (빌드 순서상 어느 쪽도 아직 없을 수 있다 -- 기본 경로로 폰백.)"""
        edit = (getattr(self.win, "dataset_root_edit", None)
                or getattr(self.win, "root_edit", None))
        if edit is None:
            return Path.home() / "libero_datasets"
        return Path(edit.text().strip()).expanduser()

    def browse_dataset_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self.win, tr("데이터 폴더"),
                                             self.win.dataset_root_edit.text())
        if d:
            self.win.dataset_root_edit.setText(d)
            self.refresh_dataset_tree()

    def browse_root(self) -> None:
        d = QFileDialog.getExistingDirectory(self.win, tr("데이터 저장 경로"), self.win.root_edit.text())
        if d:
            self.win.root_edit.setText(d)
            self.refresh_dataset_tree()

    # -------------------------------------------------------------------- tree
    def refresh_dataset_tree(self) -> None:
        self.win.dataset_tree.clear()
        root = self.dataset_root()
        if not root.is_dir():
            return
        # ---- scene 파일 (scene-v1). 재생·재판정 UI 는 #31 갤러리에서 --
        # 여기서는 목록·개수·quality 확인 + 삭제/트림 대상 선택용. 삭제는
        # legacy 와 같이 삭제 후 renumber -- delete_episodes.
        for path in iter_scene_files(root):
            item = QTreeWidgetItem([path.name, "", "scene"])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.win.dataset_tree.addTopLevelItem(item)
            try:
                if (self.win.session.active_file_path is not None
                        and path == self.win.session.active_file_path
                        and self.win.session.active_episode_cache is not None):
                    episodes = self.win.session.active_episode_cache
                else:
                    episodes = list_scene_episodes(path)
            except Exception as e:  # noqa: BLE001
                item.setText(1, f"({type(e).__name__})")
                continue
            for ep in episodes:
                label = f"  {ep['name']} · {ep.get('instruction_id', '')}"
                q = ep.get("quality_status") or (
                    "-" if ep.get("success") is None
                    else ("success" if ep["success"] else "failed"))
                child = QTreeWidgetItem([label, str(ep.get("num_samples", "")), q])
                child.setData(0, Qt.ItemDataRole.UserRole, ep["name"])
                child.setToolTip(0, ep.get("instruction", ""))
                item.addChild(child)
            item.setText(1, tr("{n}개").format(n=len(episodes)))
        for path in sorted(root.glob("*_demo.hdf5")):
            item = QTreeWidgetItem([path.name, "", ""])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            self.win.dataset_tree.addTopLevelItem(item)
            if self.win.session.active_file_path is not None and path == self.win.session.active_file_path:
                if self.win.session.active_episode_cache is None:
                    item.setText(1, tr("불러오는 중..."))
                    continue
                episodes = self.win.session.active_episode_cache
            else:
                try:
                    with h5py.File(path, "r") as f:
                        data = f["data"]
                        episodes = [{"name": n,
                                     "num_samples": int(data[n].attrs.get("num_samples", 0)),
                                     "success": (None if data[n].attrs.get("success") is None
                                                 else bool(data[n].attrs.get("success")))}
                                    for n in data]
                        episodes.sort(key=lambda d: int(d["name"].split("_")[1]))
                except OSError as e:
                    item.setText(1, f"({e})")
                    continue
            for ep in episodes:
                res = "-" if ep["success"] is None else (tr("성공") if ep["success"] else tr("실패"))
                child = QTreeWidgetItem(["  " + ep["name"], str(ep["num_samples"]), res])
                child.setData(0, Qt.ItemDataRole.UserRole, ep["name"])
                item.addChild(child)
            item.setText(1, tr("{n}개").format(n=len(episodes)))
        # 접은 채로 시작한다. 200줄 넘는 에피소드를 한 번에 펼쳐두면 정작 훑고
        # 싶은 task 목록이 화면 밖으로 밀린다. 필요한 파일만 열어두면 된다.
        self.win.dataset_tree.collapseAll()
        if hasattr(self.win, "scene_combo"):
            self.win.scene_ops.refresh_scene_combo()
        if hasattr(self.win, "gallery_scene_combo"):
            self.win._refresh_gallery_scenes()
        self.win._update_dataset_panel(self.selected_file())

    def selected_file(self) -> Path | None:
        items = self.win.dataset_tree.selectedItems()
        if not items:
            return None
        node = items[0] if items[0].parent() is None else items[0].parent()
        p = node.data(0, Qt.ItemDataRole.UserRole)
        return Path(p) if isinstance(p, str) else None

    def busy_reason(self) -> str:
        """Anything that may currently hold an .hdf5 open, by name."""
        for proc, label in ((self.win.procs.repack_process, tr("재압축")),
                            (self.win.procs.convert_process, tr("LeRobot 변환")),
                            (self.win.procs.upload_process, tr("HDF5 업로드"))):
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                return label
        return ""

    def on_dataset_selection(self) -> None:
        items = self.win.dataset_tree.selectedItems()
        item = items[0] if items else None
        # 파일 행을 골라도 오른쪽 Dataset 칸은 갱신된다 -- 재생은 에피소드 행에서만.
        self.win._update_dataset_panel(self.selected_file())
        if self.win.session.stats:
            self.win._refresh_rank_list()
            if item is not None and item.parent() is not None:
                self.win._show_analysis_for(
                    item.parent().data(0, Qt.ItemDataRole.UserRole),
                    item.data(0, Qt.ItemDataRole.UserRole))
        if item is None or item.parent() is None:
            return
        path = item.parent().data(0, Qt.ItemDataRole.UserRole)
        demo = item.data(0, Qt.ItemDataRole.UserRole)
        if not path or not demo:
            return
        self.win.playback_ops.play_episode(path, demo)

    # ------------------------------------------------------------------ select
    def on_select_jerky(self) -> None:
        """Selects the episodes that stand out *within their own task*.

        Both ends: rushing and dawdling are different mistakes but both are
        "not how this task is usually done". Compared within the task because
        mean_da is distance over time, so between tasks it ranks how far the
        arm must reach rather than how well it was driven.

        Nothing is deleted here. The selection lands in the same tree the
        operator deletes from, so they can play the takes first.
        """
        if not self.win.session.stats:
            self.win._refresh_analysis()
        if not self.win.session.stats:
            return
        flagged = {(e.path, e.demo) for e in self.win.session.stats if e.flagged}
        self.win.dataset_tree.clearSelection()
        n = 0
        for i in range(self.win.dataset_tree.topLevelItemCount()):
            parent = self.win.dataset_tree.topLevelItem(i)
            path = parent.data(0, Qt.ItemDataRole.UserRole)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if (path, child.data(0, Qt.ItemDataRole.UserRole)) in flagged:
                    child.setSelected(True)
                    # 접혀 있으면 "N개 선택됨"만 뜨고 무엇이 골랐는지 안 보인다.
                    parent.setExpanded(True)
                    n += 1
        self.win.log(f"[큐레이션] 같은 (scene·문장) 그룹 평균과 {TASK_DEV_LIMIT} 넘게 차이 나는 "
                 f"에피소드 {n}개를 선택했습니다." + ("" if n else " (없음)"))
        self.win.dataset_hint.setText(
            tr("튀는 에피소드 {n}개 선택됨 — 재생으로 확인한 뒤 '에피소드 삭제'로 지웁니다.")
            .format(n=n) if n else
            tr("같은 (scene·문장) 그룹 평균과 {d} 넘게 차이 나는 에피소드가 없습니다 "
               "(이 데이터셋은 균일합니다).").format(d=TASK_DEV_LIMIT))

    def on_select_failed(self) -> None:
        """Selects every episode marked failed, across all files.

        This is the other half of marking-instead-of-discarding: failures pile
        up during collection on purpose, and curation is where they go. Without
        this the operator would ctrl-click them one at a time down a tree of a
        hundred rows.
        """
        self.win.dataset_tree.clearSelection()
        n = 0
        # legacy 는 번역된 '실패', scene 은 quality_status 원문('failed')이
        # 상태 컬럼에 실린다 -- 둘 다 잡아야 한다 (scene 실패가 선택되지
        # 않던 실사용 버그).
        fail_labels = {tr("실패"), "failed"}
        for i in range(self.win.dataset_tree.topLevelItemCount()):
            parent = self.win.dataset_tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.text(2) in fail_labels:
                    child.setSelected(True)
                    parent.setExpanded(True)
                    n += 1
        self.win.log(f"[큐레이션] 실패로 표시된 에피소드 {n}개를 선택했습니다."
                 + ("" if n else " (없음)"))
        self.win.dataset_hint.setText(
            tr("실패 {n}개 선택됨 — '에피소드 삭제'로 한 번에 지웁니다.").format(n=n)
            if n else tr("실패로 표시된 에피소드가 없습니다."))

    # ------------------------------------------------------------------ relabel
    def on_relabel_selected(self) -> None:
        """scene 에피소드의 quality_status 를 성공↔실패로 뒤집는다.

        scene 체계의 큐레이션 수단이다 (삭제 없음, 변환이 success 만 내보냄).
        소유권 규칙은 삭제와 동일: 세션이 파일을 쥐고 있으면 saver 스레드
        경유, 아니면 직접 쓴다 (SceneWriter.set_quality_status 와 같은 필드).
        success/failed 이외의 상태(bad_data 등)는 건드리지 않는다.
        """
        by_file: dict = {}
        for item in self.win.dataset_tree.selectedItems():
            if item.parent() is None:
                continue
            p = Path(item.parent().data(0, Qt.ItemDataRole.UserRole))
            by_file.setdefault(p, []).append(item.data(0, Qt.ItemDataRole.UserRole))
        by_file = {p: v for p, v in by_file.items() if p.name.startswith("scene_")}
        if not by_file:
            QMessageBox.information(
                self.win, tr("선택 필요"),
                tr("재판정할 scene 에피소드를 선택하세요 (legacy 파일은 세션 중 "
                   "판정 버튼을 사용)."))
            return
        if self.relabel_episodes(by_file):
            self.refresh_dataset_tree()

    def on_gallery_relabel(self) -> None:
        by_file: dict = {}
        for item in self.win.gallery_list.selectedItems():
            d = item.data(Qt.ItemDataRole.UserRole)
            if d:
                by_file.setdefault(Path(d[0]), []).append(d[1])
        if not by_file:
            QMessageBox.information(self.win, tr("선택 필요"),
                                    tr("재판정할 에피소드를 선택하세요."))
            return
        if self.relabel_episodes(by_file):
            self.win._refresh_gallery()
            self.refresh_dataset_tree()

    def relabel_episodes(self, by_file: dict) -> bool:
        """재판정 공용 코어 -- Dataset 트리와 Gallery 가 같은 것을 쓴다.

        세션이 파일을 쥐고 있으면 HDF5 를 다시 열지 않는다. 같은 프로세스에서
        쓰기 중인 파일을 재오픈하면 h5py 가 거부하므로, 대신 saver 가 이미 연
        파일 핸들을 재사용하도록 큐 명령으로 복낸다. 판정값은 saver 가 채워주는
        ``active_episode_cache`` 에서 읽는다.
        """
        busy = self.busy_reason()
        if busy:
            QMessageBox.warning(self.win, tr("재판정 불가"),
                                tr("{job}이(가) 진행 중입니다.").format(job=busy))
            return False
        flipped = skipped_state = skipped_cache = 0
        cache: dict[str, dict] = {}
        if self.win.session.active_file_path is not None and self.win.session.active_episode_cache is not None:
            cache = {e["name"]: e for e in self.win.session.active_episode_cache}
        for path, names in by_file.items():
            owned = self.win.session.active_file_path is not None and path == self.win.session.active_file_path
            try:
                if owned:
                    # 세션 소유 파일: h5py 재오픈 없이 캐시에서 읽고 saver 큐로 쓴다.
                    for name in names:
                        e = cache.get(name)
                        if e is None:
                            skipped_cache += 1
                            self.win.log(f"[재판정] {path.name} / {name}: 캐시에 없어 건너뜀")
                            continue
                        q = str(e.get("quality_status", ""))
                        if "quality_status" not in e:
                            # 캐시 요약에 quality_status 가 없으면 success 로 판단.
                            success = e.get("success")
                            if success is True:
                                q = "success"
                            elif success is False:
                                q = "failed"
                        if q not in ("success", "failed"):
                            skipped_state += 1
                            continue
                        new_ok = q != "success"
                        self.win.worker.cmd_set_episode_success(name, new_ok)
                        flipped += 1
                else:
                    # 비소유 파일. 호출 경로(on_relabel_selected 의 scene 필터,
                    # scene 전용 Gallery)가 scene 파일만 넘기므로 legacy 분기는
                    # 두지 않는다 -- 도달 불가한 분기는 규약이 어긋난 채 썩는다.
                    with h5py.File(path, "a") as f:
                        for name in names:
                            q = str(f[name].attrs.get("quality_status", ""))
                            if q not in ("success", "failed"):
                                skipped_state += 1
                                continue
                            new_ok = q != "success"
                            f[name].attrs["quality_status"] = (
                                "success" if new_ok else "failed")
                            f[name].attrs["success"] = new_ok
                            flipped += 1
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self.win, tr("재판정 실패"),
                                     f"{path.name}\n{type(e).__name__}: {e}")
                return False
        parts = [f"[재판정] {flipped}개 뒤집음"]
        if skipped_state:
            parts.append(f"{skipped_state}개 건너뜀 (success/failed 아님)")
        if skipped_cache:
            parts.append(f"{skipped_cache}개 건너뜀 (세션 캐시에 없음)")
        self.win.log(", ".join(parts))
        return True

    # ------------------------------------------------------------------ delete
    def on_delete_selected(self) -> None:
        """Deletes the selected episode.

        Two paths, because who owns the file decides who may touch it. h5py is
        not thread-safe, so while a session has the file open, every
        file-touching call goes through that session's saver thread -- deleting
        behind its back would corrupt the file it is still writing into. When
        no session owns the file, nothing else has it open and this window can
        do it directly, which is the common case: curating yesterday's takes
        should not require connecting a robot first.
        """
        # 파일별로 묶는다. 여러 개를 지울 때 이름 하나씩 지우고 매번 번호를 다시
        # 매기면 두 번째부터는 이미 밀린 이름을 지우게 된다 -- 한 파일 안에서
        # 전부 지운 뒤 renumber는 마지막에 한 번만.
        by_file: dict = {}
        for item in self.win.dataset_tree.selectedItems():
            if item.parent() is None:
                continue
            p = item.parent().data(0, Qt.ItemDataRole.UserRole)
            by_file.setdefault(Path(p), []).append(item.data(0, Qt.ItemDataRole.UserRole))
        if not by_file:
            QMessageBox.information(self.win, tr("선택 필요"),
                                    tr("삭제할 에피소드를 선택하세요 (Ctrl/Shift로 여러 개)."))
            return
        if self.delete_episodes(by_file):
            self.refresh_dataset_tree()

    def describe_delete_targets(self, by_file: dict):
        """삭제 확인창용: (행 목록, 성공 개수, Hub 안내문). 파일을 읽지 못하면
        (세션이 쥔 파일 등) 캐시로 대신하고, 그것도 없으면 이름만 나열한다."""
        rows: list = []
        n_success = 0
        tasks: set = set()
        uids: set = set()
        for path, names in by_file.items():
            eps: dict = {}
            try:
                if path.name.startswith("scene_"):
                    src = (self.win.session.active_episode_cache
                           if (self.win.session.active_file_path is not None
                               and path == self.win.session.active_file_path)
                           else list_scene_episodes(path)) or []
                    eps = {e["name"]: e for e in src}
                else:
                    with h5py.File(path, "r") as f:
                        data = f["data"]
                        for n in names:
                            if n in data:
                                g = data[n]
                                ok = g.attrs.get("success", True)
                                eps[n] = {"episode_uid": n, "instruction": "",
                                          "quality_status": "success" if ok else "failed",
                                          "num_samples": int(g.attrs.get("num_samples", 0))}
            except Exception:  # noqa: BLE001 -- 잠금 등: 이름만
                eps = {}
            for n in names:
                e = eps.get(n)
                if e is None:
                    rows.append(f"  {path.name} / {n}")
                    continue
                q = str(e.get("quality_status", "?"))
                if q == "success":
                    n_success += 1
                instr = str(e.get("instruction", ""))
                if instr:
                    tasks.add(instr)
                if path.name.startswith("scene_") and e.get("episode_uid"):
                    uids.add(str(e["episode_uid"]))
                rows.append(f"  {e.get('episode_uid', n)}  [{q}]  {e.get('num_samples', '?')}f"
                            + (f"  {instr[:40]}" if instr else ""))
        hub_note = ""
        try:
            repo = self.win.upload.repo_id_for("repo_id")
        except Exception:  # noqa: BLE001
            repo = ""
        if repo and (tasks or uids) and not repo_id_error(repo):
            # 판정 단위는 에피소드(uid)다. Hub 의 meta/episode_uids.json 사이드카에
            # 지울 uid 가 있을 때만 "올라가 있다" 고 말한다. 사이드카가 없는 repo
            # (legacy 수집분만 있는 데이터셋)는 에피소드 단위 판정이 불가능하므로
            # 문장(task) 단위 일치를 '참고' 로만 표시한다 -- 같은 문장의 legacy
            # 에피소드가 있다고 이 에피소드가 올라간 것은 아니다 (실사용 혼란).
            try:
                from gello.data.dataset_sync import hub_episode_uids, hub_meta

                hub_uids, err = hub_episode_uids(repo)
                if err:
                    hub_note = ""
                elif hub_uids is not None:
                    hit = sorted(uids & hub_uids)
                    if hit:
                        hub_note = tr("Hub({r})에 이 에피소드 {k}개가 이미 올라가 "
                                      "있습니다 ({u}{more}) — 다음 전체 처리에서 "
                                      "'삭제됨' 으로 잡혀 재빌드(교체)가 필요합니다.")\
                            .format(r=repo, k=len(hit), u=", ".join(hit[:3]),
                                    more=" …" if len(hit) > 3 else "")
                    else:
                        hub_note = tr("Hub({r})에는 이 에피소드가 올라가 있지 않습니다 "
                                      "(uid 대조).").format(r=repo)
                else:
                    hub, _lens, err2 = hub_meta(repo)
                    if not err2:
                        same = [t for t in tasks if hub.get(t, 0) > 0]
                        if same:
                            hub_note = tr("참고: Hub({r})에는 uid 사이드카가 없어 에피소드 "
                                          "단위 확인이 안 됩니다. 같은 문장의 task {k}개가 "
                                          "있지만(legacy 수집분일 수 있음) 이 에피소드가 "
                                          "올라갔다는 뜻은 아닙니다.").format(r=repo, k=len(same))
            except Exception:  # noqa: BLE001 -- 오프라인 등: 안내 생략
                hub_note = ""
        return rows, n_success, hub_note

    def delete_episodes(self, by_file: dict) -> bool:
        """공용 삭제 경로. Dataset 패널과 Analysis 순위표가 같은 것을 쓴다 --
        세션 소유 검사와 실행 중 작업 검사를 두 벌로 두면 반드시 갈라진다."""
        busy = self.busy_reason()
        if busy:
            QMessageBox.warning(self.win, tr("삭제 불가"),
                                tr("{job}이(가) 진행 중입니다. 끝난 뒤 삭제하세요.").format(job=busy))
            return False
        total = sum(len(v) for v in by_file.values())
        # 확인창: 무엇을 지우는지(uid·문장·판정·프레임) 목록으로 보여주고,
        # 성공분이 섞였으면 경고, Hub 에 이미 올라간 에피소드면 재빌드 안내.
        # "실패만 선택" 으로 고른 정상 경로에서는 경고가 뜨지 않는다 -- 손으로
        # 잘못 고른 성공분만 눈에 띄게 하는 것이 목적이다.
        rows, n_success, hub_note = self.describe_delete_targets(by_file)
        detail = "\n".join(rows[:30]) + ("\n  …" if len(rows) > 30 else "")
        notes = [tr("삭제 후 남은 에피소드는 번호가 다시 매겨집니다 (scene 은 slot E번호·uid 도).")]
        if hub_note:
            notes.append(hub_note)
        notes.append(tr("파일 크기는 줄지 않습니다 (재압축 필요). 되돌릴 수 없습니다."))
        title = tr("에피소드 삭제")
        body = tr("에피소드 {n}개를 삭제합니다.\n\n{d}\n\n{notes}").format(
            n=total, d=detail, notes="\n".join(notes))
        if n_success:
            body = tr("⚠ 성공(success) 에피소드 {k}개가 포함되어 있습니다 — "
                      "정말 의도한 선택인지 확인하세요.\n\n").format(k=n_success) + body
            if QMessageBox.warning(
                    self.win, title, body,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
        elif QMessageBox.question(self.win, title, body) != QMessageBox.StandardButton.Yes:
            return False

        for path, names in by_file.items():
            owned = self.win.session.active_file_path is not None and path == self.win.session.active_file_path
            is_scene = path.name.startswith("scene_")
            if owned:
                # 세션이 파일을 쥐고 있으면 saver 스레드가 유일한 통로다. 매 삭제
                # 뒤 번호가 다시 매겨지므로 뒤에서부터 지워야 앞 이름이 안 밀린다.
                for name in sorted(names, key=lambda s: int(s.split("_")[1]), reverse=True):
                    self.win.worker.cmd_delete_episode(name)
                self.win.log(f"[삭제] {path.name}: {len(names)}개 요청 (세션 경유)")
                if is_scene:
                    # saver 가 삭제를 1건 완료할 때마다 episode_list_changed ->
                    # on_episode_list 가 카운터를 줄이며 썸네일을 지운다.
                    self.win._pending_scene_deletes += len(names)
                continue
            try:
                if is_scene:
                    delete_scene_episodes(path, names)
                    # renumber 로 uid 가 재배정되므로 해당 scene 의 썸네일 캐시를
                    # 전부 무효화한다. 삭제와 별도 try -- 썸네일 정리 실패가
                    # "삭제 실패" 로 오표기되면 안 된다 (삭제는 이미 성공했다).
                    try:
                        sid = read_scene_metadata(path).scene_id
                        n_thumbs = invalidate_scene_thumbs(sid)
                        if n_thumbs:
                            self.win.log(f"[썸네일] {path.name}: {n_thumbs}개 캐시 무효화")
                    except Exception as e:  # noqa: BLE001
                        self.win.log(f"[썸네일 캐시 정리 실패] {path.name}: {e}")
                else:
                    with h5py.File(path, "a") as f:
                        data = f["data"]
                        missing = [n for n in names if n not in data]
                        if missing:
                            raise KeyError(", ".join(missing))
                        for name in names:
                            del data[name]
                        renumber_episodes(data)
                self.win.log(f"[삭제] {path.name}: {len(names)}개 ({', '.join(sorted(names))})")
            except Exception as e:  # noqa: BLE001
                QMessageBox.critical(self.win, tr("삭제 실패"), f"{path.name}\n{type(e).__name__}: {e}")
                self.win.log(f"[삭제 실패] {path.name}: {type(e).__name__}: {e}")
        return True

    def on_delete_file(self) -> None:
        """Deletes a whole <task>_demo.hdf5. Never offered for the file a
        session is writing into -- that one is closed by ending the session."""
        path = self.selected_file()
        if path is None:
            QMessageBox.information(self.win, tr("선택 필요"), tr("삭제할 파일을 선택하세요."))
            return
        if self.win.session.active_file_path is not None and path == self.win.session.active_file_path:
            QMessageBox.warning(self.win, tr("삭제 불가"),
                                tr("지금 수집 중인 파일입니다. 먼저 세션을 종료하세요."))
            return
        busy = self.busy_reason()
        if busy:
            QMessageBox.warning(self.win, tr("삭제 불가"),
                                tr("{job}이(가) 진행 중입니다. 끝난 뒤 삭제하세요.").format(job=busy))
            return
        st = hdf5_repack_status(path)
        # 진짜 삭제한다. 오클릭 대책은 되돌리기가 아니라 닿기 어렵게 두는 것
        # (이 항목은 Dataset 메뉴에만 있다) -- 반쯤 지워진 채 디스크만 차지하는
        # 휴지통은 결국 아뮼브 비우지 않는다.
        # 이 파일의 에피소드가 Hub 에 이미 있으면 다음 전체 처리가 '삭제됨' 으로
        # 잡아 재빌드(교체)를 요구한다 -- 지금 지우는 것이 리모트에 어떤 결과를
        # 낳는지 삭제 순간에 알린다 (오프라인이면 안내 생략).
        hub_line = tr("Hub 에 올린 사본은 지금은 그대로지만, 다음 전체 처리 때 "
                      "로컬 기준으로 재빌드되어 교체됩니다.")
        if path.name.startswith("scene_"):
            try:
                names = [e["name"] for e in list_scene_episodes(path)]
                _rows, _n_ok, note = self.describe_delete_targets({path: names})
                if note:
                    hub_line = note
            except Exception:  # noqa: BLE001 -- 잠금/오프라인: 기본 안내
                pass
        confirm = QMessageBox.warning(
            self.win, tr("파일 삭제"),
            tr("{f}\n\n에피소드 {n}개, {mb:.1f} MB 를 완전히 삭제합니다.\n"
               "되돌릴 수 없습니다.\n{h}").format(
                   f=path.name, n=st["episodes"], mb=st["size"] / 1e6, h=hub_line),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
            self.win.log(f"[파일 삭제] {path.name} ({st['episodes']}개 에피소드, "
                     f"{st['size'] / 1e6:.1f} MB)")
        except OSError as e:
            QMessageBox.critical(self.win, tr("삭제 실패"), str(e))
            self.win.log(f"[파일 삭제 실패] {path.name}: {e}")
        self.refresh_dataset_tree()
