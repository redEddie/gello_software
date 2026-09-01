"""Batch pipeline dialog (repack → convert → upload)."""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gello.data.hub_upload_state import changed_files
from gello.data.libero_format import hdf5_repack_status
from gello.gui.gui_widgets import HfAccountDialog, Recents, hf_account
from gello.gui.i18n import tr


class PipelineDialog(QDialog):
    """Decide once, then walk away: compares Hub against the curated files and
    proposes the run that makes them match.

    The decision it exists to surface is which of two very different runs is
    correct. Appending is cheap but only valid while no already-pushed task has
    lost episodes; once one has, the Hub copy holds takes the operator deleted
    and nothing short of a rebuild removes them (LeRobot has no episode delete).
    Choosing wrong is invisible afterwards -- the dataset simply contains bad
    demonstrations -- so the comparison is done for the operator and the
    recommendation is pre-selected, but the button is theirs to press.
    """

    def __init__(self, parent: QWidget, data_root: str, plan: dict,
                 lerobot_repo: str, hdf5_repo: str, lerobot_root: str,
                 scripts: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("전체 처리 (재압축 → 변환 → 업로드)"))
        self.setMinimumWidth(820)
        self.plan = plan
        self.data_root = data_root
        self._scripts = scripts
        layout = QVBoxLayout(self)
        self._recents = Recents()

        head = QLabel()
        head.setWordWrap(True)
        action = plan["action"]
        if action == "blocked":
            head.setText(tr("Hub 상태를 읽지 못했습니다: {e}\n\n확실하지 않은 채로 올리지 "
                            "않습니다. 네트워크나 계정을 확인한 뒤 다시 여세요.").format(
                                e=plan["error"]))
            head.setStyleSheet("color:#e74c3c; font-weight:bold;")
        elif action == "up_to_date":
            head.setText(tr("Hub이 이미 로컬과 같습니다 ({n}개). 변환/업로드할 것이 "
                            "없습니다.").format(n=plan["local_total"]))
            head.setStyleSheet("color:#27ae60; font-weight:bold;")
        elif action == "rebuild":
            if plan["shrunk"]:
                head.setText(tr(
                    "이미 올라간 task에서 에피소드 {n}개가 삭제되었습니다. LeRobot은 게시된 "
                    "에피소드를 지울 수 없으므로, 전체를 다시 만들어 Hub을 교체해야 합니다 "
                    "(오래 걸립니다).").format(n=plan["shrunk"]))
            else:
                head.setText(tr(
                    "task {n}개의 에피소드 이력이 Hub와 어긋나 있습니다 (길이 지문 "
                    "불일치 — 지우고 다시 찍은 흔적). 이어붙이면 엉뚱한 에피소드가 "
                    "붙으므로, 전체를 다시 만들어 Hub을 교체해야 합니다.").format(
                        n=plan.get("mismatch", 0)))
            head.setStyleSheet("color:#e67e22; font-weight:bold;")
        else:
            head.setText(tr("새 에피소드 {n}개를 이어붙이면 됩니다 (Hub {h} → {l}). "
                            "이미 올라간 task에서 삭제된 것은 없습니다.").format(
                                n=plan["added"], h=plan["hub_total"], l=plan["local_total"]))
            head.setStyleSheet("color:#27ae60; font-weight:bold;")
        layout.addWidget(head)

        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels([tr("task"), tr("Hub"), tr("로컬"), tr("비고")])
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 420)
        tree.setMinimumHeight(200)
        for r in plan["rows"]:
            item = QTreeWidgetItem([r["task"], str(r["hub"]), str(r["local"]), r["note"]])
            if r["delta"] < 0:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.red)
            elif r["delta"] > 0:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.darkGreen)
            elif "편집" in r["note"]:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.darkYellow)
            tree.addTopLevelItem(item)
        layout.addWidget(tree)

        if plan["ambiguous"]:
            warn = QLabel(tr(
                "개수는 같지만 재압축 이후 편집된 흔적이 있는 task가 있습니다: {t}\n"
                "지우고 다시 찍었다면 이어붙이기로는 옛 에피소드가 Hub에 남습니다. "
                "확실하지 않으면 '전체 재빌드'를 고르세요.").format(
                    t=", ".join(x[:40] for x in plan["ambiguous"])))
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#e67e22;")
            layout.addWidget(warn)

        mode = QGroupBox(tr("LeRobot 처리 방식"))
        mcol = QVBoxLayout(mode)
        # 기본값은 언제나 전체 재빌드다. 큐레이션이 이미 올라간 에피소드를 지우는
        # 일이 잦은데, --resume 은 append 만 하므로 지운 에피소드의 청크가 Hub에
        # 남는다 -- 선언된 개수는 줄었는데 파일은 남은 상태가 된다. 빠른 쪽을
        # 기본으로 두면 그 상태가 기본이 된다.
        self.mode_rebuild = QRadioButton(
            tr("전체 재빌드 — 처음부터 만들어 Hub 교체 (삭제 반영, 권장)"))
        self.mode_resume = QRadioButton(
            tr("이어붙이기 — 새 에피소드만 추가 (빠르지만 지운 에피소드가 Hub에 남음)"))
        self.mode_rebuild.setChecked(True)
        self.mode_resume.setChecked(False)
        self.mode_resume.setEnabled(action in ("resume", "up_to_date"))
        mcol.addWidget(self.mode_rebuild)
        mcol.addWidget(self.mode_resume)
        resume_note = QLabel(tr(
            "이어붙이기는 에피소드를 하나도 지우지 않았을 때만 안전합니다."))
        resume_note.setStyleSheet("color:#e67e22;")
        resume_note.setWordWrap(True)
        mcol.addWidget(resume_note)
        layout.addWidget(mode)

        opts = QGroupBox(tr("함께 할 일"))
        ocol = QVBoxLayout(opts)
        # 다이얼로그를 연 시점의 판정으로 고정한다 -- steps() 는 재압축 단계가
        # 돌기 *전에* 호출되므로 그때 다시 판정해도 같지만, 두 곳이 따로 계산
        # 하면 언젠가 어긋난다.
        self._repack_todo = [p for p in plan["paths"]
                             if not hdf5_repack_status(p)["repacked"]]
        n_repack = len(self._repack_todo)
        self.repack_check = QCheckBox(
            tr("재압축 — 필요한 파일 {n}개").format(n=n_repack))
        self.repack_check.setChecked(n_repack > 0)
        self.repack_check.setEnabled(n_repack > 0)
        ocol.addWidget(self.repack_check)
        self.hdf5_check = QCheckBox(tr("원본 HDF5도 Hub에 업로드 (9GB 기준 약 15분)"))
        ocol.addWidget(self.hdf5_check)
        # 업로드 대상은 업로드 장부(gello/hub_upload_state.py)가 고른다:
        # 지난 업로드 성공 이후 (크기, mtime)이 바뀐 파일 + 기록 없는 파일
        # + 이번에 재압축될 파일. 예전의 "재압축한 파일만" 방식은 attr 만
        # 고친 파일(라벨 교정, 삭제·재번호)을 빠뜨렸다 -- 실제로 문법 교정분
        # 5개가 Hub에 안 올라간 사고가 있었다 (2026-08-25 교체). 변경 없는
        # 파일을 올려도 Hub이 해시로 전송은 건너뛰지만 그 판정에 파일 전체를
        # 읽는 시간이 들어서, 자동 선택이 그 시간을 없앤다. 장부가 모르는
        # 밖의 변화(Hub 쪽 삭제 등)를 위해 체크 해제 = 전체 강제 업로드
        # 탈출구를 남긴다. 파일마다 '왜 올라가는지'는 이 체크박스 툴팁과
        # 시작 로그, Hub 커밋 메시지 꼬리표 세 곳에 보인다.
        sel0 = self._hdf5_upload_selection(hdf5_repo)
        self.hdf5_only_new_check = QCheckBox(
            tr("  ↳ 변경된 파일만 자동 선택 ({n}개) — 해제하면 전체 강제 업로드")
            .format(n=len(sel0)))
        self.hdf5_only_new_check.setToolTip(
            "\n".join(f"{Path(x).name}: {r}" for x, r in sel0)
            or tr("지난 업로드 이후 바뀐 파일이 없습니다."))
        self.hdf5_only_new_check.setChecked(True)
        self.hdf5_only_new_check.setEnabled(False)  # hdf5_check 켜야 활성화
        self.hdf5_check.toggled.connect(self.hdf5_only_new_check.setEnabled)
        ocol.addWidget(self.hdf5_only_new_check)
        # --only-success 체크박스는 없앴다. 이 팀 규약은 "실패는 푸시 전에
        # 파일에서 삭제"라 필터링 업로드를 쓸 일이 없고, 실수로 켜면 로컬
        # (실패 포함)과 Hub(성공만)의 에피소드 시퀀스가 어긋나 길이 지문
        # 검증(dataset_sync)과 resume 스킵 산술이 둘 다 깨진다. CLI 플래그는
        # 수동 용도로 convert_libero_to_lerobot.py 에 남아 있다.
        layout.addWidget(opts)

        grid = QGridLayout()
        grid.addWidget(QLabel(tr("LeRobot Repo ID:")), 0, 0)
        self.lerobot_repo_edit = QLineEdit(lerobot_repo)
        grid.addWidget(self.lerobot_repo_edit, 0, 1)
        grid.addWidget(QLabel(tr("HDF5 Repo ID:")), 1, 0)
        self.hdf5_repo_edit = QLineEdit(hdf5_repo)
        grid.addWidget(self.hdf5_repo_edit, 1, 1)
        grid.addWidget(QLabel(tr("로컬 변환 폴더:")), 2, 0)
        self.root_edit = QLineEdit(lerobot_root)
        grid.addWidget(self.root_edit, 2, 1)
        layout.addLayout(grid)

        note = QLabel(tr(
            "시작할 때 로컬 변환 폴더를 비웁니다. 그래야 이어붙이기가 Hub의 현재 상태를 "
            "기준으로 삼습니다 — 그 폴더의 내용은 HDF5에서 언제든 다시 만들 수 있습니다."))
        note.setWordWrap(True)
        note.setStyleSheet("color:#888;")
        layout.addWidget(note)

        acct_row = QHBoxLayout()
        acct_text, acct_color = hf_account()
        self.acct_label = QLabel(acct_text)
        self.acct_label.setStyleSheet(f"color:{acct_color}; font-weight:bold;")
        self.acct_label.setWordWrap(True)
        acct_row.addWidget(self.acct_label, 1)
        acct_btn = QPushButton(tr("계정 전환..."))
        acct_btn.clicked.connect(self._on_account)
        acct_row.addWidget(acct_btn)
        layout.addLayout(acct_row)

        # 삭제 보호 게이트: Hub 에서 사라질 에피소드가 있으면(rebuild 로
        # 교체 시 실제 삭제) 이해했다는 체크 없이는 시작 버튼이 열리지
        # 않는다. legacy 파일을 old_data/ 로 치워 둔 상태에서 옛 repo 를
        # 대상으로 돌리면 수백 개가 조용히 사라지는 사고의 마지막 잠금이다.
        self.shrink_ack = None
        if plan.get("shrunk"):
            self.shrink_ack = QCheckBox(tr(
                "Hub에서 에피소드 {n}개가 삭제되는 것을 확인했습니다 "
                "(로컬에 없는 에피소드는 재빌드 후 Hub에서 사라집니다)")
                .format(n=plan["shrunk"]))
            self.shrink_ack.setStyleSheet("color:#e74c3c; font-weight:bold;")
            layout.addWidget(self.shrink_ack)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText(tr("시작하고 퇴근"))
        self._action_ok = action != "blocked"
        self._update_ok_enabled()
        if self.shrink_ack is not None:
            self.shrink_ack.toggled.connect(self._update_ok_enabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_ok_enabled(self, *_args) -> None:
        ok = self._action_ok
        if self.shrink_ack is not None and not self.shrink_ack.isChecked():
            ok = False
        self._ok.setEnabled(ok)

    def _on_account(self) -> None:
        HfAccountDialog(self).exec()
        text, color = hf_account()
        self.acct_label.setText(text)
        self.acct_label.setStyleSheet(f"color:{color}; font-weight:bold;")


    def _hdf5_upload_selection(self, repo: str) -> list:
        """업로드 대상 [(경로 str, 사유 str)] -- 장부 기준 변경/신규 파일에
        이번 실행에서 재압축될 파일을 합친다 (재압축은 mtime 을 바꾸므로
        다음 판정에는 어차피 걸리지만, 같은 실행 안에서 놓치지 않게)."""
        sel = {str(x): r for x, r in changed_files(repo, self.plan["paths"])}
        if getattr(self, "repack_check", None) is None or \
                self.repack_check.isChecked():
            for x in self._repack_todo:
                sel.setdefault(str(x), tr("재압축 — 이번 실행에서 다시 압축됨"))
        return [(x, sel[x]) for x in map(str, self.plan["paths"]) if x in sel]
    def steps(self) -> list:
        """The ordered subprocess steps this run will execute."""
        rebuild = self.mode_rebuild.isChecked()
        lerobot_repo = self.lerobot_repo_edit.text().strip()
        hdf5_repo = self.hdf5_repo_edit.text().strip()
        root = self.root_edit.text().strip()
        paths = self.plan["paths"]
        self._recents.add("repo_id", lerobot_repo)
        self._recents.add("hdf5_repo_id", hdf5_repo)
        self._recents.add("lerobot_root", root)

        steps = []
        if self.repack_check.isChecked() and self._repack_todo:
            steps.append({"name": tr("재압축"), "program": sys.executable,
                          "args": [self._scripts['repack'], *self._repack_todo]})
        convert = [self._scripts['convert'], *paths, "--repo-id", lerobot_repo, "--root", root]
        if not rebuild:
            convert.append("--resume")
        steps.append({"name": tr("LeRobot 변환") + ("" if not rebuild else tr(" (전체 재빌드)")),
                      "program": sys.executable, "args": convert, "clear_root": root})
        push = [self._scripts['convert'], "--repo-id", lerobot_repo, "--root", root,
                "--push-only", "--no-private"]
        if rebuild:
            push.append("--replace")
        steps.append({"name": tr("LeRobot 업로드"), "program": sys.executable, "args": push})
        if self.hdf5_check.isChecked():
            if self.hdf5_only_new_check.isChecked():
                # repo 를 다이얼로그에서 바꿨을 수 있으니 여기서 다시 판정한다.
                sel = self._hdf5_upload_selection(hdf5_repo)
                if sel:
                    steps.append({
                        "name": tr("HDF5 원본 업로드 (변경분 {n}개)")
                        .format(n=len(sel)),
                        "detail": "; ".join(
                            f"{Path(x).name}: {r}" for x, r in sel),
                        "program": sys.executable,
                        "args": [self._scripts['upload'], *[x for x, _ in sel],
                                 "--repo-id", hdf5_repo, "--no-private"]})
                else:
                    # 프로세스 없는 정보용 단계 -- '왜 안 올라갔는지'가
                    # 로그와 요약에 남는다.
                    steps.append({"name": tr("HDF5 원본 업로드 — 생략"),
                                  "note": tr("지난 업로드 이후 바뀐 파일이 "
                                             "없습니다 (장부 기준).")})
            else:
                steps.append({"name": tr("HDF5 원본 업로드 (전체 강제)"),
                              "program": sys.executable,
                              "args": [self._scripts['upload'], *paths, "--repo-id",
                                       hdf5_repo, "--no-private"]})
        return steps


def _relax_min_widths(root: QWidget) -> None:
    """좌측 패널은 가로 스크롤이 없으므로 자식들이 패널 폭에 맞춰 줄어들 수
    있어야 한다. 버튼·체크박스·라디오는 텍스트 전체 폭을 최소로 고집하는
    기본 정책이라 좁은 패널에서 페이지를 잘리게 만든다 -- 수평 최소를 풀어
    좁아지면 글자가 생략되는 쪽을 택한다 (2026-08-13 사용자 결정: 200px
    수준까지 축소 허용, ... 요약 표시 허용). '...' 찾아보기처럼 명시적으로
    고정폭을 준 위젯은 건드리지 않는다."""
    for w in root.findChildren(QWidget):
        if isinstance(w, (QPushButton, QCheckBox, QRadioButton)):
            if w.maximumWidth() >= 16777215:  # 명시 고정폭은 존중
                sp = w.sizePolicy()
                sp.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                w.setSizePolicy(sp)
    # 폼의 '라벨+입력 나란히' 배치도 최소 폭을 만든다 -- 좁아지면 입력칸이
    # 라벨 아래로 내려가게 해서 폭 하한을 더 낮춘다.
    for f in root.findChildren(QFormLayout):
        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    # 긴 안내문 라벨이 wordWrap 없이 폭을 강제하는 경우가 페이지마다 하나씩
    # 숨어 있다(업로드 큐 안내문 등). 일괄 줄바꿈 -- 단, 수평 Ignored 정책
    # 라벨(SceneInfoView 의 격자처럼 일부러 줄바꿈을 막은 것)은 제외.
    for lb in root.findChildren(QLabel):
        if lb.sizePolicy().horizontalPolicy() != QSizePolicy.Policy.Ignored:
            lb.setWordWrap(True)
            # wordWrap 만으로는 QFormLayout 이 높이를 한 줄치로 줘서 두 줄째가
            # 잘린다(오른쪽 패널 WIDE_FIELDS 에서 이미 확인된 Qt 동작).
            # heightForWidth 를 켜야 접힌 만큼 세로가 확보된다.
            sp = lb.sizePolicy()
            sp.setHeightForWidth(True)
            sp.setVerticalPolicy(QSizePolicy.Policy.MinimumExpanding)
            lb.setSizePolicy(sp)


def _shrinkable_combo(c: QComboBox) -> None:
    """항목 텍스트(카메라 이름, scene 설명 등)가 길어도 콤보가 패널 폭에 맞춰
    줄어들 수 있게 한다. 기본 정책은 가장 긴 항목만큼 최소 폭을 요구해서,
    좁은 좌측 패널에서 페이지 전체가 오른쪽으로 잘려 나갔다 (가로 스크롤을
    쓰지 않는다는 원칙과 충돌). 펼친 목록은 전체 텍스트를 그대로 보여준다."""
    c.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(6)


class SceneInfoView(QWidget):
    """describe_scene 출력 표시용 — 좁은 패널에서도 잘리지 않는 반응형.

    일반 문장 줄(objects, 빈 존, 설명)은 줄바꿈으로 접고, 격자 줄(│┌…)만
    고정폭 폰트의 비줄바꿈 라벨에 넣는다. 격자 라벨은 수평 크기 정책을
    Ignored 로 두어 패널 폭을 강제하지 않는다 -- 패널이 격자보다 좁으면
    격자 오른쪽이 살짝 잘릴 뿐, 다른 입력은 전부 접근 가능하게 남는다.
    """

    _GRID_CHARS = set("│┌┬┐├┼┤└┴┘─")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setStyleSheet("color:#888; font-size: 11px;")
        self._grid = QLabel("")
        # 'monospace' 별칭은 한국어 로케일에서 CJK 모노 폰트로 풀리는데, 그
        # 폰트는 격자 선문자(│─┌)를 2칸 폭으로 그려 격자가 어긋난다.
        self._grid.setStyleSheet(
            "font-family: 'DejaVu Sans Mono', 'Liberation Mono', monospace; "
            "color:#888; font-size: 10px;")
        self._grid.setSizePolicy(QSizePolicy.Policy.Ignored,
                                 QSizePolicy.Policy.Preferred)
        col.addWidget(self._text)
        col.addWidget(self._grid)

    def setText(self, text: str) -> None:
        grid_lines = [ln for ln in text.splitlines()
                      if set(ln) & self._GRID_CHARS]
        text_lines = [ln for ln in text.splitlines()
                      if not (set(ln) & self._GRID_CHARS)]
        self._text.setText("\n".join(text_lines))
        self._grid.setText("\n".join(grid_lines))
        self._grid.setVisible(bool(grid_lines))

    def text(self) -> str:
        return "\n".join(x for x in (self._text.text(), self._grid.text()) if x)


class PlanJsonDialog(QDialog):
    """수집 계획 원문(JSON) 편집 — 저장하려면 load_plan 검증을 통과해야 한다.

    기본 편집기는 폼 방식의 PlanEditDialog 다. 이것은 note 추가처럼 폼이
    다루지 않는 필드를 만질 때 쓰는 고급 진입로로만 남아 있다.
    """

    def __init__(self, parent, path: Path) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle(tr("수집 계획 JSON 편집 — {n}").format(n=self._path.name))
        self.setMinimumSize(680, 480)
        col = QVBoxLayout(self)
        hint = QLabel(tr(
            "저장하면 규칙 검증(scene 내 ID 유일, 따옴표 금지, target>0)을 "
            "통과해야 반영됩니다. 동사 집합(§4) 밖 문장은 경고만 합니다."))
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        col.addWidget(hint)
        self.editor = QPlainTextEdit()
        self.editor.setStyleSheet(
            "font-family: 'DejaVu Sans Mono', monospace; font-size: 12px;")
        try:
            self.editor.setPlainText(self._path.read_text(encoding="utf-8"))
        except OSError as e:
            self.editor.setPlainText("")
            QMessageBox.warning(self, tr("읽기 실패"), str(e))
        col.addWidget(self.editor, 1)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color:#e74c3c;")
        col.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

    def _save(self) -> None:
        import tempfile

        text = self.editor.toPlainText()
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                             encoding="utf-8") as tf:
                tf.write(text)
                tmp = Path(tf.name)
            plan = load_plan(tmp)
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            self.error_label.setText(f"{type(e).__name__}: {e}")
            return
        self._path.write_text(text, encoding="utf-8")
        self.warnings = plan.warnings
        super().accept()


