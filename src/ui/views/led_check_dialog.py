"""Pass/Fail checkbox prompt for the manual LED indication test.

Spec Rev.1.1 §1.1.6.3 names the control explicitly: "If the LED test is
successful, check the 'Pass' Checkbox. If the test fails and the LED does not
light up, check the 'Fail' Checkbox." §1.1.4.2 repeats it — "The user must
select the LED Pass/Fail Checkbox; otherwise, the subsequent DC Load tests will
not be permitted to proceed."

A Yes/No message box was functionally equivalent but did not match the named
control, so this dialog supplies the two checkboxes the spec asks for.

Deliberate behaviours, all of them safety-relevant:

* **Neither box is pre-ticked.** The operator has to make a positive choice;
  there is no default that could be accepted by reflex.
* **The boxes are mutually exclusive.** Ticking one clears the other, so a run
  can never carry a contradictory answer.
* **OK stays disabled until one is ticked**, which is how "the subsequent DC
  Load tests will not be permitted to proceed" is enforced at the GUI.
* **The dialog cannot be dismissed without answering** — no close button, Esc
  rejected. An unanswered LED test is not a Fail and not a Pass; letting the
  window be closed would leave the runner blocked or, worse, resumed on a
  fabricated answer.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)


class LedCheckDialog(QDialog):
    """Ask the operator to tick Pass or Fail for the manual LED check."""

    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LED Indication Test")
        # No close button: the answer is mandatory (§1.1.4.2).
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setModal(True)

        layout = QVBoxLayout(self)
        prompt = QLabel(message)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self.chk_pass = QCheckBox("Pass")
        self.chk_fail = QCheckBox("Fail")
        for box in (self.chk_pass, self.chk_fail):
            box.setChecked(False)
        self.chk_pass.toggled.connect(self._on_pass_toggled)
        self.chk_fail.toggled.connect(self._on_fail_toggled)

        row = QHBoxLayout()
        row.addWidget(self.chk_pass)
        row.addWidget(self.chk_fail)
        row.addStretch(1)
        layout.addLayout(row)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self._buttons.accepted.connect(self.accept)
        # Enabled only once a box is ticked — this is the gate that stops the
        # DC Load tests from proceeding on an unanswered LED check.
        self._ok_button().setEnabled(False)
        layout.addWidget(self._buttons)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ok_button(self):
        return self._buttons.button(QDialogButtonBox.StandardButton.Ok)

    def _on_pass_toggled(self, checked: bool) -> None:
        if checked and self.chk_fail.isChecked():
            # `setChecked` re-enters this slot pair; guarded by the `checked`
            # test above so the two boxes cannot ping-pong.
            self.chk_fail.setChecked(False)
        self._refresh_ok()

    def _on_fail_toggled(self, checked: bool) -> None:
        if checked and self.chk_pass.isChecked():
            self.chk_pass.setChecked(False)
        self._refresh_ok()

    def _refresh_ok(self) -> None:
        self._ok_button().setEnabled(
            self.chk_pass.isChecked() or self.chk_fail.isChecked()
        )

    def reject(self) -> None:  # noqa: D102 - Esc must not dismiss the prompt
        # Swallowed on purpose: an unanswered LED test has no safe default.
        return

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def result_is_pass(self) -> bool:
        """True when the operator ticked Pass. False for Fail."""
        return self.chk_pass.isChecked()
