#!/usr/bin/env python3
"""A private notes overlay that stays out of screen shares on macOS 15 and later.

The overlay is a floating notes panel on your screen. The "Presentation Output"
window shows your live desktop, captured with ScreenCaptureKit with this app's
windows left out. In Zoom, Meet, Teams, or any other app, share the
Presentation Output window, not your entire screen.

Why a separate window: up to macOS 14, windows marked NSWindowSharingNone were
left out of screen captures. From macOS 15, ScreenCaptureKit, which meeting
apps use, captures every visible window when sharing a whole display. Sharing a
window whose content you control is the reliable way to keep the overlay out.

Setup:
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python private_overlay.py

The first run asks for Screen Recording permission for the app running Python
(Terminal, iTerm, or VS Code). Grant it in System Settings > Privacy & Security
> Screen Recording, then run the tool again.

Keep the Presentation Output window open and large: meeting apps share it at
its current size, and cannot share a minimized window. It can sit behind other
windows or on a second display. The Overlay menu in the menu bar shows or hides
the notes, makes them click-through, and changes their opacity and text size.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import AVFoundation as AV
import CoreMedia as CM
import objc
import Quartz
import ScreenCaptureKit as SCK
from AppKit import (
    NSAlert, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSColor, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSMenu, NSMenuItem, NSPanel, NSScreen, NSStatusBar,
    NSStatusWindowLevel, NSTextView, NSVariableStatusItemLength, NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces, NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowSharingNone,
    NSWindowStyleMaskClosable, NSWindowStyleMaskHUDWindow, NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskNonactivatingPanel, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled,
    NSWindowStyleMaskUtilityWindow, NSWorkspace,
)
from dispatch import dispatch_queue_create
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSURL
from PyObjCTools import AppHelper


MAX_CAPTURE_WIDTH = 2560  # Meeting apps share at 1080p or less; this keeps text sharp without wasted work.
# Meeting apps' own floating controls would otherwise appear in what you share.
MEETING_APPS = ("us.zoom.xos", "com.microsoft.teams2", "com.microsoft.teams", "Cisco-Systems.Spark", "com.cisco.webexmeetingsapp")
PRIVACY_SETTINGS = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"


def frame_is_complete(sample_buffer) -> bool:
    """ScreenCaptureKit also sends status-only samples, without an image, when nothing changed."""
    attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample_buffer, False)
    if not attachments:
        return False
    status = attachments[0].get(SCK.SCStreamFrameInfoStatus)
    return status is not None and int(status) == SCK.SCFrameStatusComplete


class FrameSink(NSObject, protocols=[objc.protocolNamed("SCStreamOutput")]):
    """Shows each captured frame as soon as it arrives."""

    def initWithLayer_(self, layer):
        self = objc.super(FrameSink, self).init()
        if self is not None:
            self.layer = layer
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        if output_type != SCK.SCStreamOutputTypeScreen or not frame_is_complete(sample_buffer):
            return
        attachments = CM.CMSampleBufferGetSampleAttachmentsArray(sample_buffer, True)
        if attachments:
            attachments[0][CM.kCMSampleAttachmentKey_DisplayImmediately] = True
        if self.layer.status() == AV.AVQueuedSampleBufferRenderingStatusFailed:
            self.layer.flush()
        self.layer.enqueueSampleBuffer_(sample_buffer)


class StreamWatcher(NSObject, protocols=[objc.protocolNamed("SCStreamDelegate")]):
    def initWithReporter_(self, reporter):
        self = objc.super(StreamWatcher, self).init()
        if self is not None:
            self.reporter = reporter
        return self

    def stream_didStopWithError_(self, stream, error):
        AppHelper.callAfter(self.reporter, f"Capture stopped: {error.localizedDescription()}", True)


class Capture:
    """Streams one display, minus this app's windows, into a display layer."""

    def __init__(self, layer, screen, fps: int, excluded_bundles: tuple[str, ...], report):
        self.layer, self.screen, self.fps, self.excluded_bundles, self.report = layer, screen, fps, excluded_bundles, report
        self.sink = FrameSink.alloc().initWithLayer_(layer)
        self.watcher = StreamWatcher.alloc().initWithReporter_(report)
        self.queue = dispatch_queue_create(b"private-overlay.frames", None)
        self.stream = None
        self.attempts = 0

    def start(self) -> None:
        self.attempts += 1
        SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            False, True, self._content_ready)

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stopCaptureWithCompletionHandler_(None)
            self.stream = None

    def _content_ready(self, content, error) -> None:
        if error is not None or content is None:
            AppHelper.callAfter(self.report, "Screen Recording permission is needed. Grant it, then run the tool again.", True)
            return
        display_id = self.screen.deviceDescription()["NSScreenNumber"]
        display = next((item for item in content.displays() if item.displayID() == display_id), None)
        if display is None:
            AppHelper.callAfter(self.report, "The chosen display is not available for capture.", True)
            return
        # Leaving out the whole app covers the overlay, this output window, and any alerts.
        own = [app for app in content.applications() if app.processID() == os.getpid()]
        if not own:
            # The windows may not be listed yet. Never capture unless the overlay is left out.
            if self.attempts < 10:
                AppHelper.callLater(0.5, self.start)
            else:
                AppHelper.callAfter(self.report, "Could not leave the notes out of the capture, so nothing is shown. Restart the tool.", True)
            return
        excluded = own + [app for app in content.applications() if app.bundleIdentifier() in self.excluded_bundles]
        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(display, excluded, [])
        scale = self.screen.backingScaleFactor()
        width, height = display.width() * scale, display.height() * scale
        if width > MAX_CAPTURE_WIDTH:
            width, height = MAX_CAPTURE_WIDTH, height * MAX_CAPTURE_WIDTH / width
        config = SCK.SCStreamConfiguration.alloc().init()
        config.setWidth_(int(width))
        config.setHeight_(int(height))
        config.setMinimumFrameInterval_(CM.CMTimeMake(1, self.fps))
        config.setPixelFormat_(Quartz.kCVPixelFormatType_32BGRA)
        config.setShowsCursor_(True)
        config.setQueueDepth_(6)
        stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(content_filter, config, self.watcher)
        added, add_error = stream.addStreamOutput_type_sampleHandlerQueue_error_(self.sink, SCK.SCStreamOutputTypeScreen, self.queue, None)
        if not added:
            AppHelper.callAfter(self.report, f"Capture could not start: {add_error.localizedDescription() if add_error else 'unknown error'}", True)
            return
        self.stream = stream

        def started(start_error):
            if start_error is not None:
                self.stream = None
                AppHelper.callAfter(self.report, f"Capture could not start: {start_error.localizedDescription()}", True)
            else:
                AppHelper.callAfter(self.report, "Share this window. Your notes are not in it.")
        stream.startCaptureWithCompletionHandler_(started)


class Notes(NSObject):
    """Saves the notes as you type, so they survive a restart."""

    def initWithPath_(self, path):
        self = objc.super(Notes, self).init()
        if self is not None:
            self.path = path
        return self

    def textDidChange_(self, notification):
        try:
            self.path.write_text(notification.object().string())
        except OSError as exc:
            print(f"Could not save notes to {self.path}: {exc}", file=sys.stderr)


class Controls(NSObject):
    """Menu actions for the overlay; the same menu is in the menu bar and the status item."""

    def initWithPanel_text_(self, panel, text):
        self = objc.super(Controls, self).init()
        if self is not None:
            self.panel, self.text = panel, text
        return self

    def toggleOverlay_(self, sender):
        if self.panel.isVisible():
            self.panel.orderOut_(None)
        else:
            self.panel.orderFrontRegardless()

    def toggleClickThrough_(self, sender):
        self.panel.setIgnoresMouseEvents_(not self.panel.ignoresMouseEvents())

    def validateMenuItem_(self, item):
        # Both copies of the menu show the current click-through state.
        if item.action() == "toggleClickThrough:":
            item.setState_(1 if self.panel.ignoresMouseEvents() else 0)
        return True

    def moreOpaque_(self, sender):
        self.panel.setAlphaValue_(min(1.0, self.panel.alphaValue() + 0.1))

    def lessOpaque_(self, sender):
        self.panel.setAlphaValue_(max(0.2, self.panel.alphaValue() - 0.1))

    def largerText_(self, sender):
        self._resize_text(2)

    def smallerText_(self, sender):
        self._resize_text(-2)

    def _resize_text(self, step):
        size = max(10.0, min(48.0, self.text.font().pointSize() + step))
        font = NSFont.systemFontOfSize_(size)
        self.text.setFont_(font)
        self.text.setTypingAttributes_({NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.whiteColor()})


class OutputWindowDelegate(NSObject):
    def windowWillClose_(self, notification):
        NSApplication.sharedApplication().terminate_(None)


def make_overlay(notes_path: Path, opacity: float, screen):
    frame = screen.visibleFrame()
    style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable
             | NSWindowStyleMaskUtilityWindow | NSWindowStyleMaskHUDWindow | NSWindowStyleMaskNonactivatingPanel)
    rect = NSMakeRect(frame.origin.x + frame.size.width - 420, frame.origin.y + frame.size.height - 520, 380, 460)
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(rect, style, NSBackingStoreBuffered, False)
    panel.setTitle_("Private notes")
    panel.setFloatingPanel_(True)
    panel.setLevel_(NSStatusWindowLevel)
    panel.setHidesOnDeactivate_(False)
    panel.setReleasedWhenClosed_(False)
    panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
    # Still hides the notes from older capture APIs, screenshots, and macOS 14 and earlier.
    panel.setSharingType_(NSWindowSharingNone)
    panel.setAlphaValue_(opacity)

    scroll = NSTextView.scrollableTextView()
    scroll.setDrawsBackground_(False)
    text = scroll.documentView()
    font = NSFont.systemFontOfSize_(16)
    text.setRichText_(False)
    text.setDrawsBackground_(False)
    text.setFont_(font)
    text.setTextColor_(NSColor.whiteColor())
    text.setInsertionPointColor_(NSColor.whiteColor())
    text.setTypingAttributes_({NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.whiteColor()})
    text.setTextContainerInset_(NSMakeSize(8, 10))
    text.setString_(notes_path.read_text() if notes_path.exists() else "Your notes are private: only you see them.\n")
    panel.setContentView_(scroll)
    return panel, text


def make_output_window(screen):
    frame = screen.frame()
    width = min(1280.0, screen.visibleFrame().size.width * 0.6)
    height = width * frame.size.height / frame.size.width
    visible = screen.visibleFrame()
    rect = NSMakeRect(visible.origin.x + 40, visible.origin.y + 40, width, height)
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(rect, style, NSBackingStoreBuffered, False)
    window.setTitle_("Presentation Output")
    window.setReleasedWhenClosed_(False)
    window.setContentAspectRatio_(NSMakeSize(frame.size.width, frame.size.height))
    view = window.contentView()
    view.setWantsLayer_(True)
    layer = AV.AVSampleBufferDisplayLayer.alloc().init()
    layer.setVideoGravity_(AV.AVLayerVideoGravityResizeAspect)
    layer.setBackgroundColor_(NSColor.blackColor().CGColor())
    layer.setFrame_(view.bounds())
    layer.setAutoresizingMask_(Quartz.kCALayerWidthSizable | Quartz.kCALayerHeightSizable)
    view.layer().addSublayer_(layer)
    return window, layer


def add_item(menu, title, action, target, key=""):
    item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
    item.setTarget_(target)
    return item


def overlay_menu(controls) -> NSMenu:
    menu = NSMenu.alloc().initWithTitle_("Overlay")
    add_item(menu, "Show or Hide Notes", "toggleOverlay:", controls, "1")
    add_item(menu, "Click Through Notes", "toggleClickThrough:", controls, "2")
    menu.addItem_(NSMenuItem.separatorItem())
    add_item(menu, "More Opaque", "moreOpaque:", controls, "=")
    add_item(menu, "Less Opaque", "lessOpaque:", controls, "-")
    add_item(menu, "Larger Text", "largerText:", controls, "]")
    add_item(menu, "Smaller Text", "smallerText:", controls, "[")
    return menu


def install_menus(app, controls) -> None:
    main = NSMenu.alloc().init()
    app_menu = NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit Private Overlay", "terminate:", "q")
    # Text editing shortcuts in the notes need a standard Edit menu.
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), ("Cut", "cut:", "x"),
                               ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
        edit_menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
    for title, submenu in (("Private Overlay", app_menu), ("Edit", edit_menu), ("Overlay", overlay_menu(controls))):
        item = main.addItemWithTitle_action_keyEquivalent_(title, None, "")
        main.setSubmenu_forItem_(submenu, item)
    app.setMainMenu_(main)


def choose_screen(index: int):
    screens = list(NSScreen.screens())
    if not 1 <= index <= len(screens):
        sys.exit(f"--display must be between 1 and {len(screens)}; display 1 is the one with the menu bar.")
    return screens[index - 1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Private notes overlay that stays out of shared screens.")
    parser.add_argument("--display", type=int, default=1, help="display to share, 1 = main display (default)")
    parser.add_argument("--notes", type=Path, default=Path.home() / ".private_overlay_notes.txt", help="where notes are saved")
    parser.add_argument("--fps", type=int, default=30, help="output frame rate (default 30)")
    parser.add_argument("--opacity", type=float, default=0.9, help="notes opacity from 0.2 to 1 (default 0.9)")
    parser.add_argument("--show-meeting-apps", action="store_true", help="include Zoom, Teams, and Webex windows in the output")
    parser.add_argument("--quit-after", type=float, default=0, help=argparse.SUPPRESS)  # For automated checks.
    args = parser.parse_args()

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    screen = choose_screen(args.display)

    output, layer = make_output_window(screen)
    output_delegate = OutputWindowDelegate.alloc().init()
    output.setDelegate_(output_delegate)
    panel, text = make_overlay(args.notes, max(0.2, min(1.0, args.opacity)), screen)
    notes = Notes.alloc().initWithPath_(args.notes)
    text.setDelegate_(notes)

    controls = Controls.alloc().initWithPanel_text_(panel, text)
    install_menus(app, controls)
    status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
    status_item.button().setTitle_("◐")
    status_item.setMenu_(overlay_menu(controls))

    def report(message: str, problem: bool = False) -> None:
        output.setSubtitle_(message)
        if problem:
            print(message, file=sys.stderr)

    def ask_for_permission() -> None:
        Quartz.CGRequestScreenCaptureAccess()
        report("Waiting for Screen Recording permission.")
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Allow Screen Recording, then restart")
        alert.setInformativeText_("The Presentation Output window shows your screen, so macOS needs your permission. "
                                  "Turn on Screen Recording for the app that runs this tool, then quit and run it again. "
                                  "Your notes work in the meantime.")
        alert.addButtonWithTitle_("Open Settings")
        alert.addButtonWithTitle_("Later")
        if alert.runModal() == 1000:  # NSAlertFirstButtonReturn
            NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(PRIVACY_SETTINGS))

    output.makeKeyAndOrderFront_(None)
    panel.orderFrontRegardless()
    app.activateIgnoringOtherApps_(True)

    # The local objects above stay referenced while the event loop runs.
    capture = Capture(layer, screen, max(1, min(60, args.fps)), () if args.show_meeting_apps else MEETING_APPS, report)
    if Quartz.CGPreflightScreenCaptureAccess():
        report("Starting capture…")
        AppHelper.callAfter(capture.start)
    elif not args.quit_after:
        AppHelper.callAfter(ask_for_permission)
    else:
        report("Waiting for Screen Recording permission.")
    if args.quit_after:
        AppHelper.callLater(args.quit_after, app.terminate_, None)
    try:
        AppHelper.runEventLoop()
    finally:
        capture.stop()


if __name__ == "__main__":
    main()
