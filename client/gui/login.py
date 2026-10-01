"""Sign-in / create-account window (authenticates against the cloud REST API)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from ..cloud_api import CloudClient
from . import icons
from .common import button, hbox, label, run_bg
from .theme import C


class LoginDialog(QDialog):
    def __init__(self, core, settings: dict, app_title: str, on_cloud_url_changed):
        super().__init__()
        self.core = core
        self.settings = settings
        self.on_cloud_url_changed = on_cloud_url_changed
        self.mode = "login"
        self.setWindowTitle(f"{app_title} - Sign in")
        self.setMinimumWidth(420)
        self.setObjectName("LoginDialog")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 28, 32, 24)
        lay.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(icons.icon("transfers", C["accent"], size=40).pixmap(40, 40))
        title = label(app_title, "PageTitle")
        lay.addLayout(hbox(logo, title, None, spacing=12))
        self.subtitle = label("Sign in to FileJet", muted=True, wrap=True)
        lay.addWidget(self.subtitle)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        form.setVerticalSpacing(8)
        self.username = QLineEdit(placeholderText="Username or e-mail")
        self.email = QLineEdit(placeholderText="you@example.com")
        self.display = QLineEdit(placeholderText="Shown to your contacts")
        self.password = QLineEdit(placeholderText="Password", echoMode=QLineEdit.Password)
        self.password2 = QLineEdit(placeholderText="Repeat password", echoMode=QLineEdit.Password)
        self.rows = {}
        for key, text, w in (("username", "Username", self.username), ("email", "E-mail", self.email),
                             ("display", "Display name", self.display), ("password", "Password", self.password),
                             ("password2", "Repeat", self.password2)):
            lb = QLabel(text)
            form.addRow(lb, w)
            self.rows[key] = (lb, w)
        lay.addLayout(form)
        self.error = label("", wrap=True)
        self.error.setStyleSheet(f"color: {C['bad']};")
        lay.addWidget(self.error)

        self.submit = button("Sign in", slot=self._submit)
        self.submit.setDefault(True)
        lay.addWidget(self.submit)
        self.toggle = button("No account? Create one", "link", slot=self._toggle)
        lay.addWidget(self.toggle, alignment=Qt.AlignCenter)

        self.server_label = label("", muted=True)
        change = button("Change", "link", slot=self._change_server)
        lay.addLayout(hbox(self.server_label, change, None))
        self._update_server_label()
        self._apply_mode()
        self.password.returnPressed.connect(self._submit)
        self.password2.returnPressed.connect(self._submit)

    def _update_server_label(self):
        self.server_label.setText(f"Cloud: {self.settings['cloud_url']}")

    def _apply_mode(self):
        reg = self.mode == "register"
        for key in ("email", "display", "password2"):
            for w in self.rows[key]:
                w.setVisible(reg)
        self.rows["username"][0].setText("Username" if reg else "Username or e-mail")
        self.username.setPlaceholderText("letters, digits, . _ -" if reg else "Username or e-mail")
        self.submit.setText("Create account" if reg else "Sign in")
        self.toggle.setText("Already have an account? Sign in" if reg else "No account? Create one")
        self.subtitle.setText("Create your FileJet account"
                              if reg else "Sign in to FileJet")
        self.error.setText("")
        self.adjustSize()

    def _toggle(self):
        self.mode = "register" if self.mode == "login" else "login"
        self._apply_mode()

    def _change_server(self):
        from PySide6.QtWidgets import QInputDialog
        url, ok = QInputDialog.getText(self, "Cloud server", "Cloud app URL:", text=self.settings["cloud_url"])
        if ok and url.strip():
            self.settings["cloud_url"] = url.strip().rstrip("/") + "/"
            self.on_cloud_url_changed(self.settings["cloud_url"])
            self._update_server_label()

    def _busy(self, busy: bool):
        self.submit.setEnabled(not busy)
        self.submit.setText("Please wait..." if busy else ("Create account" if self.mode == "register" else "Sign in"))

    def _submit(self):
        user, pw = self.username.text().strip(), self.password.text()
        if not user or not pw:
            self.error.setText("Enter your username and password.")
            return
        if self.mode == "register":
            if self.password2.text() != pw:
                self.error.setText("Passwords do not match.")
                return
            email, display = self.email.text().strip(), self.display.text().strip()
            job = lambda: self.core.register(user, email, pw, display)     # noqa: E731
        else:
            job = lambda: self.core.login(user, pw)                        # noqa: E731
        self._busy(True)
        self.error.setText("")
        run_bg(job, ok=lambda _me: self.accept(), err=self._failed)

    def _failed(self, exc):
        self._busy(False)
        self.error.setText(str(exc))

    def open_web_register(self):
        QDesktopServices.openUrl(QUrl(self.settings["cloud_url"] + "register/"))
