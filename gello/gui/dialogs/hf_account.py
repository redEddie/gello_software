from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gello.gui.i18n import tr


def hf_account() -> tuple[str, str]:
    """(display text, css color) describing who a --push would upload as.

    Imported lazily and defensively: this runs on a GUI thread at startup and
    huggingface_hub may be missing, or the token cached but expired.
    """
    try:
        from huggingface_hub import whoami

        info = whoami()
        name = info.get("name", "?")
        orgs = [o["name"] for o in info.get("orgs", []) if isinstance(o, dict) and "name" in o]
        text = f"HF 로그인: {name}"
        if orgs:
            text += f"  (orgs: {', '.join(orgs)})"
        return text, "#27ae60"
    except ImportError:
        return "HF: huggingface_hub 미설치 -- 업로드 불가", "#e74c3c"
    except Exception:
        return "HF: 로그인 안 됨 -- 터미널에서 `hf auth login` 실행", "#e67e22"


def hf_stored_accounts() -> list[dict]:
    """Every locally stored HF token, with the account it actually belongs to.

    huggingface_hub keeps several tokens at once (``~/.cache/huggingface/
    stored_tokens``) and one of them is active. The stored *name* is the
    token's display name, not the account -- two profiles can belong to the
    same person, which is exactly what this machine had. So each token is
    resolved to its real username here; picking between "franka" and
    "oauth-gibeom25" tells you nothing on its own.

    Never raises: the switcher must still open when the network is down, just
    with '확인 실패' next to the entries it could not resolve.
    """
    try:
        from huggingface_hub import HfApi, get_token
        from huggingface_hub.utils._auth import get_stored_tokens
    except ImportError:
        return []
    try:
        stored = get_stored_tokens()
        active = get_token()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for name, token in stored.items():
        entry = {"profile": name, "active": token == active, "user": None, "orgs": [], "error": ""}
        try:
            info = HfApi(token=token).whoami()
            entry["user"] = info.get("name")
            entry["orgs"] = [o["name"] for o in info.get("orgs", [])
                             if isinstance(o, dict) and "name" in o]
        except Exception as e:  # noqa: BLE001
            entry["error"] = type(e).__name__
        out.append(entry)
    return out


def hf_switch_account(profile: str) -> str:
    """Makes ``profile`` the active token. Returns the resulting username.

    Subprocesses pick this up because they read the token file at startup, so
    an upload launched after the switch uploads as the new account -- one
    already running does not.
    """
    from huggingface_hub import HfApi, auth_switch

    auth_switch(profile)
    return HfApi().whoami().get("name", "?")


def hf_add_account(token: str) -> tuple[str, str]:
    """Stores a new token and makes it active. Returns (profile, username)."""
    from huggingface_hub._login import _validate_and_save_token

    return _validate_and_save_token(token.strip(), add_to_git_credential=False)


class HfAccountDialog(QDialog):
    """Switch between stored HF accounts, or add one by pasting a token.

    This machine is shared, and an upload goes out as whoever's token happens
    to be active -- which is not something to discover from the commit history
    afterwards. Switching is one click here instead of `hf auth login` in a
    terminal, and adding a second person is pasting their token once.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Hugging Face 계정"))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        self._switched_to = None

        layout.addWidget(QLabel(tr(
            "업로드는 여기서 선택된 계정으로 나갑니다. 이 PC는 공용이므로 "
            "올리기 전에 확인하세요.")))

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([tr("계정"), tr("소속 org"), tr("저장된 이름"), tr("상태")])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 160)
        self.tree.setColumnWidth(1, 170)
        self.tree.setColumnWidth(2, 150)
        self.tree.setMinimumHeight(140)
        layout.addWidget(self.tree)

        row = QHBoxLayout()
        self.switch_btn = QPushButton(tr("선택한 계정으로 전환"))
        self.switch_btn.clicked.connect(self._on_switch)
        row.addWidget(self.switch_btn)
        refresh = QPushButton(tr("새로고침"))
        refresh.clicked.connect(self._reload)
        row.addWidget(refresh)
        row.addStretch()
        layout.addLayout(row)

        add_box = QGroupBox(tr("계정 추가"))
        acol = QVBoxLayout(add_box)
        acol.addWidget(QLabel(tr(
            "huggingface.co > Settings > Access Tokens 에서 write 권한 토큰을 "
            "만들어 붙여넣으세요. 추가하면 바로 그 계정으로 전환됩니다.")))
        trow = QHBoxLayout()
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("hf_****************")
        trow.addWidget(self.token_edit, 1)
        add_btn = QPushButton(tr("추가하고 전환"))
        add_btn.clicked.connect(self._on_add)
        trow.addWidget(add_btn)
        acol.addLayout(trow)
        layout.addWidget(add_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self.tree.clear()
        accounts = hf_stored_accounts()
        if not accounts:
            self.status.setText(tr("저장된 토큰이 없습니다. 아래에서 토큰을 추가하세요."))
            self.switch_btn.setEnabled(False)
            return
        self.switch_btn.setEnabled(True)
        same = len({a["user"] for a in accounts if a["user"]}) == 1 and len(accounts) > 1
        for a in accounts:
            item = QTreeWidgetItem([
                a["user"] or "?",
                ", ".join(a["orgs"]) or "-",
                a["profile"],
                tr("● 사용 중") if a["active"] else (a["error"] or ""),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, a["profile"])
            if a["active"]:
                for c in range(4):
                    item.setForeground(c, Qt.GlobalColor.darkGreen)
            self.tree.addTopLevelItem(item)
            if a["active"]:
                self.tree.setCurrentItem(item)
        self.status.setText(tr(
            "저장된 토큰이 모두 같은 계정({u})입니다 -- 다른 사람으로 바꾸려면 "
            "그 사람의 토큰을 추가해야 합니다.").format(u=accounts[0]["user"])
            if same else "")

    def _on_switch(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        profile = items[0].data(0, Qt.ItemDataRole.UserRole)
        try:
            user = hf_switch_account(profile)
        except Exception as e:  # noqa: BLE001
            self.status.setText(tr("전환 실패: {e}").format(e=f"{type(e).__name__}: {e}"))
            return
        self._switched_to = user
        self.status.setText(tr("이제 {u} 계정으로 업로드합니다. 이미 실행 중인 업로드는 "
                               "바뀌지 않습니다.").format(u=user))
        self._reload()

    def _on_add(self) -> None:
        token = self.token_edit.text().strip()
        if not token:
            return
        try:
            profile, user = hf_add_account(token)
        except Exception as e:  # noqa: BLE001
            self.status.setText(tr("토큰 추가 실패: {e}").format(e=f"{type(e).__name__}: {e}"))
            return
        self.token_edit.clear()
        self._switched_to = user
        self.status.setText(tr("{u} 추가 완료 (저장된 이름 {p}). 이제 이 계정으로 "
                               "업로드합니다.").format(u=user, p=profile))
        self._reload()

    def switched_to(self) -> "str | None":
        return self._switched_to
