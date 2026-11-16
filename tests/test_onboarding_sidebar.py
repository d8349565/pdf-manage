"""Sidebar and first-use preferences, with isolated settings and no user documents."""
import pymupdf
import pytest
from PyQt6 import sip
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel

from app.ui import MainWindow


@pytest.fixture(scope='session')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def windows(qapp):
    opened = []

    def create():
        window = MainWindow()
        opened.append(window)
        window.show()
        qapp.processEvents()
        return window

    yield create
    for window in reversed(opened):
        if not sip.isdeleted(window):
            intro = getattr(window, '_contribution_intro', None)
            if intro is not None:
                intro.reject()
            dialog = getattr(window, '_quick_start_dialog', None)
            if dialog is not None:
                dialog.reject()
            for tab in window._tabs:
                tab.ws.reset()
            window.close()
    qapp.processEvents()


@pytest.mark.parametrize('entry', ['pin', 'menu'])
def test_sidebar_remembers_both_explicit_choices_after_restart(windows, qapp, entry):
    first = windows()
    assert first.immersive.auto_action.isChecked()
    if entry == 'pin':
        first.immersive.pin.click()
    else:
        first.immersive.auto_action.trigger()
    first.close()
    qapp.processEvents()
    fixed = windows()
    assert not fixed.immersive.auto_action.isChecked()
    assert fixed.immersive.pin.isChecked() and fixed.act_pages.isChecked()
    assert fixed.reader_split.widget(0) is fixed.left_panel
    if entry == 'pin':
        fixed.immersive.pin.click()
    else:
        fixed.immersive.auto_action.trigger()
    fixed.close()
    qapp.processEvents()
    automatic = windows()
    assert automatic.immersive.auto_action.isChecked()
    assert not automatic.immersive.pin.isChecked()
    assert automatic.left_panel.parent() is automatic.immersive.overlay


@pytest.mark.parametrize('visible', [False, True])
@pytest.mark.parametrize('fullscreen', [False, True])
def test_pinned_sidebar_remembers_visibility_after_restart(windows, qapp, tmp_path, visible, fullscreen):
    source = tmp_path / 'sidebar.pdf'
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), 'Generated sidebar sample')
        document.save(source)
    window = windows()
    assert window.open_files_in_tabs([str(source)])
    window.immersive.pin.click()
    window.act_pages.setChecked(visible)
    window.immersive.fullscreen_action.setChecked(fullscreen)
    for tab in window._tabs:
        tab.ws.reset()
    window.close()
    qapp.processEvents()
    restored = windows()
    assert restored.immersive.pin.isChecked()
    assert not restored.immersive.auto_action.isChecked()
    assert restored.act_pages.isChecked() == visible
    assert restored.reader_split.widget(0) is restored.left_panel
    assert restored.open_files_in_tabs([str(source)])
    qapp.processEvents()
    assert restored.left_panel.isVisible() == visible
    restored.act_pages.trigger()
    assert restored.left_panel.isVisible() != visible
    assert restored.immersive.pin.isChecked()


def test_closing_in_fullscreen_does_not_replace_the_saved_sidebar_choice(windows, qapp):
    window = windows()
    window.immersive.pin.click()
    window.immersive.fullscreen_action.setChecked(True)
    assert window.immersive.auto_action.isChecked()
    window.close()
    qapp.processEvents()
    restored = windows()
    assert not restored.immersive.auto_action.isChecked()
    assert restored.immersive.pin.isChecked() and restored.act_pages.isChecked()


def test_restoring_interface_layout_resets_the_sidebar_preference(windows, qapp):
    window = windows()
    window.immersive.pin.click()
    window.reset_ui_preferences()
    window.close()
    qapp.processEvents()
    restored = windows()
    assert restored.immersive.auto_action.isChecked()
    assert not restored.immersive.pin.isChecked()


def test_an_explicit_sidebar_choice_in_fullscreen_also_applies_after_leaving(windows):
    window = windows()
    window.immersive.fullscreen_action.setChecked(True)
    window.immersive.pin.click()
    window.immersive.fullscreen_action.setChecked(False)
    assert not window.immersive.auto_action.isChecked()
    assert window.immersive.pin.isChecked() and window.act_pages.isChecked()
    restored = windows()
    assert not restored.immersive.auto_action.isChecked()


def test_guide_shows_real_ui_images_finishes_once_and_can_be_replayed(windows, qapp):
    window = windows()
    window._window_settings.setValue('onboarding/contributionsV1Seen', True)
    window.show_first_run_guide()
    qapp.processEvents()
    dialog = window._quick_start_dialog
    assert dialog.isVisible() and dialog.progress.text() == '1 / 5'
    assert not dialog.back_button.isEnabled()
    for index in range(5):
        assert dialog.step == index
        assert not dialog.preview.source.isNull(), 'Each step needs a packaged current-UI capture'
        assert not dialog.preview.pixmap().isNull()
        dialog.next_button.click()
    assert window._quick_start_dialog is None
    window.close()
    qapp.processEvents()
    restarted = windows()
    restarted.show_first_run_guide()
    assert getattr(restarted, '_quick_start_dialog', None) is None
    restarted.quick_start_action.trigger()
    assert restarted._quick_start_dialog is not None
    restarted._quick_start_dialog.skip_button.click()


@pytest.mark.parametrize('dismissal', ['skip', 'escape', 'close'])
def test_dismissing_the_guide_is_remembered(windows, qapp, dismissal):
    window = windows()
    window._window_settings.setValue('onboarding/contributionsV1Seen', True)
    window.show_first_run_guide()
    qapp.processEvents()
    dialog = window._quick_start_dialog
    dialog.next_button.click()
    dialog.back_button.click()
    assert dialog.step == 0
    if dismissal == 'skip':
        dialog.skip_button.click()
    elif dismissal == 'escape':
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    else:
        dialog.close()
    assert window._quick_start_dialog is None
    window.show_first_run_guide()
    assert window._quick_start_dialog is None


@pytest.mark.parametrize('dismissal', ['start', 'escape', 'close'])
def test_contribution_intro_reaches_existing_users_once_and_survives_layout_reset(windows, qapp, monkeypatch, dismissal):
    window = windows()
    window._window_settings.setValue('onboarding/quickStartSeen', True)
    window.updates.set_automatic(False)
    window.updates.set_reminders_enabled(False)
    window.show_first_run_guide()
    intro = window._contribution_intro
    window.show_first_run_guide()
    assert window._contribution_intro is intro and intro.isVisible()
    assert not window._window_settings.value('onboarding/contributionsV1Seen', False, type=bool)
    names = [label.text() for label in intro.findChildren(QLabel) if label.objectName() == 'contributorName']
    assert len(names) == len(set(names)) == 49
    button = intro.findChild(QDialogButtonBox).buttons()[0]
    assert button.text() == '开始使用'
    if dismissal == 'start':
        button.click()
    elif dismissal == 'escape':
        QTest.keyClick(intro, Qt.Key.Key_Escape)
    else:
        intro.close()
    qapp.processEvents()
    assert window._contribution_intro is None
    assert window._window_settings.value('onboarding/contributionsV1Seen', False, type=bool)
    assert getattr(window, '_quick_start_dialog', None) is None
    window.reset_ui_preferences()
    window.close()
    qapp.processEvents()
    import app
    monkeypatch.setattr(app, '__version__', '1.0.1')
    restarted = windows()
    restarted.show_first_run_guide()
    assert getattr(restarted, '_contribution_intro', None) is None
    assert getattr(restarted, '_quick_start_dialog', None) is None


def test_new_user_sees_contributions_before_the_existing_guide(windows, qapp):
    window = windows()
    window.show_first_run_guide()
    intro = window._contribution_intro
    assert QApplication.activeModalWidget() is intro
    assert getattr(window, '_quick_start_dialog', None) is None
    intro.reject()
    qapp.processEvents()
    guide = window._quick_start_dialog
    assert guide.isVisible() and QApplication.activeModalWidget() is guide
    guide.skip_button.click()
    window.show_first_run_guide()
    assert window._contribution_intro is None and window._quick_start_dialog is None


@pytest.mark.parametrize('busy', ['opening', 'saving', 'task', 'modal'])
def test_contribution_intro_waits_until_the_main_window_is_idle(windows, qapp, busy):
    window = windows()
    window._window_settings.setValue('onboarding/quickStartSeen', True)
    modal = QDialog(window)
    if busy == 'opening': window._opening_paths = ['pending startup path']
    elif busy == 'saving': window._saving = True
    elif busy == 'task': window._task_dialog = modal
    else: modal.open()
    window.show_first_run_guide()
    assert getattr(window, '_contribution_intro', None) is None
    assert not window._window_settings.value('onboarding/contributionsV1Seen', False, type=bool)
    window._opening_paths = []
    window._saving = False
    window._task_dialog = None
    modal.reject()
    QTest.qWait(450)
    assert window._contribution_intro.isVisible()
    window._contribution_intro.reject()


def test_manual_contributions_also_record_the_milestone_receipt(windows, qapp):
    window = windows()
    window._window_settings.setValue('onboarding/quickStartSeen', True)
    seen = []
    def dismiss():
        dialog = QApplication.activeModalWidget()
        seen.append(dialog.windowTitle())
        dialog.reject()
    QTimer.singleShot(0, dismiss)
    window.contribution_action.trigger()
    assert seen == ['贡献']
    assert window._window_settings.value('onboarding/contributionsV1Seen', False, type=bool)
    window.show_first_run_guide()
    assert getattr(window, '_contribution_intro', None) is None
